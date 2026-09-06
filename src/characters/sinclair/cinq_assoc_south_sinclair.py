"""Character: Sinclair - Cinq Assoc. South Section 4 Director"""
from __future__ import annotations

import math

from src.coin import Coin
from src.effect import CoinPhase, Effect, SkillPhase
from src.passive import Passive
from src.skill import Skill
from src.status_effects import apply_declared_duel
from src.utils import (
    add_coin_power,
    add_dynamic,
    add_enemy_status,
    add_status,
    queue_status,
    check_count,
    apply_speed_based_coin_power
)
from src.characters.base import Character

def _compute_hp(level: int) -> int:
    return round(79 + (2.73 * level))


def _compute_stagger_thresholds(max_hp: int) -> list[int]:
    return [
        math.floor(max_hp * 0.85),
        math.floor(max_hp * 0.65)
    ]


def _make_skill_1_remise() -> Skill:
    coin1 = Coin(name="Remise Coin 1", coin_power=4)
    coin1.add_effect(
        Effect(
            name="On Hit: Gain 1 Haste next turn",
            phase=CoinPhase.ON_HIT,
            apply=queue_status,
            args=("haste", 1),
        )
    )

    coin2 = Coin(name="Remise Coin 2", coin_power=4)
    coin2.add_effect(
        Effect(
            name="On Hit: Gain 1 Haste next turn",
            phase=CoinPhase.ON_HIT,
            apply=queue_status,
            args=("haste", 1),
        )
    )

    skill = Skill(
        name="Remise",
        base_power = 3,
        coin_power = 4,
        offense_level = 2,
        damage_type = ("Pierce", "Gluttony"),
    )
    skill.add_coin(coin1)
    skill.add_coin(coin2)

    skill.add_effect(
        Effect(
            name="If this unit's Speed is faster than the target's by 2 or more, Coin Power +1",
            phase=SkillPhase.ON_USE,
            apply=apply_speed_based_coin_power,
            args=(2,1,1),
        )
    )
    skill.add_effect(
        Effect(
            name="On Use: Gain +2 Poise Count",
            phase=SkillPhase.ON_USE,
            apply=add_status,
            args=("poise_count", 2),
        )
    )

    return skill


def _make_skill_2_engagement() -> Skill:
    coin1 = Coin(name="Engagement Coin 1", coin_power=4)
    coin1.add_effect(
        Effect(
            name="On Hit: Gain +1 Poise Count",
            phase=CoinPhase.ON_HIT,
            apply=add_status,
            args=("poise_count", 1),
        )
    )

    coin2 = Coin(name="Engagement Coin 2", coin_power=4)
    coin2.add_effect(
        Effect(
            name="On Hit: Gain +1 Poise Count",
            phase=CoinPhase.ON_HIT,
            apply=add_status,
            args=("poise_count", 1),
        )
    )

    coin3 = Coin(name="Engagement Coin 3", coin_power=4)

    skill = Skill(
        name="Engagement",
        base_power = 4,
        coin_power = 4,
        offense_level = 2,
        damage_type = ("Pierce", "Pride"),
    )
    skill.add_coin(coin1)
    skill.add_coin(coin2)
    skill.add_coin(coin3)

    skill.add_effect(
        Effect(
            name="If this unit's Speed is faster than the target's by 2 or more, Coin Power +1",
            phase=SkillPhase.ON_USE,
            apply=apply_speed_based_coin_power,
            args=(2,2,1),
        )
    )
    skill.add_effect(
        Effect(
            name="Clash Win: Gain +2 Poise Count",
            phase=SkillPhase.CLASH_WIN,
            apply=add_status,
            args=("poise_count", 2),
        )
    )

    return skill


