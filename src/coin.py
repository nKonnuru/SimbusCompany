"""
Coin: The atomic unit of a skill's attack sequence.

Each coin has an individual coin power value and a collection of effects
keyed by CoinPhase tags. During resolution the GameLoop flips the coin and
then fires the appropriate phase effects in priority order.

Reuse is handled via ``ReuseCondition`` objects attached to the coin.
Each condition carries its own cap (``max_reuses``) and tracks how many
times *it* has triggered.  The coin itself keeps a global ``reuse_count``
that increments whenever *any* condition fires.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Callable

from src.effect import CoinPhase, Effect

if TYPE_CHECKING:
    from src.environment import Environment


# ── Reuse condition ──────────────────────────────────────────────────


@dataclass
class ReuseCondition:
    """
    A single reuse rule attached to a :class:`Coin`.

    Attributes
    ----------
    name : str
        Human-readable label for logging / debugging.
    condition : Callable[[Environment], bool]
        Guard — reuse triggers only when this returns ``True``.
    max_reuses : int
        Maximum number of times *this* condition may fire.
    used : int
        How many times this condition has already triggered.
    """

    name: str = "Reuse"
    condition: Callable[["Environment"], bool] = lambda _env: True
    max_reuses: int = 1
    used: int = 0

    # ── helpers ──────────────────────────────────────────────────────

    @property
    def remaining(self) -> int:
        """How many reuses this condition still has available."""
        return max(0, self.max_reuses - self.used)

    def can_trigger(self, env: "Environment") -> bool:
        """Return ``True`` if reuses remain **and** the condition passes."""
        return self.used < self.max_reuses and self.condition(env)

    def trigger(self) -> None:
        """Record one use of this reuse condition."""
        self.used += 1

    def reset(self) -> None:
        """Reset the usage counter (e.g. between turns)."""
        self.used = 0


@dataclass
class Coin:
    """
    Represents a single coin within a skill.

    Attributes
    ----------
    name : str
        Display / debug label (e.g. "Coin 1", "Finishing Blow").
    coin_power : int
        The bonus (or penalty) added on a successful heads flip.
    effects : dict[CoinPhase, list[Effect]]
        Phase-keyed mapping of effects that belong to this coin.
    reuse_conditions : list[ReuseCondition]
        Ordered list of conditions that may each trigger a reuse of
        this coin.  Evaluated in order; the **first** eligible
        condition fires on each reuse pass.
    reuse_count : int
        Total number of times this coin has been reused (incremented
        each time *any* ``ReuseCondition`` fires).
    """

    name: str = "Coin"
    coin_power: int = 0
    effects: dict[CoinPhase, list[Effect]] = field(default_factory=dict)
    reuse_conditions: list[ReuseCondition] = field(default_factory=list)
    reuse_count: int = 0

    # ── effect helpers ───────────────────────────────────────────────

    def add_effect(self, effect: Effect) -> None:
        """Register an effect under its declared phase."""
        if not isinstance(effect.phase, CoinPhase):
            raise TypeError(
                f"Coin effects must use CoinPhase tags, got {effect.phase!r}"
            )
        self.effects.setdefault(effect.phase, []).append(effect)

    def get_effects(self, phase: CoinPhase) -> list[Effect]:
        """Return effects for *phase*, sorted by priority (ascending)."""
        return sorted(
            self.effects.get(phase, []),
            key=lambda e: e.priority,
        )

    def execute_phase(self, phase: CoinPhase, env: "Environment") -> None:
        """Fire every effect registered for *phase* against *env*."""
        for effect in self.get_effects(phase):
            effect.execute(env)

    # ── reuse helpers ────────────────────────────────────────────────

    def add_reuse_condition(self, condition: ReuseCondition) -> None:
        """Append a reuse condition to this coin."""
        self.reuse_conditions.append(condition)

    def check_reuse(self, env: "Environment") -> bool:
        """
        Evaluate reuse conditions in order.  The first eligible
        condition triggers a reuse: incrementing its own ``used``
        counter, incrementing the coin's ``reuse_count``, and
        appending the coin back to the skill's coin list for
        another resolution.

        Returns ``True`` if a reuse was triggered.
        """
        for rc in self.reuse_conditions:
            if rc.can_trigger(env):
                rc.trigger()
                self.reuse_count += 1
                if env.skill is not None:
                    env.skill.coins.append(self)
                return True
        return False

    def reset_reuse(self) -> None:
        """Reset all reuse counters (call between turns)."""
        self.reuse_count = 0
        for rc in self.reuse_conditions:
            rc.reset()
