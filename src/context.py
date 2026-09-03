"""
CombatContext: Mutable state bag passed through every effect during a turn.

Effects read and mutate this object to apply damage changes, track kills,
record coin-flip results, etc.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from src.coin import Coin
    from src.enemy import Enemy
    from src.skill import Skill


@dataclass
class CombatContext:
    """
    Shared mutable state for one turn of combat.

    Attributes
    ----------
    current_skill : Skill | None
        The skill currently being resolved.
    current_coin : Coin | None
        The coin currently being flipped / resolved.
    attacker : Enemy | None
        The entity whose skill is currently being resolved.
    target : Enemy | None
        The entity being attacked.
    coin_result : str | None
        Result of the latest coin flip: ``"heads"`` or ``"tails"``.
    is_cracking : bool
        Whether the current hit is occurring with cracking active.
    is_crit : bool
        Whether the current hit is a critical.
    is_clashing : bool
        Whether the current skill is in a clash.
    clash_won : bool | None
        Result of the clash (None if no clash happened).
    target_killed : bool
        Whether the target was killed by the latest hit / skill.
    final_damage : int
        The running damage total that effects may modify.
    damage_modifiers : list[int]
        Ordered list of additive damage adjustments applied so far.
    log : list[str]
        Human-readable event log for debugging / replay.
    extra : dict[str, Any]
        Free-form bucket for custom data that effects need to share.
    """

    current_skill: "Skill | None" = None
    current_coin: "Coin | None" = None
    attacker: "Enemy | None" = None
    target: "Enemy | None" = None
    coin_result: str | None = None
    is_cracking: bool = False
    is_crit: bool = False
    is_clashing: bool = False
    clash_won: bool | None = None
    target_killed: bool = False
    final_damage: int = 0
    damage_modifiers: list[int] = field(default_factory=list)
    log: list[str] = field(default_factory=list)
    extra: dict[str, Any] = field(default_factory=dict)

    # ── convenience helpers ──────────────────────────────────────────

    def add_damage(self, amount: int, source: str = "") -> None:
        """Apply an additive damage modifier and log it."""
        self.damage_modifiers.append(amount)
        self.final_damage += amount
        if source:
            self.log.append(f"[damage] {source}: {amount:+d}")

    def reset_for_skill(self, skill: "Skill") -> None:
        """Reset per-skill state at the start of a new skill resolution."""
        self.current_skill = skill
        self.current_coin = None
        self.coin_result = None
        self.is_cracking = False
        self.is_crit = False
        self.is_clashing = False
        self.clash_won = None
        self.target_killed = False
        self.final_damage = skill.base_power
        self.damage_modifiers.clear()

    def reset_for_coin(self, coin: "Coin") -> None:
        """Reset per-coin state before each coin flip."""
        self.current_coin = coin
        self.coin_result = None
        self.is_crit = False
        self.target_killed = False
