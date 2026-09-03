"""
Enemy: A hostile entity in combat.

Enemies have a base level, a defense level scaled relative to their base,
HP, status effects, and passives that trigger during combat phases.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

from src.passive import Passive


TREMOR_SUBTYPES: tuple[str, ...] = (
    "decay",
    "reverb",
    "everlasting",
    "chain",
    "scorch",
    "hemmorage",
)


@dataclass
class Enemy:
    """
    A combat entity with stats, statuses, and passives.

    Attributes
    ----------
    name : str
        Display name.
    base_level : int
        The entity's base level — governs overall stat scaling.
    defense_level : int
        Defense level expressed as a signed offset from *base_level*.
        Effective defense = base_level + defense_level.
    hp : int
        Current hit points.
    max_hp : int
        Maximum hit points.
    phys_res : dict[str, float]
        Physical resistance by type (e.g. ``{"Slash": 1.0, "Pierce": 0.5}``).
        ``1.0`` = neutral, ``>1.0`` = resists, ``<1.0`` = weak.
    sin_res : dict[str, float]
        Sin resistance by type (e.g. ``{"Wrath": 1.0, "Lust": 0.5}``).
    speed : int
        The enemy's speed value, used for speed-comparison-based effects.
    observation_level : int
        Number of observation stacks on this enemy (3 %% per level to static).
    statuses : dict[str, Any]
        Active status effects keyed by status name.  Values can be
        stacks, duration, or any status-specific payload.
    passives : list[Passive]
        Passives that may trigger at their designated phases.
    stagger_thresholds : list[int]
        Flat HP breakpoints. If HP falls from >= threshold to < threshold,
        the entity becomes staggered.
    is_staggered : bool
        Whether the entity is currently staggered.
    """

    name: str = "Enemy"
    base_level: int = 1
    defense_level: int = 0
    hp: int = 100
    max_hp: int = 100
    phys_res: dict[str, float] = field(default_factory=dict)
    sin_res: dict[str, float] = field(default_factory=dict)
    speed: int = 0
    observation_level: int = 0
    statuses: dict[str, Any] = field(default_factory=dict)
    next_turn_statuses: dict[str, Any] = field(default_factory=dict)
    passives: list[Passive] = field(default_factory=list)
    stagger_thresholds: list[int] = field(default_factory=list)
    is_staggered: bool = False
    stagger_turns_remaining: int = 0
    stagger_level: int = 0

    # ── derived stats ────────────────────────────────────────────────

    @property
    def effective_defense(self) -> int:
        """Actual defense value = base_level + defense_level offset."""
        return self.base_level + self.defense_level

    # ── HP helpers ───────────────────────────────────────────────────

    @property
    def is_alive(self) -> bool:
        return self.hp > 0

    def take_damage(self, amount: int) -> int:
        """
        Reduce HP by *amount* (clamped to 0).  Returns actual damage taken.
        """
        old_hp = self.hp
        actual = min(amount, self.hp)
        self.hp -= actual
        self._update_stagger_from_hp(old_hp)
        return actual

    def heal(self, amount: int) -> int:
        """Heal up to *max_hp*.  Returns actual amount healed."""
        actual = min(amount, self.max_hp - self.hp)
        self.hp += actual
        return actual

    def _update_stagger_from_hp(self, old_hp: int) -> None:
        """
        Set ``is_staggered`` when HP crosses below any configured threshold.

        Stagger lasts for the current turn plus the next turn.
        Stagger level tracks how many thresholds are crossed during the
        first stagger turn.
        """
        if not self.stagger_thresholds:
            return

        current_hp = self.hp
        crossed_count = 0
        for threshold in self.stagger_thresholds:
            if old_hp >= threshold and current_hp < threshold:
                crossed_count += 1

        if crossed_count <= 0:
            return

        if not self.is_staggered:
            self.apply_stagger(turns=2)
            self.stagger_level = 0

        # Level only grows during the first stagger turn.
        if self.stagger_turns_remaining == 2:
            self.stagger_level += crossed_count

    def raise_stagger_threshold(self, amount: int) -> int:
        """
        Raise the highest stagger threshold by ``amount``.

        Returns the actual amount raised (after clamping).
        If the new threshold ends up above current HP, stagger is applied
        immediately ("forceful" stagger).
        """
        if amount <= 0 or not self.stagger_thresholds:
            return 0

        idx = max(range(len(self.stagger_thresholds)), key=self.stagger_thresholds.__getitem__)
        old_threshold = self.stagger_thresholds[idx]
        new_threshold = min(self.max_hp, old_threshold + amount)
        self.stagger_thresholds[idx] = new_threshold

        if self.hp < new_threshold:
            self.apply_stagger(turns=2)

        return max(0, new_threshold - old_threshold)

    def apply_stagger(self, turns: int = 2) -> None:
        """Apply stagger for at least ``turns`` turns."""
        applied_turns = max(0, int(turns))
        if applied_turns <= 0:
            return
        self.is_staggered = True
        self.stagger_turns_remaining = max(self.stagger_turns_remaining, applied_turns)
        if self.stagger_level <= 0:
            self.stagger_level = 1

    def tick_stagger_duration(self) -> None:
        """Advance stagger duration by one turn and clear when it expires."""
        if self.stagger_turns_remaining <= 0:
            self.stagger_turns_remaining = 0
            self.is_staggered = False
            self.stagger_level = 0
            return

        self.stagger_turns_remaining -= 1
        if self.stagger_turns_remaining <= 0:
            self.stagger_turns_remaining = 0
            self.is_staggered = False
            self.stagger_level = 0

    def get_stagger_physical_resistance(self) -> float:
        """Return physical resistance multiplier implied by current stagger level."""
        if not self.is_staggered:
            return 1.0

        level = max(1, int(self.stagger_level))
        return 2.0 + (level - 1) * 0.5

    # ── status helpers ───────────────────────────────────────────────

    def has_status(self, status_name: str) -> bool:
        return status_name in self.statuses

    def get_status(self, status_name: str, default: Any = None) -> Any:
        return self.statuses.get(status_name, default)

    def set_status(self, status_name: str, value: Any) -> None:
        self.statuses[status_name] = value
        if status_name in {"tremor_type", "tremor_potency", "tremor_count"}:
            self.refresh_tremor_decay_effect()

    def remove_status(self, status_name: str) -> None:
        self.statuses.pop(status_name, None)
        if status_name in {"tremor_type", "tremor_potency", "tremor_count"}:
            self.refresh_tremor_decay_effect()

    def queue_next_turn_status(self, status_name: str, value: Any) -> None:
        """Queue a status to be applied at the start of the next turn."""
        self.next_turn_statuses[status_name] = value

    def apply_queued_statuses(self) -> None:
        """Drain queued statuses into the active status pool."""
        for status_name, value in self.next_turn_statuses.items():
            self.set_status(status_name, value)
        self.next_turn_statuses.clear()

    # ── tremor helpers ───────────────────────────────────────────────

    def has_tremor(self) -> bool:
        """Return True when the entity currently has active Tremor."""
        return (
            int(self.get_status("tremor_potency", 0)) > 0
            and int(self.get_status("tremor_count", 0)) > 0
        )

    def is_tremor_superposition(self) -> bool:
        """Return True when Tremor Superposition is currently applied."""
        if bool(self.get_status("tremor_superposition", False)):
            return True
        tremor_type = str(self.get_status("tremor_type", "")).strip().lower()
        return tremor_type == "superposition"

    def convert_tremor_amplitude(self, new_type: str) -> bool:
        """
        Convert current Tremor (or converted Tremor) to ``new_type``.

        Rules:
        - Conversion requires existing Tremor (potency and count > 0).
        - Conversion is blocked by Tremor Superposition.
        - Tremor potency/count are preserved.
        - Existing Tremor subtype is overwritten unconditionally.

        Returns
        -------
        bool
            ``True`` if conversion was applied, else ``False``.
        """
        if not self.has_tremor() or self.is_tremor_superposition():
            return False

        self.set_status("tremor_type", str(new_type).strip().lower())
        return True

    def refresh_tremor_decay_effect(self) -> None:
        """
        Recompute Tremor Decay's derived defense-level-down status.

        Rule:
        - If active Tremor type is "decay", lose 1 defense level per 4
          Tremor potency (floor division).
        - Otherwise remove the derived status.
        """
        tremor_type = str(self.get_status("tremor_type", "")).strip().lower()
        if tremor_type != "decay" or not self.has_tremor():
            self.remove_status("defense_level_down")
            return

        tremor_potency = max(0, int(self.get_status("tremor_potency", 0)))
        down = tremor_potency // 4
        if down > 0:
            self.statuses["defense_level_down"] = down
        else:
            self.remove_status("defense_level_down")

    # ── passive helpers ──────────────────────────────────────────────

    def add_charge_count(self, amount: int) -> None:
        """Add *amount* to charge_count, auto-setting potency to 1 if it was 0."""
        current_potency = self.get_status("charge_potency", 0)
        current_count = self.get_status("charge_count", 0)
        new_count = max(0, current_count + amount)
        self.set_status("charge_count", min(new_count, 99))
        if current_potency == 0 and new_count > 0:
            self.set_status("charge_potency", 1)

    def add_passive(self, passive: Passive) -> None:
        self.passives.append(passive)

    def reset_passives(self) -> None:
        """Reset proc counters on all passives (call at turn start)."""
        for p in self.passives:
            p.reset_procs()
