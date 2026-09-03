"""
Environment: Single-use combat context created fresh for every skill resolution.

Holds every modifier, flag, and accumulator.  Acts as the central hub that
the GameLoop, Skill coin loop, and all Effect / Passive callbacks operate on.

Replaces the earlier CombatContext with the full damage-calculation pipeline
from the reference guide.
"""

from __future__ import annotations

import math
import random
from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from src.coin import Coin
    from src.effect import Effect
    from src.enemy import Enemy
    from src.skill import Skill
    from src.unit import Unit


@dataclass
class CoinEnvironment:
    """
    Per-coin mutable state created fresh for each coin resolution.

    Stores coin-scoped data that resets every coin: a coin-specific
    dynamic modifier, reuse tracking, and arbitrary per-coin flags.

    Attributes
    ----------
    dynamic : float
        Additive modifier specific to this coin (e.g. +0.30 for +30 %
        at 10+ Speed).  Starts at 0.0 each coin; **added** to the main
        ``Environment.dynamic`` to form the total dynamic multiplier
        used in the damage formula.
    is_reuse : bool
        ``True`` when the current resolution is a reuse of the coin.
    reuse_count : int
        How many times this coin has been reused so far.
    flags : dict[str, Any]
        Arbitrary per-coin flags (e.g. deathrite trigger tracking).
        Cleared automatically when a new ``CoinEnvironment`` is created.
    """

    dynamic: float = 0.0
    is_reuse: bool = False
    reuse_count: int = 0
    flags: dict[str, Any] = field(default_factory=dict)


