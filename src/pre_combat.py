"""
Pre-combat checks: validation across the whole turn's skill selection.

These run once per turn, after Turn Start and before Combat Start —
the point where every unit's skill for the turn is known, but nothing
has resolved yet. A check sees the entire selection at once, which is
what distinguishes it from a skill/coin Effect (one skill) or a Passive
(one entity).

Sin Resonance is the main such rule — it can only be computed once every
unit's skill for the turn is known and ordered. See ``src.resonance``.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Any, Callable

if TYPE_CHECKING:
    from src.action import Action
    from src.enemy import Enemy
    from src.team import Team


@dataclass
class TurnPlan:
    """
    Everything a pre-combat check can read or change.

    Attributes
    ----------
    actions : list[Action]
        Every skill selected this turn, in resolution order (fastest
        first, team order breaking ties) as of when checks run. A check
        may reorder, mutate, or drop entries; ``GameLoop`` re-sorts
        afterward, so a check that changes a unit's speed still takes
        effect on the order.
    team : Team | None
        The player's roster, when the turn was driven by one.
    enemies : list[Enemy]
        Every enemy in the encounter.
    log : list[str]
        The turn's broadcast log. Append to it to leave a trace of what
        a check decided.
    state : dict[str, Any]
        Data a check publishes for the resolution phase. ``GameLoop``
        merges this into every skill's ``env.global_state``, so effects
        and passives can read it during resolution — the same channel
        ``units``/``enemies`` already arrive on. Sin Resonance publishes
        its result here under ``"resonance"``.
    """

    actions: list["Action"] = field(default_factory=list)
    team: "Team | None" = None
    enemies: list["Enemy"] = field(default_factory=list)
    log: list[str] = field(default_factory=list)
    state: dict[str, Any] = field(default_factory=dict)


#: A check receives the whole plan and mutates it in place.
PreCombatCheck = Callable[[TurnPlan], None]


# Imported here, after TurnPlan is defined, so the registry below can
# name it. src.resonance only needs TurnPlan for typing, so there is no
# runtime import cycle.
from src.resonance import resonance_check  # noqa: E402


#: Registry of checks that run for every turn, **in list order**.
#:
#: To add one, write a ``(TurnPlan) -> None`` function and append it.
#: ``GameLoop.pre_combat_checks`` overrides this list when set, so tests
#: can supply their own without touching global state.
#:
#: Order matters for anything that changes speed: ``run_turn`` re-sorts
#: the actions *after* checks run, and Sin Resonance is derived from the
#: ordering, so a speed-changing check must be registered **before**
#: ``resonance_check`` or resonance will be computed on a stale chain.
PRE_COMBAT_CHECKS: list[PreCombatCheck] = [resonance_check]