def _make_skill_3_contre_attaque() -> Skill:
    coin1 = Coin(name="Contre Attaque Coin 1", coin_power=4)
    coin1.add_effect(
        Effect(
            name="On Hit: Inflict Declared Duel - Sinclair",
            phase=CoinPhase.ON_HIT,
            apply=apply_declared_duel,
        )
    )

    coin2 = Coin(name="Contre Attaque Coin 2", coin_power=4)

    coin3 = Coin(name="Contre Attaque Coin 3", coin_power=4)
    coin3.add_effect(
        Effect(
            name="+50% Damage on Critical Hit",
            phase=CoinPhase.ON_CRIT,
            apply=add_dynamic,
            args=(0.5,)
        )
    )

    skill = Skill(
        name="Contre Attaque",
        base_power = 5,
        coin_power = 4,
        offense_level = 2,
        damage_type = ("Pierce", "Lust")
    )

    skill.add_coin(coin1)
    skill.add_coin(coin2)
    skill.add_coin(coin3)

    skill.add_effect(
        Effect(
            name = "If this unit's Speed is faster than the target's, Coin Power +1 for every 2 Speed difference (Max 3)",
            phase = SkillPhase.ON_USE,
            apply = apply_speed_based_coin_power,
            args = (2, 3, 1)
        )
    )

    def consume_poise_count(env: Environment) -> None:
        """Consume 10 Poise Count and gain Poise equal to the amount consumed."""
        if env.unit is not None:
            current_poise = int(env.unit.get_status("poise_count", 0))
            if current_poise >= 10:
                consumed_poise = 10
                if env.enemy is not None and env.enemy.has_status("Declared Duel - Sinclair"):
                    consumed_poise *= 2
                env.unit.set_status("poise_count", max(current_poise - 10, 1))
                env.unit.add_status("poise_potency", consumed_poise)
            
                

    skill.add_effect(
        Effect(
            name = "Clash Win: Consume 10 Poise Count. Gain Poise equal to Poise Count consumed. Against targets with Declared Duel - Sinclair, gain Poise equal to (Poise Count consumed x 2 )instead.",
            phase = SkillPhase.CLASH_WIN,
            apply = consume_poise_count
        )
    )

    skill.add_effect(
        Effect(
            name = "Clash Win: If this unite conducted a Single Combat with the target Slot's Attack Skill, inflict 1 Fragile On Hit (Twice per Turn)",
            phase = SkillPhase.CLASH_WIN,
            apply = add_enemy_status,
            args = ("fragility", 1, 10),
            max_procs = 2
        )
    )

    return skill


def _make_defense_Defensive() -> Skill:
    return Skill(
        name="Defensive",
        damage_type=("Evade", "Gluttony"),
    )


def _make_passive_Slumbering_Bloodthirst() -> Passive:
    def queue_poise_speed_bonus(env) -> None:
        """Queue +2 Max Speed next turn per 5 Poise Count (Max 6)."""
        target = env.unit if env.unit is not None else env.enemy
        if target is None:
            return
            
        poise = int(target.get_status("poise_count", 0))
        if poise >= 5:
            bonus = min((poise // 5) * 2, 6)
            
            # Using the queue method added to Enemy/Unit classes
            current_queued = target.next_turn_statuses.get("max_speed_up", 0)
            target.queue_next_turn_status("max_speed_up", current_queued + bonus)
            
            if env.is_debugging:
                env.log.append(f"     [passive] Queued +{bonus} Max Speed for next turn (Poise: {poise})")

    def check_all_allies_faster(env) -> bool:
        """Return True if the slowest ally is faster than the fastest enemy."""
        units = env.global_state.get("units", [])
        enemies = env.global_state.get("enemies", [])
        
        # Filter out dead entities if necessary, assuming only living ones matter
        living_units = [u for u in units if u.hp > 0]
        living_enemies = [e for e in enemies if e.hp > 0]
        
        if not living_units or not living_enemies:
            return False
            
        min_ally_speed = min(u.speed for u in living_units)
        max_enemy_speed = max(e.speed for e in living_enemies)
        
        return min_ally_speed > max_enemy_speed

    passive = Passive(
        name="Slumbering Bloodthirst",
    )

    passive.add_effect(
        Effect(
            name="Turn End: Gain +2 Max Speed next turn per 5 Poise Count (Max 6)",
            phase=SkillPhase.TURN_END,
            apply=queue_poise_speed_bonus,
        )
    )

    # Combat Start, not Turn End: the buff is granted before any skill
    # resolves, so this turn's Pierce skills are boosted by it, and the
    # standard TURN_END_EFFECTS_TO_CLEAR sweep removes it afterwards.
    # Applied directly rather than queued for the same reason.
    passive.add_effect(
        Effect(
            name="Combat Start: If all allies are faster than all enemies, gain +1 Pierce DMG Up",
            phase=SkillPhase.COMBAT_START,
            apply=add_status,
            args=("pierce_dmg_up", 1, 10),
            condition=check_all_allies_faster,
        )
    )

    return passive


def make_sinclair_cinq_assoc_south_section_4_director(level) -> Character:
    """Construct and return Sinclair - Cinq Assoc. South Section 4 Director."""
    hp = _compute_hp(level)
    return Character(
        name="Sinclair",
        id_name="Cinq Assoc. South Section 4 Director",
        base_level=level,
        skill_1=[_make_skill_1_remise()],
        skill_2=[_make_skill_2_engagement()],
        skill_3=[_make_skill_3_contre_attaque()],
        defense=[_make_defense_Defensive()],
        hp=hp,
        max_hp=hp,
        speed_min=4,
        speed_max=8,
        defense_level=-4,
        stagger_thresholds=_compute_stagger_thresholds(hp),
        phys_res={
            "Slash": 2.0,
            "Pierce": 0.5,
            "Blunt": 1.0,
        },
        sin_res={},
        passives=[_make_passive_Slumbering_Bloodthirst()],
    )
    