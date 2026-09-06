"""
Sin Resonance: the turn's cross-skill Affinity rule.

Resonance can only be computed once every unit's skill for the turn is
known and ordered, which is exactly the vantage point a pre-combat check
has (see ``src.pre_combat``). It grants Offense Level — Defense Level for
defensive skills — to the skills involved, before any of them resolve.

Two flavors, both keyed on a skill's Affinity (``skill.damage_type[1]``):

- **Sin Resonance** ("Reson.") — 2+ skills of the same Affinity anywhere
  in the chain. Adjacency is *not* required. The bonus ramps by the
  skill's position among that Affinity's skills, so the rightmost gains
  the most.
- **Absolute Sin Resonance** ("A-Reson.") — 3+ skills of the same
  Affinity *consecutive in the full chain*; a different Affinity between
  them breaks the run. The bonus is keyed on the run's length and is
  flat across every skill in it.

The two never stack: a skill takes whichever grants more. A normal
Reson. chain builds *around* an A-Reson. run, so deep into a long chain
the positional value can overtake a short run's flat value — see
``_combine_bonus``.

These are internal level adjustments. They are not statuses, never touch
the status dict, and are not in ``TURN_END_EFFECTS_TO_CLEAR``.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import TYPE_CHECKING

from src.utils import PHYSICAL_DAMAGE_TYPES, SIN_DAMAGE_TYPES

if TYPE_CHECKING:
    from src.action import Action
    from src.pre_combat import TurnPlan
    from src.skill import Skill


# Offense/Defense Level granted, indexed as described. Index 0 is unused;
# the final entry also serves every position/length past it (11+).
#
#                          1  2  3  4  5  6  7  8  9 10 11+
#: Reson.: index = the skill's position among that Affinity's skills.
RESONANCE_OL = (0, 0, 1, 3, 3, 5, 5, 7, 7, 9, 9, 11)
#: A-Reson.: index = the consecutive run's length; applied flat to all of them.
ABSOLUTE_RESONANCE_OL = (0, 0, 0, 3, 5, 5, 7, 7, 9, 9, 11, 11)

#: A run must be at least this long to count as Absolute Sin Resonance.
ABSOLUTE_MINIMUM = 3
#: This many skills of one Affinity are needed for any Sin Resonance.
RESONANCE_MINIMUM = 2


def _table_lookup(table: tuple[int, ...], index: int) -> int:
    """Read *table* at *index*, clamping past its end (the 11+ row)."""
    if index <= 0:
        return 0
    return table[min(index, len(table) - 1)]


def _combine_bonus(reson: int, absolute: int) -> int:
    """
    Combine a skill's two possible bonuses.

    The two never stack — the skill takes whichever grants more. This is
    load-bearing in both directions:

    - A short chain is *lifted* by its run: 3 consecutive of one Affinity
      are positions 1/2/3 (+0/+1/+3) but a length-3 run pays +3 flat, so
      all three get +3.
    - A long chain *beats* a short run: in ``P P L P P L P P P`` the only
      run is the last three Pride (flat +3), but they sit at Pride
      positions 5/6/7 (+5/+5/+7) — so they keep +5/+5/+7.
    """
    return max(reson, absolute)


def sin_of(skill: "Skill | None") -> str | None:
    """
    Return *skill*'s normalized Affinity, or ``None`` if it has no known one.

    Uses the same ``.strip().lower()`` normalization the damage pipeline
    already applies to ``damage_type`` (see ``Environment``), since skills
    declare it capitalized (``("Slash", "Lust")``) while the type tuples
    in ``utils`` are lowercase.
    """
    if skill is None:
        return None
    damage_type = getattr(skill, "damage_type", None)
    if not damage_type or len(damage_type) < 2:
        return None
    sin = str(damage_type[1]).strip().lower()
    return sin if sin in SIN_DAMAGE_TYPES else None


def is_offensive(skill: "Skill | None") -> bool:
    """
    True when *skill* deals physical damage, so it takes Offense Level.

    Anything whose physical type is not one of ``PHYSICAL_DAMAGE_TYPES``
    is treated as defensive and takes Defense Level instead — today only
    ``"Evade"``, but this stays correct if Guard/Counter are added.
    Defensive skills still carry a real Affinity (Ryoshu's Charged Evade
    is ``("Evade", "Lust")``), so they chain normally.
    """
    if skill is None:
        return False
    damage_type = getattr(skill, "damage_type", None)
    if not damage_type:
        return False
    return str(damage_type[0]).strip().lower() in PHYSICAL_DAMAGE_TYPES


@dataclass(frozen=True)
class ResonanceChain:
    """
    One unbroken run of a single Affinity — an A-Reson. "line".

    Positions are indices into the turn's chain, which is speed order
    (fastest first). Only runs of ``ABSOLUTE_MINIMUM`` or longer are
    recorded as chains.

    Attributes
    ----------
    sin : str
        Normalized Affinity name.
    start : int
        Chain index of this run's first skill.
    length : int
        How many consecutive skills the run covers.
    bonus : int
        The flat Offense/Defense Level this run granted each of its
        members, before the ``max`` against their positional Reson.
        value (see ``_combine_bonus``).
    """

    sin: str
    start: int
    length: int
    bonus: int = 0

    @property
    def end(self) -> int:
        """Chain index just past this run (exclusive)."""
        return self.start + self.length

    @property
    def indices(self) -> tuple[int, ...]:
        """Every chain index this run covers."""
        return tuple(range(self.start, self.end))

    def contains(self, index: int) -> bool:
        """True when chain position *index* is part of this run."""
        return self.start <= index < self.end


@dataclass(frozen=True)
class SinResonance:
    """
    One Affinity's resonance for the turn, keeping every line.

    Attributes
    ----------
    sin : str
        Normalized Affinity name, e.g. ``"lust"``.
    count : int
        How many skills of this Affinity were selected — the Reson.
        count. Only recorded when it reaches ``RESONANCE_MINIMUM``.
    indices : tuple[int, ...]
        The Reson. line: every chain position holding this Affinity, in
        order. Adjacency is irrelevant here — these may be scattered.
    chains : tuple[ResonanceChain, ...]
        Every A-Reson. line for this Affinity, in chain order. Empty
        when no run reached ``ABSOLUTE_MINIMUM``.

    There is deliberately no single "the A-Reson. number" — see
    ``absolute_longest`` vs ``absolute_sum``, which differ whenever an
    Affinity forms more than one run.
    """

    sin: str
    count: int
    indices: tuple[int, ...] = ()
    chains: tuple[ResonanceChain, ...] = ()

    @property
    def absolute_longest(self) -> int:
        """
        Length of the **longest** run — the dashboard figure.

        Two separate runs of 3 read as 3, not 6: separate chains are
        counted separately rather than summed.
        """
        return max((chain.length for chain in self.chains), default=0)

    @property
    def absolute_sum(self) -> int:
        """
        Total length across **all** runs — two runs of 3 give 6.

        A different quantity from ``absolute_longest``, for rules phrased
        as "the sum of X A-Reson.".
        """
        return sum(chain.length for chain in self.chains)


@dataclass(frozen=True)
class ResonanceResult:
    """
    The whole turn's resonance, queryable by effects and passives.

    Reachable from ``env.global_state["resonance"]`` at every phase from
    Combat Start onward (Turn Start runs before the chain is final, so
    it has none), and from ``GameLoop.resonance`` after the turn.
    """

    by_sin: dict[str, SinResonance] = field(default_factory=dict)

    @staticmethod
    def _key(sin: str | None) -> str | None:
        return str(sin).strip().lower() if sin is not None else None

    def count(self, sin: str | None) -> int:
        """Reson. count for *sin* — 0 when it did not resonate."""
        entry = self.by_sin.get(self._key(sin)) if sin is not None else None
        return entry.count if entry is not None else 0

    def absolute_longest(self, sin: str | None) -> int:
        """
        A-Reson. for *sin* as its **longest** run — the dashboard rule.

        An A-Reson. also counts as a Reson. of the same size; that needs
        no special handling, since ``count`` is the Affinity's total and
        is therefore always at least as large as any run inside it.
        """
        entry = self.by_sin.get(self._key(sin)) if sin is not None else None
        return entry.absolute_longest if entry is not None else 0

    def absolute_sum(self, sin: str | None) -> int:
        """A-Reson. for *sin* summed across every run — two 3s give 6."""
        entry = self.by_sin.get(self._key(sin)) if sin is not None else None
        return entry.absolute_sum if entry is not None else 0

    def chains(self, sin: str | None = None) -> tuple[ResonanceChain, ...]:
        """
        Every A-Reson. line, in chain order.

        ``sin=None`` returns the lines of all Affinities together.
        """
        if sin is not None:
            entry = self.by_sin.get(self._key(sin))
            return entry.chains if entry is not None else ()
        found = [chain for entry in self.by_sin.values() for chain in entry.chains]
        return tuple(sorted(found, key=lambda c: c.start))

    def chain_at(self, index: int) -> ResonanceChain | None:
        """
        The A-Reson. line containing chain position *index*, or ``None``.

        This is how a skill asks about its **own** run rather than some
        other run elsewhere in the turn.
        """
        if index < 0:
            return None
        for entry in self.by_sin.values():
            for chain in entry.chains:
                if chain.contains(index):
                    return chain
        return None

    def indices_of(self, sin: str | None) -> tuple[int, ...]:
        """The Reson. line for *sin*: every chain position it occupies."""
        entry = self.by_sin.get(self._key(sin)) if sin is not None else None
        return entry.indices if entry is not None else ()

    def summary(self) -> str:
        """
        One-line dashboard string, e.g. ``Lust x5 (A-Reson 3) | Pride x2``.

        Reports the longest run, matching the dashboard rule. When an
        Affinity formed more than one run the sum is appended, since the
        two figures diverge there.
        """
        if not self.by_sin:
            return "none"
        parts = []
        for sin in sorted(self.by_sin, key=lambda s: (-self.by_sin[s].count, s)):
            entry = self.by_sin[sin]
            text = f"{sin.capitalize()} x{entry.count}"
            if entry.chains:
                text += f" (A-Reson {entry.absolute_longest}"
                if len(entry.chains) > 1:
                    text += f", sum {entry.absolute_sum}"
                text += ")"
            parts.append(text)
        return " | ".join(parts)


def compute_resonance(
    actions: list["Action"],
) -> tuple[ResonanceResult, list[int]]:
    """
    Compute the turn's resonance from *actions*, already in chain order.

    Chain order is speed order — the resolution order, fastest first —
    which is what a pre-combat check receives.

    Returns the queryable result plus a list of per-action bonuses
    parallel to *actions*, so the caller decides where each lands
    (Offense vs Defense Level).
    """
    sins: list[str | None] = [sin_of(action.skill) for action in actions]
    bonuses = [0] * len(actions)

    # ── Reson.: bucket by Affinity, ignoring adjacency entirely ──────
    positions: dict[str, list[int]] = {}
    for index, sin in enumerate(sins):
        if sin is not None:
            positions.setdefault(sin, []).append(index)

    for sin, indices in positions.items():
        if len(indices) < RESONANCE_MINIMUM:
            continue
        for position, index in enumerate(indices, start=1):
            bonuses[index] = _table_lookup(RESONANCE_OL, position)

    # ── A-Reson.: maximal runs of one Affinity in the full chain ─────
    # Scanning the full chain (not each Affinity's own subsequence) is
    # what makes a different Affinity in between break the run. Every
    # qualifying run is kept as its own line, not collapsed to a max:
    # "longest" and "sum" are both derived from these afterwards.
    runs: dict[str, list[ResonanceChain]] = {}
    run_start = 0
    for index in range(len(sins) + 1):
        at_end = index == len(sins)
        if not at_end and sins[index] is not None and sins[index] == sins[run_start]:
            continue

        sin = sins[run_start] if run_start < len(sins) else None
        length = index - run_start
        if sin is not None and length >= ABSOLUTE_MINIMUM:
            flat = _table_lookup(ABSOLUTE_RESONANCE_OL, length)
            runs.setdefault(sin, []).append(
                ResonanceChain(sin=sin, start=run_start, length=length, bonus=flat)
            )
            for run_index in range(run_start, index):
                bonuses[run_index] = _combine_bonus(bonuses[run_index], flat)
        run_start = index

    result = ResonanceResult(
        by_sin={
            sin: SinResonance(
                sin=sin,
                count=len(indices),
                indices=tuple(indices),
                chains=tuple(runs.get(sin, ())),
            )
            for sin, indices in positions.items()
            if len(indices) >= RESONANCE_MINIMUM
        }
    )
    return result, bonuses


def resonance_check(plan: "TurnPlan") -> None:
    """
    Pre-combat check: compute resonance and apply it to the turn.

    Writes each skill's bonus onto its own Action — Offense Level for
    attack skills, Defense Level for defensive ones — and publishes the
    result to ``plan.state`` so effects and passives can query it during
    resolution via ``utils.check_resonance``.

    Also stamps each Action with its ``chain_index``, assigned here — in
    the same pass as the computation — so a skill's position and the
    result it indexes into can never disagree.
    """
    result, bonuses = compute_resonance(plan.actions)
    plan.state["resonance"] = result

    plan.log.append(f"[resonance] {result.summary()}")

    for index, (action, bonus) in enumerate(zip(plan.actions, bonuses)):
        action.chain_index = index
        if bonus <= 0:
            continue
        offensive = is_offensive(action.skill)
        if offensive:
            action.offense_level_bonus = bonus
        else:
            action.defense_level_bonus = bonus
        owner = action.unit.name if action.unit is not None else "-"
        plan.log.append(
            f"[resonance] {owner} {action.skill.name} "
            f"({sin_of(action.skill)}): +{bonus} "
            f"{'Offense' if offensive else 'Defense'} Level"
        )
