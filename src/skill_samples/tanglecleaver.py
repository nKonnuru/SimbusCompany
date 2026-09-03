"""
Sample skill: **Tanglecleaver**

Spec implemented:
- Base power 5, coin power 4, offense level 5
- Damage type: ("Blunt", "Wrath")
- 3 coins
- +1 coin power per 8 (Burn potency + Tremor potency) on target (max +2)
- Each coin attempts to spend 1 Tigermark Round

Coin 1:
- [On Hit] +3 Tremor Potency
- [On Hit] +3 Burn Potency

Coin 2:
- [On Hit] +3 Tremor Count
- [On Hit] +3 Burn Count

Coin 3:
- If this coin spent Tigermark Round: +50% damage
- [On Hit] Amplitude Conversion -> Tremor Scorch
- [On Hit] Trigger Tremor Burst, then reduce Tremor Count by 1
- If this coin spent Tigermark Round: activate the burst+reduce package
  2 additional times
"""

from __future__ import annotations

from src.coin import Coin
from src.effect import CoinPhase, Effect, SkillPhase
from src.environment import Environment
from src.skill import Skill


# =====================================================================
#  Helpers
# =====================================================================


def _get_potency(enemy) -> tuple[int, int]:
    """Return (burn_potency, tremor_potency) with safe integer coercion."""
    burn = max(0, int(enemy.get_status("burn_potency", 0)))
    tremor = max(0, int(enemy.get_status("tremor_potency", 0)))
    return burn, tremor


def _spend_tigermark_round(env: Environment) -> None:
    """Attempt to spend 1 Tigermark Round; record result in coin flags."""
    if env.unit is None or env.coin_env is None:
        return

    rounds = max(0, int(env.unit.get_status("tigermark_round", 0)))
    spent = rounds > 0

    if spent:
        new_rounds = rounds - 1
        if new_rounds == 0:
            env.unit.remove_status("tigermark_round")
        else:
            env.unit.set_status("tigermark_round", min(new_rounds, 99))

    env.coin_env.flags["spent_tigermark_round"] = spent


# =====================================================================
#  Skill-level callbacks
# =====================================================================