@dataclass
class Environment:
    """
    Mutable state bag for one skill's complete resolution.

    Created at the start of each skill in the turn, torn down after it.
    Every effect callback receives this object and mutates it.

    Multiplier model
    ----------------
    ``damage = max(floor(current_power × static × (dynamic + coin_dynamic)), 1)``

    * **static** — additive multiplier built from defense stats, resists,
      OL difference, observation, clash wins, and crit bonus.
    * **dynamic** — skill-wide multiplicative multiplier, starts at 1.0.
    * **coin_dynamic** — per-coin additive modifier (from ``CoinEnvironment``),
      starts at 0.0 each coin.  The total dynamic used in the formula is
      ``dynamic + coin_dynamic``.

    Attributes
    ----------
    See field comments below — grouped into multiplier fields, combat
    state, control flags, effect management, and event state.
    """

    # ── references (set at construction) ─────────────────────────────
    skill: "Skill | None" = None
    unit: "Unit | None" = None
    enemy: "Enemy | None" = None

    # ── multiplier fields ────────────────────────────────────────────
    static: float = 1.0
    dynamic: float = 1.0
    p_res_mod: float = 0.0        # physical resistance modifier
    s_res_mod: float = 0.0        # sin resistance modifier
    ol: int = 0                    # attacker offense level (skill OL + unit level)
    def_level_mod: int = 0         # dynamic defense-level adjustment

    # ── combat state ─────────────────────────────────────────────────
    base: int = 0                  # skill base power (copied from skill.base_power)
    coin_power: int = 0            # per-coin power added on Heads
    current_power: int = 0         # running power: base + (heads × coin_power) + buffs
    current_damage: int = 0        # damage dealt by the current coin
    total: int = 0                 # accumulated damage across all coins

    # ── damage breakdown tracking ────────────────────────────────────
    coin_damages: list[int] = field(default_factory=list)        # skill damage per coin
    status_damages: dict[str, int] = field(default_factory=dict) # total damage per status

    # ── crit ─────────────────────────────────────────────────────────
    crit_bonus: float = 0.20       # added to static on crit
    crit_odds_mult: float = 1.0    # multiplier on poise-based crit chance
    crit_odds_bonus: float = 0.0   # flat additive bonus to crit chance
    did_crit: bool = False         # whether the current coin crit

    # ── coin tracking ────────────────────────────────────────────────
    current_coin: "Coin | None" = None
    current_coin_index: int = -1
    coin_result: str | None = None  # "heads" or "tails"
    coin_env: "CoinEnvironment | None" = None
    sequence: list[str | None] = field(default_factory=list)

    # ── clash / combat flags ─────────────────────────────────────────
    clash_count: int = 0
    is_clashing: bool = False
    clash_won: bool | None = None
    is_cracking: bool = False
    target_killed: bool = False

    # ── control flags ────────────────────────────────────────────────
    CONSUME_RUPTURE: bool = True
    CONSUME_SINKING: bool = True
    CONSUME_BLEED: bool = True
    CONSUME_POISE: bool = True
    CANCEL_ATTACK: bool = False
    CANCEL_COIN: bool = False
    ignore_fixed_damage: bool = True

    # ── active-effect management ─────────────────────────────────────
    # dict[Effect] → [data: Any, duration: int]
    effects: dict[Any, list] = field(default_factory=dict)
    apply_queue: list[Any] = field(default_factory=list)
    _updating_queue: bool = field(default=False, init=False, repr=False)

    # ── logging / debug ──────────────────────────────────────────────
    log: list[str] = field(default_factory=list)
    is_debugging: bool = False
    global_state: dict[str, Any] = field(default_factory=dict)

    # ══════════════════════════════════════════════════════════════════
    #  Construction helpers
    # ══════════════════════════════════════════════════════════════════

    @classmethod
    def from_skill(
        cls,
        skill: "Skill",
        unit: "Unit",
        enemy: "Enemy",
        *,
        clash_count: int = 0,
        sequence: list[str | None] | None = None,
        ignore_fixed_damage: bool = True,
        is_debugging: bool = False,
    ) -> "Environment":
        """
        Build a fully-initialised Environment for *skill* used by *unit*
        against *enemy*.  Computes resist mods, OL, and the initial
        static multiplier.
        """
        env = cls(
            skill=skill,
            unit=unit,
            enemy=enemy,
            clash_count=clash_count,
            ignore_fixed_damage=ignore_fixed_damage,
            is_debugging=is_debugging,
        )

        # Copy base combat values from the skill
        env.base = skill.base_power
        env.coin_power = skill.coin_power if hasattr(skill, "coin_power") else 0
        env.current_power = env.base
        env.ol = skill.offense_level + unit.base_level

        # Sequence (pre-determined flips or None for random)
        if sequence is not None:
            env.sequence = list(sequence)
        else:
            env.sequence = [None] * len(skill.coins)

        # ── Resistance calculation ───────────────────────────────────
        p_type, s_type = skill.damage_type
        phys_res = enemy.phys_res.get(p_type, 1.0)
        sin_res = enemy.sin_res.get(s_type, 1.0)

        env.p_res_mod = phys_res - 1.0
        if env.p_res_mod < 0:
            env.p_res_mod /= 2  # weakness halved

        env.s_res_mod = sin_res - 1.0
        if env.s_res_mod < 0:
            env.s_res_mod /= 2

        # ── Initial static multiplier ────────────────────────────────
        env._recompute_static()

        return env

    # ══════════════════════════════════════════════════════════════════
    #  Properties
    # ══════════════════════════════════════════════════════════════════

    @property
    def def_level(self) -> int:
        """Enemy effective defense, allowing effects to shift it."""
        if self.enemy is None:
            return 0
        defense_level_down = max(0, int(self.enemy.get_status("defense_level_down", 0)))
        return self.enemy.effective_defense + self.def_level_mod - defense_level_down

    # ══════════════════════════════════════════════════════════════════
    #  Static multiplier recomputation
    # ══════════════════════════════════════════════════════════════════

    def _recompute_static(self) -> None:
        """
        Rebuild *static* from current OL, def_level, resist mods,
        observation, and clash count.  Called during construction and
        again before damage on each coin, because effects may have
        shifted OL, def_level, or resists in the meantime.
        """
        ol_diff = self.ol - self.def_level
        ol_mult = ol_diff / (abs(ol_diff) + 25) if (abs(ol_diff) + 25) != 0 else 0.0

        observation = 0.0
        if self.enemy is not None:
            observation = self.enemy.observation_level * 0.03

        self.static = (
            1.0
            + ol_mult
            + self.p_res_mod
            + self.s_res_mod
            + observation
            + (self.clash_count * 0.03)
        )

    def recompute_static(self) -> None:
        """Public wrapper — same as ``_recompute_static``."""
        self._recompute_static()

    # ══════════════════════════════════════════════════════════════════
    #  Per-coin damage formula
    # ══════════════════════════════════════════════════════════════════

    def compute_coin_damage(self) -> int:
        """
        Apply the core damage formula for the current coin:

            damage = max(floor(current_power × static × (dynamic + coin_dynamic)), 1)

        ``dynamic`` is the skill-wide multiplier (default 1.0).
        ``coin_dynamic`` is the per-coin additive modifier (default 0.0).
        If *did_crit*, ``crit_bonus`` is temporarily added to static.
        The result is stored in ``current_damage`` and accumulated into
        ``total``.  Returns the per-coin damage value.
        """
        self._recompute_static()

        effective_static = self.get_effective_static()

        _, _, total_dynamic = self.get_dynamic_breakdown()

        damage = max(
            math.floor(
                self.current_power * effective_static * total_dynamic
            ),
            1,
        )
        self.current_damage = damage
        self.total += damage
        return damage

    def get_effective_static(self) -> float:
        """Return the effective static used by the current damage formula."""
        effective_static = self.static
        if self.did_crit:
            effective_static += self.crit_bonus

        # Stagger override: while staggered, all physical resistances are
        # treated according to stagger level (2.0 + 0.5 per level above 1).
        if self.skill is not None and self.enemy is not None and self.enemy.is_staggered:
            phys_type = str(self.skill.damage_type[0]).strip().lower()
            if phys_type in {"slash", "pierce", "blunt"}:
                stagger_p_res_mod = self.enemy.get_stagger_physical_resistance() - 1.0
                effective_static += stagger_p_res_mod - self.p_res_mod

        return effective_static

    def get_fragility_dynamic_bonus(self) -> float:
        """Return extra dynamic bonus granted by fragility-like statuses."""
        if self.skill is None or self.enemy is None:
            return 0.0

        phys_type = str(self.skill.damage_type[0]).strip().lower()
        bonus = 0.0

        if phys_type == "slash":
            slash_fragility = max(0, int(self.enemy.get_status("slash_fragility", 0)))
            bonus += min(slash_fragility, 10) * 0.1

        return bonus

    def get_dynamic_breakdown(self) -> tuple[float, float, float]:
        """Return (coin_dynamic, fragility_bonus, total_dynamic)."""
        coin_dynamic = self.coin_env.dynamic if self.coin_env is not None else 0.0
        fragility_bonus = self.get_fragility_dynamic_bonus()
        total_dynamic = self.dynamic + coin_dynamic + fragility_bonus
        return coin_dynamic, fragility_bonus, total_dynamic

    def get_stagger_debug(self) -> str:
        """Return a compact stagger debug suffix for log lines."""
        if self.enemy is None:
            return "stagger_level=0, stagger_turns_left=0"
        return (
            f"stagger_level={int(self.enemy.stagger_level)}, "
            f"stagger_turns_left={int(self.enemy.stagger_turns_remaining)}"
        )

    def get_stagger_preview_after_current_hit(self) -> tuple[int, bool]:
        """
        Predict stagger level immediately after applying current coin hit damage.

        Returns
        -------
        tuple[int, bool]
            (predicted_level_after_hit, changed_this_coin)
        """
        if self.enemy is None or self.current_damage <= 0:
            current = int(self.enemy.stagger_level) if self.enemy is not None else 0
            return current, False

        old_hp = int(self.enemy.hp)
        new_hp = max(0, old_hp - int(self.current_damage))
        current_level = int(self.enemy.stagger_level)

        crossed = 0
        for threshold in self.enemy.stagger_thresholds:
            if old_hp >= threshold and new_hp < threshold:
                crossed += 1

        if crossed <= 0:
            return current_level, False

        if not self.enemy.is_staggered:
            predicted = min(3, crossed)
        elif int(self.enemy.stagger_turns_remaining) == 2:
            predicted = min(3, current_level + crossed)
        else:
            predicted = current_level

        return predicted, predicted != current_level

    def get_stagger_preview_debug(self) -> str:
        """Return compact pre-hit stagger preview debug suffix."""
        after_level, changed = self.get_stagger_preview_after_current_hit()
        return f"stagger_after_hit={after_level}, changed_this_coin={changed}"

    # ══════════════════════════════════════════════════════════════════
    #  Coin-state management
    # ══════════════════════════════════════════════════════════════════

    def advance_coin(self, coin: "Coin", index: int) -> None:
        """Set up state for the next coin resolution."""
        self.current_coin = coin
        self.current_coin_index = index
        self.current_damage = 0
        self.did_crit = False
        self.coin_result = None
        self.CANCEL_COIN = False
        self.target_killed = False
        self.coin_env = None

    # ══════════════════════════════════════════════════════════════════
    #  Generic attribute access  (get / set / add)
    # ══════════════════════════════════════════════════════════════════

    def get_target(self, name: str) -> Any:
        """
        Route a dotted name to the correct object.

        Prefixes: ``unit.``, ``enemy.``, ``skill.`` → respective object.
        No prefix → self (the Environment).
        """
        if name.startswith("unit."):
            return self.unit
        if name.startswith("enemy."):
            return self.enemy
        if name.startswith("skill."):
            return self.skill
        return self

    @staticmethod
    def _resolve(obj: Any, attr_path: str) -> tuple[Any, str]:
        """Walk a dotted path, returning (parent_obj, final_attr)."""
        parts = attr_path.split(".")
        for part in parts[:-1]:
            obj = getattr(obj, part)
        return obj, parts[-1]

    def get(self, name: str) -> Any:
        """Read an attribute via dotted path (e.g. ``"enemy.hp"``)."""
        target = self.get_target(name)
        # strip the prefix if present
        attr = name.split(".", 1)[1] if "." in name and target is not self else name
        obj, final = self._resolve(target, attr)
        return getattr(obj, final)

    def set(self, name: str, value: Any) -> None:
        """Write an attribute via dotted path."""
        target = self.get_target(name)
        attr = name.split(".", 1)[1] if "." in name and target is not self else name
        obj, final = self._resolve(target, attr)
        setattr(obj, final, value)

    def add(self, name: str, value: Any) -> None:
        """Increment an attribute via dotted path (``attr += value``)."""
        target = self.get_target(name)
        attr = name.split(".", 1)[1] if "." in name and target is not self else name
        obj, final = self._resolve(target, attr)
        setattr(obj, final, getattr(obj, final) + value)

    # ══════════════════════════════════════════════════════════════════
    #  Active-effect lifecycle
    # ══════════════════════════════════════════════════════════════════

    def add_active_effect(
        self,
        effect: Any,
        data: Any = None,
        duration: int = -1,
    ) -> None:
        """
        Register an effect as *active* in this environment.

        Parameters
        ----------
        effect : Effect (or any object with lifecycle callbacks)
        data : arbitrary payload the effect can read/write
        duration : number of ``update()`` ticks before auto-removal.
            ``-1`` means permanent (lasts until the skill ends).
        """
        self.effects[effect] = [data, duration]

    def queue_effect(self, effect: Any) -> None:
        """Queue an effect to be applied after the current iteration."""
        self.apply_queue.append(effect)

    def update(self) -> None:
        """
        Tick down all active-effect durations by 1.  Remove expired ones
        (calling their ``on_remove`` callback if present).
        """
        to_delete: list[Any] = []
        for effect, info in self.effects.items():
            if info[1] == -1:
                continue  # permanent
            info[1] -= 1
            if info[1] == 0:
                if hasattr(effect, "on_remove"):
                    effect.on_remove(effect, self)
                to_delete.append(effect)
        for effect in to_delete:
            del self.effects[effect]

    def update_apply_queue(self) -> None:
        """
        Drain ``apply_queue`` in LIFO order, applying each queued effect.

        Uses a re-entrancy guard to prevent infinite loops when an
        effect's application queues further effects.
        """
        if self._updating_queue:
            return
        self._updating_queue = True
        while self.apply_queue:
            queued = self.apply_queue.pop()
            if hasattr(queued, "execute"):
                queued.execute(self)
        self._updating_queue = False

    # ══════════════════════════════════════════════════════════════════
    #  Event hooks — broadcast to all active effects
    # ══════════════════════════════════════════════════════════════════

    def on_poise_gained(self, potency: int, count: int) -> None:
        """Broadcast poise-gained event to all active effects."""
        for effect in list(self.effects):
            if hasattr(effect, "on_poise_gained"):
                effect.on_poise_gained(effect, self, potency, count)
        self.update_apply_queue()

    def on_status_applied(
        self, status_name: str, potency: int, count: int
    ) -> None:
        """Broadcast status-applied event to all active effects."""
        for effect in list(self.effects):
            if hasattr(effect, "on_status_applied"):
                effect.on_status_applied(effect, self, status_name, potency, count)
        self.update_apply_queue()

    def on_tremor_burst(self, _allow_everlasting_bonus: bool = True) -> None:
        """
        Trigger Tremor Burst behavior and broadcast to active effects.

        Core behavior (current implementation):
        - Read target tremor potency
        - Raise target stagger threshold by that potency
        - Record actual raised amount in ``tremor_last_burst_raised``
        - If Tremor type is ``reverb``:
          deal Sloth damage = tremor_potency
                - If Tremor type is ``everlasting``:
                    roll two independent bonus-burst chances:
                    min(tremor_potency, 50)% and min(tremor_count, 50)%
        - If Tremor type is ``scorch``:
          deal Wrath damage = floor((tremor_potency + burn_potency) / 2)
          and reduce burn count by 1
        - If Tremor type is ``hemmorage``:
          deal Lust damage = floor((tremor_potency + bleed_potency) / 2)
          and reduce bleed count by 1
        """
        if self.enemy is not None:
            potency = max(0, int(self.enemy.get_status("tremor_potency", 0)))
            raised = self.enemy.raise_stagger_threshold(potency)
            self.enemy.set_status("tremor_last_burst_raised", raised)

            tremor_type = str(self.enemy.get_status("tremor_type", "")).strip().lower()
            if tremor_type == "reverb":
                if potency > 0:
                    sloth_res = self.enemy.sin_res.get("Sloth", 1.0)
                    sloth_mod = sloth_res - 1.0
                    if sloth_mod < 0:
                        sloth_mod /= 2
                    reverb_dmg_raw = max(math.floor(potency * (1.0 + sloth_mod)), 1)
                    reverb_dmg = self.enemy.take_damage(reverb_dmg_raw)
                    self.total += reverb_dmg
                    self.status_damages["tremor_reverb"] = (
                        self.status_damages.get("tremor_reverb", 0) + reverb_dmg
                    )
                    if not self.enemy.is_alive:
                        self.target_killed = True
                    if self.is_debugging:
                        self.log.append(
                            f"     [tremor_reverb] {reverb_dmg} Sloth damage "
                            f"(raw={potency}, sloth_mod={sloth_mod:+.2f}) -> "
                            f"enemy HP {self.enemy.hp}/{self.enemy.max_hp}"
                        )

            if tremor_type == "everlasting" and _allow_everlasting_bonus:
                tremor_count = max(0, int(self.enemy.get_status("tremor_count", 0)))
                pot_chance = min(potency, 50) / 100.0
                count_chance = min(tremor_count, 50) / 100.0

                extra_bursts = 0
                if random.random() < pot_chance:
                    extra_bursts += 1
                if random.random() < count_chance:
                    extra_bursts += 1

                if self.is_debugging:
                    self.log.append(
                        f"     [tremor_everlasting] bonus rolls "
                        f"(pot={pot_chance:.0%}, count={count_chance:.0%}) -> +{extra_bursts} bursts"
                    )

                for _ in range(extra_bursts):
                    self.on_tremor_burst(_allow_everlasting_bonus=False)

            if tremor_type == "scorch":
                burn_potency = max(0, int(self.enemy.get_status("burn_potency", 0)))
                scorch_raw = max(math.floor((potency + burn_potency) / 2), 0)
                if scorch_raw > 0:
                    wrath_res = self.enemy.sin_res.get("Wrath", 1.0)
                    wrath_mod = wrath_res - 1.0
                    if wrath_mod < 0:
                        wrath_mod /= 2
                    scorch_dmg_raw = max(math.floor(scorch_raw * (1.0 + wrath_mod)), 1)
                    scorch_dmg = self.enemy.take_damage(scorch_dmg_raw)
                    self.total += scorch_dmg
                    self.status_damages["tremor_scorch"] = (
                        self.status_damages.get("tremor_scorch", 0) + scorch_dmg
                    )
                    if not self.enemy.is_alive:
                        self.target_killed = True
                    if self.is_debugging:
                        self.log.append(
                            f"     [tremor_scorch] {scorch_dmg} Wrath damage "
                            f"(raw={scorch_raw}, wrath_mod={wrath_mod:+.2f}) -> "
                            f"enemy HP {self.enemy.hp}/{self.enemy.max_hp}"
                        )

                burn_count = max(0, int(self.enemy.get_status("burn_count", 0)))
                new_burn_count = max(0, burn_count - 1)
                if new_burn_count == 0:
                    self.enemy.remove_status("burn_count")
                else:
                    self.enemy.set_status("burn_count", min(new_burn_count, 99))
                if self.is_debugging and burn_count > 0:
                    self.log.append(
                        f"     [tremor_scorch] burn_count {burn_count} -> {new_burn_count}"
                    )

            if tremor_type == "hemmorage":
                bleed_potency = max(0, int(self.enemy.get_status("bleed_potency", 0)))
                hemmorage_raw = max(math.floor((potency + bleed_potency) / 2), 0)
                if hemmorage_raw > 0:
                    lust_res = self.enemy.sin_res.get("Lust", 1.0)
                    lust_mod = lust_res - 1.0
                    if lust_mod < 0:
                        lust_mod /= 2
                    hemmorage_dmg_raw = max(math.floor(hemmorage_raw * (1.0 + lust_mod)), 1)
                    hemmorage_dmg = self.enemy.take_damage(hemmorage_dmg_raw)
                    self.total += hemmorage_dmg
                    self.status_damages["tremor_hemmorage"] = (
                        self.status_damages.get("tremor_hemmorage", 0) + hemmorage_dmg
                    )
                    if not self.enemy.is_alive:
                        self.target_killed = True
                    if self.is_debugging:
                        self.log.append(
                            f"     [tremor_hemmorage] {hemmorage_dmg} Lust damage "
                            f"(raw={hemmorage_raw}, lust_mod={lust_mod:+.2f}) -> "
                            f"enemy HP {self.enemy.hp}/{self.enemy.max_hp}"
                        )

                bleed_count = max(0, int(self.enemy.get_status("bleed_count", 0)))
                new_bleed_count = max(0, bleed_count - 1)
                if new_bleed_count == 0:
                    self.enemy.remove_status("bleed_count")
                else:
                    self.enemy.set_status("bleed_count", min(new_bleed_count, 99))
                if self.is_debugging and bleed_count > 0:
                    self.log.append(
                        f"     [tremor_hemmorage] bleed_count {bleed_count} -> {new_bleed_count}"
                    )

            if self.is_debugging:
                self.log.append(
                    f"     [tremor_burst] potency={potency}, raised={raised}, "
                    f"thresholds={self.enemy.stagger_thresholds}, staggered={self.enemy.is_staggered}"
                )

        for effect in list(self.effects):
            if hasattr(effect, "on_tremor_burst"):
                effect.on_tremor_burst(effect, self)
        self.update_apply_queue()

    # ══════════════════════════════════════════════════════════════════
    #  Convenience / legacy compatibility
    # ══════════════════════════════════════════════════════════════════

    def add_damage(self, amount: int, source: str = "") -> None:
        """
        Quick additive damage adjustment (modifies ``current_power``).

        Kept for backward-compat with effects written for CombatContext.
        """
        self.current_power += amount
        if source:
            self.log.append(f"[damage] {source}: {amount:+d}")

    @property
    def final_damage(self) -> int:
        """Alias so older code referencing ``ctx.final_damage`` keeps working."""
        return self.total

    @final_damage.setter
    def final_damage(self, value: int) -> None:
        self.total = value

    # ── debug helper ─────────────────────────────────────────────────

    def debug_coin(self, coin_index: int) -> str:
        """Return a one-line summary of the current coin's state."""
        _, _, total_dyn = self.get_dynamic_breakdown()
        effective_static = self.get_effective_static()
        stagger_debug = self.get_stagger_debug()
        stagger_preview = self.get_stagger_preview_debug()
        return (
            f"Coin {coin_index + 1}: power={self.current_power}, "
            f"damage={self.current_damage}, static={effective_static:.3f}, "
            f"dynamic={total_dyn:.3f}, "
            f"p_res={self.p_res_mod:.3f}, s_res={self.s_res_mod:.3f}, "
            f"ol_diff={self.ol - self.def_level}, {stagger_debug}, {stagger_preview}"
        )
