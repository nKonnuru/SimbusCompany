"""
Passive: An ongoing ability belonging to an entity (Unit or Enemy).

A passive is similar to a skill conditional but it:
- Belongs to an entity rather than a specific skill
- Triggers at the same timing points as skill / coin effects
- Can have a limited number of procs per turn
- Has an optional condition guard that is checked before firing
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Callable

from src.effect import CoinPhase, Effect, SkillPhase

if TYPE_CHECKING:
    from src.environment import Environment

ConditionFn = Callable[["Environment"], bool]


@dataclass
class Passive:
    """
    An ongoing ability that triggers at specific phases if its condition holds.

    Attributes
    ----------
    name : str
        Human-readable label for the passive.
    effects : dict[SkillPhase | CoinPhase, list[Effect]]
        Phase-keyed mapping — works identically to Skill / Coin effect dicts.
    max_procs : int | None
        Maximum number of times this passive may fire per turn.
        ``None`` means unlimited.
    condition : ConditionFn | None
        Optional guard checked before firing.  When set, the passive only
        executes when ``condition(ctx)`` returns True.
    priority : int
        Lower values fire first when multiple passives compete (default 0).
    """

    name: str = "Passive"
    effects: dict[SkillPhase | CoinPhase, list[Effect]] = field(
        default_factory=dict,
    )
    max_procs: int | None = None
    condition: ConditionFn | None = None
    priority: int = 0
    _proc_count: int = field(default=0, init=False, repr=False)

    # ── query ────────────────────────────────────────────────────────

    @property
    def proc_count(self) -> int:
        """How many times this passive has fired this turn."""
        return self._proc_count

    def can_proc(self, env: "Environment") -> bool:
        """Return True if this passive is still eligible to fire."""
        if self.max_procs is not None and self._proc_count >= self.max_procs:
            return False
        if self.condition is not None and not self.condition(env):
            return False
        return True

    # ── mutation ─────────────────────────────────────────────────────

    def add_effect(self, effect: Effect) -> None:
        """Register an effect under its declared phase."""
        self.effects.setdefault(effect.phase, []).append(effect)

    def get_effects(self, phase: SkillPhase | CoinPhase) -> list[Effect]:
        """Return effects for *phase*, sorted by priority (ascending)."""
        return sorted(
            self.effects.get(phase, []),
            key=lambda e: e.priority,
        )

    def get_phase_proc_limit(self, phase: SkillPhase | CoinPhase) -> int | None:
        """Return the display proc limit for a specific phase based on effects."""
        limits = [e.max_procs for e in self.get_effects(phase) if e.max_procs is not None]
        if not limits:
            return None
        return max(limits)

    def get_phase_proc_count(self, phase: SkillPhase | CoinPhase) -> int:
        """Return the display proc count for a specific phase based on effects."""
        effects = self.get_effects(phase)
        if not effects:
            return 0
        return max(e.proc_count for e in effects)

    def execute_phase(
        self,
        phase: SkillPhase | CoinPhase,
        env: "Environment",
    ) -> None:
        """
        Fire every effect for *phase* if the passive can still proc.

        If at least one effect executes, the proc counter is incremented
        once (one proc per phase invocation, not per effect).
        """
        if not self.can_proc(env):
            return
        effects = self.get_effects(phase)
        if not effects:
            return
        executed_any = False
        for effect in effects:
            if effect.execute(env):
                executed_any = True
        if not executed_any:
            return
        self._proc_count += 1
        phase_proc_count = self.get_phase_proc_count(phase)
        phase_proc_limit = self.get_phase_proc_limit(phase)
        env.log.append(
            f"[passive] {self.name} proc'd "
            f"({phase_proc_count}/{phase_proc_limit or '∞'})"
        )

    def reset_procs(self) -> None:
        """Reset the proc counter (typically called at turn start)."""
        self._proc_count = 0
        for effects in self.effects.values():
            for effect in effects:
                effect.reset_procs()
