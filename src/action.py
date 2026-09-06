"""
Action: a unit's declared move for the current turn.

Pairs a Unit with the Skill it is using this turn, so turn order and
skill resolution can read the acting unit directly (``action.unit``)
with no ownership lookup. ``Unit.skills`` remains the unit's permanent
kit and is never mutated to express "what's happening this turn" —
Actions are the mechanism for that instead. See ``GameLoop.actions``.

An Action also carries that skill's own configuration for the turn:
its target, its clash state, its coin sequence, and the speed it
resolves at. Every one of those is ``None`` by default and falls back
to the corresponding turn-wide ``GameLoop`` value, so a caller that
declares only ``unit`` and ``skill`` behaves exactly as it did before
per-action config existed.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING

from src.skill import Skill
from src.unit import Unit

if TYPE_CHECKING:
    from src.enemy import Enemy


@dataclass
class Action:
    """
    One unit's chosen skill for this turn, plus that skill's config.

    Attributes
    ----------
    unit : Unit | None
        The acting unit. ``None`` only for a standalone, ownerless skill
        passed via the legacy ``GameLoop.skills`` list, which
        ``GameLoop._turn_actions`` wraps in a synthesized Action.
    skill : Skill
        The skill being used. This *is* the chosen form — a slot may
        hold several forms (see ``Unit.get_skill_forms``) and the caller
        picks one before building the Action.
    target : Enemy | None
        Which enemy this skill attacks. Unset → ``GameLoop._pick_target()``
        (the first living enemy), preserving the old single-target behavior.
    speed : int | None
        The speed this action resolves at. Unset → the owner's
        ``effective_speed`` (so Haste/Bind apply), or ``skill.speed`` for
        an ownerless skill. Set it explicitly to give one unit several
        actions in a turn, each ordering independently.
    is_clashing, clash_won, clash_count
        This action's clash state. Resolved as a single unit rather than
        three independent fields — see ``resolve_clash``.
    sequence : list[str | None] | None
        Predetermined coin flips for this skill only. Unset →
        ``GameLoop.sequence``.
    slot : str | None
        Which slot the skill came from (``"1"``/``"2"``/``"3"``/
        ``"defense"``). Metadata only — display, logging, and pre-combat
        checks that ask "how many allies used a Skill 3 this turn".
    offense_level_bonus, defense_level_bonus : int
        **Engine-written**, unlike every field above: a caller leaves
        these alone and the pre-combat phase fills them in. Sin
        Resonance writes the turn's Offense Level here for an attack
        skill, or Defense Level for a defensive one (see
        ``src.resonance``). ``_resolve_action`` folds the offense bonus
        into ``env.ol``; the defense bonus is recorded but not yet
        consumed, since the engine has no defensive resolution path.
    chain_index : int
        Also engine-written: this action's position in the turn's chain
        (speed order, fastest first), or ``-1`` before the pre-combat
        phase has run. Lets a skill ask about its **own** A-Reson. run
        rather than some other run elsewhere in the turn — see
        ``ResonanceResult.chain_at``. Stamped by ``resonance_check`` in
        the same pass that computes resonance, so the two always agree.
    """

    unit: Unit | None
    skill: Skill
    target: "Enemy | None" = None
    speed: int | None = None
    is_clashing: bool | None = None
    clash_won: bool | None = None
    clash_count: int | None = None
    sequence: list[str | None] | None = None
    slot: str | None = None
    offense_level_bonus: int = 0
    defense_level_bonus: int = 0
    chain_index: int = -1

    def resolve_clash(
        self,
        loop_is_clashing: bool,
        loop_clash_won: bool | None,
        loop_clash_count: int,
    ) -> tuple[bool, bool | None, int]:
        """
        Return this action's effective ``(is_clashing, clash_won, clash_count)``.

        Clash state resolves as one unit, not three independent fields:
        ``clash_won=None`` legitimately means "clashing, no result yet",
        so it cannot also stand for "unset". ``is_clashing`` is therefore
        the sole discriminator — if this action declares it, the action's
        whole triple is used; otherwise the loop's whole triple is.
        """
        if self.is_clashing is None:
            return loop_is_clashing, loop_clash_won, loop_clash_count
        return (
            self.is_clashing,
            self.clash_won,
            self.clash_count if self.clash_count is not None else 0,
        )

    def resolve_speed(self) -> int:
        """
        Return the speed this action orders by.

        An explicit ``speed`` wins (the multiple-actions-per-unit case);
        otherwise the owner's ``effective_speed``, so Haste/Bind apply;
        otherwise the skill's own vestigial ``speed`` field, which only
        matters for an ownerless skill.
        """
        if self.speed is not None:
            return self.speed
        if self.unit is not None:
            return self.unit.effective_speed
        return self.skill.speed
