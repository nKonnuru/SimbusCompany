"""
Unit: A player-controlled entity that extends Enemy with skills and speed.

A Unit has all the properties of an Enemy (level, defense, HP, statuses,
passives) plus a set of skills and a base speed value.
"""

from __future__ import annotations

from dataclasses import dataclass, field
import random

from src.enemy import Enemy
from src.skill import Skill


@dataclass
class Unit(Enemy):
    """
    A player-controlled combatant.

    Inherits
    --------
    Everything from Enemy: name, base_level, defense_level, hp, max_hp,
    statuses, passives.

    Additional Attributes
    ---------------------
    skills : list[Skill]
        The skills this unit can use in combat.
    speed : int
        Base speed value influencing turn order.

    Example
    -------
    >>> unit = Unit(
    ...     name="Yi Sang",
    ...     base_level=40,
    ...     defense_level=-5,
    ...     hp=200,
    ...     max_hp=200,
    ...     speed=6,
    ...     skills=[slash, pierce],
    ... )
    """

    skills: list[Skill] = field(default_factory=list)
    # Slot-based storage supports multiple forms per slot later (1-2, 3-3, etc.).
    skill_slots: dict[str, list[Skill]] = field(
        default_factory=lambda: {"1": [], "2": [], "3": [], "defense": []}
    )
    speed: int = 0
    speed_min: int = 0
    speed_max: int = 0
    sp: int = 0                # sanity — shifts coin-flip odds (base 50 + sp)

    def __post_init__(self) -> None:
        # Keep the old flat skills list as the execution source, but allow
        # callers to initialize skills by slot.
        if any(self.skill_slots.values()) and not self.skills:
            self.sync_skills_from_slots()

        # If a speed range is provided and no concrete speed is set yet,
        # default to the minimum speed.
        if self.speed == 0 and self.speed_min > 0 and self.speed_max >= self.speed_min:
            self.speed = self.speed_min

    # ── skill helpers ────────────────────────────────────────────────

    def add_skill(self, skill: Skill, slot: str | None = None) -> None:
        """Append a skill to this unit and optionally to a named slot."""
        self.skills.append(skill)
        if slot is not None:
            self.skill_slots.setdefault(slot, []).append(skill)

    def sync_skills_from_slots(self) -> None:
        """Rebuild flat skill list from skill slot groups in slot order."""
        ordered: list[Skill] = []
        for slot in ("1", "2", "3", "defense"):
            ordered.extend(self.skill_slots.get(slot, []))
        self.skills = ordered

    def get_skill_forms(self, slot: str) -> list[Skill]:
        """Return all configured forms for a given skill slot."""
        return list(self.skill_slots.get(slot, []))

    def roll_speed(self, rng: random.Random | None = None) -> int:
        """Roll and set current speed from [speed_min, speed_max] range."""
        if self.speed_min <= 0 or self.speed_max < self.speed_min:
            return self.speed

        roller = rng if rng is not None else random
        self.speed = roller.randint(self.speed_min, self.speed_max)
        return self.speed
