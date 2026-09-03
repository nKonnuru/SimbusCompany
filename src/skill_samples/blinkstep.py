"""
Sample skill: **Blinkstep**

Demonstrates conditional coin-power boosts, on-hit status infliction,
clash-win effects, and conditional coin reuse.

Skill summary
-------------
- Base power 3, coin power 4, 2 coins.
- **Condition**: +1 coin power if the unit is 3+ speed over the enemy.
- **Condition**: +1 coin power if the enemy has 4+ rupture potency.
- **Clash Win**: apply 1 rupture count to the enemy.
- **Coin 1 – On Hit**: inflict 1 rupture potency on the enemy.
- **Coin 2 – On Hit**: inflict 1 rupture count on the enemy.
- **Coin 2 – Reuse**: reuse once (1 available) if the unit has 10+ speed.
"""

from __future__ import annotations

from src.coin import Coin, ReuseCondition
from src.effect import CoinPhase, Effect, SkillPhase
from src.environment import Environment
from src.skill import Skill


# ═══════════════════════════════════════════════════════════════════════
#  Effect callbacks
# ═══════════════════════════════════════════════════════════════════════


def _speed_advantage_boost(env: Environment) -> None:
    """Add +1 coin power when the unit is 3+ speed over the enemy."""
    env.coin_power += 1


def _speed_advantage_condition(env: Environment) -> bool:
    """True when the unit's speed exceeds the enemy's by at least 3."""
    if env.unit is None or env.enemy is None:
        return False
    return env.unit.speed - env.enemy.speed >= 3


def _rupture_potency_boost(env: Environment) -> None:
    """Add +1 coin power when the enemy has 4+ rupture potency."""
    env.coin_power += 1


def _rupture_potency_condition(env: Environment) -> bool:
    """True when the enemy has a 'rupture_potency' status >= 4."""
    if env.enemy is None:
        return False
    return env.enemy.get_status("rupture_potency", 0) >= 4


def _clash_win_apply_rupture_count(env: Environment) -> None:
    """Apply 1 rupture count to the enemy on clash win."""
    if env.enemy is not None:
        current = env.enemy.get_status("rupture_count", 0)
        env.enemy.set_status("rupture_count", min(current + 1, 99))


def _coin1_inflict_rupture_potency(env: Environment) -> None:
    """Coin 1 on-hit: inflict +1 rupture potency on the enemy."""
    if env.enemy is not None:
        current = env.enemy.get_status("rupture_potency", 0)
        env.enemy.set_status("rupture_potency", min(current + 1, 99))


def _coin2_inflict_rupture_count(env: Environment) -> None:
    """Coin 2 on-hit: inflict +1 rupture count on the enemy."""
    if env.enemy is not None:
        current = env.enemy.get_status("rupture_count", 0)
        env.enemy.set_status("rupture_count", min(current + 1, 99))


def _reuse_speed_10_condition(env: Environment) -> bool:
    """True when the unit has 10+ speed."""
    if env.unit is None:
        return False
    return env.unit.speed >= 10


# ═══════════════════════════════════════════════════════════════════════
#  Build functions
# ═══════════════════════════════════════════════════════════════════════


def make_blinkstep() -> Skill:
    """Construct and return the Blinkstep skill."""

    # ── Coins ────────────────────────────────────────────────────────
    coin1 = Coin(name="Blinkstep Coin 1", coin_power=4)
    coin1.add_effect(
        Effect(
            name="+1 Rupture Potency",
            phase=CoinPhase.ON_HIT,
            apply=_coin1_inflict_rupture_potency,
        )
    )

    coin2 = Coin(name="Blinkstep Coin 2", coin_power=4)
    coin2.add_effect(
        Effect(
            name="+1 Rupture Count",
            phase=CoinPhase.ON_HIT,
            apply=_coin2_inflict_rupture_count,
        )
    )
    coin2.add_reuse_condition(
        ReuseCondition(
            name="Reuse if 10+ Speed",
            condition=_reuse_speed_10_condition,
            max_reuses=1,
        )
    )

    # ── Skill ────────────────────────────────────────────────────────
    skill = Skill(
        name="Blinkstep",
        base_power=3,
        coin_power=4,
        offense_level=1,
        damage_type=("Slash", "Sloth"),
    )
    skill.add_coin(coin1)
    skill.add_coin(coin2)

    # Skill-level conditional effects (fire at BEFORE_ATTACK so they
    # modify coin_power before the first flip).
    skill.add_effect(
        Effect(
            name="+1 Coin Power (Speed Advantage)",
            phase=SkillPhase.BEFORE_ATTACK,
            apply=_speed_advantage_boost,
            condition=_speed_advantage_condition,
        )
    )
    skill.add_effect(
        Effect(
            name="+1 Coin Power (Rupture Potency ≥ 4)",
            phase=SkillPhase.BEFORE_ATTACK,
            apply=_rupture_potency_boost,
            condition=_rupture_potency_condition,
        )
    )
    skill.add_effect(
        Effect(
            name="Clash Win: +1 Rupture Count",
            phase=SkillPhase.CLASH_WIN,
            apply=_clash_win_apply_rupture_count,
        )
    )

    return skill
