"""
Effect: A single unit of game logic that fires during a specific phase.

Each effect is bound to a tag (phase name) and carries a callable that
receives the current combat context and mutates it (e.g. modifying damage,
applying statuses, etc.).
"""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum
from typing import TYPE_CHECKING, Any, Callable

if TYPE_CHECKING:
    from src.environment import Environment


# ── Phase tags ──────────────────────────────────────────────────────────


class SkillPhase(str, Enum):
    """Phases that fire once per skill activation."""

    TURN_START = "turn_start"
    COMBAT_START = "combat_start"
    PERSISTENT = "persistent"  # conditionals / dynamics checked every phase
    BEFORE_USE = "before_use"
    ON_USE = "on_use"
    CLASH_START = "clash_start"
    CLASH_WIN = "clash_win"
    CLASH_LOSE = "clash_lose"
    BEFORE_ATTACK = "before_attack"
    ON_UNOPPOSED_ATTACK = "on_unopposed_attack"
    ON_KILL = "on_kill"
    AFTER_ATTACK = "after_attack"
    TURN_END = "turn_end"


class CoinPhase(str, Enum):
    """Phases that fire once per coin flip / hit."""

    COIN_START = "coin_start"
    ON_HIT = "on_hit"
    ON_HIT_HEADS = "on_hit_heads"
    ON_HIT_TAILS = "on_hit_tails"
    ON_HIT_WITH_CRACKING = "on_hit_with_cracking"
    ON_HIT_WITHOUT_CRACKING = "on_hit_without_cracking"
    ON_CRIT = "on_crit"
    ON_CRIT_HEADS = "on_crit_heads"
    ON_CRIT_TAILS = "on_crit_tails"
    ON_KILL = "on_kill"
    REUSE = "reuse"


# ── Effect data class ───────────────────────────────────────────────────

# Type alias for the function an effect executes.
# It receives the mutable Environment and returns nothing.
EffectFn = Callable[..., None]


@dataclass
class Effect:
    """
    A single effect that fires during a given phase.

    Attributes
    ----------
    name : str
        Human-readable label for debugging / logging.
    phase : SkillPhase | CoinPhase
        The phase tag this effect is bound to.
    apply : EffectFn
        Callable that mutates the current Environment.
    args : tuple[Any, ...]
        Positional arguments forwarded to ``apply`` after ``env``.
    kwargs : dict[str, Any]
        Keyword arguments forwarded to ``apply`` after ``env``.
    condition_args : tuple[Any, ...]
        Positional arguments forwarded to ``condition`` after ``env``.
    condition_kwargs : dict[str, Any]
        Keyword arguments forwarded to ``condition`` after ``env``.
    max_procs : int | None
        Optional per-turn proc cap for this effect. ``None`` means unlimited.
    priority : int
        Lower values execute first within the same phase (default 0).
    condition : EffectFn | None
        Optional guard — if provided, `apply` only runs when
        `condition(env, *condition_args, **condition_kwargs)` returns a truthy value.
    """

    name: str
    phase: SkillPhase | CoinPhase
    apply: EffectFn
    args: tuple[Any, ...] = field(default_factory=tuple)
    kwargs: dict[str, Any] = field(default_factory=dict)
    condition_args: tuple[Any, ...] = field(default_factory=tuple)
    condition_kwargs: dict[str, Any] = field(default_factory=dict)
    max_procs: int | None = None
    priority: int = 0
    condition: EffectFn | None = None
    _proc_count: int = field(default=0, init=False, repr=False)

    @property
    def proc_count(self) -> int:
        """How many times this effect has fired this turn."""
        return self._proc_count

    def can_proc(self) -> bool:
        """Return True if this effect has not reached its proc cap."""
        if self.max_procs is None:
            return True
        return self._proc_count < self.max_procs

    def execute(self, env: "Environment") -> bool:
        """Run the effect against *env* and return True when it fires."""
        if not self.can_proc():
            return False
        if self.condition is not None:
            if not self.condition(env, *self.condition_args, **self.condition_kwargs):
                return False
        self.apply(env, *self.args, **self.kwargs)
        self._proc_count += 1
        return True

    def reset_procs(self) -> None:
        """Reset per-turn proc tracking for this effect."""
        self._proc_count = 0
