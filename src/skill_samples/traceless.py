"""
Sample skill: **Traceless to Sight and Sound Alike**

Demonstrates persistent per-coin dynamics, coin-level dynamics,
on-hit status infliction, conditional coin power, and coin reuse.

Skill summary
-------------
- Base power 5, coin power 4, 3 coins, offense level 3.
- Damage type: ("Slash", "Gluttony").

**Persistent** (rechecked every coin):
  - If unit Speed > target Speed, deal +(speed_diff × Rupture Potency
    on target)% damage (max 50%).  Applied to ``CoinEnvironment.dynamic``.
  - If unit Speed > target Speed, gain Coin Power +1 per 2 Speed
    difference (max 2).

**[Before Use]** If target has 15+ Rupture Potency, Coin Power +1.
**[Before Use]** Gain 3 Strider【Mao】 (stored as ``strider_mao`` status).
**[Clash Win]** Inflict +2 Rupture Count.

Coin 1:
  - [On Hit] Inflict +1 Rupture Count.
  - [On Hit] Inflict Deathrite【Haste】 — *not yet implemented*.

Coin 2:
  - [On Hit] Inflict 1 Rupture Potency.

Coin 3:
  - At 10+ Speed, deal +30% damage (coin-level ``CoinEnvironment.dynamic``).
  - [On Hit] Inflict 3 Rupture Potency.
  - [On Hit] At 10+ Speed, Reuse Coin once (once per Skill).
  - [Reuse — On Hit] Inflict Deathrite【Haste】 — *not yet implemented*.
    Reuse also re-fires the base coin On Hit (3 Rupture Potency).
"""

from __future__ import annotations

from src.coin import Coin, ReuseCondition
from src.effect import CoinPhase, Effect, SkillPhase
from src.environment import Environment
from src.skill import Skill


# ═══════════════════════════════════════════════════════════════════════
#  Persistent callbacks  (fire every coin)
# ═══════════════════════════════════════════════════════════════════════


def _persistent_speed_rupture_damage(env: Environment) -> None:
    """
    Per-coin: +(speed_diff × rupture_potency)% damage, max 50 %.

    Written to ``CoinEnvironment.dynamic`` so it recalculates from
    scratch every coin (CoinEnvironment is fresh each coin).
    """
    if env.unit is None or env.enemy is None or env.coin_env is None:
        return
    speed_diff = env.unit.speed - env.enemy.speed
    if speed_diff <= 0:
        return
    rupture_pot = env.enemy.get_status("rupture_potency", 0)
    bonus_pct = min(speed_diff * rupture_pot, 50)
    env.coin_env.dynamic += bonus_pct / 100.0


