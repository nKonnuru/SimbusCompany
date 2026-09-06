"""
Team: the player's ordered roster of Identities for a battle.

Team order is the position an ID occupies in the party (position 0 is
first). It is deliberately *not* turn order — skills resolve by speed
(see ``GameLoop._order_actions``). Team order's mechanical role is
breaking speed ties: when two selected skills roll the same speed, the
one whose owner sits earlier on the team resolves first.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Iterator

from src.unit import Unit


@dataclass
class Team:
    """
    An ordered roster of Units.

    Attributes
    ----------
    members : list[Unit]
        The team in order — index 0 is the first position. The same
        Identity may appear twice: character builders are factories, so
        two calls produce two independent Units.

    Example
    -------
    >>> team = Team(members=[sinclair, ryoshu])
    >>> team.position_of(ryoshu)
    1
    """

    members: list[Unit] = field(default_factory=list)

    def position_of(self, unit: Unit | None) -> int:
        """
        Return *unit*'s team-order index, or ``len(members)`` if it is
        not on the team (so a stranger sorts last instead of raising).

        Matched by identity rather than equality: Unit is a mutable
        dataclass whose ``__eq__`` compares every field, so two distinct
        members built from the same Identity would otherwise collide.
        """
        for index, member in enumerate(self.members):
            if member is unit:
                return index
        return len(self.members)

    def __iter__(self) -> Iterator[Unit]:
        return iter(self.members)

    def __len__(self) -> int:
        return len(self.members)

    def __getitem__(self, index: int) -> Unit:
        return self.members[index]