def _persistent_coin_power_from_burn_tremor_potency(env: Environment) -> None:
    """
    +1 coin power per 8 (Burn Potency + Tremor Potency), max +2.

    Recomputed per coin and applied idempotently because coin power persists.
    """
    prev = int(env.global_state.get("_tanglecleaver_cp_bonus", 0))
    env.coin_power -= prev

    if env.enemy is None:
        env.global_state["_tanglecleaver_cp_bonus"] = 0
        return

    burn, tremor = _get_potency(env.enemy)
    bonus = min((burn + tremor) // 8, 2)
    env.coin_power += bonus
    env.global_state["_tanglecleaver_cp_bonus"] = bonus


# =====================================================================
#  Coin effects
# =====================================================================


def _coin1_inflict_tremor_potency(env: Environment) -> None:
    if env.enemy is None:
        return
    current = max(0, int(env.enemy.get_status("tremor_potency", 0)))
    env.enemy.set_status("tremor_potency", min(current + 3, 99))


def _coin1_inflict_burn_potency(env: Environment) -> None:
    if env.enemy is None:
        return
    current = max(0, int(env.enemy.get_status("burn_potency", 0)))
    env.enemy.set_status("burn_potency", min(current + 3, 99))


def _coin2_inflict_tremor_count(env: Environment) -> None:
    if env.enemy is None:
        return
    current = max(0, int(env.enemy.get_status("tremor_count", 0)))
    env.enemy.set_status("tremor_count", min(current + 3, 99))


def _coin2_inflict_burn_count(env: Environment) -> None:
    if env.enemy is None:
        return
    current = max(0, int(env.enemy.get_status("burn_count", 0)))
    env.enemy.set_status("burn_count", min(current + 3, 99))


def _coin3_spent_damage_boost(env: Environment) -> None:
    """If this coin spent Tigermark Round, gain +50% damage."""
    if env.coin_env is None:
        return
    if bool(env.coin_env.flags.get("spent_tigermark_round", False)):
        env.coin_env.dynamic += 0.50


def _coin3_convert_to_scorch(env: Environment) -> None:
    """On hit: convert current tremor amplitude to Scorch."""
    if env.enemy is not None:
        env.enemy.convert_tremor_amplitude("scorch")


def _coin3_burst_then_reduce_once(env: Environment) -> None:
    """Trigger burst once, then reduce Tremor Count by 1."""
    if env.enemy is None or env.target_killed:
        return

    env.on_tremor_burst()

    count = max(0, int(env.enemy.get_status("tremor_count", 0)))
    new_count = max(0, count - 1)
    if new_count == 0:
        env.enemy.remove_status("tremor_count")
    else:
        env.enemy.set_status("tremor_count", min(new_count, 99))


def _coin3_extra_burst_packages_if_spent(env: Environment) -> None:
    """If spent this coin, repeat burst+reduce package 2 additional times."""
    if env.coin_env is None:
        return
    if not bool(env.coin_env.flags.get("spent_tigermark_round", False)):
        return

    _coin3_burst_then_reduce_once(env)
    _coin3_burst_then_reduce_once(env)


# =====================================================================
#  Build function
# =====================================================================


def make_tanglecleaver() -> Skill:
    """Construct and return Tanglecleaver."""

    coin1 = Coin(name="Tanglecleaver Coin 1", coin_power=4)
    coin1.add_effect(
        Effect(
            name="Spend 1 Tigermark Round",
            phase=CoinPhase.COIN_START,
            apply=_spend_tigermark_round,
            priority=-10,
        )
    )
    coin1.add_effect(
        Effect(
            name="+3 Tremor Potency",
            phase=CoinPhase.ON_HIT,
            apply=_coin1_inflict_tremor_potency,
        )
    )
    coin1.add_effect(
        Effect(
            name="+3 Burn Potency",
            phase=CoinPhase.ON_HIT,
            apply=_coin1_inflict_burn_potency,
        )
    )

    coin2 = Coin(name="Tanglecleaver Coin 2", coin_power=4)
    coin2.add_effect(
        Effect(
            name="Spend 1 Tigermark Round",
            phase=CoinPhase.COIN_START,
            apply=_spend_tigermark_round,
            priority=-10,
        )
    )
    coin2.add_effect(
        Effect(
            name="+3 Tremor Count",
            phase=CoinPhase.ON_HIT,
            apply=_coin2_inflict_tremor_count,
        )
    )
    coin2.add_effect(
        Effect(
            name="+3 Burn Count",
            phase=CoinPhase.ON_HIT,
            apply=_coin2_inflict_burn_count,
        )
    )

    coin3 = Coin(name="Tanglecleaver Coin 3", coin_power=4)
    coin3.add_effect(
        Effect(
            name="Spend 1 Tigermark Round",
            phase=CoinPhase.COIN_START,
            apply=_spend_tigermark_round,
            priority=-10,
        )
    )
    coin3.add_effect(
        Effect(
            name="+50% Damage if Tigermark spent",
            phase=CoinPhase.COIN_START,
            apply=_coin3_spent_damage_boost,
        )
    )
    coin3.add_effect(
        Effect(
            name="Amplitude Conversion -> Scorch",
            phase=CoinPhase.ON_HIT,
            apply=_coin3_convert_to_scorch,
            priority=-10,
        )
    )
    coin3.add_effect(
        Effect(
            name="Burst then -1 Tremor Count",
            phase=CoinPhase.ON_HIT,
            apply=_coin3_burst_then_reduce_once,
            priority=0,
        )
    )
    coin3.add_effect(
        Effect(
            name="Spent: repeat burst package x2",
            phase=CoinPhase.ON_HIT,
            apply=_coin3_extra_burst_packages_if_spent,
            priority=1,
        )
    )

    skill = Skill(
        name="Tanglecleaver",
        base_power=5,
        coin_power=4,
        offense_level=5,
        damage_type=("Blunt", "Wrath"),
    )
    skill.add_coin(coin1)
    skill.add_coin(coin2)
    skill.add_coin(coin3)

    skill.add_effect(
        Effect(
            name="Coin Power +1 per 8 (Burn Pot + Tremor Pot), max 2",
            phase=SkillPhase.PERSISTENT,
            apply=_persistent_coin_power_from_burn_tremor_potency,
        )
    )

    return skill
