"""
Skill: A complete attack action composed of one or more Coins.

A skill carries its own speed value (used for turn ordering), a sequence
of Coins, and a dictionary of skill-level effects keyed by SkillPhase tags.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import TYPE_CHECKING

from src.coin import Coin
from src.effect import Effect, SkillPhase

if TYPE_CHECKING:
    from src.environment import Environment


@dataclass
class Skill:
    """
    Represents a full skill with its coins and phase-tagged effects.

    Attributes
    ----------
    name : str
        Human-readable skill name.
    speed : int
        Determines activation order within the turn (higher = faster).
    base_power : int
        Flat base damage of the skill before coins are resolved.
    coin_power : int
        Power added per Heads flip (stacks across coins).
    offense_level : int
        Skill-level OL contribution (added to unit's base_level for total OL).
    damage_type : tuple[str, str]
        ``(physical_type, sin_type)`` — e.g. ``("Slash", "Wrath")``.
    coins : list[Coin]
        Ordered sequence of coins that make up this skill's attack.
    effects : dict[SkillPhase, list[Effect]]
        Phase-keyed mapping of skill-level effects.
    """

    name: str = "Skill"
    speed: int = 0
    base_power: int = 0
    coin_power: int = 0
    offense_level: int = 0
    damage_type: tuple[str, str] = ("Blunt", "Wrath")
    coins: list[Coin] = field(default_factory=list)
    effects: dict[SkillPhase, list[Effect]] = field(default_factory=dict)

    # ── coin management ──────────────────────────────────────────────

    def add_coin(self, coin: Coin) -> None:
        """Append a coin to the skill's sequence."""
        self.coins.append(coin)

    # ── effect management ────────────────────────────────────────────

    def add_effect(self, effect: Effect) -> None:
        """Register a skill-level effect under its declared phase."""
        if not isinstance(effect.phase, SkillPhase):
            raise TypeError(
                f"Skill effects must use SkillPhase tags, got {effect.phase!r}"
            )
        self.effects.setdefault(effect.phase, []).append(effect)

    def get_effects(self, phase: SkillPhase) -> list[Effect]:
        """Return effects for *phase*, sorted by priority (ascending)."""
        return sorted(
            self.effects.get(phase, []),
            key=lambda e: e.priority,
        )

    def execute_phase(self, phase: SkillPhase, env: "Environment") -> None:
        """Fire every effect registered for *phase* against *env*."""
        for effect in self.get_effects(phase):
            effect.execute(env)

    # ── aggregation helpers ──────────────────────────────────────────

    @staticmethod
    def collect_effects(
        skills: list["Skill"], phase: SkillPhase
    ) -> list[Effect]:
        """
        Gather effects for *phase* across many skills, sorted by priority.

        This is the "filter" step described in the game loop – it pulls
        every relevant effect from the full skill list for a given phase,
        merges them, and returns them ready to execute.
        """
        merged: list[Effect] = []
        for skill in skills:
            merged.extend(skill.effects.get(phase, []))
        return sorted(merged, key=lambda e: e.priority)
