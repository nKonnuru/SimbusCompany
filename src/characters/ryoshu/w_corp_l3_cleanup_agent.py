"""Character: Ryoshu - W Corp. L3 Cleanup Agent."""

from __future__ import annotations

import math

from src.coin import Coin
from src.effect import CoinPhase, Effect, SkillPhase
from src.passive import Passive
from src.skill import Skill
from src.utils import (
    add_coin_power,
    add_dynamic,
    add_enemy_status,
    add_status,
    check_count,
    check_enemy_hp_below,
    deal_bonus_damage_from_current,
)
from src.characters.base import Character


def _compute_hp(level: int) -> int:
    """HP formula for this ID: 66 + (2.28 * level), rounded to nearest int."""
    return round(66 + (2.28 * level))


def _compute_stagger_thresholds(max_hp: int) -> list[int]:
    """Compute absolute stagger breakpoints at 70%, 40%, and 20% HP."""
    return [
        math.floor(max_hp * 0.70),
        math.floor(max_hp * 0.40),
        math.floor(max_hp * 0.20),
    ]


def _make_skill_1_ec() -> Skill:
    """Create Ryoshu Skill 1: E.C."""
    coin1 = Coin(name="E.C. Coin 1", coin_power=2)
    coin1.add_effect(
        Effect(
            name="On Hit: Gain +2 Charge Count",
            phase=CoinPhase.ON_HIT,
            apply=add_status,
            args=("charge_count", 2),
        )
    )

    coin2 = Coin(name="E.C. Coin 2", coin_power=2)
    coin2.add_effect(
        Effect(
            name="On Hit: Gain +2 Charge Count",
            phase=CoinPhase.ON_HIT,
            apply=add_status,
            args=("charge_count", 2),
        )
    )

    coin3 = Coin(name="E.C. Coin 3", coin_power=2)

    skill = Skill(
        name="E.C.",
        base_power=3,
        coin_power=2,
        offense_level=5,
        damage_type=("Slash", "Lust"),
    )
    skill.add_coin(coin1)
    skill.add_coin(coin2)
    skill.add_coin(coin3)

    skill.add_effect(
        Effect(
            name="Persistent: +10% damage at 10+ Charge",
            phase=SkillPhase.PERSISTENT,
            apply=add_dynamic,
            args=(0.10,),
            condition=check_count,
            condition_args=("charge_count", 10),
        )
    )
    skill.add_effect(
        Effect(
            name="Persistent: +10% damage at 15+ Charge",
            phase=SkillPhase.PERSISTENT,
            apply=add_dynamic,
            args=(0.10,),
            condition=check_count,
            condition_args=("charge_count", 15),
        )
    )

    return skill


def _make_skill_2_leap() -> Skill:
    """Create Ryoshu Skill 2: Leap."""
    coin1 = Coin(name="Leap Coin 1", coin_power=5)
    coin1.add_effect(
        Effect(
            name="On Hit: +2 Slash Fragility at 10+ Charge",
            phase=CoinPhase.ON_HIT,
            apply=add_enemy_status,
            args=("slash_fragility", 2),
            condition=check_count,
            condition_args=("charge_count", 10),
        )
    )

    coin2 = Coin(name="Leap Coin 2", coin_power=5)

    coin3 = Coin(name="Leap Coin 3", coin_power=5)
    coin3.add_effect(
        Effect(
            name="On Hit: +30% damage below 30% HP",
            phase=CoinPhase.ON_HIT,
            apply=deal_bonus_damage_from_current,
            args=(0.30, "leap_low_hp_bonus"),
            condition=check_enemy_hp_below,
            condition_args=(0.30,),
        )
    )

    skill = Skill(
        name="Leap",
        base_power=2,
        coin_power=5,
        offense_level=5,
        damage_type=("Slash", "Pride"),
    )
    skill.add_coin(coin1)
    skill.add_coin(coin2)
    skill.add_coin(coin3)

    skill.add_effect(
        Effect(
            name="On Use: Gain +7 Charge Count",
            phase=SkillPhase.ON_USE,
            apply=add_status,
            args=("charge_count", 7),
        )
    )

    skill.add_effect(
        Effect(
            name="Persistent: +1 Coin Power at 10+ Charge",
            phase=SkillPhase.PERSISTENT,
            apply=add_coin_power,
            args=(1,),
            condition=check_count,
            condition_args=("charge_count", 10),
        )
    )
    skill.add_effect(
        Effect(
            name="Persistent: +1 Coin Power at 15+ Charge",
            phase=SkillPhase.PERSISTENT,
            apply=add_coin_power,
            args=(1,),
            condition=check_count,
            condition_args=("charge_count", 15),
        )
    )

    skill.add_effect(
        Effect(
            name="On Kill: Gain 3 Haste next turn",
            phase=SkillPhase.ON_KILL,
            apply=add_status,
            args=("haste_count", 3),
        )
    )

    return skill


def _ddedr_on_use_consume_charge_and_gain_coin_power(env) -> None:
    if env.unit is None:
        return

    charge_count = int(env.unit.get_status("charge_count", 0))
    consumed = 15 if charge_count >= 15 else charge_count

    remaining = max(0, charge_count - consumed)
    if remaining == 0:
        env.unit.remove_status("charge_count")
    else:
        env.unit.set_status("charge_count", remaining)

    env.global_state["ddedr_consumed_15"] = consumed >= 15
    add_coin_power(env, 5)


