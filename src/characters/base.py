"""Shared character payload types."""

from __future__ import annotations

from dataclasses import dataclass, field

from src.skill import Skill
from src.unit import Unit
from src.passive import Passive


@dataclass
class Character(Unit):
    """
    Character payload used by character definitions.

    Skill slots are lists so each slot can hold multiple forms later
    (for example 1-2 or 3-3 variants) without schema changes.
    """

    id_name: str = ""

    skill_1: list[Skill] = field(default_factory=list)
    skill_2: list[Skill] = field(default_factory=list)
    skill_3: list[Skill] = field(default_factory=list)
    defense: list[Skill] = field(default_factory=list)

    passive_effects: list[Passive] = field(default_factory=list)

    def __post_init__(self) -> None:
        self.skill_slots["1"] = list(self.skill_1)
        self.skill_slots["2"] = list(self.skill_2)
        self.skill_slots["3"] = list(self.skill_3)
        self.skill_slots["defense"] = list(self.defense)
        self.sync_skills_from_slots()

        if self.passive_effects:
            self.passives = list(self.passive_effects)
        else:
            self.passive_effects = list(self.passives)

        super().__post_init__()

    @property
    def sinner_name(self) -> str:
        """Alias for Unit.name to keep character sample ergonomics."""
        return self.name

    @property
    def level(self) -> int:
        """Alias for Unit.base_level."""
        return self.base_level

    @property
    def skills_by_type(self) -> dict[str, list[Skill]]:
        """Return all skill groups keyed by slot label."""
        return {
            "1": self.skill_1,
            "2": self.skill_2,
            "3": self.skill_3,
            "defense": self.defense,
        }

    @property
    def effective_defense_level(self) -> int:
        """Computed defense level used in combat formulas."""
        return self.base_level + self.defense_level