def _persistent_speed_coin_power(env: Environment) -> None:
    """
    Per-coin: +1 Coin Power per 2 Speed difference, max 2.

    Idempotent: removes the previous bonus before re-applying, since
    ``env.coin_power`` persists across coins.
    """
    prev = env.global_state.get("_traceless_speed_cp", 0)
    env.coin_power -= prev

    if env.unit is None or env.enemy is None:
        env.global_state["_traceless_speed_cp"] = 0
        return

    speed_diff = env.unit.speed - env.enemy.speed
    if speed_diff <= 0:
        env.global_state["_traceless_speed_cp"] = 0
        return

    bonus = min(speed_diff // 2, 2)
    env.coin_power += bonus
    env.global_state["_traceless_speed_cp"] = bonus


# ═══════════════════════════════════════════════════════════════════════
#  Before Use callbacks
# ═══════════════════════════════════════════════════════════════════════


def _before_use_rupture_condition(env: Environment) -> bool:
    """True when the enemy has 15+ Rupture Potency."""
    if env.enemy is None:
        return False
    return env.enemy.get_status("rupture_potency", 0) >= 15


def _before_use_rupture_coin_power(env: Environment) -> None:
    """Coin Power +1 when target has 15+ Rupture Potency."""
    env.coin_power += 1


def _before_use_strider_mao(env: Environment) -> None:
    """Gain 3 Strider【Mao】 (base and max stack: 3)."""
    if env.unit is not None:
        env.unit.set_status("strider_mao", 3)


# ═══════════════════════════════════════════════════════════════════════
#  Clash Win callback
# ═══════════════════════════════════════════════════════════════════════


def _clash_win_rupture_count(env: Environment) -> None:
    """Inflict +2 Rupture Count on the enemy on clash win."""
    if env.enemy is not None:
        current = env.enemy.get_status("rupture_count", 0)
        env.enemy.set_status("rupture_count", min(current + 2, 99))


# ═══════════════════════════════════════════════════════════════════════
#  Coin 1 callbacks
# ═══════════════════════════════════════════════════════════════════════


def _coin1_inflict_rupture_count(env: Environment) -> None:
    """Coin 1 on-hit: inflict +1 Rupture Count on the enemy."""
    if env.enemy is not None:
        current = env.enemy.get_status("rupture_count", 0)
        env.enemy.set_status("rupture_count", min(current + 1, 99))


# ═══════════════════════════════════════════════════════════════════════
#  Coin 2 callbacks
# ═══════════════════════════════════════════════════════════════════════


def _coin2_inflict_rupture_potency(env: Environment) -> None:
    """Coin 2 on-hit: inflict +1 Rupture Potency on the enemy."""
    if env.enemy is not None:
        current = env.enemy.get_status("rupture_potency", 0)
        env.enemy.set_status("rupture_potency", min(current + 1, 99))


# ═══════════════════════════════════════════════════════════════════════
#  Coin 3 callbacks
# ═══════════════════════════════════════════════════════════════════════


def _coin3_speed_damage_boost(env: Environment) -> None:
    """Coin 3 COIN_START: +30 % damage at 10+ Speed (coin dynamic)."""
    if env.unit is not None and env.unit.speed >= 10 and env.coin_env is not None:
        env.coin_env.dynamic += 0.30


def _coin3_inflict_rupture_potency(env: Environment) -> None:
    """Coin 3 on-hit: inflict +3 Rupture Potency on the enemy."""
    if env.enemy is not None:
        current = env.enemy.get_status("rupture_potency", 0)
        env.enemy.set_status("rupture_potency", min(current + 3, 99))


def _reuse_speed_10_condition(env: Environment) -> bool:
    """True when the unit has 10+ Speed."""
    if env.unit is None:
        return False
    return env.unit.speed >= 10


# ═══════════════════════════════════════════════════════════════════════
#  Deathrite【Haste】 infliction
# ═══════════════════════════════════════════════════════════════════════


def _inflict_deathrite_haste(env: Environment) -> None:
    """Inflict Deathrite【Haste】 on the enemy (base & max stack = 3)."""
    if env.enemy is not None:
        env.enemy.set_status("deathrite_haste", 3)


# ═══════════════════════════════════════════════════════════════════════
#  Build function
# ═══════════════════════════════════════════════════════════════════════


def make_traceless() -> Skill:
    """Construct and return the *Traceless to Sight and Sound Alike* skill."""

    # ── Coins ────────────────────────────────────────────────────────
    coin1 = Coin(name="Traceless Coin 1", coin_power=4)
    coin1.add_effect(
        Effect(
            name="+1 Rupture Count",
            phase=CoinPhase.ON_HIT,
            apply=_coin1_inflict_rupture_count,
        )
    )
    coin1.add_effect(
        Effect(
            name="Inflict Deathrite【Haste】",
            phase=CoinPhase.ON_HIT,
            apply=_inflict_deathrite_haste,
        )
    )

    coin2 = Coin(name="Traceless Coin 2", coin_power=4)
    coin2.add_effect(
        Effect(
            name="+1 Rupture Potency",
            phase=CoinPhase.ON_HIT,
            apply=_coin2_inflict_rupture_potency,
        )
    )

    coin3 = Coin(name="Traceless Coin 3", coin_power=4)
    coin3.add_effect(
        Effect(
            name="+30% Damage at 10+ Speed",
            phase=CoinPhase.COIN_START,
            apply=_coin3_speed_damage_boost,
        )
    )
    coin3.add_effect(
        Effect(
            name="+3 Rupture Potency",
            phase=CoinPhase.ON_HIT,
            apply=_coin3_inflict_rupture_potency,
        )
    )
    coin3.add_reuse_condition(
        ReuseCondition(
            name="Reuse at 10+ Speed (once per Skill)",
            condition=_reuse_speed_10_condition,
            max_reuses=1,
        )
    )
    # Reuse on-hit: Deathrite【Haste】 (fires alongside base coin's +3 Rupture Potency)
    coin3.add_effect(
        Effect(
            name="Reuse: Inflict Deathrite【Haste】",
            phase=CoinPhase.ON_HIT,
            apply=_inflict_deathrite_haste,
            condition=lambda env: env.coin_env is not None and env.coin_env.is_reuse,
        )
    )

    # ── Skill ────────────────────────────────────────────────────────
    skill = Skill(
        name="Traceless to Sight and Sound Alike",
        base_power=5,
        coin_power=4,
        offense_level=3,
        damage_type=("Slash", "Gluttony"),
    )
    skill.add_coin(coin1)
    skill.add_coin(coin2)
    skill.add_coin(coin3)

    # ── Persistent effects (fire every coin) ─────────────────────────
    skill.add_effect(
        Effect(
            name="Persistent: +(Speed Diff × Rupture Pot)% Damage (max 50%)",
            phase=SkillPhase.PERSISTENT,
            apply=_persistent_speed_rupture_damage,
        )
    )
    skill.add_effect(
        Effect(
            name="Persistent: Coin Power from Speed Diff (+1/2, max 2)",
            phase=SkillPhase.PERSISTENT,
            apply=_persistent_speed_coin_power,
        )
    )

    # ── Before Use effects ───────────────────────────────────────────
    skill.add_effect(
        Effect(
            name="+1 Coin Power (Rupture Potency ≥ 15)",
            phase=SkillPhase.BEFORE_USE,
            apply=_before_use_rupture_coin_power,
            condition=_before_use_rupture_condition,
        )
    )
    skill.add_effect(
        Effect(
            name="Gain 3 Strider【Mao】",
            phase=SkillPhase.BEFORE_USE,
            apply=_before_use_strider_mao,
        )
    )

    # ── Clash Win ────────────────────────────────────────────────────
    skill.add_effect(
        Effect(
            name="Clash Win: +2 Rupture Count",
            phase=SkillPhase.CLASH_WIN,
            apply=_clash_win_rupture_count,
        )
    )

    return skill