def _ddedr_on_kill_charge_barrier(env) -> None:
    if env.unit is None:
        return
    if not bool(env.global_state.get("ddedr_consumed_15", False)):
        return

    current = int(env.unit.get_status("charge_barrier", 0))
    env.unit.set_status("charge_barrier", min(current + 7, 99))


def _ddedr_track_heads_hit_for_last_coin(env) -> None:
    key = "ddedr_last_coin_dynamic_stacks"
    env.global_state[key] = int(env.global_state.get(key, 0)) + 1


def _ddedr_apply_tracked_dynamic_on_last_coin(env) -> None:
    key = "ddedr_last_coin_dynamic_stacks"
    stacks = max(0, int(env.global_state.get(key, 0)))
    if stacks <= 0:
        return

    add_dynamic(env, 0.10 * stacks)
    env.global_state[key] = 0


def _make_skill_3_ddedr() -> Skill:
    """Create Ryoshu Skill 3: D.D.E.D.R."""
    coin1 = Coin(name="D.D.E.D.R. Coin 1", coin_power=2)
    coin1.add_effect(
        Effect(
            name="Heads Hit: Track +10% for final coin",
            phase=CoinPhase.ON_HIT_HEADS,
            apply=_ddedr_track_heads_hit_for_last_coin,
        )
    )

    coin2 = Coin(name="D.D.E.D.R. Coin 2", coin_power=2)
    coin2.add_effect(
        Effect(
            name="Heads Hit: Track +10% for final coin",
            phase=CoinPhase.ON_HIT_HEADS,
            apply=_ddedr_track_heads_hit_for_last_coin,
        )
    )

    coin3 = Coin(name="D.D.E.D.R. Coin 3", coin_power=2)
    coin3.add_effect(
        Effect(
            name="Heads Hit: Track +10% for final coin",
            phase=CoinPhase.ON_HIT_HEADS,
            apply=_ddedr_track_heads_hit_for_last_coin,
        )
    )

    coin4 = Coin(name="D.D.E.D.R. Coin 4", coin_power=2)
    coin4.add_effect(
        Effect(
            name="Coin Start: Apply tracked final-coin dynamic",
            phase=CoinPhase.COIN_START,
            apply=_ddedr_apply_tracked_dynamic_on_last_coin,
        )
    )
    coin4.add_effect(
        Effect(
            name="On Kill: +7 Charge Barrier (if consumed 15)",
            phase=CoinPhase.ON_KILL,
            apply=_ddedr_on_kill_charge_barrier,
        )
    )

    skill = Skill(
        name="D.D.E.D.R.",
        base_power=3,
        coin_power=2,
        offense_level=5,
        damage_type=("Slash", "Envy"),
    )
    skill.add_coin(coin1)
    skill.add_coin(coin2)
    skill.add_coin(coin3)
    skill.add_coin(coin4)

    skill.add_effect(
        Effect(
            name="On Use: D.D.E.D.R charge conversion",
            phase=SkillPhase.ON_USE,
            apply=_ddedr_on_use_consume_charge_and_gain_coin_power,
        )
    )

    return skill


def _make_defense_charged_evade() -> Skill:
    skill = Skill(
        name="Charged Evade",
        damage_type=("Evade", "Lust"),
    )

    def add_base_power_by_status(env, status_name: str, divisor: int, max_bonus: int) -> None:
        """Add base power based on the stacks of a specific status on the unit."""
        if env.unit is None:
            return
            
        stacks = int(env.unit.get_status(status_name, 0))
        bonus = min(stacks // divisor, max_bonus)
        
        if bonus > 0:
            env.base += bonus
            env.current_power += bonus
            if env.is_debugging:
                env.log.append(f"     [base_power_bonus] +{bonus} Base Power (from {status_name})")

    skill.add_effect(
        Effect(
            name="On Use: Base Power +1 per 5 Charge Count (Max 3)",
            phase=SkillPhase.ON_USE,
            apply=add_base_power_by_status,
            args=("charge_count", 5, 3)
        )
    )

    return skill


def _make_passive_dimensional_demon_edge() -> Passive:
    passive = Passive(
        name="Dimensional Demon Edge",
    )
    passive.add_effect(
        Effect(
            name="On Kill: Gain +3 Charge Count",
            phase=CoinPhase.ON_KILL,
            apply=add_status,
            args=("charge_count", 3),
            max_procs=3,
        )
    )
    return passive


def make_ryoshu_w_corp_l3_cleanup_agent(level: int = 60) -> Character:
    """Build Ryoshu - W Corp. L3 Cleanup Agent at the requested level."""
    hp = _compute_hp(level)
    return Character(
        name="Ryoshu",
        id_name="W Corp. L3 Cleanup Agent",
        base_level=level,
        skill_1=[_make_skill_1_ec()],
        skill_2=[_make_skill_2_leap()],
        skill_3=[_make_skill_3_ddedr()],
        defense=[_make_defense_charged_evade()],
        hp=hp,
        max_hp=hp,
        speed_min=3,
        speed_max=6,
        defense_level=-4,
        stagger_thresholds=_compute_stagger_thresholds(hp),
        phys_res={
            "Slash": 0.5,
            "Pierce": 1.0,
            "Blunt": 2.0,
        },
        sin_res={},
        passives=[_make_passive_dimensional_demon_edge()],
    )
