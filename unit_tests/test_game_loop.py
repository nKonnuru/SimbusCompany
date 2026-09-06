"""Tests for the game loop, skill, coin, effect, passive, enemy, unit, and environment systems."""

from __future__ import annotations

import math
import os
import tempfile
import unittest
from unittest.mock import patch

from src.action import Action
from src.coin import Coin, ReuseCondition
from src.characters import (
    make_ryoshu_w_corp_l3_cleanup_agent,
    make_sinclair_cinq_assoc_south_section_4_director,
)
from src.context import CombatContext
from src.effect import CoinPhase, Effect, SkillPhase
from src.enemy import Enemy
from src.environment import Environment
from src.game_loop import GameLoop
from src.passive import Passive
from src.pre_combat import PRE_COMBAT_CHECKS, TurnPlan
from src.resonance import (
    compute_resonance,
    is_offensive,
    resonance_check,
    sin_of,
)
from src.skill import Skill
from src.team import Team
from src.skill_samples.blinkstep import make_blinkstep
from src.skill_samples.tanglecleaver import _coin3_burst_then_reduce_once, make_tanglecleaver
from src.skill_samples.traceless import make_traceless
from src.skill_samples.unleashed_violence import (
    _combat_start_spend_tremor_count,
    _coin3_reduce_tremor_count_by_3,
    make_unleashed_violence,
)
from src.status_effects import (
    DECLARED_DUEL_STATUS,
    process_on_hit_statuses,
    reset_turn_status_effects,
)
from src import terminal_frontend as terminal_frontend
from src.unit import Unit
from src.utils import (
    TURN_END_EFFECTS_TO_CLEAR,
    add_coin_power,
    add_dynamic,
    add_enemy_status,
    add_status,
    apply_speed_based_coin_power,
    check_absolute_resonance,
    check_absolute_resonance_longest,
    check_absolute_resonance_sum,
    check_count,
    check_enemy_hp_below,
    check_resonance,
    check_speed_advantage,
    get_resonance,
    consume_charge_count,
    deal_bonus_damage_from_current,
    set_status,
)


# â”€â”€ helpers â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€


def _make_damage_effect(
    name: str,
    phase: SkillPhase | CoinPhase,
    amount: int,
    priority: int = 0,
) -> Effect:
    """Create an additive damage effect for testing."""
    return Effect(
        name=name,
        phase=phase,
        apply=lambda ctx, _amt=amount, _n=name: ctx.add_damage(_amt, source=_n),
        priority=priority,
    )


def _make_coin(
    name: str = "Coin",
    coin_power: int = 2,
    effects: list[Effect] | None = None,
) -> Coin:
    coin = Coin(name=name, coin_power=coin_power)
    for e in effects or []:
        coin.add_effect(e)
    return coin


def _make_skill(
    name: str = "Slash",
    speed: int = 5,
    base_power: int = 0,
    coins: list[Coin] | None = None,
    effects: list[Effect] | None = None,
) -> Skill:
    skill = Skill(name=name, speed=speed, base_power=base_power)
    for c in coins or []:
        skill.add_coin(c)
    for e in effects or []:
        skill.add_effect(e)
    return skill


# â”€â”€ Effect tests â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€


class TestEffect(unittest.TestCase):
    def test_execute_applies(self):
        env = Environment()
        eff = _make_damage_effect("buff", SkillPhase.BEFORE_ATTACK, 10)
        eff.execute(env)
        self.assertEqual(env.current_power, 10)

    def test_execute_skips_when_condition_false(self):
        env = Environment()
        eff = Effect(
            name="guarded",
            phase=SkillPhase.BEFORE_ATTACK,
            apply=lambda e: e.add_damage(99),
            condition=lambda e: False,
        )
        eff.execute(env)
        self.assertEqual(env.current_power, 0)

    def test_execute_runs_when_condition_true(self):
        env = Environment()
        eff = Effect(
            name="guarded",
            phase=SkillPhase.BEFORE_ATTACK,
            apply=lambda e: e.add_damage(5),
            condition=lambda e: True,
        )
        eff.execute(env)
        self.assertEqual(env.current_power, 5)

    def test_execute_runs_condition_with_args(self):
        env = Environment()
        env.current_power = 3

        def _has_power_at_least(effect_env, minimum_power: int) -> bool:
            return effect_env.current_power >= minimum_power

        eff = Effect(
            name="parameterized_guard",
            phase=SkillPhase.BEFORE_ATTACK,
            apply=lambda e: e.add_damage(7),
            condition=_has_power_at_least,
            condition_args=(3,),
        )
        eff.execute(env)
        self.assertEqual(env.current_power, 10)

    def test_execute_skips_condition_with_args(self):
        env = Environment()
        env.current_power = 2

        def _has_power_at_least(effect_env, minimum_power: int) -> bool:
            return effect_env.current_power >= minimum_power

        eff = Effect(
            name="parameterized_guard",
            phase=SkillPhase.BEFORE_ATTACK,
            apply=lambda e: e.add_damage(7),
            condition=_has_power_at_least,
            condition_args=(3,),
        )
        eff.execute(env)
        self.assertEqual(env.current_power, 2)


# â”€â”€ Coin tests â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€


class TestCoin(unittest.TestCase):
    def test_add_and_get_effects_sorted(self):
        coin = Coin(name="C1")
        e_low = _make_damage_effect("low", CoinPhase.ON_HIT, 1, priority=10)
        e_high = _make_damage_effect("high", CoinPhase.ON_HIT, 2, priority=1)
        coin.add_effect(e_low)
        coin.add_effect(e_high)

        effects = coin.get_effects(CoinPhase.ON_HIT)
        self.assertEqual([e.name for e in effects], ["high", "low"])

    def test_add_effect_rejects_skill_phase(self):
        coin = Coin()
        eff = _make_damage_effect("bad", SkillPhase.TURN_START, 1)
        with self.assertRaises(TypeError):
            coin.add_effect(eff)

    def test_execute_phase(self):
        env = Environment()
        coin = _make_coin(
            effects=[_make_damage_effect("hit_buff", CoinPhase.ON_HIT, 7)]
        )
        coin.execute_phase(CoinPhase.ON_HIT, env)
        self.assertEqual(env.current_power, 7)


# â”€â”€ Skill tests â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€


class TestSkill(unittest.TestCase):
    def test_add_effect_rejects_coin_phase(self):
        skill = Skill()
        eff = _make_damage_effect("bad", CoinPhase.ON_HIT, 1)
        with self.assertRaises(TypeError):
            skill.add_effect(eff)

    def test_collect_effects_merges_and_sorts(self):
        s1 = _make_skill(effects=[
            _make_damage_effect("a", SkillPhase.BEFORE_ATTACK, 1, priority=5),
        ])
        s2 = _make_skill(effects=[
            _make_damage_effect("b", SkillPhase.BEFORE_ATTACK, 2, priority=1),
        ])
        merged = Skill.collect_effects([s1, s2], SkillPhase.BEFORE_ATTACK)
        self.assertEqual([e.name for e in merged], ["b", "a"])

    def test_execute_phase(self):
        env = Environment()
        skill = _make_skill(effects=[
            _make_damage_effect("buff", SkillPhase.ON_USE, 4),
        ])
        skill.execute_phase(SkillPhase.ON_USE, env)
        self.assertEqual(env.current_power, 4)


# â”€â”€ Environment tests â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€


class TestEnvironment(unittest.TestCase):
    def _make_env(self, **kwargs) -> Environment:
        """Build an Environment from a skill/unit/enemy with sensible defaults."""
        skill = kwargs.pop("skill", None) or Skill(
            name="TestSkill", base_power=5, coin_power=3,
            offense_level=10, damage_type=("Slash", "Wrath"),
            coins=[_make_coin()],
        )
        unit = kwargs.pop("unit", None) or Unit(
            name="TestUnit", base_level=40, sp=45,
            statuses={"poise_potency": 5, "poise_count": 3},
        )
        enemy = kwargs.pop("enemy", None) or Enemy(
            name="TestEnemy", base_level=30, defense_level=0,
            hp=500, max_hp=500,
            phys_res={"Slash": 1.0}, sin_res={"Wrath": 1.0},
        )
        return Environment.from_skill(skill, unit, enemy, **kwargs)

    def test_initial_ol_computation(self):
        env = self._make_env()
        # OL = skill.offense_level(10) + unit.base_level(40) = 50
        self.assertEqual(env.ol, 50)

    def test_def_level_property(self):
        env = self._make_env()
        # enemy effective defense = base_level(30) + defense_level(0) = 30
        self.assertEqual(env.def_level, 30)

    def test_def_level_includes_def_lvl_down_status(self):
        enemy = Enemy(
            name="Debuffed", base_level=30, defense_level=0,
            statuses={"def_lvl_down": 3},
        )
        env = self._make_env(enemy=enemy)
        self.assertEqual(env.def_level, 27)

    def test_def_level_includes_def_lvl_up_status(self):
        enemy = Enemy(
            name="Buffed", base_level=30, defense_level=0,
            statuses={"def_lvl_up": 4},
        )
        env = self._make_env(enemy=enemy)
        self.assertEqual(env.def_level, 34)

    def test_def_level_nets_def_lvl_up_and_down(self):
        enemy = Enemy(
            name="Both", base_level=30, defense_level=0,
            statuses={"def_lvl_up": 4, "def_lvl_down": 1},
        )
        env = self._make_env(enemy=enemy)
        self.assertEqual(env.def_level, 33)

    def test_def_level_clamps_negative_status_values(self):
        """A negative stack contributes 0, mirroring effective_speed."""
        enemy = Enemy(
            name="Weird", base_level=30, defense_level=0,
            statuses={"def_lvl_up": -5, "def_lvl_down": -5},
        )
        env = self._make_env(enemy=enemy)
        self.assertEqual(env.def_level, 30)

    def test_def_level_is_zero_without_enemy(self):
        self.assertEqual(Environment().def_level, 0)

    def test_def_level_subtracts_both_skill_and_tremor_decay(self):
        """def_lvl_down and Tremor Decay are separate sources that sum."""
        enemy = Enemy(
            name="Decaying", base_level=30, defense_level=0,
            statuses={
                "def_lvl_down": 2,
                "tremor_type": "decay",
                "tremor_potency": 12,
                "tremor_count": 3,
            },
        )
        env = self._make_env(enemy=enemy)
        # 30 - 2 (skill) - 3 (decay: 12 // 4)
        self.assertEqual(env.def_level, 25)

    def test_def_level_mod(self):
        env = self._make_env()
        env.def_level_mod = -10
        self.assertEqual(env.def_level, 20)

    def test_effective_ol_adds_off_lvl_up(self):
        unit = Unit(name="Buffed", base_level=40, statuses={"off_lvl_up": 6})
        env = self._make_env(unit=unit)
        self.assertEqual(env.ol, 50)            # raw field is untouched
        self.assertEqual(env.effective_ol, 56)

    def test_effective_ol_subtracts_off_lvl_down(self):
        unit = Unit(name="Debuffed", base_level=40, statuses={"off_lvl_down": 6})
        env = self._make_env(unit=unit)
        self.assertEqual(env.effective_ol, 44)

    def test_effective_ol_nets_up_and_down(self):
        unit = Unit(
            name="Both", base_level=40,
            statuses={"off_lvl_up": 6, "off_lvl_down": 2},
        )
        env = self._make_env(unit=unit)
        self.assertEqual(env.effective_ol, 54)

    def test_effective_ol_clamps_negative_status_values(self):
        unit = Unit(
            name="Weird", base_level=40,
            statuses={"off_lvl_up": -5, "off_lvl_down": -5},
        )
        env = self._make_env(unit=unit)
        self.assertEqual(env.effective_ol, 50)

    def test_effective_ol_is_raw_ol_without_unit(self):
        """The lightweight-env and _fire_passives paths leave unit None."""
        env = Environment(ol=7)
        self.assertEqual(env.effective_ol, 7)

    def test_effective_ol_stacks_on_top_of_resonance_bonus(self):
        """Resonance writes env.ol; the status layers on at read time."""
        unit = Unit(name="Resonant", base_level=40, statuses={"off_lvl_down": 3})
        env = self._make_env(unit=unit)
        env.ol += 4  # what _resolve_action does with offense_level_bonus
        self.assertEqual(env.effective_ol, 51)

    def test_static_uses_effective_ol_not_raw_ol(self):
        """Regression guard: the statuses must reach the damage formula."""
        plain = self._make_env()
        plain.recompute_static()

        unit = Unit(name="Buffed", base_level=40, statuses={"off_lvl_up": 10})
        buffed = self._make_env(unit=unit)
        buffed.recompute_static()

        # ol_diff 20 -> 20/45 vs ol_diff 30 -> 30/55
        self.assertAlmostEqual(plain.static, 1.0 + 20 / 45)
        self.assertAlmostEqual(buffed.static, 1.0 + 30 / 55)
        self.assertGreater(buffed.static, plain.static)

    def test_debug_coin_reports_effective_ol_diff(self):
        unit = Unit(name="Buffed", base_level=40, statuses={"off_lvl_up": 10})
        env = self._make_env(unit=unit)
        self.assertIn("ol_diff=30", env.debug_coin(0))

    def test_static_with_neutral_resists(self):
        """With neutral resists (1.0), p_res_mod and s_res_mod should be 0."""
        env = self._make_env()
        self.assertAlmostEqual(env.p_res_mod, 0.0)
        self.assertAlmostEqual(env.s_res_mod, 0.0)
        # static = 1.0 + ol_mult + 0 + 0 + 0 + 0
        ol_diff = 50 - 30  # 20
        expected_ol_mult = 20 / (20 + 25)
        self.assertAlmostEqual(env.static, 1.0 + expected_ol_mult, places=5)

    def test_resist_weakness_halved(self):
        """Physical weakness should be halved."""
        enemy = Enemy(
            name="Weak", base_level=30,
            phys_res={"Slash": 0.5}, sin_res={"Wrath": 1.0},
        )
        env = self._make_env(enemy=enemy)
        # (0.5 - 1) / 2 = -0.25
        self.assertAlmostEqual(env.p_res_mod, -0.25)

    def test_resist_above_neutral(self):
        """Resistance > 1.0 is NOT halved."""
        enemy = Enemy(
            name="Tough", base_level=30,
            phys_res={"Slash": 1.5}, sin_res={"Wrath": 1.0},
        )
        env = self._make_env(enemy=enemy)
        self.assertAlmostEqual(env.p_res_mod, 0.5)

    def test_compute_coin_damage_min_one(self):
        """Damage can never be less than 1."""
        env = self._make_env()
        env.current_power = 0
        env.static = 0.01
        env.dynamic = 0.01
        damage = env.compute_coin_damage()
        self.assertEqual(damage, 1)

    def test_compute_coin_damage_formula(self):
        """Verify the core formula: floor(power Ã— static Ã— dynamic)."""
        env = self._make_env()
        env.current_power = 10
        env.static = 1.5
        env.dynamic = 1.0
        env.did_crit = False
        # Need to prevent recompute from overriding our manual static
        env._recompute_static = lambda: None  # type: ignore[method-assign]
        damage = env.compute_coin_damage()
        self.assertEqual(damage, math.floor(10 * 1.5 * 1.0))
        self.assertEqual(env.total, damage)

    def test_compute_coin_damage_with_crit(self):
        """Crit should add crit_bonus to static."""
        env = self._make_env()
        env.current_power = 10
        base_static = 1.5
        env.static = base_static
        env.dynamic = 1.0
        env.did_crit = True
        env.crit_bonus = 0.20
        env._recompute_static = lambda: None  # type: ignore[method-assign]
        damage = env.compute_coin_damage()
        self.assertEqual(damage, math.floor(10 * (1.5 + 0.20) * 1.0))

    def test_compute_coin_damage_includes_slash_fragility_dynamic_bonus(self):
        enemy = Enemy(
            name="Fragile",
            base_level=30,
            defense_level=0,
            hp=500,
            max_hp=500,
            statuses={"slash_fragility": 3},
        )
        env = self._make_env(enemy=enemy)
        env.current_power = 10
        env.static = 1.5
        env.dynamic = 1.0
        env.did_crit = False
        env._recompute_static = lambda: None  # type: ignore[method-assign]

        damage = env.compute_coin_damage()

        # +0.3 dynamic from 3 Slash Fragility stacks
        self.assertEqual(damage, math.floor(10 * 1.5 * 1.3))

    def test_compute_coin_damage_slash_fragility_caps_at_ten(self):
        enemy = Enemy(
            name="Fragile",
            base_level=30,
            defense_level=0,
            hp=500,
            max_hp=500,
            statuses={"slash_fragility": 99},
        )
        env = self._make_env(enemy=enemy)
        env.current_power = 10
        env.static = 1.0
        env.dynamic = 1.0
        env.did_crit = False
        env._recompute_static = lambda: None  # type: ignore[method-assign]

        damage = env.compute_coin_damage()

        # capped at +1.0 dynamic (10 stacks)
        self.assertEqual(damage, math.floor(10 * 1.0 * 2.0))

    def test_compute_coin_damage_staggered_enemy_overrides_physical_res_to_two(self):
        enemy = Enemy(
            name="Staggered",
            base_level=30,
            defense_level=0,
            hp=500,
            max_hp=500,
            phys_res={"Slash": 0.5},
            sin_res={"Wrath": 1.0},
            is_staggered=True,
            stagger_turns_remaining=1,
        )
        env = self._make_env(enemy=enemy)
        env.current_power = 10
        env.dynamic = 1.0
        env.did_crit = False

        damage = env.compute_coin_damage()

        # Stagger forces Slash physical resist to 2.0 regardless of base 0.5.
        # static = 1 + ol_mult + p_res_mod(1.0) + s_res_mod(0.0)
        ol_diff = 50 - 30
        expected_ol_mult = ol_diff / (ol_diff + 25)
        expected_static = 1.0 + expected_ol_mult + 1.0
        self.assertEqual(damage, math.floor(10 * expected_static * 1.0))

    def test_compute_coin_damage_staggered_enemy_level_three_uses_three_point_zero_res(self):
        enemy = Enemy(
            name="Staggered",
            base_level=30,
            defense_level=0,
            hp=500,
            max_hp=500,
            phys_res={"Slash": 0.5},
            sin_res={"Wrath": 1.0},
            is_staggered=True,
            stagger_turns_remaining=1,
            stagger_level=3,
        )
        env = self._make_env(enemy=enemy)
        env.current_power = 10
        env.dynamic = 1.0
        env.did_crit = False

        damage = env.compute_coin_damage()

        # Level 3 stagger forces physical resist to 3.0 (p_res_mod=2.0).
        ol_diff = 50 - 30
        expected_ol_mult = ol_diff / (ol_diff + 25)
        expected_static = 1.0 + expected_ol_mult + 2.0
        self.assertEqual(damage, math.floor(10 * expected_static * 1.0))

    def test_compute_coin_damage_non_slash_ignores_slash_fragility(self):
        enemy = Enemy(
            name="Fragile",
            base_level=30,
            defense_level=0,
            hp=500,
            max_hp=500,
            statuses={"slash_fragility": 10},
        )
        pierce_skill = Skill(
            name="PierceSkill",
            base_power=5,
            coin_power=3,
            offense_level=10,
            damage_type=("Pierce", "Wrath"),
            coins=[_make_coin()],
        )
        env = self._make_env(skill=pierce_skill, enemy=enemy)
        env.current_power = 10
        env.static = 1.5
        env.dynamic = 1.0
        env.did_crit = False
        env._recompute_static = lambda: None  # type: ignore[method-assign]

        damage = env.compute_coin_damage()

        self.assertEqual(damage, math.floor(10 * 1.5 * 1.0))

    def test_compute_coin_damage_includes_generic_fragility_dynamic_bonus(self):
        """Generic 'fragility' applies regardless of the attacker's damage type."""
        pierce_skill = Skill(
            name="PierceSkill", base_power=5, coin_power=3,
            offense_level=10, damage_type=("Pierce", "Gluttony"),
            coins=[_make_coin()],
        )
        enemy = Enemy(
            name="Fragile", base_level=30, defense_level=0,
            hp=500, max_hp=500, statuses={"fragility": 4},
        )
        env = self._make_env(skill=pierce_skill, enemy=enemy)
        env.current_power = 10
        env.static = 1.5
        env.dynamic = 1.0
        env.did_crit = False
        env._recompute_static = lambda: None  # type: ignore[method-assign]

        damage = env.compute_coin_damage()

        # +0.4 dynamic from 4 generic Fragility stacks
        self.assertEqual(damage, math.floor(10 * 1.5 * 1.4))

    def test_compute_coin_damage_sin_type_fragility_only_matches_that_sin(self):
        """'wrath_fragility' only applies when the skill's sin type is Wrath."""
        enemy_matches = Enemy(
            name="Fragile", base_level=30, defense_level=0,
            hp=500, max_hp=500, statuses={"wrath_fragility": 2},
        )
        env = self._make_env(enemy=enemy_matches)  # default skill sin type = Wrath
        env.current_power = 10
        env.static = 1.5
        env.dynamic = 1.0
        env.did_crit = False
        env._recompute_static = lambda: None  # type: ignore[method-assign]
        self.assertEqual(env.compute_coin_damage(), math.floor(10 * 1.5 * 1.2))

        enemy_mismatch = Enemy(
            name="Fragile", base_level=30, defense_level=0,
            hp=500, max_hp=500, statuses={"lust_fragility": 2},
        )
        env2 = self._make_env(enemy=enemy_mismatch)  # default skill sin type = Wrath
        env2.current_power = 10
        env2.static = 1.5
        env2.dynamic = 1.0
        env2.did_crit = False
        env2._recompute_static = lambda: None  # type: ignore[method-assign]
        self.assertEqual(env2.compute_coin_damage(), math.floor(10 * 1.5 * 1.0))

    def test_compute_coin_damage_includes_generic_damage_up_dynamic_bonus(self):
        """Generic 'dmg_up' on the attacker applies regardless of skill type."""
        unit = Unit(
            name="Buffed", base_level=40, sp=45,
            statuses={"dmg_up": 5},
        )
        env = self._make_env(unit=unit)
        env.current_power = 10
        env.static = 1.5
        env.dynamic = 1.0
        env.did_crit = False
        env._recompute_static = lambda: None  # type: ignore[method-assign]

        damage = env.compute_coin_damage()

        # +0.5 dynamic from 5 generic Damage Up stacks
        self.assertEqual(damage, math.floor(10 * 1.5 * 1.5))

    def test_compute_coin_damage_pierce_damage_up_only_matches_pierce_skill(self):
        """'pierce_dmg_up' only applies when the used skill's physical type is Pierce."""
        unit = Unit(name="Buffed", base_level=40, sp=45, statuses={"pierce_dmg_up": 3})

        pierce_skill = Skill(
            name="PierceSkill", base_power=5, coin_power=3,
            offense_level=10, damage_type=("Pierce", "Gluttony"),
            coins=[_make_coin()],
        )
        env = self._make_env(skill=pierce_skill, unit=unit)
        env.current_power = 10
        env.static = 1.5
        env.dynamic = 1.0
        env.did_crit = False
        env._recompute_static = lambda: None  # type: ignore[method-assign]
        self.assertEqual(env.compute_coin_damage(), math.floor(10 * 1.5 * 1.3))

        env2 = self._make_env(unit=unit)  # default skill physical type = Slash
        env2.current_power = 10
        env2.static = 1.5
        env2.dynamic = 1.0
        env2.did_crit = False
        env2._recompute_static = lambda: None  # type: ignore[method-assign]
        self.assertEqual(env2.compute_coin_damage(), math.floor(10 * 1.5 * 1.0))

    def test_clash_count_adds_to_static(self):
        """Each clash win adds 3% to static."""
        env = self._make_env(clash_count=3)
        env.recompute_static()
        ol_diff = 50 - 30
        expected = 1.0 + ol_diff / (ol_diff + 25) + 0 + 0 + 0 + (3 * 0.03)
        self.assertAlmostEqual(env.static, expected, places=5)

    def test_observation_level(self):
        """Observation level adds 3% per level to static."""
        enemy = Enemy(
            name="Observed", base_level=30,
            phys_res={"Slash": 1.0}, sin_res={"Wrath": 1.0},
            observation_level=2,
        )
        env = self._make_env(enemy=enemy)
        ol_diff = 50 - 30
        expected = 1.0 + ol_diff / (ol_diff + 25) + 0 + 0 + (2 * 0.03) + 0
        self.assertAlmostEqual(env.static, expected, places=5)

    def test_get_set_add_accessors(self):
        env = self._make_env()
        env.set("dynamic", 2.0)
        self.assertEqual(env.get("dynamic"), 2.0)
        env.add("dynamic", 0.5)
        self.assertEqual(env.get("dynamic"), 2.5)

    def test_get_set_dotted_path(self):
        env = self._make_env()
        original_hp = env.enemy.hp
        env.add("enemy.hp", -10)
        self.assertEqual(env.enemy.hp, original_hp - 10)

    def test_advance_coin(self):
        env = self._make_env()
        c = Coin(name="C1")
        env.advance_coin(c, 0)
        self.assertIs(env.current_coin, c)
        self.assertEqual(env.current_coin_index, 0)
        self.assertEqual(env.current_damage, 0)
        self.assertFalse(env.did_crit)

    def test_active_effect_lifecycle(self):
        """Effects with duration should be removed after update() ticks."""

        class FakeEffect:
            removed = False
            def on_remove(self, effect, env):
                FakeEffect.removed = True

        eff = FakeEffect()
        env = self._make_env()
        env.add_active_effect(eff, data=None, duration=2)
        self.assertIn(eff, env.effects)

        env.update()  # tick 1: duration 2 â†’ 1
        self.assertIn(eff, env.effects)

        env.update()  # tick 2: duration 1 â†’ 0 â†’ removed
        self.assertNotIn(eff, env.effects)
        self.assertTrue(FakeEffect.removed)

    def test_permanent_effect_not_removed(self):
        """Effects with duration=-1 should persist through updates."""
        env = self._make_env()
        env.add_active_effect("permanent", data=None, duration=-1)
        for _ in range(10):
            env.update()
        self.assertIn("permanent", env.effects)

    def test_apply_queue(self):
        """Queued effects should be drained by update_apply_queue."""
        tracker = []

        class QueuedEff:
            def execute(self, env):
                tracker.append("executed")

        env = self._make_env()
        env.queue_effect(QueuedEff())
        env.queue_effect(QueuedEff())
        env.update_apply_queue()
        self.assertEqual(len(tracker), 2)
        self.assertEqual(len(env.apply_queue), 0)

    def test_backward_compat_final_damage(self):
        """env.final_damage should alias env.total."""
        env = self._make_env()
        env.total = 42
        self.assertEqual(env.final_damage, 42)
        env.final_damage = 100
        self.assertEqual(env.total, 100)

    def test_tremor_burst_raises_stagger_threshold_by_potency(self):
        enemy = Enemy(
            name="BurstTarget",
            hp=100,
            max_hp=100,
            stagger_thresholds=[70],
            statuses={"tremor_potency": 12},
        )
        env = self._make_env(enemy=enemy)

        env.on_tremor_burst()

        self.assertEqual(enemy.stagger_thresholds, [82])
        self.assertEqual(enemy.get_status("tremor_last_burst_raised", 0), 12)
        self.assertFalse(enemy.is_staggered)

    def test_tremor_burst_force_staggers_when_threshold_raised_above_hp(self):
        enemy = Enemy(
            name="BurstTarget",
            hp=75,
            max_hp=100,
            stagger_thresholds=[70],
            statuses={"tremor_potency": 10},
        )
        env = self._make_env(enemy=enemy)

        env.on_tremor_burst()

        self.assertEqual(enemy.stagger_thresholds, [80])
        self.assertEqual(enemy.get_status("tremor_last_burst_raised", 0), 10)
        self.assertTrue(enemy.is_staggered)

    def test_tremor_scorch_burst_deals_wrath_and_reduces_burn_count(self):
        enemy = Enemy(
            name="ScorchTarget",
            hp=100,
            max_hp=100,
            stagger_thresholds=[40],
            statuses={
                "tremor_potency": 20,
                "tremor_count": 3,
                "tremor_type": "scorch",
                "burn_potency": 10,
                "burn_count": 2,
            },
            sin_res={"Wrath": 1.0},
        )
        env = self._make_env(enemy=enemy)

        env.on_tremor_burst()

        # Scorch damage raw = floor((20 + 10) / 2) = 15
        self.assertEqual(env.status_damages.get("tremor_scorch", 0), 15)
        self.assertEqual(enemy.hp, 85)
        self.assertEqual(enemy.get_status("burn_count", 0), 1)

    def test_tremor_scorch_burst_removes_burn_count_when_depleted(self):
        enemy = Enemy(
            name="ScorchTarget",
            hp=100,
            max_hp=100,
            stagger_thresholds=[40],
            statuses={
                "tremor_potency": 10,
                "tremor_count": 3,
                "tremor_type": "scorch",
                "burn_potency": 8,
                "burn_count": 1,
            },
            sin_res={"Wrath": 1.0},
        )
        env = self._make_env(enemy=enemy)

        env.on_tremor_burst()

        self.assertFalse(enemy.has_status("burn_count"))

    def test_non_scorch_tremor_burst_does_not_apply_scorch_packet(self):
        enemy = Enemy(
            name="RegularTremorTarget",
            hp=100,
            max_hp=100,
            stagger_thresholds=[40],
            statuses={
                "tremor_potency": 20,
                "tremor_count": 3,
                "tremor_type": "reverb",
                "burn_potency": 10,
                "burn_count": 2,
            },
            sin_res={"Wrath": 1.0},
        )
        env = self._make_env(enemy=enemy)

        env.on_tremor_burst()

        self.assertEqual(env.status_damages.get("tremor_scorch", 0), 0)
        # Reverb can still deal its own burst damage; this test only verifies
        # Scorch-specific packet is not applied.
        self.assertEqual(env.status_damages.get("tremor_reverb", 0), 20)
        self.assertEqual(enemy.get_status("burn_count", 0), 2)

    def test_tremor_reverb_burst_deals_sloth_damage_equal_to_tremor_potency(self):
        enemy = Enemy(
            name="ReverbTarget",
            hp=100,
            max_hp=100,
            stagger_thresholds=[30],
            statuses={
                "tremor_potency": 14,
                "tremor_count": 3,
                "tremor_type": "reverb",
            },
            sin_res={"Sloth": 1.0},
        )
        env = self._make_env(enemy=enemy)

        env.on_tremor_burst()

        self.assertEqual(env.status_damages.get("tremor_reverb", 0), 14)
        self.assertEqual(enemy.hp, 86)

    def test_tremor_everlasting_can_add_two_extra_bursts(self):
        enemy = Enemy(
            name="EverlastingTarget",
            hp=200,
            max_hp=200,
            stagger_thresholds=[20],
            statuses={
                "tremor_potency": 20,
                "tremor_count": 50,
                "tremor_type": "everlasting",
            },
        )
        env = self._make_env(enemy=enemy)

        with patch("src.environment.random.random", side_effect=[0.0, 0.0]):
            env.on_tremor_burst()

        # base burst + 2 extra bursts = 3 * 20 threshold raise
        self.assertEqual(enemy.stagger_thresholds, [80])

    def test_tremor_everlasting_no_extra_bursts_when_rolls_fail(self):
        enemy = Enemy(
            name="EverlastingTarget",
            hp=200,
            max_hp=200,
            stagger_thresholds=[20],
            statuses={
                "tremor_potency": 20,
                "tremor_count": 50,
                "tremor_type": "everlasting",
            },
        )
        env = self._make_env(enemy=enemy)

        with patch("src.environment.random.random", side_effect=[0.99, 0.99]):
            env.on_tremor_burst()

        self.assertEqual(enemy.stagger_thresholds, [40])

    def test_tremor_everlasting_chance_caps_at_50_percent(self):
        enemy = Enemy(
            name="EverlastingTarget",
            hp=500,
            max_hp=500,
            stagger_thresholds=[100],
            statuses={
                "tremor_potency": 80,
                "tremor_count": 99,
                "tremor_type": "everlasting",
            },
        )
        env = self._make_env(enemy=enemy)

        # If not capped, 0.6 would pass with 80% / 99% chance.
        # With cap at 50%, both should fail.
        with patch("src.environment.random.random", side_effect=[0.6, 0.6]):
            env.on_tremor_burst()

        self.assertEqual(enemy.stagger_thresholds, [180])

    def test_tremor_hemmorage_burst_deals_lust_and_reduces_bleed_count(self):
        enemy = Enemy(
            name="HemmorageTarget",
            hp=100,
            max_hp=100,
            stagger_thresholds=[30],
            statuses={
                "tremor_potency": 20,
                "tremor_count": 3,
                "tremor_type": "hemmorage",
                "bleed_potency": 10,
                "bleed_count": 2,
            },
            sin_res={"Lust": 1.0},
        )
        env = self._make_env(enemy=enemy)

        env.on_tremor_burst()

        # raw = floor((20 + 10) / 2) = 15
        self.assertEqual(env.status_damages.get("tremor_hemmorage", 0), 15)
        self.assertEqual(enemy.hp, 85)
        self.assertEqual(enemy.get_status("bleed_count", 0), 1)

    def test_tremor_hemmorage_burst_removes_bleed_count_when_depleted(self):
        enemy = Enemy(
            name="HemmorageTarget",
            hp=100,
            max_hp=100,
            stagger_thresholds=[30],
            statuses={
                "tremor_potency": 10,
                "tremor_count": 3,
                "tremor_type": "hemmorage",
                "bleed_potency": 4,
                "bleed_count": 1,
            },
            sin_res={"Lust": 1.0},
        )
        env = self._make_env(enemy=enemy)

        env.on_tremor_burst()

        self.assertFalse(enemy.has_status("bleed_count"))


# â”€â”€ GameLoop tests â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€


class TestGameLoop(unittest.TestCase):
    def test_skill_ordering(self):
        slow = _make_skill(name="Slow", speed=1)
        fast = _make_skill(name="Fast", speed=10)
        loop = GameLoop(skills=[slow, fast])
        results = loop.run_turn()
        # Fast resolves first â†’ appears at index 0
        self.assertEqual(results[0]["skill"], "Fast")
        self.assertEqual(results[1]["skill"], "Slow")

    def test_run_turn_returns_results_per_skill(self):
        s1 = _make_skill(name="Alpha", speed=3, coins=[_make_coin()])
        s2 = _make_skill(name="Beta", speed=7, coins=[_make_coin()])
        loop = GameLoop(skills=[s1, s2])
        results = loop.run_turn()
        self.assertEqual(len(results), 2)
        # Beta is faster â†’ resolved first
        self.assertEqual(results[0]["skill"], "Beta")
        self.assertEqual(results[1]["skill"], "Alpha")

    def test_broadcast_phase_triggers_all_skills(self):
        """Turn-start effects from every skill should fire."""
        tracker: list[str] = []

        def make_tracker(label: str):
            return Effect(
                name=label,
                phase=SkillPhase.TURN_START,
                apply=lambda ctx, _l=label: tracker.append(_l),
            )

        s1 = _make_skill(name="A", effects=[make_tracker("A_start")])
        s2 = _make_skill(name="B", effects=[make_tracker("B_start")])
        loop = GameLoop(skills=[s1, s2])
        loop.run_turn()
        self.assertIn("A_start", tracker)
        self.assertIn("B_start", tracker)

    def test_coin_damage_accumulated(self):
        """Coins should add their power to final_damage."""
        coin = _make_coin(coin_power=3)
        skill = _make_skill(name="Hit", speed=1, base_power=4, coins=[coin])
        loop = GameLoop(skills=[skill])
        results = loop.run_turn()
        # Heads â†’ 4+3=7, Tails â†’ 4.  Either way > 0.
        self.assertGreater(results[0]["final_damage"], 0)

    def test_coin_on_hit_effect_fires(self):
        """A CoinPhase.ON_HIT effect should execute during coin resolution."""
        hit_log: list[str] = []
        eff = Effect(
            name="on_hit_tracker",
            phase=CoinPhase.ON_HIT,
            apply=lambda ctx: hit_log.append("hit"),
        )
        coin = _make_coin()
        coin.add_effect(eff)
        skill = _make_skill(coins=[coin])
        loop = GameLoop(skills=[skill])
        loop.run_turn()
        self.assertEqual(hit_log, ["hit"])

    def test_skill_stops_remaining_coins_after_kill(self):
        """When target dies mid-skill, remaining coins should not resolve."""
        coins = [
            _make_coin(name="C1", coin_power=0),
            _make_coin(name="C2", coin_power=0),
            _make_coin(name="C3", coin_power=0),
        ]
        kill_skill = Skill(
            name="KillEarly",
            base_power=10,
            coin_power=0,
            offense_level=5,
            damage_type=("Slash", "Wrath"),
            coins=coins,
        )
        unit = Unit(name="U", skills=[kill_skill])
        enemy = Enemy(name="E", hp=1, max_hp=1)

        loop = GameLoop(units=[unit], enemies=[enemy], sequence=["heads", "heads", "heads"])
        result = loop.run_turn()[0]

        self.assertEqual(len(result["coin_damages"]), 1)


# â”€â”€ Team / per-action config tests â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€


class TestTeamTurn(unittest.TestCase):
    """Team ordering, per-action configuration, and pre-combat checks."""

    def _make_unit(self, name: str, speed: int) -> Unit:
        skill = Skill(
            name=f"{name}Skill",
            base_power=5,
            coin_power=0,
            offense_level=0,
            damage_type=("Slash", "Wrath"),
            coins=[_make_coin(name=f"{name}Coin", coin_power=0)],
        )
        unit = Unit(name=name, base_level=10, hp=100, max_hp=100, speed=speed)
        unit.add_skill(skill, slot="1")
        return unit

    def _make_enemy(self, name: str = "E", hp: int = 500) -> Enemy:
        return Enemy(
            name=name, base_level=10, defense_level=0, hp=hp, max_hp=hp,
            phys_res={"Slash": 1.0}, sin_res={"Wrath": 1.0},
        )

    # â”€â”€ Team â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€

    def test_position_of_uses_identity_not_equality(self):
        """Two units built alike are distinct members, not one."""
        a, b = self._make_unit("Same", 5), self._make_unit("Same", 5)
        team = Team(members=[a, b])
        self.assertEqual(team.position_of(a), 0)
        self.assertEqual(team.position_of(b), 1)

    def test_position_of_returns_length_for_stranger(self):
        team = Team(members=[self._make_unit("A", 5)])
        self.assertEqual(team.position_of(self._make_unit("B", 5)), 1)

    def test_team_populates_units(self):
        a, b = self._make_unit("A", 5), self._make_unit("B", 3)
        loop = GameLoop(team=Team(members=[a, b]), enemies=[self._make_enemy()])
        self.assertEqual(loop.units, [a, b])

    def test_explicit_units_wins_over_team(self):
        a, b = self._make_unit("A", 5), self._make_unit("B", 3)
        loop = GameLoop(team=Team(members=[a, b]), units=[a], enemies=[])
        self.assertEqual(loop.units, [a])

    # â”€â”€ Ordering â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€

    def test_faster_unit_resolves_first_regardless_of_team_order(self):
        slow, fast = self._make_unit("Slow", 3), self._make_unit("Fast", 9)
        loop = GameLoop(
            team=Team(members=[slow, fast]),   # slow is team position 0
            enemies=[self._make_enemy()],
            actions=[
                Action(unit=slow, skill=slow.skills[0]),
                Action(unit=fast, skill=fast.skills[0]),
            ],
        )
        results = loop.run_turn()
        self.assertEqual([r["unit"] for r in results], ["Fast", "Slow"])

    def test_team_order_breaks_speed_tie(self):
        a, b = self._make_unit("A", 5), self._make_unit("B", 5)
        actions = [Action(unit=b, skill=b.skills[0]), Action(unit=a, skill=a.skills[0])]

        # A ahead of B on the team â†’ A first, despite B being declared first.
        results = GameLoop(
            team=Team(members=[a, b]), enemies=[self._make_enemy()], actions=list(actions)
        ).run_turn()
        self.assertEqual([r["unit"] for r in results], ["A", "B"])

        # Swap team order â†’ the tie flips, same declaration order.
        results = GameLoop(
            team=Team(members=[b, a]), enemies=[self._make_enemy()], actions=list(actions)
        ).run_turn()
        self.assertEqual([r["unit"] for r in results], ["B", "A"])

    def test_haste_changes_order_via_effective_speed(self):
        a, b = self._make_unit("A", 5), self._make_unit("B", 3)
        b.set_status("haste", 4)  # 3 + 4 = 7 > 5
        results = GameLoop(
            team=Team(members=[a, b]),
            enemies=[self._make_enemy()],
            actions=[
                Action(unit=a, skill=a.skills[0]),
                Action(unit=b, skill=b.skills[0]),
            ],
        ).run_turn()
        self.assertEqual([r["unit"] for r in results], ["B", "A"])

    def test_action_speed_overrides_unit_speed(self):
        """One unit acting twice orders each action independently."""
        unit = self._make_unit("Solo", 5)
        other = self._make_unit("Other", 7)
        skill = unit.skills[0]
        results = GameLoop(
            team=Team(members=[unit, other]),
            enemies=[self._make_enemy()],
            actions=[
                Action(unit=unit, skill=skill, speed=2, slot="slow"),
                Action(unit=unit, skill=skill, speed=9, slot="fast"),
                Action(unit=other, skill=other.skills[0]),
            ],
        ).run_turn()
        self.assertEqual([r["slot"] for r in results], ["fast", None, "slow"])

    # â”€â”€ Per-action configuration â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€

    def test_per_action_target(self):
        a, b = self._make_unit("A", 5), self._make_unit("B", 3)
        e1, e2 = self._make_enemy("E1"), self._make_enemy("E2")
        GameLoop(
            team=Team(members=[a, b]),
            enemies=[e1, e2],
            sequence=["heads"] * 4,
            actions=[
                Action(unit=a, skill=a.skills[0], target=e2),
                Action(unit=b, skill=b.skills[0], target=e1),
            ],
        ).run_turn()
        # Both took damage â€” proving each action hit its own target, not
        # the shared "first living enemy" that _pick_target would return.
        self.assertLess(e1.hp, e1.max_hp)
        self.assertLess(e2.hp, e2.max_hp)

    def test_target_defaults_to_first_living_enemy(self):
        a = self._make_unit("A", 5)
        e1, e2 = self._make_enemy("E1"), self._make_enemy("E2")
        GameLoop(
            team=Team(members=[a]), enemies=[e1, e2],
            actions=[Action(unit=a, skill=a.skills[0])],
        ).run_turn()
        self.assertLess(e1.hp, e1.max_hp)
        self.assertEqual(e2.hp, e2.max_hp)

    def test_per_action_clash_state_is_isolated(self):
        a, b = self._make_unit("A", 9), self._make_unit("B", 3)
        loop = GameLoop(
            team=Team(members=[a, b]),
            enemies=[self._make_enemy()],
            actions=[
                Action(unit=a, skill=a.skills[0], is_clashing=True,
                       clash_won=True, clash_count=3),
                Action(unit=b, skill=b.skills[0]),
            ],
        )
        loop.run_turn()
        clashing_env, plain_env = loop.envs[0], loop.envs[1]
        self.assertTrue(clashing_env.is_clashing)
        self.assertEqual(clashing_env.clash_count, 3)
        self.assertFalse(plain_env.is_clashing)
        self.assertEqual(plain_env.clash_count, 0)

    def test_action_without_clash_config_falls_back_to_loop(self):
        """The legacy turn-global clash path still applies to every skill."""
        a, b = self._make_unit("A", 9), self._make_unit("B", 3)
        loop = GameLoop(
            team=Team(members=[a, b]),
            enemies=[self._make_enemy()],
            is_clashing=True, clash_won=False, clash_count=2,
            actions=[
                Action(unit=a, skill=a.skills[0]),
                Action(unit=b, skill=b.skills[0]),
            ],
        )
        loop.run_turn()
        for env in loop.envs:
            self.assertTrue(env.is_clashing)
            self.assertIs(env.clash_won, False)
            self.assertEqual(env.clash_count, 2)

    def test_action_clash_won_none_is_honored_not_treated_as_unset(self):
        """is_clashing is the sole discriminator; clash_won=None survives."""
        a = self._make_unit("A", 5)
        loop = GameLoop(
            team=Team(members=[a]),
            enemies=[self._make_enemy()],
            is_clashing=True, clash_won=True, clash_count=5,
            actions=[Action(unit=a, skill=a.skills[0], is_clashing=True,
                            clash_won=None, clash_count=1)],
        )
        loop.run_turn()
        self.assertTrue(loop.envs[0].is_clashing)
        self.assertIsNone(loop.envs[0].clash_won)   # not the loop's True
        self.assertEqual(loop.envs[0].clash_count, 1)

    def test_per_action_sequence(self):
        a, b = self._make_unit("A", 9), self._make_unit("B", 3)
        loop = GameLoop(
            team=Team(members=[a, b]),
            enemies=[self._make_enemy()],
            sequence=["tails"] * 4,
            actions=[
                Action(unit=a, skill=a.skills[0], sequence=["heads"] * 4),
                Action(unit=b, skill=b.skills[0]),   # falls back to loop's tails
            ],
        )
        loop.run_turn()
        self.assertEqual(loop.envs[0].sequence[0], "heads")
        self.assertEqual(loop.envs[1].sequence[0], "tails")

    def test_result_carries_unit_and_slot(self):
        a = self._make_unit("A", 5)
        results = GameLoop(
            team=Team(members=[a]), enemies=[self._make_enemy()],
            actions=[Action(unit=a, skill=a.skills[0], slot="3")],
        ).run_turn()
        self.assertEqual(results[0]["unit"], "A")
        self.assertEqual(results[0]["slot"], "3")

    # â”€â”€ Pre-combat checks â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€

    def test_check_runs_once_between_turn_start_and_combat_start(self):
        tracker: list[str] = []
        a = self._make_unit("A", 5)
        a.skills[0].add_effect(Effect(
            name="ts", phase=SkillPhase.TURN_START,
            apply=lambda ctx: tracker.append("turn_start"),
        ))
        a.skills[0].add_effect(Effect(
            name="cs", phase=SkillPhase.COMBAT_START,
            apply=lambda ctx: tracker.append("combat_start"),
        ))

        GameLoop(
            team=Team(members=[a]),
            enemies=[self._make_enemy()],
            actions=[Action(unit=a, skill=a.skills[0])],
            pre_combat_checks=[lambda plan: tracker.append("check")],
        ).run_turn()

        self.assertEqual(tracker, ["turn_start", "check", "combat_start"])

    def test_check_sees_every_selected_action_in_order(self):
        seen: list[str] = []
        slow, fast = self._make_unit("Slow", 2), self._make_unit("Fast", 8)

        def _capture(plan: TurnPlan) -> None:
            seen.extend(a.unit.name for a in plan.actions)
            self.assertEqual(len(plan.team), 2)
            self.assertEqual(len(plan.enemies), 1)

        GameLoop(
            team=Team(members=[slow, fast]),
            enemies=[self._make_enemy()],
            actions=[
                Action(unit=slow, skill=slow.skills[0]),
                Action(unit=fast, skill=fast.skills[0]),
            ],
            pre_combat_checks=[_capture],
        ).run_turn()

        self.assertEqual(seen, ["Fast", "Slow"])

    def test_check_that_changes_speed_changes_resolution_order(self):
        """Proves the re-sort after checks run."""
        a, b = self._make_unit("A", 9), self._make_unit("B", 3)

        def _bind_the_leader(plan: TurnPlan) -> None:
            plan.actions[0].unit.set_status("bind", 8)   # A: 9 - 8 = 1

        results = GameLoop(
            team=Team(members=[a, b]),
            enemies=[self._make_enemy()],
            actions=[
                Action(unit=a, skill=a.skills[0]),
                Action(unit=b, skill=b.skills[0]),
            ],
            pre_combat_checks=[_bind_the_leader],
        ).run_turn()

        self.assertEqual([r["unit"] for r in results], ["B", "A"])

    def test_check_can_drop_an_action(self):
        a, b = self._make_unit("A", 9), self._make_unit("B", 3)
        results = GameLoop(
            team=Team(members=[a, b]),
            enemies=[self._make_enemy()],
            actions=[
                Action(unit=a, skill=a.skills[0]),
                Action(unit=b, skill=b.skills[0]),
            ],
            pre_combat_checks=[lambda plan: plan.actions.pop(0)],
        ).run_turn()
        self.assertEqual([r["unit"] for r in results], ["B"])

    def test_check_can_replace_the_action_list_wholesale(self):
        a, b = self._make_unit("A", 9), self._make_unit("B", 3)
        results = GameLoop(
            team=Team(members=[a, b]),
            enemies=[self._make_enemy()],
            actions=[Action(unit=a, skill=a.skills[0])],
            pre_combat_checks=[
                lambda plan: setattr(
                    plan, "actions", [Action(unit=b, skill=b.skills[0])]
                )
            ],
        ).run_turn()
        self.assertEqual([r["unit"] for r in results], ["B"])

    def test_check_can_log_to_the_broadcast_log(self):
        a = self._make_unit("A", 5)
        loop = GameLoop(
            team=Team(members=[a]),
            enemies=[self._make_enemy()],
            actions=[Action(unit=a, skill=a.skills[0])],
            pre_combat_checks=[lambda plan: plan.log.append("[check] ok")],
        )
        loop.run_turn()
        self.assertIn("[check] ok", loop._broadcast_env.log)

    def test_no_checks_registered_is_a_no_op(self):
        a = self._make_unit("A", 5)
        results = GameLoop(
            team=Team(members=[a]), enemies=[self._make_enemy()],
            actions=[Action(unit=a, skill=a.skills[0])],
        ).run_turn()
        self.assertEqual(len(results), 1)


# â”€â”€ Sin Resonance tests â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€


class TestResonance(unittest.TestCase):
    """
    Sin Resonance: the pre-combat check that reads the whole selection.

    Most of these drive ``compute_resonance`` directly with a synthetic
    chain, which is far cheaper than running whole turns and lets the
    rule be pinned exactly.
    """

    def _chain(self, *sins: str, physical: str = "Slash") -> list[Action]:
        """Build actions whose skills carry the given Affinities, in order."""
        return [
            Action(
                unit=None,
                skill=Skill(name=f"{sin}{i}", damage_type=(physical, sin)),
            )
            for i, sin in enumerate(sins)
        ]

    def _bonuses(self, *sins: str) -> list[int]:
        return compute_resonance(self._chain(*sins))[1]

    # â”€â”€ the two worked examples â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€

    def test_worked_example_a_absolute_wins(self):
        """L P L L L P L â€” the middle three Lust are lifted to +3 by A-Reson."""
        result, bonuses = compute_resonance(
            self._chain("Lust", "Pride", "Lust", "Lust", "Lust", "Pride", "Lust")
        )
        #                    L   P   L   L   L   P   L
        self.assertEqual(bonuses, [0, 0, 3, 3, 3, 1, 5])
        self.assertEqual(result.count("lust"), 5)
        self.assertEqual(result.absolute_longest("lust"), 3)
        self.assertEqual(result.count("pride"), 2)
        self.assertEqual(result.absolute_longest("pride"), 0)

    def test_worked_example_b_positional_wins(self):
        """
        P P L P P L P P P â€” the only run is the last three Pride (flat +3),
        but they sit at Pride positions 5/6/7, so they keep +5/+5/+7.

        This is the case that rules out "A-Reson. replaces Reson.".
        """
        result, bonuses = compute_resonance(
            self._chain(
                "Pride", "Pride", "Lust", "Pride", "Pride",
                "Lust", "Pride", "Pride", "Pride",
            )
        )
        #                    P   P   L   P   P   L   P   P   P
        self.assertEqual(bonuses, [0, 1, 0, 3, 3, 1, 5, 5, 7])
        self.assertEqual(result.count("pride"), 7)
        self.assertEqual(result.absolute_longest("pride"), 3)

    def test_the_two_never_stack(self):
        """Every bonus equals max(positional, flat) â€” never their sum."""
        # In example A the A-Reson. members would be +1+3, +3+3, +3+3 if
        # the two stacked; they are +3, +3, +3.
        self.assertEqual(
            self._bonuses("Lust", "Pride", "Lust", "Lust", "Lust", "Pride", "Lust"),
            [0, 0, 3, 3, 3, 1, 5],
        )

    # â”€â”€ normal resonance â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€

    def test_reson_does_not_require_adjacency(self):
        self.assertEqual(self._bonuses("Lust", "Pride", "Lust"), [0, 0, 1])

    def test_two_of_a_sin_is_the_minimum(self):
        self.assertEqual(self._bonuses("Lust", "Pride"), [0, 0])

    def test_single_skill_never_resonates(self):
        result, bonuses = compute_resonance(self._chain("Lust"))
        self.assertEqual(bonuses, [0])
        self.assertEqual(result.by_sin, {})

    def test_reson_ramp_follows_the_table(self):
        self.assertEqual(
            self._bonuses(*["Lust"] * 10),
            # positions 1..10, but 3+ consecutive also makes this an
            # A-Reson. of length 10 -> flat +11 beats every positional.
            [11] * 10,
        )

    def test_reson_ramp_without_adjacency(self):
        """Separate the same sins so only the positional ramp applies."""
        chain = []
        for _ in range(6):
            chain.extend(["Lust", "Pride"])
        # Lust at even indices -> positions 1..6 -> 0,1,3,3,5,5
        bonuses = self._bonuses(*chain)
        self.assertEqual([bonuses[i] for i in range(0, 12, 2)], [0, 1, 3, 3, 5, 5])

    # â”€â”€ absolute resonance â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€

    def test_absolute_needs_three_consecutive(self):
        # 3 Lust total but only 2 adjacent -> no A-Reson.
        result, bonuses = compute_resonance(self._chain("Lust", "Pride", "Lust", "Lust"))
        self.assertEqual(result.absolute_longest("lust"), 0)
        self.assertEqual(bonuses, [0, 0, 1, 3])

    def test_a_different_sin_breaks_the_run(self):
        result, _ = compute_resonance(self._chain("Lust", "Lust", "Pride", "Lust"))
        self.assertEqual(result.count("lust"), 3)
        self.assertEqual(result.absolute_longest("lust"), 0)

    def test_absolute_lifts_a_short_chain(self):
        """3 consecutive: the +0/+1/+3 ramp becomes a flat +3."""
        self.assertEqual(self._bonuses("Lust", "Lust", "Lust"), [3, 3, 3])

    def test_two_runs_count_as_the_longest_not_the_sum(self):
        result, _ = compute_resonance(
            self._chain(
                "Lust", "Lust", "Lust", "Pride", "Lust", "Lust", "Lust",
            )
        )
        # Two runs of 3 -> 3, never 6.
        self.assertEqual(result.absolute_longest("lust"), 3)
        self.assertEqual(result.count("lust"), 6)

    def test_longest_run_is_reported(self):
        result, _ = compute_resonance(
            self._chain(
                "Lust", "Lust", "Lust", "Pride",
                "Lust", "Lust", "Lust", "Lust",
            )
        )
        self.assertEqual(result.absolute_longest("lust"), 4)

    # â”€â”€ table boundaries â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€

    def test_tables_clamp_past_eleven(self):
        long_run = self._bonuses(*["Lust"] * 15)
        self.assertEqual(long_run, [11] * 15)

    def test_absolute_table_values(self):
        for length, expected in [
            (3, 3), (4, 5), (5, 5), (6, 7), (7, 7),
            (8, 9), (9, 9), (10, 11), (11, 11),
        ]:
            with self.subTest(length=length):
                bonuses = self._bonuses(*["Lust"] * length)
                self.assertTrue(
                    all(b == expected for b in bonuses),
                    f"run of {length} -> {bonuses}, expected all {expected}",
                )

    # â”€â”€ affinity / skill classification â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€

    def test_defensive_skill_chains_and_takes_defense_level(self):
        actions = [
            Action(unit=None, skill=Skill(name="Atk", damage_type=("Slash", "Lust"))),
            Action(unit=None, skill=Skill(name="Def", damage_type=("Evade", "Lust"))),
        ]
        plan = TurnPlan(actions=actions)
        resonance_check(plan)

        self.assertEqual(plan.state["resonance"].count("lust"), 2)
        # Second skill earns +1; it is defensive, so it lands on defense.
        self.assertEqual(actions[1].defense_level_bonus, 1)
        self.assertEqual(actions[1].offense_level_bonus, 0)
        self.assertEqual(actions[0].offense_level_bonus, 0)

    def test_is_offensive_classification(self):
        self.assertTrue(is_offensive(Skill(damage_type=("Slash", "Lust"))))
        self.assertTrue(is_offensive(Skill(damage_type=("Pierce", "Lust"))))
        self.assertTrue(is_offensive(Skill(damage_type=("Blunt", "Lust"))))
        self.assertFalse(is_offensive(Skill(damage_type=("Evade", "Lust"))))

    def test_sin_of_normalizes_case(self):
        self.assertEqual(sin_of(Skill(damage_type=("Slash", "Lust"))), "lust")
        self.assertEqual(sin_of(Skill(damage_type=("Slash", " GLOOM "))), "gloom")

    def test_unknown_affinity_chains_with_nothing_and_breaks_runs(self):
        self.assertIsNone(sin_of(Skill(damage_type=("Slash", "Bogus"))))
        result, bonuses = compute_resonance(
            self._chain("Lust", "Bogus", "Lust", "Lust")
        )
        # The unknown sin breaks adjacency, so no A-Reson...
        self.assertEqual(result.absolute_longest("lust"), 0)
        # ...and it never earns a bonus itself.
        self.assertEqual(bonuses[1], 0)
        self.assertEqual(bonuses, [0, 0, 1, 3])

    # â”€â”€ integration through a real turn â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€

    def _resonating_unit(self, name: str, speed: int, sin: str) -> Unit:
        skill = Skill(
            name=f"{name}Skill", base_power=5, coin_power=0, offense_level=0,
            damage_type=("Slash", sin), coins=[_make_coin(coin_power=0)],
        )
        unit = Unit(name=name, base_level=10, hp=100, max_hp=100, speed=speed)
        unit.add_skill(skill, slot="1")
        return unit

    def test_resonance_raises_env_ol_in_a_real_turn(self):
        fast = self._resonating_unit("Fast", 9, "Lust")
        slow = self._resonating_unit("Slow", 3, "Lust")
        enemy = Enemy(
            name="E", base_level=10, defense_level=0, hp=500, max_hp=500,
            phys_res={"Slash": 1.0}, sin_res={"Lust": 1.0},
        )
        loop = GameLoop(
            team=Team(members=[fast, slow]),
            enemies=[enemy],
            actions=[
                Action(unit=fast, skill=fast.skills[0]),
                Action(unit=slow, skill=slow.skills[0]),
            ],
        )
        loop.run_turn()

        # Both Lust -> Reson. 2: first +0, second +1.
        self.assertEqual(loop.resonance.count("lust"), 2)
        base_ol = 0 + 10  # skill.offense_level + unit.base_level
        self.assertEqual(loop.envs[0].ol, base_ol)
        self.assertEqual(loop.envs[1].ol, base_ol + 1)

    def test_resonance_does_not_mutate_the_permanent_kit(self):
        a = self._resonating_unit("A", 9, "Lust")
        b = self._resonating_unit("B", 3, "Lust")
        enemy = Enemy(name="E", base_level=10, hp=500, max_hp=500)
        GameLoop(
            team=Team(members=[a, b]), enemies=[enemy],
            actions=[
                Action(unit=a, skill=a.skills[0]),
                Action(unit=b, skill=b.skills[0]),
            ],
        ).run_turn()

        # The bonus lives on the Action/Environment, never on the Skill.
        self.assertEqual(a.skills[0].offense_level, 0)
        self.assertEqual(b.skills[0].offense_level, 0)

    def test_non_resonating_turn_grants_nothing(self):
        a = self._resonating_unit("A", 9, "Lust")
        b = self._resonating_unit("B", 3, "Pride")
        enemy = Enemy(name="E", base_level=10, hp=500, max_hp=500)
        loop = GameLoop(
            team=Team(members=[a, b]), enemies=[enemy],
            actions=[
                Action(unit=a, skill=a.skills[0]),
                Action(unit=b, skill=b.skills[0]),
            ],
        )
        loop.run_turn()
        self.assertEqual(loop.resonance.by_sin, {})
        self.assertEqual(loop.envs[0].ol, 10)
        self.assertEqual(loop.envs[1].ol, 10)

    def test_chain_order_is_speed_order_not_team_order(self):
        """Team order picks the tiebreak; the chain itself follows speed."""
        slow_lust = self._resonating_unit("SlowLust", 2, "Lust")
        fast_lust = self._resonating_unit("FastLust", 9, "Lust")
        enemy = Enemy(name="E", base_level=10, hp=500, max_hp=500)
        loop = GameLoop(
            team=Team(members=[slow_lust, fast_lust]),  # slow is position 0
            enemies=[enemy],
            actions=[
                Action(unit=slow_lust, skill=slow_lust.skills[0]),
                Action(unit=fast_lust, skill=fast_lust.skills[0]),
            ],
        )
        loop.run_turn()
        # Fast resolves first -> chain position 1 -> +0; slow gets +1,
        # even though slow sits earlier on the team.
        self.assertEqual(loop.envs[0].ol, 10)      # FastLust
        self.assertEqual(loop.envs[1].ol, 10 + 1)  # SlowLust

    # â”€â”€ condition helpers â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€

    def test_check_resonance_reads_global_state(self):
        env = Environment(skill=Skill(damage_type=("Slash", "Lust")))
        result, _ = compute_resonance(self._chain("Lust", "Lust", "Lust"))
        env.global_state["resonance"] = result

        self.assertTrue(check_resonance(env))                      # own sin
        self.assertTrue(check_resonance(env, "lust", 3))
        self.assertFalse(check_resonance(env, "lust", 4))
        self.assertFalse(check_resonance(env, "pride"))
        self.assertTrue(check_absolute_resonance_longest(env, "lust", 3))
        self.assertFalse(check_absolute_resonance_longest(env, "lust", 4))
        self.assertTrue(check_absolute_resonance_sum(env, "lust", 3))
        # Own-chain form needs a chain_index; this env has none (-1).
        self.assertFalse(check_absolute_resonance(env))
        env.chain_index = 1
        self.assertTrue(check_absolute_resonance(env, 3))
        self.assertFalse(check_absolute_resonance(env, 4))

    def test_check_resonance_false_without_a_resonance_aware_loop(self):
        env = Environment(skill=Skill(damage_type=("Slash", "Lust")))
        self.assertFalse(check_resonance(env))
        self.assertFalse(check_absolute_resonance(env))

    def test_check_resonance_gates_an_effect_in_a_real_turn(self):
        fired: list[str] = []
        a = self._resonating_unit("A", 9, "Lust")
        b = self._resonating_unit("B", 3, "Lust")
        b.skills[0].add_effect(Effect(
            name="on_reson",
            phase=SkillPhase.ON_USE,
            apply=lambda ctx: fired.append("yes"),
            condition=check_resonance,
            condition_args=("lust", 2),
        ))
        enemy = Enemy(name="E", base_level=10, hp=500, max_hp=500)
        GameLoop(
            team=Team(members=[a, b]), enemies=[enemy],
            actions=[
                Action(unit=a, skill=a.skills[0]),
                Action(unit=b, skill=b.skills[0]),
            ],
        ).run_turn()
        self.assertEqual(fired, ["yes"])

    # â”€â”€ summary / registration â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€

    def test_summary_string(self):
        result, _ = compute_resonance(
            self._chain("Lust", "Pride", "Lust", "Lust", "Lust", "Pride", "Lust")
        )
        self.assertEqual(result.summary(), "Lust x5 (A-Reson 3) | Pride x2")

    def test_summary_when_nothing_resonates(self):
        self.assertEqual(compute_resonance(self._chain("Lust"))[0].summary(), "none")

    def test_resonance_check_is_registered_by_default(self):
        self.assertIn(resonance_check, PRE_COMBAT_CHECKS)

    def test_check_logs_to_the_broadcast_log(self):
        a = self._resonating_unit("A", 9, "Lust")
        b = self._resonating_unit("B", 3, "Lust")
        enemy = Enemy(name="E", base_level=10, hp=500, max_hp=500)
        loop = GameLoop(
            team=Team(members=[a, b]), enemies=[enemy],
            actions=[
                Action(unit=a, skill=a.skills[0]),
                Action(unit=b, skill=b.skills[0]),
            ],
        )
        loop.run_turn()
        log = "\n".join(loop._broadcast_env.log)
        self.assertIn("[resonance] Lust x2", log)
        self.assertIn("+1 Offense Level", log)


class TestResonanceChains(unittest.TestCase):
    """
    Every Reson. / A-Reson. line kept and reachable at any turn phase.

    Motivated by a skill reading "the **sum** of Envy A-Reson.", which
    the earlier longest-run-only storage could not express.
    """

    def _chain(self, *sins: str) -> list[Action]:
        return [
            Action(unit=None, skill=Skill(name=f"{s}{i}", damage_type=("Slash", s)))
            for i, s in enumerate(sins)
        ]

    def _unit(self, name: str, speed: int, sin: str) -> Unit:
        skill = Skill(
            name=f"{name}Skill", base_power=5, coin_power=0, offense_level=0,
            damage_type=("Slash", sin), coins=[_make_coin(coin_power=0)],
        )
        unit = Unit(name=name, base_level=10, hp=100, max_hp=100, speed=speed)
        unit.add_skill(skill, slot="1")
        return unit

    # ── the motivating shape: two chains of 3 ────────────────────────

    def _two_envy_chains(self):
        # E E E P E E E -> two runs of 3, split by the Pride
        return compute_resonance(
            self._chain("Envy", "Envy", "Envy", "Pride", "Envy", "Envy", "Envy")
        )

    def test_longest_and_sum_diverge(self):
        result, _ = self._two_envy_chains()
        self.assertEqual(result.absolute_longest("envy"), 3)   # dashboard rule
        self.assertEqual(result.absolute_sum("envy"), 6)       # the Counter skill
        self.assertEqual(result.count("envy"), 6)

    def test_both_chains_are_kept(self):
        result, _ = self._two_envy_chains()
        chains = result.chains("envy")
        self.assertEqual(len(chains), 2)
        self.assertEqual([(c.start, c.length) for c in chains], [(0, 3), (4, 3)])
        self.assertTrue(all(c.sin == "envy" for c in chains))
        # Each run of 3 pays a flat +3.
        self.assertTrue(all(c.bonus == 3 for c in chains))

    def test_chains_unfiltered_spans_every_affinity(self):
        result, _ = compute_resonance(
            self._chain("Envy", "Envy", "Envy", "Lust", "Lust", "Lust")
        )
        all_chains = result.chains()
        self.assertEqual([c.sin for c in all_chains], ["envy", "lust"])
        self.assertEqual([c.start for c in all_chains], [0, 3])
        self.assertEqual(len(result.chains("envy")), 1)

    def test_chains_empty_for_a_sin_that_never_ran(self):
        result, _ = self._two_envy_chains()
        self.assertEqual(result.chains("pride"), ())
        self.assertEqual(result.absolute_sum("pride"), 0)
        self.assertEqual(result.chains("nonesuch"), ())

    def test_chain_at_finds_the_containing_run(self):
        result, _ = self._two_envy_chains()
        self.assertEqual(result.chain_at(0).start, 0)
        self.assertEqual(result.chain_at(2).start, 0)
        self.assertEqual(result.chain_at(4).start, 4)
        self.assertEqual(result.chain_at(6).start, 4)
        self.assertIsNone(result.chain_at(3))    # the Pride between them
        self.assertIsNone(result.chain_at(-1))   # no resolving skill
        self.assertIsNone(result.chain_at(99))

    def test_chain_geometry(self):
        result, _ = self._two_envy_chains()
        second = result.chains("envy")[1]
        self.assertEqual(second.end, 7)
        self.assertEqual(second.indices, (4, 5, 6))
        self.assertTrue(second.contains(5))
        self.assertFalse(second.contains(3))

    def test_a_run_of_two_is_not_a_chain(self):
        result, _ = compute_resonance(self._chain("Envy", "Envy", "Pride"))
        self.assertEqual(result.chains("envy"), ())
        self.assertEqual(result.absolute_sum("envy"), 0)
        self.assertEqual(result.absolute_longest("envy"), 0)
        self.assertEqual(result.count("envy"), 2)   # still a normal Reson.

    def test_indices_of_gives_the_reson_line(self):
        result, _ = self._two_envy_chains()
        self.assertEqual(result.indices_of("envy"), (0, 1, 2, 4, 5, 6))
        self.assertEqual(result.indices_of("pride"), ())   # only one Pride

    def test_summary_reports_sum_only_when_chains_differ(self):
        two, _ = self._two_envy_chains()
        self.assertIn("A-Reson 3, sum 6", two.summary())
        one, _ = compute_resonance(self._chain("Envy", "Envy", "Envy"))
        self.assertNotIn("sum", one.summary())

    # ── a full mixed chain ───────────────────────────────────────────

    def test_mixed_ten_skill_chain(self):
        """
        Lust sloth Pride Pride Lust Sloth sloth Sloth lust Pride

        Three Affinities interleaved, with exactly one A-Reson. run
        (Sloth at 5-7). Written in mixed case on purpose: Affinity keys
        normalize, so "Sloth"/"sloth" are the same Affinity.
        """
        result, bonuses = compute_resonance(self._chain(
            "Lust", "sloth", "Pride", "Pride", "Lust",
            "Sloth", "sloth", "Sloth", "lust", "Pride",
        ))

        # ── Sloth: 4 total, one run of 3 at indices 5-7 ──────────────
        self.assertEqual(result.count("sloth"), 4)
        self.assertEqual(result.absolute_longest("sloth"), 3)
        self.assertEqual(result.absolute_sum("sloth"), 3)   # single run
        self.assertEqual(result.indices_of("sloth"), (1, 5, 6, 7))
        self.assertEqual(
            [(c.start, c.length) for c in result.chains("sloth")], [(5, 3)]
        )

        # ── Pride: 3 total, but its longest run is only 2 (idx 2-3) ──
        self.assertEqual(result.count("pride"), 3)
        self.assertEqual(result.absolute_longest("pride"), 0)
        self.assertEqual(result.indices_of("pride"), (2, 3, 9))
        self.assertEqual(result.chains("pride"), ())

        # ── Lust: 3 total (idx 0, 4, 8), never adjacent ──────────────
        self.assertEqual(result.count("lust"), 3)
        self.assertEqual(result.absolute_longest("lust"), 0)
        self.assertEqual(result.indices_of("lust"), (0, 4, 8))
        self.assertEqual(result.chains("lust"), ())

        # Sloth is the only Affinity that absolutely resonates.
        self.assertEqual([c.sin for c in result.chains()], ["sloth"])

        # ── per-skill bonuses ────────────────────────────────────────
        #  idx sin    positional Reson.        A-Reson.   final
        #   0  Lust   pos 1 -> +0              -          +0
        #   1  sloth  pos 1 -> +0              -          +0
        #   2  Pride  pos 1 -> +0              -          +0
        #   3  Pride  pos 2 -> +1              -          +1
        #   4  Lust   pos 2 -> +1              -          +1
        #   5  Sloth  pos 2 -> +1              +3         +3
        #   6  sloth  pos 3 -> +3              +3         +3
        #   7  Sloth  pos 4 -> +3              +3         +3
        #   8  lust   pos 3 -> +3              -          +3
        #   9  Pride  pos 3 -> +3              -          +3
        self.assertEqual(bonuses, [0, 0, 0, 1, 1, 3, 3, 3, 3, 3])

        self.assertEqual(result.summary(), "Sloth x4 (A-Reson 3) | Lust x3 | Pride x3")

    # ── chain_index threading ────────────────────────────────────────

    def test_chain_index_is_assigned_in_speed_order(self):
        fast = self._unit("Fast", 9, "Lust")
        slow = self._unit("Slow", 2, "Lust")
        actions = [
            Action(unit=slow, skill=slow.skills[0]),   # declared first
            Action(unit=fast, skill=fast.skills[0]),
        ]
        loop = GameLoop(
            team=Team(members=[slow, fast]),
            enemies=[Enemy(name="E", base_level=10, hp=500, max_hp=500)],
            actions=actions,
        )
        loop.run_turn()
        # Speed order, not declaration order.
        self.assertEqual(fast.skills[0].name, "FastSkill")
        self.assertEqual(actions[1].chain_index, 0)   # fast
        self.assertEqual(actions[0].chain_index, 1)   # slow

    def test_chain_index_reaches_the_environment(self):
        a = self._unit("A", 9, "Lust")
        b = self._unit("B", 3, "Lust")
        loop = GameLoop(
            team=Team(members=[a, b]),
            enemies=[Enemy(name="E", base_level=10, hp=500, max_hp=500)],
            actions=[
                Action(unit=a, skill=a.skills[0]),
                Action(unit=b, skill=b.skills[0]),
            ],
        )
        loop.run_turn()
        self.assertEqual([env.chain_index for env in loop.envs], [0, 1])

    def test_own_chain_query_sees_its_own_run_not_another(self):
        """Two runs of different lengths: each member reads its own."""
        # L L L L  P  E E E   -> Lust run of 4, Envy run of 3
        result, _ = compute_resonance(
            self._chain("Lust", "Lust", "Lust", "Lust", "Pride", "Envy", "Envy", "Envy")
        )
        env = Environment(skill=Skill(damage_type=("Slash", "Lust")))
        env.global_state["resonance"] = result

        env.chain_index = 0                                    # in the Lust run of 4
        self.assertTrue(check_absolute_resonance(env, 4))
        self.assertFalse(check_absolute_resonance(env, 5))

        env.chain_index = 5                                    # in the Envy run of 3
        self.assertTrue(check_absolute_resonance(env, 3))
        self.assertFalse(check_absolute_resonance(env, 4))

        env.chain_index = 4                                    # the lone Pride
        self.assertFalse(check_absolute_resonance(env, 1))

    def test_own_chain_query_is_false_without_a_resolving_skill(self):
        result, _ = self._two_envy_chains()
        env = Environment(skill=Skill(damage_type=("Slash", "Envy")))
        env.global_state["resonance"] = result
        # Broadcast envs carry no chain_index.
        self.assertEqual(env.chain_index, -1)
        self.assertFalse(check_absolute_resonance(env, 3))
        # ...but an Affinity-keyed question still answers there.
        self.assertTrue(check_absolute_resonance_sum(env, "envy", 6))

    # ── phase availability ───────────────────────────────────────────

    def _phase_probe(self, seen: dict) -> Unit:
        """A unit whose skill records what resonance looks like per phase."""
        unit = self._unit("Probe", 9, "Lust")
        for phase in (SkillPhase.TURN_START, SkillPhase.COMBAT_START, SkillPhase.TURN_END):
            unit.skills[0].add_effect(Effect(
                name=f"probe_{phase.value}",
                phase=phase,
                apply=lambda ctx, p=phase: seen.__setitem__(
                    p.value, get_resonance(ctx)
                ),
            ))
        return unit

    def test_resonance_is_absent_at_turn_start_present_from_combat_start(self):
        seen: dict = {}
        probe = self._phase_probe(seen)
        other = self._unit("Other", 3, "Lust")
        GameLoop(
            team=Team(members=[probe, other]),
            enemies=[Enemy(name="E", base_level=10, hp=500, max_hp=500)],
            actions=[
                Action(unit=probe, skill=probe.skills[0]),
                Action(unit=other, skill=other.skills[0]),
            ],
        ).run_turn()

        # Turn Start runs before the chain is final -> nothing to read.
        self.assertIsNone(seen["turn_start"])
        # Combat Start onward -> the real thing.
        self.assertIsNotNone(seen["combat_start"])
        self.assertEqual(seen["combat_start"].count("lust"), 2)
        self.assertIsNotNone(seen["turn_end"])
        self.assertEqual(seen["turn_end"].count("lust"), 2)

    def test_a_combat_start_effect_can_read_the_a_reson_sum(self):
        """The Counter skill's actual query, driven through a real turn."""
        fired: list[str] = []
        members = [self._unit(f"E{i}", 10 - i, "Envy") for i in range(3)]
        members[0].skills[0].add_effect(Effect(
            name="Combat Start: 3+ Envy A-Reson sum",
            phase=SkillPhase.COMBAT_START,
            apply=lambda ctx: fired.append("yes"),
            condition=check_absolute_resonance_sum,
            condition_args=("envy", 3),
        ))
        GameLoop(
            team=Team(members=members),
            enemies=[Enemy(name="E", base_level=10, hp=500, max_hp=500)],
            actions=[Action(unit=u, skill=u.skills[0]) for u in members],
        ).run_turn()
        self.assertEqual(fired, ["yes"])

    def test_resonance_does_not_leak_across_turns(self):
        seen: dict = {}
        probe = self._phase_probe(seen)
        other = self._unit("Other", 3, "Lust")
        loop = GameLoop(
            team=Team(members=[probe, other]),
            enemies=[Enemy(name="E", base_level=10, hp=500, max_hp=500)],
            actions=[
                Action(unit=probe, skill=probe.skills[0]),
                Action(unit=other, skill=other.skills[0]),
            ],
        )
        loop.run_turn()
        self.assertIsNotNone(seen["combat_start"])

        seen.clear()
        loop.run_turn()
        # Second turn's Turn Start must not see the first turn's result.
        self.assertIsNone(seen["turn_start"])

    # ── broadcast envs now carry turn context at all ─────────────────

    def test_broadcast_env_sees_units_and_enemies(self):
        """
        Regression: broadcast global_state used to be empty, so any
        TURN_START/TURN_END effect reading global_state["units"] saw [].
        """
        seen: dict = {}
        unit = self._unit("U", 5, "Lust")
        unit.skills[0].add_effect(Effect(
            name="turn_end probe",
            phase=SkillPhase.TURN_END,
            apply=lambda ctx: seen.update(
                units=list(ctx.global_state.get("units", [])),
                enemies=list(ctx.global_state.get("enemies", [])),
            ),
        ))
        enemy = Enemy(name="E", base_level=10, hp=500, max_hp=500)
        GameLoop(
            team=Team(members=[unit]), enemies=[enemy],
            actions=[Action(unit=unit, skill=unit.skills[0])],
        ).run_turn()

        self.assertEqual(seen["units"], [unit])
        self.assertEqual(seen["enemies"], [enemy])


# â”€â”€ Passive tests â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€


class TestPassive(unittest.TestCase):
    def test_execute_phase_fires_effects(self):
        env = Environment()
        eff = _make_damage_effect("p_buff", SkillPhase.BEFORE_ATTACK, 5)
        passive = Passive(name="Armor", effects={SkillPhase.BEFORE_ATTACK: [eff]})
        passive.execute_phase(SkillPhase.BEFORE_ATTACK, env)
        self.assertEqual(env.current_power, 5)

    def test_proc_limit_respected(self):
        env = Environment()
        eff = _make_damage_effect("p_buff", SkillPhase.ON_USE, 3)
        passive = Passive(
            name="Once",
            effects={SkillPhase.ON_USE: [eff]},
            max_procs=1,
        )
        passive.execute_phase(SkillPhase.ON_USE, env)
        passive.execute_phase(SkillPhase.ON_USE, env)  # should be ignored
        self.assertEqual(env.current_power, 3)
        self.assertEqual(passive.proc_count, 1)

    def test_condition_blocks_execution(self):
        env = Environment()
        eff = _make_damage_effect("guarded", SkillPhase.ON_USE, 10)
        passive = Passive(
            name="Conditional",
            effects={SkillPhase.ON_USE: [eff]},
            condition=lambda e: False,
        )
        passive.execute_phase(SkillPhase.ON_USE, env)
        self.assertEqual(env.current_power, 0)

    def test_condition_allows_execution(self):
        env = Environment()
        eff = _make_damage_effect("guarded", SkillPhase.ON_USE, 10)
        passive = Passive(
            name="Conditional",
            effects={SkillPhase.ON_USE: [eff]},
            condition=lambda e: True,
        )
        passive.execute_phase(SkillPhase.ON_USE, env)
        self.assertEqual(env.current_power, 10)

    def test_reset_procs(self):
        env = Environment()
        eff = _make_damage_effect("p", SkillPhase.ON_USE, 1)
        passive = Passive(name="R", effects={SkillPhase.ON_USE: [eff]}, max_procs=1)
        passive.execute_phase(SkillPhase.ON_USE, env)
        self.assertEqual(passive.proc_count, 1)
        passive.reset_procs()
        self.assertEqual(passive.proc_count, 0)

    def test_no_effects_for_phase_does_not_increment(self):
        env = Environment()
        eff = _make_damage_effect("p", SkillPhase.ON_USE, 1)
        passive = Passive(name="X", effects={SkillPhase.ON_USE: [eff]})
        # Fire for a phase that has no registered effects
        passive.execute_phase(SkillPhase.TURN_START, env)
        self.assertEqual(passive.proc_count, 0)

    def test_add_effect(self):
        passive = Passive(name="P")
        eff = _make_damage_effect("e", CoinPhase.ON_HIT, 1)
        passive.add_effect(eff)
        self.assertEqual(passive.get_effects(CoinPhase.ON_HIT), [eff])


# â”€â”€ Enemy tests â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€


class TestEnemy(unittest.TestCase):
    def test_effective_defense(self):
        enemy = Enemy(base_level=40, defense_level=-5)
        self.assertEqual(enemy.effective_defense, 35)

    def test_effective_speed_with_no_haste_or_bind(self):
        enemy = Enemy(speed=6)
        self.assertEqual(enemy.effective_speed, 6)

    def test_effective_speed_adds_haste(self):
        enemy = Enemy(speed=6, statuses={"haste": 4})
        self.assertEqual(enemy.effective_speed, 10)

    def test_effective_speed_subtracts_bind(self):
        enemy = Enemy(speed=6, statuses={"bind": 4})
        self.assertEqual(enemy.effective_speed, 2)

    def test_effective_speed_combines_haste_and_bind_additively(self):
        enemy = Enemy(speed=6, statuses={"haste": 5, "bind": 2})
        self.assertEqual(enemy.effective_speed, 9)

    def test_effective_speed_floored_at_one(self):
        enemy = Enemy(speed=3, statuses={"bind": 10})
        self.assertEqual(enemy.effective_speed, 1)

    def test_has_sanity_defaults_false_and_sp_is_settable(self):
        enemy = Enemy(sp=10)
        self.assertFalse(enemy.has_sanity)
        self.assertEqual(enemy.sp, 10)

    def test_adjust_sp_caps_at_positive_45(self):
        enemy = Enemy(sp=40)
        self.assertEqual(enemy.adjust_sp(20), 45)

    def test_adjust_sp_floors_at_negative_45(self):
        enemy = Enemy(sp=-40)
        self.assertEqual(enemy.adjust_sp(-20), -45)

    def test_take_damage(self):
        enemy = Enemy(hp=50, max_hp=50)
        actual = enemy.take_damage(20)
        self.assertEqual(actual, 20)
        self.assertEqual(enemy.hp, 30)

    def test_take_damage_clamped(self):
        enemy = Enemy(hp=10, max_hp=50)
        actual = enemy.take_damage(999)
        self.assertEqual(actual, 10)
        self.assertEqual(enemy.hp, 0)
        self.assertFalse(enemy.is_alive)

    def test_take_damage_partially_absorbed_by_shield(self):
        enemy = Enemy(hp=50, max_hp=50, shield=20)
        actual = enemy.take_damage(15)
        self.assertEqual(actual, 0)
        self.assertEqual(enemy.hp, 50)
        self.assertEqual(enemy.shield, 5)

    def test_take_damage_overflows_past_depleted_shield(self):
        enemy = Enemy(hp=50, max_hp=50, shield=8)
        actual = enemy.take_damage(20)
        self.assertEqual(actual, 12)
        self.assertEqual(enemy.hp, 38)
        self.assertEqual(enemy.shield, 0)

    def test_take_damage_with_no_shield_behaves_as_before(self):
        enemy = Enemy(hp=50, max_hp=50)
        actual = enemy.take_damage(15)
        self.assertEqual(actual, 15)
        self.assertEqual(enemy.hp, 35)
        self.assertEqual(enemy.shield, 0)

    def test_heal(self):
        enemy = Enemy(hp=50, max_hp=100)
        actual = enemy.heal(30)
        self.assertEqual(actual, 30)
        self.assertEqual(enemy.hp, 80)

    def test_heal_clamped(self):
        enemy = Enemy(hp=90, max_hp=100)
        actual = enemy.heal(50)
        self.assertEqual(actual, 10)
        self.assertEqual(enemy.hp, 100)

    def test_statuses(self):
        enemy = Enemy()
        enemy.set_status("burn", 3)
        self.assertTrue(enemy.has_status("burn"))
        self.assertEqual(enemy.get_status("burn"), 3)
        enemy.remove_status("burn")
        self.assertFalse(enemy.has_status("burn"))
        self.assertIsNone(enemy.get_status("burn"))

    def test_reset_passives(self):
        env = Environment()
        eff = _make_damage_effect("p", SkillPhase.ON_USE, 1)
        passive = Passive(name="P", effects={SkillPhase.ON_USE: [eff]}, max_procs=1)
        passive.execute_phase(SkillPhase.ON_USE, env)
        enemy = Enemy(passives=[passive])
        enemy.reset_passives()
        self.assertEqual(passive.proc_count, 0)

    def test_stagger_triggers_when_crossing_threshold(self):
        enemy = Enemy(hp=100, max_hp=100, stagger_thresholds=[70])
        self.assertFalse(enemy.is_staggered)
        enemy.take_damage(31)  # 100 -> 69 crosses below 70
        self.assertTrue(enemy.is_staggered)

    def test_stagger_not_triggered_when_landing_on_threshold(self):
        enemy = Enemy(hp=100, max_hp=100, stagger_thresholds=[70])
        enemy.take_damage(30)  # 100 -> 70 is not below 70
        self.assertFalse(enemy.is_staggered)

    def test_raise_stagger_threshold_clamps_to_max_hp(self):
        enemy = Enemy(hp=90, max_hp=100, stagger_thresholds=[95])
        raised = enemy.raise_stagger_threshold(10)
        self.assertEqual(raised, 5)
        self.assertEqual(enemy.stagger_thresholds, [100])
        self.assertTrue(enemy.is_staggered)
        self.assertEqual(enemy.stagger_turns_remaining, 2)

    def test_stagger_lasts_current_and_next_turn(self):
        enemy = Enemy(hp=100, max_hp=100, stagger_thresholds=[70])
        enemy.take_damage(31)  # 100 -> 69, triggers stagger
        self.assertTrue(enemy.is_staggered)
        self.assertEqual(enemy.stagger_turns_remaining, 2)

        enemy.tick_stagger_duration()
        self.assertTrue(enemy.is_staggered)
        self.assertEqual(enemy.stagger_turns_remaining, 1)

        enemy.tick_stagger_duration()
        self.assertFalse(enemy.is_staggered)
        self.assertEqual(enemy.stagger_turns_remaining, 0)

    def test_stagger_level_counts_thresholds_crossed_in_first_turn(self):
        enemy = Enemy(hp=100, max_hp=100, stagger_thresholds=[70, 40, 20])
        enemy.take_damage(85)  # 100 -> 15 crosses all three thresholds
        self.assertTrue(enemy.is_staggered)
        self.assertEqual(enemy.stagger_turns_remaining, 2)
        self.assertEqual(enemy.stagger_level, 3)
        self.assertEqual(enemy.get_stagger_physical_resistance(), 3.0)

    def test_stagger_level_is_uncapped_with_many_threshold_crossings(self):
        enemy = Enemy(hp=100, max_hp=100, stagger_thresholds=[90, 80, 70, 60, 50])
        enemy.take_damage(60)  # 100 -> 40 crosses five thresholds
        self.assertTrue(enemy.is_staggered)
        self.assertEqual(enemy.stagger_turns_remaining, 2)
        self.assertEqual(enemy.stagger_level, 5)
        self.assertEqual(enemy.get_stagger_physical_resistance(), 4.0)

    def test_stagger_level_does_not_increase_on_second_turn(self):
        enemy = Enemy(hp=100, max_hp=100, stagger_thresholds=[70, 40])
        enemy.take_damage(31)  # first-turn stagger level becomes 1
        self.assertEqual(enemy.stagger_level, 1)

        enemy.tick_stagger_duration()  # now second stagger turn
        self.assertEqual(enemy.stagger_turns_remaining, 1)

        # Cross thresholds while on second stagger turn; level should not increase.
        enemy.heal(100)   # back to 100
        enemy.take_damage(80)  # 100 -> 20 crosses 70 and 40
        self.assertEqual(enemy.stagger_level, 1)
        self.assertEqual(enemy.get_stagger_physical_resistance(), 2.0)

    def test_convert_tremor_amplitude_requires_existing_tremor(self):
        enemy = Enemy(statuses={"tremor_potency": 10, "tremor_count": 0})
        applied = enemy.convert_tremor_amplitude("decay")
        self.assertFalse(applied)
        self.assertIsNone(enemy.get_status("tremor_type"))

    def test_convert_tremor_amplitude_overwrites_type_and_preserves_values(self):
        enemy = Enemy(
            statuses={
                "tremor_potency": 12,
                "tremor_count": 7,
                "tremor_type": "reverb",
            }
        )
        applied = enemy.convert_tremor_amplitude("chain")
        self.assertTrue(applied)
        self.assertEqual(enemy.get_status("tremor_type"), "chain")
        self.assertEqual(enemy.get_status("tremor_potency"), 12)
        self.assertEqual(enemy.get_status("tremor_count"), 7)

    def test_convert_tremor_amplitude_blocked_by_superposition_flag(self):
        enemy = Enemy(
            statuses={
                "tremor_potency": 10,
                "tremor_count": 3,
                "tremor_superposition": True,
                "tremor_type": "decay",
            }
        )
        applied = enemy.convert_tremor_amplitude("scorch")
        self.assertFalse(applied)
        self.assertEqual(enemy.get_status("tremor_type"), "decay")

    def test_convert_tremor_amplitude_blocked_by_superposition_type(self):
        enemy = Enemy(
            statuses={
                "tremor_potency": 10,
                "tremor_count": 3,
                "tremor_type": "superposition",
            }
        )
        applied = enemy.convert_tremor_amplitude("everlasting")
        self.assertFalse(applied)
        self.assertEqual(enemy.get_status("tremor_type"), "superposition")

    def test_tremor_decay_applies_def_level_down_on_conversion(self):
        enemy = Enemy(
            statuses={
                "tremor_potency": 10,
                "tremor_count": 3,
                "tremor_type": "reverb",
            }
        )
        self.assertEqual(enemy.tremor_decay_def_level_down(), 0)

        applied = enemy.convert_tremor_amplitude("decay")
        self.assertTrue(applied)
        self.assertEqual(enemy.tremor_decay_def_level_down(), 2)

    def test_tremor_decay_def_level_down_tracks_potency_change(self):
        enemy = Enemy(
            statuses={
                "tremor_potency": 8,
                "tremor_count": 3,
                "tremor_type": "decay",
            }
        )
        self.assertEqual(enemy.tremor_decay_def_level_down(), 2)

        enemy.set_status("tremor_potency", 15)
        self.assertEqual(enemy.tremor_decay_def_level_down(), 3)

    def test_tremor_decay_def_level_down_tracks_every_mutation_route(self):
        """
        Derived on read, so it follows potency however it changed — no
        recompute call anywhere in this test.
        """
        # Constructor-supplied statuses bypass every accessor.
        enemy = Enemy(
            statuses={
                "tremor_potency": 12,
                "tremor_count": 3,
                "tremor_type": "decay",
            }
        )
        self.assertEqual(enemy.tremor_decay_def_level_down(), 3)

        enemy.add_status("tremor_potency", 4)      # 16
        self.assertEqual(enemy.tremor_decay_def_level_down(), 4)

        enemy.set_status("tremor_potency", 8)
        self.assertEqual(enemy.tremor_decay_def_level_down(), 2)

        enemy.reduce_status("tremor_potency", 4)   # 4
        self.assertEqual(enemy.tremor_decay_def_level_down(), 1)

    def test_tremor_decay_def_level_down_removed_when_tremor_not_active(self):
        enemy = Enemy(
            statuses={
                "tremor_potency": 12,
                "tremor_count": 2,
                "tremor_type": "decay",
            }
        )
        self.assertEqual(enemy.tremor_decay_def_level_down(), 3)

        enemy.remove_status("tremor_count")
        self.assertEqual(enemy.tremor_decay_def_level_down(), 0)

    def test_tremor_decay_def_level_down_gone_on_conversion_away(self):
        """Converting out of decay drops the reduction immediately."""
        enemy = Enemy(
            statuses={
                "tremor_potency": 12,
                "tremor_count": 3,
                "tremor_type": "decay",
            }
        )
        self.assertEqual(enemy.tremor_decay_def_level_down(), 3)

        self.assertTrue(enemy.convert_tremor_amplitude("reverb"))
        self.assertEqual(enemy.tremor_decay_def_level_down(), 0)

    def test_tremor_decay_never_touches_the_def_lvl_down_status(self):
        """The two sources are independent; decay writes no status at all."""
        enemy = Enemy(
            statuses={
                "def_lvl_down": 5,
                "tremor_potency": 12,
                "tremor_count": 3,
                "tremor_type": "decay",
            }
        )
        self.assertEqual(enemy.tremor_decay_def_level_down(), 3)
        self.assertEqual(enemy.get_status("def_lvl_down", 0), 5)

        enemy.set_status("tremor_potency", 20)
        enemy.convert_tremor_amplitude("reverb")
        enemy.remove_status("tremor_count")

        self.assertEqual(enemy.tremor_decay_def_level_down(), 0)
        self.assertEqual(enemy.get_status("def_lvl_down", 0), 5)


# â”€â”€ Unit tests â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€


class TestUnit(unittest.TestCase):
    def test_inherits_enemy_fields(self):
        unit = Unit(name="Yi Sang", base_level=40, defense_level=-5, hp=200, max_hp=200)
        self.assertEqual(unit.effective_defense, 35)
        self.assertTrue(unit.is_alive)
        self.assertIsInstance(unit, Enemy)
        self.assertEqual(unit.effective_defense, 35)
        self.assertTrue(unit.is_alive)
        self.assertIsInstance(unit, Enemy)

    def test_has_skills_and_speed(self):
        skill = _make_skill(name="Slash")
        unit = Unit(name="Faust", skills=[skill], speed=7)
        self.assertEqual(len(unit.skills), 1)
        self.assertEqual(unit.speed, 7)

    def test_add_skill(self):
        unit = Unit(name="Faust")
        unit.add_skill(_make_skill(name="S1"))
        unit.add_skill(_make_skill(name="S2"))
        self.assertEqual(len(unit.skills), 2)

    def test_unit_inherits_stagger_threshold_logic(self):
        unit = Unit(name="Outis", hp=120, max_hp=120, stagger_thresholds=[80])
        self.assertFalse(unit.is_staggered)
        unit.take_damage(41)  # 120 -> 79 crosses below 80
        self.assertTrue(unit.is_staggered)

    def test_has_sanity_defaults_true(self):
        """Units always have sanity, unlike most enemies."""
        unit = Unit(name="Yi Sang")
        self.assertTrue(unit.has_sanity)


# â”€â”€ GameLoop with entities tests â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€


class TestGameLoopWithEntities(unittest.TestCase):
    def test_turn_end_enemy_cleanup_list_removes_registered_effects(self):
        """Enemy statuses listed in cleanup registry should be removed at turn end."""
        enemy = Enemy(name="CleanupDummy", statuses={"test_turn_end_cleanup": 3})
        loop = GameLoop(enemies=[enemy])

        TURN_END_EFFECTS_TO_CLEAR.add("test_turn_end_cleanup")
        try:
            loop.run_turn()
        finally:
            TURN_END_EFFECTS_TO_CLEAR.discard("test_turn_end_cleanup")

        self.assertFalse(enemy.has_status("test_turn_end_cleanup"))

    def test_turn_end_cleanup_clears_fragility_and_damage_up_family_from_both_sides(self):
        """Fragility/Damage Up/Speed Up variants clear from enemies AND units."""
        enemy = Enemy(
            name="Target",
            statuses={"fragility": 3, "wrath_fragility": 2, "slash_fragility": 1},
        )
        unit = Unit(
            name="Attacker",
            statuses={"dmg_up": 3, "pierce_dmg_up": 2, "max_speed_up": 4, "min_speed_up": 1},
        )
        loop = GameLoop(units=[unit], enemies=[enemy])
        loop.run_turn()

        for status_name in ("fragility", "wrath_fragility", "slash_fragility"):
            self.assertFalse(enemy.has_status(status_name))
        for status_name in ("dmg_up", "pierce_dmg_up", "max_speed_up", "min_speed_up"):
            self.assertFalse(unit.has_status(status_name))

    def test_turn_end_cleanup_clears_haste_and_bind_from_both_sides(self):
        enemy = Enemy(name="Target", statuses={"haste": 4, "bind": 2})
        unit = Unit(name="Attacker", statuses={"haste": 3, "bind": 1})
        loop = GameLoop(units=[unit], enemies=[enemy])
        loop.run_turn()

        self.assertFalse(enemy.has_status("haste"))
        self.assertFalse(enemy.has_status("bind"))
        self.assertFalse(unit.has_status("haste"))
        self.assertFalse(unit.has_status("bind"))

    def test_turn_end_cleanup_clears_off_and_def_level_from_both_sides(self):
        level_statuses = {
            "off_lvl_up": 3, "off_lvl_down": 2,
            "def_lvl_up": 4, "def_lvl_down": 1,
        }
        enemy = Enemy(name="Target", statuses=dict(level_statuses))
        unit = Unit(name="Attacker", statuses=dict(level_statuses))
        loop = GameLoop(units=[unit], enemies=[enemy])
        loop.run_turn()

        for status_name in level_statuses:
            self.assertFalse(enemy.has_status(status_name))
            self.assertFalse(unit.has_status(status_name))

    def test_tremor_decay_def_level_down_survives_turn_end(self):
        """
        Decay's reduction is derived, not a status, so the turn-end sweep
        that clears def_lvl_down leaves it applied for as long as Decay
        Tremor is active.
        """
        enemy = Enemy(
            name="DecayEnemy",
            statuses={
                "def_lvl_down": 4,
                "tremor_type": "decay",
                "tremor_potency": 12,
                "tremor_count": 3,
            },
        )
        loop = GameLoop(enemies=[enemy])
        loop.run_turn()

        self.assertFalse(enemy.has_status("def_lvl_down"))
        self.assertEqual(enemy.tremor_decay_def_level_down(), 3)

    def test_charge_barrier_full_lifecycle(self):
        """Charge Barrier grants Shield at turn start, then converts to
        Charge Count and expires at turn end â€” for both enemies and units."""
        enemy = Enemy(name="Target", statuses={"charge_barrier": 5})
        unit = Unit(name="Attacker", statuses={"charge_barrier": 4})
        loop = GameLoop(units=[unit], enemies=[enemy])

        loop.run_turn()

        # Shield = 3x barrier, granted at turn start and untouched since
        # nothing dealt damage this turn.
        self.assertEqual(enemy.shield, 15)
        self.assertEqual(unit.shield, 12)

        # Barrier converted 1:1 into Charge Count, then removed.
        self.assertFalse(enemy.has_status("charge_barrier"))
        self.assertFalse(unit.has_status("charge_barrier"))
        self.assertEqual(enemy.get_status("charge_count", 0), 5)
        self.assertEqual(unit.get_status("charge_count", 0), 4)

    def test_charge_barrier_shield_absorbs_damage_taken_during_the_turn(self):
        enemy = Enemy(name="Target", hp=100, max_hp=100, statuses={"charge_barrier": 10})
        loop = GameLoop(enemies=[enemy])
        loop._process_turn_start_statuses()

        self.assertEqual(enemy.shield, 30)
        actual = enemy.take_damage(25)
        self.assertEqual(actual, 0)
        self.assertEqual(enemy.hp, 100)
        self.assertEqual(enemy.shield, 5)

    def test_skills_collected_from_units(self):
        """Skills from Unit.skills should be used in the turn."""
        coin = _make_coin()
        skill = _make_skill(name="UnitSlash", speed=5, coins=[coin])
        unit = Unit(name="U1", skills=[skill])
        loop = GameLoop(units=[unit])
        results = loop.run_turn()
        self.assertEqual(len(results), 1)
        self.assertEqual(results[0]["skill"], "UnitSlash")

    def test_passive_fires_during_skill_phase(self):
        """A passive on a unit should fire at matching skill phases."""
        tracker: list[str] = []
        passive = Passive(
            name="BonusDmg",
            effects={
                SkillPhase.BEFORE_ATTACK: [
                    Effect(
                        name="passive_buff",
                        phase=SkillPhase.BEFORE_ATTACK,
                        apply=lambda ctx: tracker.append("passive_fired"),
                    )
                ]
            },
        )
        coin = _make_coin()
        skill = _make_skill(name="S", coins=[coin])
        unit = Unit(name="U", skills=[skill], passives=[passive])
        loop = GameLoop(units=[unit])
        loop.run_turn()
        self.assertIn("passive_fired", tracker)

    def test_passive_fires_during_coin_phase(self):
        """A passive with a CoinPhase tag should fire during coin resolution."""
        tracker: list[str] = []
        passive = Passive(
            name="OnHitBonus",
            effects={
                CoinPhase.ON_HIT: [
                    Effect(
                        name="hit_passive",
                        phase=CoinPhase.ON_HIT,
                        apply=lambda ctx: tracker.append("coin_passive"),
                    )
                ]
            },
        )
        coin = _make_coin()
        skill = _make_skill(name="S", coins=[coin])
        enemy = Enemy(name="E", passives=[passive])
        loop = GameLoop(skills=[skill], enemies=[enemy])
        loop.run_turn()
        self.assertIn("coin_passive", tracker)

    def test_coin_on_kill_passive_triggers_once_and_logs_effect_cap(self):
        """Coin ON_KILL passive should trigger once per kill and show effect cap in log."""
        tracker = {"n": 0}
        passive = Passive(name="KillCharge")
        passive.add_effect(
            Effect(
                name="On Kill: +3 Charge",
                phase=CoinPhase.ON_KILL,
                apply=lambda ctx: tracker.__setitem__("n", tracker["n"] + 1),
                max_procs=3,
            )
        )

        coin = _make_coin(name="Finisher", coin_power=0)
        skill = _make_skill(name="S", base_power=10, coins=[coin])
        unit = Unit(name="U", skills=[skill], passives=[passive])
        enemy = Enemy(name="E", hp=1, max_hp=1)

        loop = GameLoop(units=[unit], enemies=[enemy], sequence=["heads"])
        results = loop.run_turn()

        self.assertEqual(tracker["n"], 1)
        self.assertIn("[passive] KillCharge proc'd (1/3)", results[0]["log"])

    def test_passive_proc_limit_across_turn(self):
        """A passive with max_procs=1 should only fire once per turn."""
        counter = {"n": 0}
        passive = Passive(
            name="Once",
            effects={
                SkillPhase.BEFORE_ATTACK: [
                    Effect(
                        name="once_buff",
                        phase=SkillPhase.BEFORE_ATTACK,
                        apply=lambda ctx: counter.__setitem__("n", counter["n"] + 1),
                    )
                ]
            },
            max_procs=1,
        )
        c1, c2 = _make_coin(), _make_coin()
        s1 = _make_skill(name="S1", speed=5, coins=[c1])
        s2 = _make_skill(name="S2", speed=3, coins=[c2])
        unit = Unit(name="U", skills=[s1, s2], passives=[passive])
        loop = GameLoop(units=[unit])
        loop.run_turn()
        # Two skills resolve â†’ BEFORE_ATTACK fires twice, but passive procs only once
        self.assertEqual(counter["n"], 1)

    def test_passive_procs_reset_each_turn(self):
        """Passive proc counters should reset between turns."""
        counter = {"n": 0}
        passive = Passive(
            name="OncePerTurn",
            effects={
                SkillPhase.BEFORE_ATTACK: [
                    Effect(
                        name="buff",
                        phase=SkillPhase.BEFORE_ATTACK,
                        apply=lambda ctx: counter.__setitem__("n", counter["n"] + 1),
                    )
                ]
            },
            max_procs=1,
        )
        skill = _make_skill(name="S", speed=1, coins=[_make_coin()])
        unit = Unit(name="U", skills=[skill], passives=[passive])
        loop = GameLoop(units=[unit])
        loop.run_turn()
        loop.run_turn()
        # Should fire once per turn â†’ 2 total
        self.assertEqual(counter["n"], 2)

    def test_backward_compat_skills_list(self):
        """Passing skills directly (no units) should still work."""
        skill = _make_skill(name="Direct", speed=1, coins=[_make_coin()])
        loop = GameLoop(skills=[skill])
        results = loop.run_turn()
        self.assertEqual(len(results), 1)
        self.assertEqual(results[0]["skill"], "Direct")

    def test_mixed_skills_and_units(self):
        """Skills from both the skills list and units should merge."""
        direct = _make_skill(name="Direct", speed=1, coins=[_make_coin()])
        # speed=10 here is irrelevant to ordering now (only matters for a
        # skill with no resolvable owner) â€” the unit's own speed is what
        # decides "faster" for an owned skill.
        unit_skill = _make_skill(name="UnitSkill", speed=10, coins=[_make_coin()])
        unit = Unit(name="U", skills=[unit_skill], speed=10)
        loop = GameLoop(skills=[direct], units=[unit])
        results = loop.run_turn()
        self.assertEqual(len(results), 2)
        self.assertEqual(results[0]["skill"], "UnitSkill")  # faster (owner speed=10)
        self.assertEqual(results[1]["skill"], "Direct")

    def test_action_based_ordering_uses_owner_effective_speed(self):
        """
        Turn order via explicit Action pairs follows owner.effective_speed
        (so Haste/Bind apply) â€” not each skill's own (now-vestigial) speed
        field. Skill.speed values below are deliberately misleading: if
        ordering ever regressed to sorting by raw skill.speed, this would
        fail loudly.
        """
        slow_unit = Unit(name="Slow", speed=5)
        fast_unit = Unit(name="Fast", speed=3, statuses={"haste": 4})  # effective 7
        slow_skill = _make_skill(name="SlowAction", speed=99, coins=[_make_coin()])
        fast_skill = _make_skill(name="FastAction", speed=1, coins=[_make_coin()])
        loop = GameLoop(
            units=[slow_unit, fast_unit],
            actions=[
                Action(unit=slow_unit, skill=slow_skill),
                Action(unit=fast_unit, skill=fast_skill),
            ],
        )
        results = loop.run_turn()
        self.assertEqual(results[0]["skill"], "FastAction")  # effective_speed 7
        self.assertEqual(results[1]["skill"], "SlowAction")  # effective_speed 5
        # unit.skills (the permanent kit) is never touched by Action-based turns.
        self.assertEqual(slow_unit.skills, [])
        self.assertEqual(fast_unit.skills, [])

    def test_action_based_ordering_merges_with_standalone_skills(self):
        """Standalone (ownerless) skills still participate alongside Actions."""
        unit = Unit(name="U", speed=8)
        unit_skill = _make_skill(name="UnitSkill", coins=[_make_coin()])
        standalone = _make_skill(name="Standalone", speed=2, coins=[_make_coin()])
        loop = GameLoop(
            units=[unit],
            skills=[standalone],
            actions=[Action(unit=unit, skill=unit_skill)],
        )
        results = loop.run_turn()
        self.assertEqual(len(results), 2)
        self.assertEqual(results[0]["skill"], "UnitSkill")   # owner effective_speed=8
        self.assertEqual(results[1]["skill"], "Standalone")  # ownerless fallback speed=2

    def test_action_resolves_with_correct_owner_environment(self):
        """
        Resolving via Action builds the Environment against action.unit
        directly. Neither unit ever puts its skill in unit.skills, so this
        pairing could only have come from the Action, not a unit.skills scan.
        """
        unit_a = Unit(name="A", speed=5, sp=20)
        unit_b = Unit(name="B", speed=5, sp=0)
        skill_a = _make_skill(name="SkillA", coins=[_make_coin()])
        skill_b = _make_skill(name="SkillB", coins=[_make_coin()])
        enemy = Enemy(name="Target", hp=100, max_hp=100)
        loop = GameLoop(
            units=[unit_a, unit_b],
            enemies=[enemy],
            actions=[
                Action(unit=unit_a, skill=skill_a),
                Action(unit=unit_b, skill=skill_b),
            ],
        )
        loop.run_turn()
        envs_by_skill = {env.skill.name: env for env in loop.envs}
        self.assertIs(envs_by_skill["SkillA"].unit, unit_a)
        self.assertIs(envs_by_skill["SkillB"].unit, unit_b)

    def test_ryoshu_leap_no_starting_charge_does_not_gain_coin_power_bonus(self):
        """Leap should keep base coin power when starting below 10 charge."""
        unit = make_ryoshu_w_corp_l3_cleanup_agent(level=60)
        unit.remove_status("charge_count")
        unit.remove_status("charge_potency")

        enemy = Enemy(
            name="Dummy",
            base_level=55,
            defense_level=0,
            hp=240,
            max_hp=240,
            phys_res={"Slash": 1.0},
            sin_res={"Pride": 1.0},
        )

        leap = unit.get_skill_forms("2")[0]
        unit.skills = [leap]
        unit.speed = 5

        loop = GameLoop(
            units=[unit],
            enemies=[enemy],
            sequence=["heads"] * 20,
            is_debugging=True,
        )
        results = loop.run_turn()

        self.assertEqual(results[0]["skill"], "Leap")
        self.assertEqual(loop.envs[0].coin_power, 5)
        self.assertEqual(enemy.get_status("slash_fragility", 0), 0)


# â”€â”€ Core combat resolution tests â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€


class TestGameLoopCoreResolution(unittest.TestCase):
    def test_heads_vs_tails_branching(self):
        hits: list[str] = []

        def _mark(label: str):
            return Effect(
                name=label,
                phase=CoinPhase.ON_HIT_HEADS if label == "heads" else CoinPhase.ON_HIT_TAILS,
                apply=lambda _env, _l=label: hits.append(_l),
            )

        coin = Coin(name="BranchCoin", coin_power=0)
        coin.add_effect(_mark("heads"))
        coin.add_effect(_mark("tails"))

        skill = Skill(
            name="BranchSkill",
            base_power=1,
            coin_power=0,
            offense_level=1,
            damage_type=("Slash", "Wrath"),
            coins=[coin],
        )
        unit = Unit(name="BranchUser", skills=[skill])
        enemy = Enemy(name="BranchTarget", hp=50, max_hp=50)
        loop = GameLoop(units=[unit], enemies=[enemy], sequence=["heads"])
        loop.run_turn()
        self.assertEqual(hits, ["heads"])

        hits.clear()
        loop = GameLoop(units=[unit], enemies=[enemy], sequence=["tails"])
        loop.run_turn()
        self.assertEqual(hits, ["tails"])

    def test_crit_consumes_poise_and_triggers_crit_phase(self):
        hits: list[str] = []

        coin = Coin(name="CritCoin", coin_power=0)
        coin.add_effect(
            Effect(
                name="crit_heads",
                phase=CoinPhase.ON_CRIT_HEADS,
                apply=lambda _env: hits.append("crit_heads"),
            )
        )

        skill = Skill(
            name="CritSkill",
            base_power=1,
            coin_power=0,
            offense_level=1,
            damage_type=("Slash", "Wrath"),
            coins=[coin],
        )
        unit = Unit(name="CritUser", statuses={"poise_potency": 10, "poise_count": 1}, skills=[skill])
        enemy = Enemy(name="Dummy", hp=50, max_hp=50)

        with patch("src.game_loop.random.random", return_value=0.0):
            loop = GameLoop(units=[unit], enemies=[enemy], sequence=["heads"])
            loop.run_turn()

        self.assertEqual(unit.get_status("poise_count", 0), 0)
        self.assertEqual(hits, ["crit_heads"])
        self.assertTrue(loop.envs[0].did_crit)

    def test_cancel_coin_skips_damage_and_on_hit(self):
        hit_log: list[str] = []

        coin = Coin(name="CancelCoin", coin_power=0)
        coin.add_effect(
            Effect(
                name="cancel",
                phase=CoinPhase.COIN_START,
                apply=lambda env: setattr(env, "CANCEL_COIN", True),
            )
        )
        coin.add_effect(
            Effect(
                name="on_hit",
                phase=CoinPhase.ON_HIT,
                apply=lambda _env: hit_log.append("hit"),
            )
        )

        skill = Skill(
            name="CancelSkill",
            base_power=1,
            coin_power=0,
            offense_level=1,
            damage_type=("Slash", "Wrath"),
            coins=[coin],
        )
        loop = GameLoop(skills=[skill], sequence=["heads"])
        result = loop.run_turn()[0]

        self.assertEqual(result["coin_damages"], [])
        self.assertEqual(hit_log, [])

    def test_cancel_attack_skips_all_coins(self):
        cancel = Effect(
            name="cancel_attack",
            phase=SkillPhase.BEFORE_ATTACK,
            apply=lambda env: setattr(env, "CANCEL_ATTACK", True),
        )
        skill = Skill(
            name="CancelAttack",
            base_power=1,
            coin_power=0,
            offense_level=1,
            damage_type=("Slash", "Wrath"),
            coins=[_make_coin(), _make_coin()],
        )
        skill.add_effect(cancel)
        loop = GameLoop(skills=[skill], sequence=["heads", "heads"])
        result = loop.run_turn()[0]
        self.assertEqual(result["coin_damages"], [])


class TestProcBleed(unittest.TestCase):
    """Direct coverage of Environment.proc_bleed."""

    def _env(self, **kwargs) -> Environment:
        skill = Skill(
            name="S", base_power=1, coin_power=0, offense_level=1,
            damage_type=("Slash", "Wrath"), coins=[Coin(name="c")],
        )
        unit = Unit(name="U", speed=10)
        enemy = Enemy(name="E", hp=100, max_hp=100, sin_res={"Wrath": 1.0})
        return Environment.from_skill(skill, unit, enemy, **kwargs)

    def test_full_procs_when_not_lethal(self):
        env = self._env()
        e = Enemy(name="B", hp=100, max_hp=100,
                  statuses={"bleed_potency": 4, "bleed_count": 5})
        dealt = env.proc_bleed(e, 3, is_self=False)
        self.assertEqual(dealt, 12)
        self.assertEqual(e.get_status("bleed_count", 0), 2)
        self.assertEqual(env.status_damages.get("bleed", 0), 12)
        self.assertEqual(env.total, 12)

    def test_overkill_trims_to_procs_needed(self):
        env = self._env()
        e = Enemy(name="B", hp=12, max_hp=100,
                  statuses={"bleed_potency": 10, "bleed_count": 5})
        dealt = env.proc_bleed(e, 5, is_self=False)
        self.assertEqual(dealt, 12)          # HP lost, not 10*ceil
        self.assertFalse(e.is_alive)
        self.assertEqual(e.get_status("bleed_count", 0), 3)  # only 2 spent

    def test_shield_counts_toward_kill_threshold(self):
        env = self._env()
        e = Enemy(name="B", hp=4, max_hp=100, shield=6,
                  statuses={"bleed_potency": 5, "bleed_count": 4})
        dealt = env.proc_bleed(e, 3, is_self=False)
        self.assertFalse(e.is_alive)
        self.assertEqual(dealt, 4)           # 6 absorbed by shield, 4 to HP
        self.assertEqual(e.get_status("bleed_count", 0), 2)  # 2 procs killed

    def test_consume_bleed_false_keeps_count(self):
        env = self._env()
        env.CONSUME_BLEED = False
        e = Enemy(name="B", hp=100, max_hp=100,
                  statuses={"bleed_potency": 3, "bleed_count": 4})
        dealt = env.proc_bleed(e, 2, is_self=True)
        self.assertEqual(dealt, 6)
        self.assertEqual(e.get_status("bleed_count", 0), 4)   # not decremented
        self.assertEqual(env.self_damage.get("bleed", 0), 6)

    def test_is_self_routing(self):
        env = self._env()
        e = Enemy(name="B", hp=100, max_hp=100,
                  statuses={"bleed_potency": 2, "bleed_count": 9})
        env.proc_bleed(e, 1, is_self=True)
        self.assertEqual(env.self_damage.get("bleed", 0), 2)
        self.assertNotIn("bleed", env.status_damages)
        self.assertEqual(env.total, 0)

    def test_noop_paths(self):
        env = self._env()
        self.assertEqual(env.proc_bleed(None, 3, is_self=False), 0)
        e = Enemy(name="B", hp=100, max_hp=100, statuses={"bleed_count": 3})
        self.assertEqual(env.proc_bleed(e, 3, is_self=False), 0)  # potency 0
        self.assertEqual(e.get_status("bleed_count", 0), 3)
        e2 = Enemy(name="B2", hp=100, max_hp=100,
                   statuses={"bleed_potency": 5, "bleed_count": 2})
        self.assertEqual(env.proc_bleed(e2, 0, is_self=False), 0)  # 0 procs


class TestStatusOnHit(unittest.TestCase):
    def _run_single_coin(self, enemy: Enemy, *, clash_count: int = 0) -> dict:
        coin = Coin(name="Hit", coin_power=0)
        skill = Skill(
            name="StatusSkill",
            base_power=1,
            coin_power=0,
            offense_level=1,
            damage_type=("Slash", "Wrath"),
            coins=[coin],
        )
        unit = Unit(name="User", skills=[skill], speed=10)
        loop = GameLoop(
            units=[unit],
            enemies=[enemy],
            sequence=["heads"],
            is_debugging=True,
            clash_count=clash_count,
        )
        return loop.run_turn()[0]

    def test_rupture_on_hit_consumes_count(self):
        enemy = Enemy(
            name="Rupture",
            hp=100,
            max_hp=100,
            statuses={"rupture_potency": 5, "rupture_count": 2},
            sin_res={"Wrath": 1.0},
        )
        result = self._run_single_coin(enemy)

        self.assertEqual(result["status_damages"].get("rupture", 0), 5)
        self.assertEqual(enemy.get_status("rupture_count", 0), 1)

    def test_sinking_on_hit_consumes_count(self):
        enemy = Enemy(
            name="Sinking",
            hp=100,
            max_hp=100,
            statuses={"sinking_potency": 4, "sinking_count": 2},
            sin_res={"Gloom": 1.0},
        )
        result = self._run_single_coin(enemy)

        self.assertEqual(result["status_damages"].get("sinking", 0), 4)
        self.assertEqual(enemy.get_status("sinking_count", 0), 1)

    def test_sinking_on_hit_reduces_sanity_when_target_has_sanity(self):
        """A target with sanity loses sp instead of taking damage."""
        enemy = Enemy(
            name="Sinking",
            hp=100,
            max_hp=100,
            has_sanity=True,
            sp=10,
            statuses={"sinking_potency": 4, "sinking_count": 2},
            sin_res={"Gloom": 1.0},  # irrelevant to the sanity path â€” unresisted
        )
        result = self._run_single_coin(enemy)

        self.assertEqual(enemy.sp, 6)
        self.assertEqual(enemy.get_status("sinking_count", 0), 1)
        # No sinking damage â€” only sanity changed. (The coin's own 1
        # base_power hit still applies, same as it would with any target.)
        self.assertNotIn("sinking", result["status_damages"])
        self.assertEqual(enemy.hp, 99)

    def test_sinking_sanity_reduction_floors_at_negative_45(self):
        enemy = Enemy(
            name="Sinking",
            hp=100,
            max_hp=100,
            has_sanity=True,
            sp=-40,
            statuses={"sinking_potency": 10, "sinking_count": 1},
        )
        self._run_single_coin(enemy)

        self.assertEqual(enemy.sp, -45)

    # â”€â”€ Bleed: consumed by the unit THROWING coins, not the target â”€â”€â”€â”€â”€â”€
    def _run_owner_bleed(
        self,
        *,
        unit_statuses: dict | None = None,
        enemy_statuses: dict | None = None,
        coins: int = 1,
        unit_hp: int = 100,
        enemy_hp: int = 100,
        is_clashing: bool = False,
        clash_won: bool | None = None,
        clash_count: int = 0,
    ):
        skill_coins = [Coin(name=f"Hit{i}", coin_power=0) for i in range(coins)]
        skill = Skill(
            name="BleedSkill",
            base_power=1,
            coin_power=0,
            offense_level=1,
            damage_type=("Slash", "Wrath"),
            coins=skill_coins,
        )
        unit = Unit(
            name="Bleeder",
            skills=[skill],
            speed=10,
            hp=unit_hp,
            max_hp=100,
            statuses=dict(unit_statuses or {}),
        )
        enemy = Enemy(
            name="Target",
            hp=enemy_hp,
            max_hp=100,
            sin_res={"Wrath": 1.0},
            statuses=dict(enemy_statuses or {}),
        )
        loop = GameLoop(
            units=[unit],
            enemies=[enemy],
            sequence=["heads"] * max(coins, 1),
            is_debugging=True,
            is_clashing=is_clashing,
            clash_won=clash_won,
            clash_count=clash_count,
        )
        result = loop.run_turn()[0]
        return unit, enemy, result, loop

    def test_bleed_procs_on_owner_once_per_coin(self):
        unit, enemy, result, _ = self._run_owner_bleed(
            unit_statuses={"bleed_potency": 3, "bleed_count": 4}, coins=2,
        )
        self.assertEqual(result["self_damage"].get("bleed", 0), 6)  # 2 coins x 3
        self.assertEqual(unit.get_status("bleed_count", 0), 2)
        self.assertEqual(unit.hp, 94)
        self.assertNotIn("bleed", result["status_damages"])
        self.assertEqual(result["total_damage"], sum(result["coin_damages"]))
        self.assertEqual(enemy.hp, 100 - sum(result["coin_damages"]))

    def test_bleed_per_coin_stops_when_count_exhausted(self):
        unit, _enemy, result, _ = self._run_owner_bleed(
            unit_statuses={"bleed_potency": 5, "bleed_count": 1}, coins=3,
        )
        self.assertEqual(result["self_damage"].get("bleed", 0), 5)
        self.assertEqual(unit.hp, 95)
        self.assertFalse(unit.has_status("bleed_count"))
        self.assertFalse(unit.has_status("bleed_potency"))

    def test_bleed_zero_potency_is_noop(self):
        unit, _enemy, result, _ = self._run_owner_bleed(
            unit_statuses={"bleed_count": 3}, coins=2,
        )
        self.assertNotIn("bleed", result["self_damage"])
        self.assertEqual(unit.get_status("bleed_count", 0), 3)
        self.assertEqual(unit.hp, 100)

    def test_landing_coin_does_not_consume_target_bleed(self):
        _unit, enemy, result, _ = self._run_owner_bleed(
            enemy_statuses={"bleed_potency": 3, "bleed_count": 4}, coins=2,
        )
        self.assertNotIn("bleed", result["status_damages"])
        self.assertEqual(enemy.get_status("bleed_count", 0), 4)
        self.assertEqual(enemy.get_status("bleed_potency", 0), 3)

    def test_clash_bleed_procs_target_before_coins(self):
        _unit, enemy, result, _ = self._run_owner_bleed(
            enemy_statuses={"bleed_potency": 4, "bleed_count": 5},
            coins=1, is_clashing=True, clash_won=True, clash_count=3,
        )
        self.assertEqual(enemy.get_status("bleed_count", 0), 2)  # 5 - min(3, 5)
        self.assertEqual(result["status_damages"].get("bleed", 0), 12)  # 4 x 3
        self.assertEqual(result["total_damage"], 12 + sum(result["coin_damages"]))

    def test_clash_bleed_procs_owner_then_per_coin(self):
        unit, _enemy, result, _ = self._run_owner_bleed(
            unit_statuses={"bleed_potency": 2, "bleed_count": 5},
            coins=1, is_clashing=True, clash_won=True, clash_count=3,
        )
        # clash: min(3, 5) = 3 procs (5 -> 2, 6 dmg); then 1 coin: 1 proc (2 -> 1, 2 dmg)
        self.assertEqual(unit.get_status("bleed_count", 0), 1)
        self.assertEqual(result["self_damage"].get("bleed", 0), 8)
        self.assertEqual(unit.hp, 92)
        self.assertNotIn("bleed", result["status_damages"])

    def test_clash_bleed_capped_by_own_count(self):
        _unit, enemy, result, _ = self._run_owner_bleed(
            enemy_statuses={"bleed_potency": 4, "bleed_count": 2},
            coins=1, is_clashing=True, clash_won=False, clash_count=9,
        )
        self.assertFalse(enemy.has_status("bleed_count"))  # min(9, 2) = 2 -> 0
        self.assertEqual(result["status_damages"].get("bleed", 0), 8)

    def test_clash_bleed_noop_when_clash_count_zero(self):
        unit, _enemy, result, _ = self._run_owner_bleed(
            unit_statuses={"bleed_potency": 5, "bleed_count": 3},
            coins=1, is_clashing=True, clash_won=True, clash_count=0,
        )
        self.assertEqual(unit.get_status("bleed_count", 0), 2)  # clash no-op; 1 coin proc
        self.assertEqual(result["self_damage"].get("bleed", 0), 5)

    def test_clash_bleed_full_spend_when_not_lethal(self):
        _unit, enemy, result, _ = self._run_owner_bleed(
            enemy_statuses={"bleed_potency": 10, "bleed_count": 5},
            coins=1, is_clashing=True, clash_won=True, clash_count=4,
        )
        self.assertEqual(result["status_damages"].get("bleed", 0), 40)  # 10 x min(4, 5)
        self.assertEqual(enemy.get_status("bleed_count", 0), 1)

    def test_clash_bleed_lethal_trims_procs_and_ends_skill(self):
        _unit, enemy, result, loop = self._run_owner_bleed(
            enemy_statuses={"bleed_potency": 10, "bleed_count": 5},
            enemy_hp=12, coins=3, is_clashing=True, clash_won=True, clash_count=4,
        )
        self.assertFalse(enemy.is_alive)
        # only 2 procs needed to kill (12 hp / 10), not the full budget of 4
        self.assertEqual(result["status_damages"].get("bleed", 0), 12)
        self.assertEqual(enemy.get_status("bleed_count", 0), 3)  # 5 - 2
        self.assertEqual(result["coin_damages"], [])
        self.assertTrue(loop.envs[0].target_killed)

    def test_owner_death_by_clash_bleed_cancels_attack(self):
        unit, enemy, result, loop = self._run_owner_bleed(
            unit_statuses={"bleed_potency": 10, "bleed_count": 3},
            coins=3, unit_hp=15, is_clashing=True, clash_won=True, clash_count=2,
        )
        self.assertFalse(unit.is_alive)  # 15 - 10*2 -> 0
        self.assertEqual(result["coin_damages"], [])
        self.assertEqual(enemy.hp, 100)
        self.assertTrue(loop.envs[0].CANCEL_ATTACK)

    def test_owner_death_by_per_coin_bleed_stops_remaining_coins(self):
        unit, _enemy, result, loop = self._run_owner_bleed(
            unit_statuses={"bleed_potency": 3, "bleed_count": 5},
            coins=4, unit_hp=6,
        )
        self.assertFalse(unit.is_alive)  # coin1 -> 3, coin2 -> 0
        self.assertEqual(len(result["coin_damages"]), 1)
        self.assertEqual(result["self_damage"].get("bleed", 0), 6)
        self.assertTrue(loop.envs[0].CANCEL_ATTACK)

    def test_reused_coin_also_procs_owner_bleed(self):
        coin = Coin(name="ReuseHit", coin_power=0)
        coin.add_reuse_condition(
            ReuseCondition(name="Always", condition=lambda _env: True, max_reuses=1)
        )
        skill = Skill(
            name="ReuseBleed", base_power=1, coin_power=0, offense_level=1,
            damage_type=("Slash", "Wrath"), coins=[coin],
        )
        unit = Unit(
            name="Bleeder", skills=[skill], speed=10, hp=100, max_hp=100,
            statuses={"bleed_potency": 3, "bleed_count": 5},
        )
        enemy = Enemy(name="Target", hp=100, max_hp=100, sin_res={"Wrath": 1.0})
        loop = GameLoop(
            units=[unit], enemies=[enemy], sequence=["heads", "heads"],
            is_debugging=True,
        )
        result = loop.run_turn()[0]
        self.assertEqual(result["self_damage"].get("bleed", 0), 6)  # original + 1 reuse
        self.assertEqual(unit.get_status("bleed_count", 0), 3)

    def test_deathrite_haste_trigger_on_rupture(self):
        enemy = Enemy(
            name="Deathrite",
            hp=100,
            max_hp=100,
            sin_res={"Gluttony": 1.0},
            statuses={
                "rupture_potency": 4,
                "rupture_count": 1,
                "deathrite_haste": 1,
            },
        )
        result = self._run_single_coin(enemy)

        self.assertEqual(result["status_damages"].get("deathrite", 0), 4)
        self.assertFalse(enemy.has_status("deathrite_haste"))

    def test_rupture_on_hit_removes_potency_when_count_reaches_zero(self):
        """Regression: rupture_potency used to leak past rupture_count hitting 0."""
        enemy = Enemy(
            name="Rupture",
            hp=100,
            max_hp=100,
            statuses={"rupture_potency": 5, "rupture_count": 1},
            sin_res={"Wrath": 1.0},
        )
        self._run_single_coin(enemy)

        self.assertFalse(enemy.has_status("rupture_count"))
        self.assertFalse(enemy.has_status("rupture_potency"))

    def test_consume_rupture_false_still_deals_damage_keeps_count(self):
        enemy = Enemy(
            name="Rupture",
            hp=100,
            max_hp=100,
            statuses={"rupture_potency": 5, "rupture_count": 3, "deathrite_haste": 2},
            sin_res={"Wrath": 1.0},
        )
        coin = Coin(name="Hit", coin_power=0)
        skill = Skill(
            name="StatusSkill", base_power=1, coin_power=0, offense_level=1,
            damage_type=("Slash", "Wrath"), coins=[coin],
        )
        skill.add_effect(Effect(
            name="disable_rupture_consume",
            phase=SkillPhase.ON_USE,
            apply=lambda env: setattr(env, "CONSUME_RUPTURE", False),
        ))
        unit = Unit(name="User", skills=[skill], speed=10)
        loop = GameLoop(units=[unit], enemies=[enemy], sequence=["heads"], is_debugging=True)
        result = loop.run_turn()[0]

        self.assertEqual(result["status_damages"].get("rupture", 0), 5)  # damage still lands
        self.assertEqual(enemy.get_status("rupture_count", 0), 3)        # count untouched
        self.assertEqual(enemy.get_status("deathrite_haste", 0), 2)      # rider did not fire

    def test_sinking_on_hit_removes_potency_when_count_reaches_zero_without_sanity(self):
        enemy = Enemy(
            name="Sinking",
            hp=100,
            max_hp=100,
            statuses={"sinking_potency": 4, "sinking_count": 1},
            sin_res={"Gloom": 1.0},
        )
        self._run_single_coin(enemy)

        self.assertFalse(enemy.has_status("sinking_count"))
        self.assertFalse(enemy.has_status("sinking_potency"))

    def test_sinking_on_hit_removes_potency_when_count_reaches_zero_with_sanity(self):
        """Regression: the sanity branch shares the same consumption block."""
        enemy = Enemy(
            name="Sinking",
            hp=100,
            max_hp=100,
            has_sanity=True,
            sp=10,
            statuses={"sinking_potency": 4, "sinking_count": 1},
        )
        self._run_single_coin(enemy)

        self.assertFalse(enemy.has_status("sinking_count"))
        self.assertFalse(enemy.has_status("sinking_potency"))


class TestReuseResolution(unittest.TestCase):
    def test_coin_reuse_adds_second_resolution(self):
        coin = Coin(name="Reuse", coin_power=0)
        coin.add_reuse_condition(
            ReuseCondition(name="Always", condition=lambda _env: True, max_reuses=1)
        )
        skill = Skill(
            name="ReuseSkill",
            base_power=1,
            coin_power=0,
            offense_level=1,
            damage_type=("Slash", "Wrath"),
            coins=[coin],
        )
        unit = Unit(name="User", skills=[skill])
        enemy = Enemy(name="Target", hp=50, max_hp=50)

        loop = GameLoop(units=[unit], enemies=[enemy], sequence=["heads", "heads"])
        result = loop.run_turn()[0]

        self.assertEqual(coin.reuse_count, 1)
        self.assertEqual(len(result["coin_damages"]), 2)


class TestEnvironmentEventsAndDebug(unittest.TestCase):
    def test_event_hooks_execute_and_flush_queue(self):
        tracker: list[str] = []

        class Queued:
            def execute(self, _env):
                tracker.append("queued")

        class EventEffect:
            def on_poise_gained(self, _effect, env, _potency, _count):
                tracker.append("poise")
                env.queue_effect(Queued())

            def on_status_applied(self, _effect, _env, name, _potency, _count):
                tracker.append(f"status:{name}")

            def on_tremor_burst(self, _effect, _env):
                tracker.append("burst")

        env = Environment()
        env.enemy = Enemy(
            name="Target",
            hp=100,
            max_hp=100,
            stagger_thresholds=[80],
            statuses={"tremor_potency": 5, "tremor_count": 2},
            sin_res={"Sloth": 1.0},
        )
        effect = EventEffect()
        env.add_active_effect(effect, data=None, duration=-1)

        env.on_poise_gained(2, 1)
        env.on_status_applied("burn", 3, 1)
        env.on_tremor_burst()

        self.assertIn("poise", tracker)
        self.assertIn("queued", tracker)
        self.assertIn("status:burn", tracker)
        self.assertIn("burst", tracker)

    def test_debug_coin_string_contains_summary(self):
        env = Environment()
        env.current_power = 3
        env.current_damage = 2
        env.coin_power = 1
        env.skill = Skill(name="Debug", damage_type=("Slash", "Wrath"))
        env.enemy = Enemy(name="Target", hp=10, max_hp=10)
        env.static = 1.0
        env.dynamic = 1.0

        summary = env.debug_coin(0)
        self.assertIn("Coin 1", summary)
        self.assertIn("power=3", summary)


class TestTurnEndStatusProcessing(unittest.TestCase):
    def test_turn_end_ticks_burn_charge_tremor_poise(self):
        enemy = Enemy(
            name="Enemy",
            hp=50,
            max_hp=50,
            statuses={
                "burn_potency": 5,
                "burn_count": 2,
                "charge_count": 2,
                "tremor_potency": 7,
                "tremor_count": 1,
            },
        )
        unit = Unit(
            name="Unit",
            hp=40,
            max_hp=40,
            statuses={
                "poise_potency": 3,
                "poise_count": 2,
                "burn_potency": 4,
                "burn_count": 1,
                "charge_count": 1,
            },
        )
        loop = GameLoop(units=[unit], enemies=[enemy])
        loop.run_turn()

        self.assertEqual(enemy.hp, 45)
        self.assertEqual(enemy.get_status("burn_count", 0), 1)
        self.assertEqual(enemy.get_status("charge_count", 0), 1)
        self.assertFalse(enemy.has_status("tremor_count"))
        self.assertFalse(enemy.has_status("tremor_potency"))

        self.assertEqual(unit.get_status("poise_count", 0), 1)
        self.assertFalse(unit.has_status("burn_count"))
        self.assertFalse(unit.has_status("burn_potency"))
        self.assertFalse(unit.has_status("charge_count"))

    def test_turn_end_ticks_poise_on_enemies_too(self):
        """Poise turn-end decay now runs for enemies, not just units."""
        enemy = Enemy(
            name="Enemy",
            hp=50,
            max_hp=50,
            statuses={"poise_potency": 4, "poise_count": 2},
        )
        loop = GameLoop(units=[], enemies=[enemy])
        loop.run_turn()

        self.assertEqual(enemy.get_status("poise_count", 0), 1)
        self.assertEqual(enemy.get_status("poise_potency", 0), 4)

    def test_turn_end_poise_on_enemy_clears_both_at_zero(self):
        enemy = Enemy(
            name="Enemy",
            hp=50,
            max_hp=50,
            statuses={"poise_potency": 4, "poise_count": 1},
        )
        loop = GameLoop(units=[], enemies=[enemy])
        loop.run_turn()

        self.assertFalse(enemy.has_status("poise_count"))
        self.assertFalse(enemy.has_status("poise_potency"))


# â”€â”€ Entity / Unit helpers â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€


class TestEnemyHelpers(unittest.TestCase):
    def test_add_charge_count_sets_potency_if_missing(self):
        """Charge Count gain now goes through the general add_status rule."""
        enemy = Enemy(statuses={})
        enemy.add_status("charge_count", 2)
        self.assertEqual(enemy.get_status("charge_count", 0), 2)
        self.assertEqual(enemy.get_status("charge_potency", 0), 1)

    def test_is_tremor_superposition_true_for_flag_and_type(self):
        enemy = Enemy(statuses={"tremor_superposition": True})
        self.assertTrue(enemy.is_tremor_superposition())

        enemy = Enemy(statuses={"tremor_type": "superposition"})
        self.assertTrue(enemy.is_tremor_superposition())

    def test_apply_stagger_sets_turns(self):
        enemy = Enemy(hp=50, max_hp=50)
        enemy.apply_stagger(turns=2)
        self.assertTrue(enemy.is_staggered)
        self.assertEqual(enemy.stagger_turns_remaining, 2)

    def test_add_status_creates_fresh_when_absent(self):
        enemy = Enemy(statuses={})
        result = enemy.add_status("rupture_potency", 3)
        self.assertEqual(result, 3)
        self.assertEqual(enemy.get_status("rupture_potency", 0), 3)

    def test_add_status_adds_onto_existing_value(self):
        enemy = Enemy(statuses={"rupture_potency": 4})
        result = enemy.add_status("rupture_potency", 3)
        self.assertEqual(result, 7)
        self.assertEqual(enemy.get_status("rupture_potency", 0), 7)

    def test_add_status_clamps_at_cap(self):
        enemy = Enemy(statuses={"tremor_count": 97})
        result = enemy.add_status("tremor_count", 5, cap=99)
        self.assertEqual(result, 99)
        self.assertEqual(enemy.get_status("tremor_count", 0), 99)

    def test_add_status_works_on_unit_too(self):
        """add_status is inherited by Unit from Enemy."""
        unit = Unit(name="U", statuses={})
        result = unit.add_status("burn_count", 2)
        self.assertEqual(result, 2)
        self.assertEqual(unit.get_status("burn_count", 0), 2)

    def test_add_status_initializes_missing_partner_count_to_one(self):
        enemy = Enemy(statuses={})
        enemy.add_status("rupture_potency", 3)
        self.assertEqual(enemy.get_status("rupture_potency", 0), 3)
        self.assertEqual(enemy.get_status("rupture_count", 0), 1)

    def test_add_status_initializes_missing_partner_potency_to_one(self):
        """The reverse direction: gaining count alone initializes potency too."""
        enemy = Enemy(statuses={})
        enemy.add_status("tremor_count", 4)
        self.assertEqual(enemy.get_status("tremor_count", 0), 4)
        self.assertEqual(enemy.get_status("tremor_potency", 0), 1)

    def test_add_status_does_not_overwrite_an_existing_positive_partner(self):
        enemy = Enemy(statuses={"burn_potency": 7})
        enemy.add_status("burn_count", 2)
        self.assertEqual(enemy.get_status("burn_potency", 0), 7)

    def test_add_status_init_partner_false_stays_one_sided(self):
        enemy = Enemy(statuses={})
        enemy.add_status("bleed_potency", 3, init_partner=False)
        self.assertEqual(enemy.get_status("bleed_potency", 0), 3)
        self.assertFalse(enemy.has_status("bleed_count"))

    def test_add_status_does_nothing_extra_for_non_paired_status(self):
        enemy = Enemy(statuses={})
        enemy.add_status("tigermark_round", 1)
        self.assertEqual(enemy.get_status("tigermark_round", 0), 1)

    def test_reduce_status_reduces_without_removing(self):
        enemy = Enemy(statuses={"tigermark_round": 3})
        result = enemy.reduce_status("tigermark_round", 1)
        self.assertEqual(result, 2)
        self.assertEqual(enemy.get_status("tigermark_round", 0), 2)

    def test_reduce_status_removes_non_paired_status_at_zero(self):
        enemy = Enemy(statuses={"tigermark_round": 1})
        result = enemy.reduce_status("tigermark_round", 1)
        self.assertEqual(result, 0)
        self.assertFalse(enemy.has_status("tigermark_round"))

    def test_reduce_status_floors_at_zero_instead_of_going_negative(self):
        enemy = Enemy(statuses={"tigermark_round": 2})
        result = enemy.reduce_status("tigermark_round", 10)
        self.assertEqual(result, 0)
        self.assertFalse(enemy.has_status("tigermark_round"))

    def test_reduce_status_removes_paired_potency_when_count_hits_zero(self):
        enemy = Enemy(statuses={"rupture_potency": 5, "rupture_count": 1})
        enemy.reduce_status("rupture_count", 1)
        self.assertFalse(enemy.has_status("rupture_count"))
        self.assertFalse(enemy.has_status("rupture_potency"))

    def test_reduce_status_removes_paired_count_when_potency_hits_zero(self):
        """Reducing either side of a pair triggers the same cleanup."""
        enemy = Enemy(statuses={"rupture_potency": 1, "rupture_count": 5})
        enemy.reduce_status("rupture_potency", 1)
        self.assertFalse(enemy.has_status("rupture_potency"))
        self.assertFalse(enemy.has_status("rupture_count"))

    def test_reduce_status_cleanup_false_leaves_partner_untouched(self):
        """Charge's case: potency keeps tracking independently of count."""
        enemy = Enemy(statuses={"charge_potency": 3, "charge_count": 1})
        enemy.reduce_status("charge_count", 1, cleanup=False)
        self.assertFalse(enemy.has_status("charge_count"))
        self.assertEqual(enemy.get_status("charge_potency", 0), 3)

    def test_reduce_status_pairs_poise_potency_with_poise_count(self):
        enemy = Enemy(statuses={"poise_potency": 2, "poise_count": 1})
        enemy.reduce_status("poise_count", 1)
        self.assertFalse(enemy.has_status("poise_count"))
        self.assertFalse(enemy.has_status("poise_potency"))


class TestUnitHelpers(unittest.TestCase):
    def test_add_skill_with_slot(self):
        unit = Unit(name="U")
        skill = _make_skill(name="S1")
        unit.add_skill(skill, slot="2")
        self.assertIn(skill, unit.skills)
        self.assertIn(skill, unit.get_skill_forms("2"))

    def test_sync_skills_from_slots_orders_by_slot(self):
        s1 = _make_skill(name="S1")
        s2 = _make_skill(name="S2")
        s3 = _make_skill(name="S3")
        unit = Unit(name="U")
        unit.skill_slots["1"] = [s1]
        unit.skill_slots["3"] = [s3]
        unit.skill_slots["2"] = [s2]
        unit.sync_skills_from_slots()
        self.assertEqual([s.name for s in unit.skills], ["S1", "S2", "S3"])

    def test_roll_speed_uses_rng(self):
        class DummyRng:
            def randint(self, _low, _high):
                return 5

        unit = Unit(name="U", speed_min=3, speed_max=7)
        unit.roll_speed(rng=DummyRng())
        self.assertEqual(unit.speed, 5)

    def test_speed_defaults_to_min(self):
        unit = Unit(name="U", speed_min=3, speed_max=6, speed=0)
        self.assertEqual(unit.speed, 3)

    def test_roll_speed_max_speed_up_raises_ceiling(self):
        class RecordingRng:
            def randint(self, low, high):
                self.seen = (low, high)
                return high

        unit = Unit(name="U", speed_min=3, speed_max=7, statuses={"max_speed_up": 4})
        rng = RecordingRng()
        unit.roll_speed(rng=rng)
        self.assertEqual(rng.seen, (3, 11))
        self.assertEqual(unit.speed, 11)

    def test_roll_speed_min_speed_up_raises_floor(self):
        class RecordingRng:
            def randint(self, low, high):
                self.seen = (low, high)
                return low

        unit = Unit(name="U", speed_min=3, speed_max=7, statuses={"min_speed_up": 6})
        rng = RecordingRng()
        unit.roll_speed(rng=rng)
        # Floor raised to 9, which also lifts the ceiling since it can't be lower.
        self.assertEqual(rng.seen, (9, 9))
        self.assertEqual(unit.speed, 9)


# â”€â”€ Utils helpers â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€


class TestUtilsHelpers(unittest.TestCase):
    def test_add_status_env_initializes_poise_and_clears_at_zero(self):
        """add_poise_count was removed: this is now plain add_status/reduce_status."""
        unit = Unit(name="U")
        env = Environment(unit=unit)

        add_status(env, "poise_count", 2)
        self.assertEqual(unit.get_status("poise_count", 0), 2)
        self.assertEqual(unit.get_status("poise_potency", 0), 1)

        add_status(env, "poise_count", -2)
        self.assertEqual(unit.get_status("poise_count", 0), 0)
        self.assertEqual(unit.get_status("poise_potency", 0), 0)
        self.assertFalse(unit.has_status("poise_potency"))

    def test_consume_charge_count_tracks_total_without_flag(self):
        """Every unit tracks lifetime consumption, even without the potency flag."""
        unit = Unit(name="U", statuses={"charge_count": 12})
        env = Environment(unit=unit)

        consumed = consume_charge_count(env, 5)

        self.assertEqual(consumed, 5)
        self.assertEqual(unit.get_status("charge_count", 0), 7)
        self.assertEqual(unit.charge_consumed_total, 5)
        self.assertEqual(unit.get_status("charge_potency", 0), 0)

    def test_consume_charge_count_caps_at_available_amount(self):
        unit = Unit(name="U", statuses={"charge_count": 4})
        env = Environment(unit=unit)

        consumed = consume_charge_count(env, 15)

        self.assertEqual(consumed, 4)
        self.assertFalse(unit.has_status("charge_count"))
        self.assertEqual(unit.charge_consumed_total, 4)

    def test_consume_charge_count_grants_potency_every_ten_with_flag(self):
        """+1 Charge Potency per 10 cumulative consumed, only with the identity flag."""
        unit = Unit(
            name="U",
            statuses={"charge_count": 30},
            gains_charge_potency_on_consume=True,
        )
        env = Environment(unit=unit)

        consume_charge_count(env, 9)
        self.assertEqual(unit.charge_consumed_total, 9)
        self.assertEqual(unit.get_status("charge_potency", 0), 0)

        # Crossing the 10-consumed threshold grants +1, with the 1 extra
        # (11 total consumed so far) carrying toward the next threshold.
        consume_charge_count(env, 2)
        self.assertEqual(unit.charge_consumed_total, 11)
        self.assertEqual(unit.get_status("charge_potency", 0), 1)

        # Consuming the remaining 19 crosses two more thresholds (20, 30).
        consume_charge_count(env, 19)
        self.assertEqual(unit.charge_consumed_total, 30)
        self.assertEqual(unit.get_status("charge_potency", 0), 3)

    def test_add_status_prefers_unit(self):
        unit = Unit(name="U")
        enemy = Enemy(name="E")
        env = Environment(unit=unit, enemy=enemy)
        add_status(env, "charge_count", 2)
        self.assertEqual(unit.get_status("charge_count", 0), 2)
        self.assertFalse(enemy.has_status("charge_count"))

    def test_set_status_removes_at_zero(self):
        unit = Unit(name="U", statuses={"burn_count": 2})
        env = Environment(unit=unit)
        set_status(env, "burn_count", 0)
        self.assertFalse(unit.has_status("burn_count"))

    def test_add_enemy_status_updates_enemy(self):
        enemy = Enemy(name="E")
        env = Environment(enemy=enemy)
        add_enemy_status(env, "rupture_count", 3)
        self.assertEqual(enemy.get_status("rupture_count", 0), 3)

    def test_add_dynamic_and_coin_power(self):
        env = Environment()
        env.dynamic = 1.0
        add_dynamic(env, 0.5)
        add_coin_power(env, 2)
        self.assertAlmostEqual(env.dynamic, 1.5)
        self.assertEqual(env.coin_power, 2)

    def test_check_count_and_enemy_hp_below(self):
        unit = Unit(name="U", statuses={"charge_count": 5})
        enemy = Enemy(name="E", hp=40, max_hp=100)
        env = Environment(unit=unit, enemy=enemy)
        self.assertTrue(check_count(env, "charge_count", 3))
        self.assertFalse(check_count(env, "charge_count", 6))
        self.assertTrue(check_enemy_hp_below(env, 0.5))
        self.assertFalse(check_enemy_hp_below(env, 0.2))

    def test_deal_bonus_damage_from_current(self):
        enemy = Enemy(name="E", hp=50, max_hp=50)
        env = Environment(enemy=enemy)
        env.current_damage = 10
        deal_bonus_damage_from_current(env, 0.3, source="bonus")
        self.assertEqual(enemy.hp, 47)
        self.assertEqual(env.total, 3)
        self.assertEqual(env.status_damages.get("bonus", 0), 3)

    def test_check_speed_advantage_uses_effective_speed_with_haste(self):
        """Haste should count toward speed-advantage checks, not just raw speed."""
        unit = Unit(name="U", speed=4, statuses={"haste": 3})  # effective 7
        enemy = Enemy(name="E", speed=5)
        env = Environment(unit=unit, enemy=enemy)

        # Raw speed (4 vs 5) would give no advantage; Haste flips it to +2.
        self.assertEqual(check_speed_advantage(env, difference_per_step=1, max_steps=5), 2)

    def test_check_speed_advantage_uses_effective_speed_with_bind(self):
        """Bind should reduce speed-advantage checks, not just raw speed."""
        unit = Unit(name="U", speed=8, statuses={"bind": 5})  # effective 3
        enemy = Enemy(name="E", speed=5)
        env = Environment(unit=unit, enemy=enemy)

        # Raw speed (8 vs 5) would give +3; Bind drops it to a loss (0 steps).
        self.assertEqual(check_speed_advantage(env, difference_per_step=1, max_steps=5), 0)

    def test_apply_speed_based_coin_power_scales_with_haste(self):
        unit = Unit(name="U", speed=4, statuses={"haste": 3})  # effective 7
        enemy = Enemy(name="E", speed=5)
        env = Environment(unit=unit, enemy=enemy)

        apply_speed_based_coin_power(env, difference_per_step=1, max_steps=5, power_per_step=1)

        self.assertEqual(env.coin_power, 2)

# â”€â”€ CombatContext â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€


class TestCombatContext(unittest.TestCase):
    def test_add_damage_and_reset(self):
        ctx = CombatContext()
        skill = Skill(name="S", base_power=5)
        ctx.reset_for_skill(skill)
        self.assertEqual(ctx.final_damage, 5)
        ctx.add_damage(3, source="bonus")
        self.assertEqual(ctx.final_damage, 8)
        self.assertIn("[damage] bonus: +3", ctx.log)

    def test_reset_for_coin(self):
        ctx = CombatContext()
        coin = Coin(name="C")
        ctx.current_coin = coin
        ctx.coin_result = "heads"
        ctx.is_crit = True
        ctx.target_killed = True
        ctx.reset_for_coin(coin)
        self.assertIs(ctx.current_coin, coin)
        self.assertIsNone(ctx.coin_result)
        self.assertFalse(ctx.is_crit)
        self.assertFalse(ctx.target_killed)


# â”€â”€ Terminal frontend helpers â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€


class TestTerminalFrontend(unittest.TestCase):
    def test_format_statuses(self):
        enemy = Enemy(statuses={"b": 2, "a": 1})
        formatted = terminal_frontend._format_statuses(enemy)
        self.assertEqual(formatted, "a=1, b=2")

    def test_get_player_skill_options_uses_first_form(self):
        s1 = _make_skill(name="S1")
        s1b = _make_skill(name="S1b")
        s2 = _make_skill(name="S2")
        unit = Unit(name="U")
        unit.skill_slots["1"] = [s1, s1b]
        unit.skill_slots["2"] = [s2]
        unit.sync_skills_from_slots()
        options = terminal_frontend._get_player_skill_options(unit)
        self.assertEqual([opt[2].name for opt in options], ["S1", "S2"])

    def test_safe_input_returns_none_on_eof(self):
        with patch("builtins.input", side_effect=EOFError):
            self.assertIsNone(terminal_frontend._safe_input("prompt"))

    def test_save_battle_report_writes_expected_sections(self):
        team = Team(members=[
            Unit(name="U1", hp=100, max_hp=100, skills=[]),
            Unit(name="U2", hp=90, max_hp=100, skills=[]),
        ])
        enemy = Enemy(name="E", hp=100, max_hp=100)
        history = [
            {
                "turn": 1,
                "results": [
                    {
                        "skill": "Test",
                        "unit": "U1",
                        "slot": "1",
                        "total_damage": 5,
                        "coin_damages": [5],
                        "status_damages": {},
                        "self_damage": {},
                        "log": ["line"],
                    }
                ],
                "turn_damage": 5,
                "broadcast_log": [],
                "team_hp_after": [("U1", 100, 100), ("U2", 90, 100)],
                "enemy_hp_after": 95,
            }
        ]

        with tempfile.TemporaryDirectory() as tmpdir:
            with patch.object(terminal_frontend, "_workspace_root", return_value=tmpdir):
                report = terminal_frontend._save_battle_report(
                    team=team,
                    enemy=enemy,
                    history=history,
                    level=60,
                    seed=123,
                )

            self.assertTrue(os.path.exists(report))
            with open(report, "r", encoding="utf-8") as handle:
                content = handle.read()
            self.assertIn("BATTLE REPORT", content)
            self.assertIn("[TEAM_ORDER]", content)
            self.assertIn("pos_1=U1", content)
            self.assertIn("pos_2=U2", content)
            self.assertIn("[TURN_HISTORY]", content)
            self.assertIn("[DETAILED_LOG]", content)


# â”€â”€ Character builders â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€


class TestCharacterBuilders(unittest.TestCase):
    def test_ryoshu_builder_populates_slots_and_passives(self):
        unit = make_ryoshu_w_corp_l3_cleanup_agent(level=60)
        self.assertEqual(unit.id_name, "W Corp. L3 Cleanup Agent")
        self.assertEqual(len(unit.skill_slots["1"]), 1)
        self.assertEqual(len(unit.skill_slots["2"]), 1)
        self.assertEqual(len(unit.skill_slots["3"]), 1)
        self.assertEqual(len(unit.skill_slots["defense"]), 1)
        self.assertEqual(unit.skill_slots["defense"][0].name, "Charged Evade")
        self.assertTrue(unit.passives)


# â”€â”€ Ryoshu W Corp. L3 Cleanup Agent effects â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€


class TestRyoshuWCorpEffects(unittest.TestCase):
    def _make_unit_enemy(
        self,
        skill_slot: str,
        *,
        charge_count: int = 0,
        enemy_hp: int = 999,
        enemy_max_hp: int = 999,
        enemy_statuses: dict | None = None,
    ) -> tuple[Unit, Enemy, Skill]:
        unit = make_ryoshu_w_corp_l3_cleanup_agent(level=60)
        unit.statuses = {"charge_count": charge_count} if charge_count else {}
        skill = unit.get_skill_forms(skill_slot)[0]
        unit.skills = [skill]
        unit.speed = 6
        enemy = Enemy(
            name="Ryoshu Test Target",
            base_level=60,
            hp=enemy_hp,
            max_hp=enemy_max_hp,
            phys_res={"Slash": 1.0, "Pierce": 1.0, "Blunt": 1.0},
            sin_res={"Lust": 1.0, "Pride": 1.0, "Envy": 1.0},
            statuses=enemy_statuses or {},
        )
        return unit, enemy, skill

    def _run(self, unit: Unit, enemy: Enemy, skill: Skill, sequence: list[str]) -> tuple[GameLoop, dict]:
        loop = GameLoop(
            units=[unit],
            enemies=[enemy],
            sequence=sequence,
            is_debugging=True,
        )
        return loop, loop.run_turn()[0]

    def test_ec_first_two_coins_gain_two_charge_each(self):
        unit, enemy, skill = self._make_unit_enemy("1")
        self._run(unit, enemy, skill, ["tails"] * 3)

        # E.C. adds four Charge, then turn end decays the count by one.
        self.assertEqual(unit.get_status("charge_count", 0), 3)

    def test_ec_persistent_damage_bonus_requires_ten_charge(self):
        unit, enemy, skill = self._make_unit_enemy("1", charge_count=10)
        loop, _result = self._run(unit, enemy, skill, ["tails"] * 3)

        # Persistent effects run once per coin. Both 10+ and 15+ thresholds
        # are checked from the initial charge state before E.C. adds charge.
        self.assertGreaterEqual(loop.envs[0].dynamic, 0.30)

    def test_ec_fifteen_charge_adds_both_persistent_bonuses(self):
        unit, enemy, skill = self._make_unit_enemy("1", charge_count=15)
        loop, _result = self._run(unit, enemy, skill, ["tails"] * 3)

        self.assertGreaterEqual(loop.envs[0].dynamic, 0.60)

    def test_leap_on_use_gains_seven_charge(self):
        unit, enemy, skill = self._make_unit_enemy("2")
        self._run(unit, enemy, skill, ["tails"] * 3)

        # Leap adds seven Charge, then turn end decays the count by one.
        self.assertEqual(unit.get_status("charge_count", 0), 6)

    def test_leap_persistent_coin_power_thresholds(self):
        unit, enemy, skill = self._make_unit_enemy("2", charge_count=15)
        loop, _result = self._run(unit, enemy, skill, ["tails"] * 3)

        # Leap starts at 15 Charge, so both +1 persistent coin-power effects
        # apply on every coin.
        self.assertEqual(loop.envs[0].coin_power, 11)

    def test_leap_first_coin_applies_two_slash_fragility_at_ten_charge(self):
        unit, enemy, skill = self._make_unit_enemy("2", charge_count=10)
        _loop, result = self._run(unit, enemy, skill, ["tails"] * 3)

        # The effect applies on hit; transient Fragility is removed during
        # the final turn-end cleanup pass.
        self.assertIn("slash_fragility", "\n".join(result["log"]))

    def test_leap_first_coin_does_not_apply_fragility_below_ten_charge(self):
        unit, enemy, skill = self._make_unit_enemy("2", charge_count=0)
        self._run(unit, enemy, skill, ["tails"] * 3)

        self.assertEqual(enemy.get_status("slash_fragility", 0), 0)

    def test_leap_final_coin_deals_low_hp_bonus_damage(self):
        unit, enemy, skill = self._make_unit_enemy(
            "2",
            enemy_hp=29,
            enemy_max_hp=100,
        )
        _loop, result = self._run(unit, enemy, skill, ["tails", "tails", "heads"])

        self.assertGreater(result["status_damages"].get("leap_low_hp_bonus", 0), 0)

    def test_leap_final_coin_skips_low_hp_bonus_at_thirty_percent(self):
        unit, enemy, skill = self._make_unit_enemy(
            "2",
            enemy_hp=30,
            enemy_max_hp=100,
        )
        _loop, result = self._run(unit, enemy, skill, ["tails"] * 3)

        self.assertEqual(result["status_damages"].get("leap_low_hp_bonus", 0), 0)

    def test_leap_on_kill_grants_three_haste(self):
        unit, enemy, skill = self._make_unit_enemy("2", enemy_hp=1, enemy_max_hp=1)
        self._run(unit, enemy, skill, ["heads"] * 3)

        # Haste is queued for next turn, matching Sinclair's Haste grants.
        self.assertEqual(unit.next_turn_statuses.get("haste", 0), 3)

    def test_ddedr_consumes_fifteen_charge_and_adds_five_coin_power(self):
        unit, enemy, skill = self._make_unit_enemy("3", charge_count=15)
        loop, _result = self._run(unit, enemy, skill, ["tails"] * 4)

        self.assertEqual(unit.get_status("charge_count", 0), 0)
        self.assertEqual(loop.envs[0].coin_power, 7)
        self.assertTrue(loop.envs[0].global_state["ddedr_consumed_15"])

    def test_ddedr_consumes_partial_charge_without_barrier_flag(self):
        unit, enemy, skill = self._make_unit_enemy("3", charge_count=10)
        loop, _result = self._run(unit, enemy, skill, ["tails"] * 4)

        self.assertEqual(unit.get_status("charge_count", 0), 0)
        self.assertFalse(loop.envs[0].global_state["ddedr_consumed_15"])

    def test_ddedr_tracks_heads_and_applies_ten_percent_per_head_to_final_coin(self):
        unit, enemy, skill = self._make_unit_enemy("3")
        loop, _result = self._run(unit, enemy, skill, ["heads", "heads", "tails", "tails"])

        self.assertEqual(loop.envs[0].global_state["ddedr_last_coin_dynamic_stacks"], 0)
        self.assertGreaterEqual(loop.envs[0].dynamic, 0.20)

    def test_ddedr_final_coin_applies_charge_barrier_after_kill(self):
        unit, enemy, skill = self._make_unit_enemy("3", charge_count=15, enemy_hp=10, enemy_max_hp=10)
        self._run(unit, enemy, skill, ["tails"] * 4)

        # Charge Barrier is granted mid-turn, then converted into Charge
        # Count and removed by the same turn's turn-end processing.
        # Charge Count composition: +3 from the "Dimensional Demon Edge"
        # on-kill passive, -1 from the same turn's Charge decay, +7 from
        # the Charge Barrier conversion = 9.
        self.assertEqual(unit.get_status("charge_barrier", 0), 0)
        self.assertEqual(unit.get_status("charge_count", 0), 9)

    def test_ddedr_does_not_apply_charge_barrier_without_fifteen_charge(self):
        unit, enemy, skill = self._make_unit_enemy("3", charge_count=10, enemy_hp=10, enemy_max_hp=10)
        self._run(unit, enemy, skill, ["tails"] * 4)

        self.assertEqual(unit.get_status("charge_barrier", 0), 0)

    def test_charged_evade_adds_one_base_power_per_five_charge_up_to_three(self):
        unit, enemy, skill = self._make_unit_enemy("defense", charge_count=20)
        loop, _result = self._run(unit, enemy, skill, ["tails"])

        self.assertEqual(loop.envs[0].base, 3)
        self.assertEqual(loop.envs[0].current_power, 3)

    def test_dimensional_demon_edge_grants_three_charge_on_kill(self):
        unit, enemy, skill = self._make_unit_enemy("1", enemy_hp=1, enemy_max_hp=1)
        self._run(unit, enemy, skill, ["heads"] * 3)

        # E.C. adds two Charge before the kill passive adds three; turn end
        # then decays the resulting count by one.
        self.assertEqual(unit.get_status("charge_count", 0), 4)

    def test_dimensional_demon_edge_stops_after_three_procs(self):
        unit = make_ryoshu_w_corp_l3_cleanup_agent(level=60)
        passive = unit.passives[0]
        env = Environment(unit=unit)

        for _ in range(4):
            passive.execute_phase(CoinPhase.ON_KILL, env)

        self.assertEqual(unit.get_status("charge_count", 0), 9)
        self.assertEqual(passive.proc_count, 3)


# â”€â”€ Cinq Sinclair effects â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€


class TestCinqSinclairEffects(unittest.TestCase):
    def _make_unit_enemy(
        self,
        skill_slot: str,
        *,
        speed: int = 6,
        enemy_speed: int = 3,
        enemy_hp: int = 999,
        poise_count: int = 0,
        enemy_statuses: dict | None = None,
    ) -> tuple[Unit, Enemy, Skill]:
        unit = make_sinclair_cinq_assoc_south_section_4_director(level=60)
        skill = unit.get_skill_forms(skill_slot)[0]
        unit.skills = [skill]
        unit.speed = speed
        unit.statuses = {"poise_count": poise_count} if poise_count else {}
        enemy = Enemy(
            name="Cinq Test Target",
            base_level=60,
            hp=enemy_hp,
            max_hp=999 if enemy_hp != 999 else enemy_hp,
            speed=enemy_speed,
            phys_res={"Pierce": 1.0},
            sin_res={"Gluttony": 1.0, "Pride": 1.0, "Lust": 1.0},
            statuses=enemy_statuses or {},
        )
        return unit, enemy, skill

    def _run(
        self,
        unit: Unit,
        enemy: Enemy,
        sequence: list[str],
        *,
        clash_won: bool | None = None,
        random_roll: float = 0.99,
    ) -> tuple[GameLoop, dict]:
        loop = GameLoop(
            units=[unit],
            enemies=[enemy],
            sequence=sequence,
            is_debugging=True,
            is_clashing=clash_won is not None,
            clash_won=clash_won,
            clash_count=1 if clash_won is not None else 0,
        )
        with patch("src.game_loop.random.random", return_value=random_roll):
            return loop, loop.run_turn()[0]

    def test_builder_creates_cinq_identity_stats_and_all_slots(self):
        unit = make_sinclair_cinq_assoc_south_section_4_director(level=60)

        self.assertEqual(unit.name, "Sinclair")
        self.assertEqual(unit.id_name, "Cinq Assoc. South Section 4 Director")
        self.assertEqual(unit.hp, round(79 + 2.73 * 60))
        self.assertEqual(unit.stagger_thresholds, [
            math.floor(unit.max_hp * 0.85),
            math.floor(unit.max_hp * 0.65),
        ])
        self.assertEqual([len(unit.get_skill_forms(slot)) for slot in ("1", "2", "3", "defense")], [1, 1, 1, 1])
        self.assertEqual(len(unit.passives), 1)

    def test_remise_on_use_speed_advantage_adds_one_coin_power(self):
        unit, enemy, skill = self._make_unit_enemy("1", speed=6, enemy_speed=3)
        loop, _result = self._run(unit, enemy, ["tails", "tails"])

        self.assertEqual(loop.envs[0].coin_power, 5)

    def test_remise_on_use_speed_requirement_is_exclusive(self):
        unit, enemy, skill = self._make_unit_enemy("1", speed=4, enemy_speed=3)
        loop, _result = self._run(unit, enemy, ["tails", "tails"])

        self.assertEqual(loop.envs[0].coin_power, 4)

    def test_remise_gains_two_poise_and_queues_two_haste(self):
        unit, enemy, skill = self._make_unit_enemy("1")
        self._run(unit, enemy, ["tails", "tails"])

        # Haste is queued for the next turn by both Remise coins. The kit's
        # Poise effects use the status store directly.
        self.assertEqual(unit.get_status("poise_count", 0), 1)
        self.assertEqual(unit.get_status("poise_potency", 0), 1)
        self.assertEqual(unit.next_turn_statuses.get("haste", 0), 2)

    def test_engagement_on_use_adds_two_coin_power_at_four_speed_advantage(self):
        unit, enemy, skill = self._make_unit_enemy("2", speed=7, enemy_speed=3)
        loop, _result = self._run(unit, enemy, ["tails"] * 3)

        self.assertEqual(loop.envs[0].coin_power, 6)

    def test_engagement_first_two_coins_gain_poise(self):
        unit, enemy, skill = self._make_unit_enemy("2")
        self._run(unit, enemy, ["tails"] * 3)

        # The two coin On Hit effects apply; the +2 Clash Win effect does not.
        self.assertEqual(unit.get_status("poise_count", 0), 1)

    def test_engagement_clash_win_gains_two_poise_count(self):
        unit, enemy, skill = self._make_unit_enemy("2")
        self._run(unit, enemy, ["tails"] * 3, clash_won=True)

        # Clash Win +2 plus the first two coin On Hit effects.
        self.assertEqual(unit.get_status("poise_count", 0), 3)

    def test_engagement_speed_bonus_is_zero_below_two_speed_difference(self):
        unit, enemy, skill = self._make_unit_enemy("2", speed=4, enemy_speed=3)
        loop, _result = self._run(unit, enemy, ["tails"] * 3)

        self.assertEqual(loop.envs[0].coin_power, 4)

    def test_contre_attaque_applies_declared_duel_on_first_coin(self):
        unit, enemy, skill = self._make_unit_enemy("3")
        _loop, result = self._run(unit, enemy, ["tails"] * 3)

        record = enemy.get_status(DECLARED_DUEL_STATUS)
        self.assertEqual(record, 1)
        self.assertEqual(unit.next_turn_statuses.get("haste", 0), 3)

    def test_contre_attaque_speed_bonus_scales_by_two_up_to_three(self):
        unit, enemy, skill = self._make_unit_enemy("3", speed=12, enemy_speed=3)
        loop, _result = self._run(unit, enemy, ["tails"] * 3)

        self.assertEqual(loop.envs[0].coin_power, 7)

    def test_contre_attaque_clash_win_consumes_ten_poise_and_gains_poise(self):
        unit, enemy, skill = self._make_unit_enemy("3", poise_count=12)
        self._run(unit, enemy, ["tails"] * 3, clash_won=True)

        # Consume 10 and leave the remaining 2 Poise Count.
        self.assertEqual(unit.get_status("poise_count", 0), 1)
        self.assertEqual(unit.get_status("poise_potency", 0), 10)

    def test_contre_attaque_declared_duel_doubles_gained_poise(self):
        unit, enemy, skill = self._make_unit_enemy(
            "3",
            poise_count=12,
            enemy_statuses={"Declared Duel - Sinclair": 1},
        )
        _loop, result = self._run(unit, enemy, ["tails"] * 3, clash_won=True)

        # The clash-win effect grants 20 Poise before later crit processing.
        self.assertIn("poise=20/", "\n".join(result["log"]))

    def test_contre_attaque_consumes_exactly_ten_poise_and_keeps_one(self):
        unit, enemy, skill = self._make_unit_enemy("3", poise_count=10)
        self._run(unit, enemy, ["tails"] * 3, clash_won=True)

        self.assertEqual(unit.get_status("poise_count", 0), 0)
        self.assertEqual(unit.get_status("poise_potency", 0), 0)

    def test_contre_attaque_clash_win_applies_fragile_with_effect_cap(self):
        unit, enemy, skill = self._make_unit_enemy("3")
        loop, _result = self._run(unit, enemy, ["tails"] * 3, clash_won=True)

        # The effect applies on hit; transient fragility is removed during
        # the final turn-end cleanup pass, so check the per-coin debug
        # snapshot instead of the post-turn status.
        self.assertIn("fragility", "\n".join(loop.envs[0].log))
        fragile_effect = next(
            effect
            for effect in skill.effects[SkillPhase.CLASH_WIN]
            if "Fragile" in effect.name
        )
        self.assertEqual(fragile_effect.max_procs, 2)
        self.assertEqual(sum("[phase] clash_win" in line for line in loop.envs[0].log), 1)

    def test_contre_attaque_has_three_coins_with_five_base_and_four_coin_power(self):
        unit = make_sinclair_cinq_assoc_south_section_4_director(level=60)
        skill = unit.get_skill_forms("3")[0]

        self.assertEqual(len(skill.coins), 3)
        self.assertEqual(skill.base_power, 5)
        self.assertEqual(skill.coin_power, 4)

    def test_contre_attaque_crit_effect_adds_fifty_percent_dynamic(self):
        unit, enemy, skill = self._make_unit_enemy("3", poise_count=3)
        unit.set_status("poise_potency", 3)
        loop, _result = self._run(
            unit,
            enemy,
            ["tails", "tails", "tails"],
            random_roll=0.0,
        )

        self.assertTrue(loop.envs[0].did_crit)
        self.assertGreaterEqual(loop.envs[0].dynamic, 1.5)

    def test_slumbering_bloodthirst_queues_max_speed_from_poise(self):
        unit, enemy, skill = self._make_unit_enemy("1", poise_count=15)
        env = Environment(
            unit=unit,
            enemy=enemy,
            global_state={"units": [unit], "enemies": [enemy]},
            is_debugging=True,
        )
        passive = unit.passives[0]
        passive.execute_phase(SkillPhase.TURN_END, env)

        self.assertEqual(unit.next_turn_statuses.get("max_speed_up", 0), 6)

    def test_slumbering_bloodthirst_requires_all_living_allies_to_be_faster(self):
        unit, enemy, skill = self._make_unit_enemy("1", speed=7, enemy_speed=3)
        env = Environment(
            unit=unit,
            enemy=enemy,
            global_state={"units": [unit], "enemies": [enemy]},
        )
        # Combat Start, so the buff is up before this turn's skills resolve.
        unit.passives[0].execute_phase(SkillPhase.COMBAT_START, env)

        self.assertEqual(unit.get_status("pierce_dmg_up", 0), 1)

    def test_declared_duel_only_gives_haste_to_declaring_unit(self):
        declarer = Unit(name="Declarer", speed=6)
        other_unit = Unit(name="Other", speed=6)
        target = Enemy(name="Target", statuses={
            DECLARED_DUEL_STATUS: 1,
        })
        env = Environment(unit=other_unit, enemy=target)

        process_on_hit_statuses(env)

        self.assertNotIn("haste", other_unit.next_turn_statuses)
        self.assertEqual(target.get_status(DECLARED_DUEL_STATUS), 1)

    def test_declared_duel_haste_is_capped_at_four_hits_per_turn(self):
        unit = Unit(name="Sinclair")
        target = Enemy(name="Target", statuses={
            DECLARED_DUEL_STATUS: 1,
        })
        proc_counts = {}
        env = Environment(
            unit=unit,
            enemy=target,
            global_state={"_status_proc_counts": proc_counts},
        )

        for _ in range(5):
            process_on_hit_statuses(env)

        self.assertEqual(unit.next_turn_statuses.get("haste", 0), 4)

        reset_turn_status_effects(proc_counts)
        process_on_hit_statuses(env)
        self.assertEqual(unit.next_turn_statuses.get("haste", 0), 5)

    # ── Slumbering Bloodthirst through a real turn ───────────────────

    def _run_full_turn(
        self, *, unit_speed: int, enemy_speed: int, keep_passive: bool = True
    ) -> tuple[Unit, GameLoop, dict, list[int]]:
        """
        Drive Sinclair through an actual GameLoop turn.

        The other Slumbering Bloodthirst tests call
        ``passive.execute_phase`` directly with a hand-built
        ``global_state``. That bypasses the real path: this passive
        fires on a broadcast phase, whose shared env is the only thing
        that can supply ``global_state["units"]`` to its condition, so
        only a real ``run_turn`` exercises how it actually resolves.

        Returns the unit, the loop, the skill's result, and a spy list
        of the unit's ``pierce_dmg_up`` recorded *during* resolution —
        the status is swept at turn end, so it cannot be read afterwards.
        """
        sinclair = make_sinclair_cinq_assoc_south_section_4_director(level=60)
        sinclair.speed = unit_speed
        if not keep_passive:
            sinclair.passives = []

        seen: list[int] = []
        skill = sinclair.skills[0]
        skill.add_effect(Effect(
            name="spy",
            phase=SkillPhase.ON_USE,
            apply=lambda ctx: seen.append(int(ctx.unit.get_status("pierce_dmg_up", 0))),
            priority=-100,
        ))

        enemy = Enemy(
            name="Target", base_level=60, hp=999, max_hp=999, speed=enemy_speed,
            phys_res={"Pierce": 1.0}, sin_res={"Gluttony": 1.0},
        )
        loop = GameLoop(
            team=Team(members=[sinclair]),
            enemies=[enemy],
            actions=[Action(unit=sinclair, skill=skill)],
            sequence=["heads"] * 8,
        )
        result = loop.run_turn()[0]
        return sinclair, loop, result, seen

    def test_all_allies_faster_grants_pierce_dmg_up_before_skills_resolve(self):
        """
        Granted at Combat Start, so this turn's skills are boosted by it.

        The condition reads ``global_state["units"]``, which only the
        broadcast env supplies — so this covers that path too.
        """
        sinclair, loop, _, seen = self._run_full_turn(unit_speed=20, enemy_speed=1)

        # Present while the skill resolved...
        self.assertEqual(seen, [1])
        # ...and swept by the normal turn-end cleanup afterwards.
        self.assertEqual(sinclair.get_status("pierce_dmg_up", 0), 0)
        self.assertIn(
            "[turn_end] Sinclair: [pierce_dmg_up] removed by turn-end cleanup",
            loop._broadcast_env.log,  # noqa: SLF001
        )

    def test_all_allies_faster_blocked_when_an_enemy_is_faster(self):
        """When the condition fails, the status is never granted at all."""
        sinclair, loop, _, seen = self._run_full_turn(unit_speed=5, enemy_speed=99)

        self.assertEqual(seen, [0])
        self.assertEqual(sinclair.get_status("pierce_dmg_up", 0), 0)
        self.assertNotIn(
            "[turn_end] Sinclair: [pierce_dmg_up] removed by turn-end cleanup",
            loop._broadcast_env.log,  # noqa: SLF001
        )

    def test_all_allies_faster_actually_raises_this_turn_damage(self):
        """
        The end-to-end point: the buff must move real damage.

        Both runs use identical speeds — Sinclair's skills also scale on
        speed advantage, so varying speed would confound this. The only
        difference is whether the passive is present at all.
        """
        _, _, boosted, _ = self._run_full_turn(unit_speed=20, enemy_speed=1)
        _, _, plain, _ = self._run_full_turn(
            unit_speed=20, enemy_speed=1, keep_passive=False
        )

        self.assertGreater(boosted["total_damage"], plain["total_damage"])


# â”€â”€ Skill sample builders â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€


class TestSkillSamples(unittest.TestCase):
    def test_blinkstep_structure(self):
        skill = make_blinkstep()
        self.assertEqual(skill.name, "Blinkstep")
        self.assertEqual(len(skill.coins), 2)
        self.assertEqual(skill.base_power, 3)
        self.assertEqual(skill.coin_power, 4)
        self.assertEqual(len(skill.coins[1].reuse_conditions), 1)
        self.assertEqual(skill.coins[1].reuse_conditions[0].max_reuses, 1)

    def test_traceless_reuse_condition(self):
        skill = make_traceless()
        self.assertEqual(len(skill.coins), 3)
        self.assertEqual(len(skill.coins[2].reuse_conditions), 1)
        names = [e.name for e in skill.coins[2].effects.get(CoinPhase.ON_HIT, [])]
        self.assertTrue(any("Reuse: Inflict Deathrite" in name for name in names))

    def test_tanglecleaver_persistent_effect_exists(self):
        skill = make_tanglecleaver()
        names = [e.name for e in skill.effects.get(SkillPhase.PERSISTENT, [])]
        self.assertIn("Coin Power +1 per 8 (Burn Pot + Tremor Pot), max 2", names)

    def test_unleashed_violence_has_combat_start_effect(self):
        skill = make_unleashed_violence()
        names = [e.name for e in skill.effects.get(SkillPhase.COMBAT_START, [])]
        self.assertIn("Combat Start: Spend 5 Tremor Count", names)

    def test_unleashed_violence_combat_start_spend_clears_tremor_potency_too(self):
        """Regression: this used to leave tremor_potency behind at 0 count."""
        unit = Unit(name="U", statuses={"tremor_potency": 6, "tremor_count": 5})
        env = Environment(unit=unit)

        _combat_start_spend_tremor_count(env)

        self.assertFalse(unit.has_status("tremor_count"))
        self.assertFalse(unit.has_status("tremor_potency"))

    def test_unleashed_violence_coin3_tremor_reduction_clears_potency_too(self):
        enemy = Enemy(statuses={"tremor_potency": 6, "tremor_count": 3})
        env = Environment(enemy=enemy)

        _coin3_reduce_tremor_count_by_3(env)

        self.assertFalse(enemy.has_status("tremor_count"))
        self.assertFalse(enemy.has_status("tremor_potency"))

    def test_tanglecleaver_coin3_burst_then_reduce_clears_potency_too(self):
        enemy = Enemy(statuses={"tremor_potency": 6, "tremor_count": 1})
        env = Environment(enemy=enemy)

        _coin3_burst_then_reduce_once(env)

        self.assertFalse(enemy.has_status("tremor_count"))
        self.assertFalse(enemy.has_status("tremor_potency"))


class TestTremorBurstStatusCleanup(unittest.TestCase):
    """on_tremor_burst's Scorch/Hemmorage branches should clean up pairs too."""

    def test_scorch_burst_clears_burn_potency_when_burn_count_hits_zero(self):
        enemy = Enemy(
            hp=100,
            max_hp=100,
            statuses={
                "tremor_type": "scorch",
                "tremor_potency": 6,
                "tremor_count": 1,
                "burn_potency": 4,
                "burn_count": 1,
            },
            sin_res={"Wrath": 1.0},
        )
        env = Environment(enemy=enemy)

        env.on_tremor_burst()

        self.assertFalse(enemy.has_status("burn_count"))
        self.assertFalse(enemy.has_status("burn_potency"))

    def test_hemmorage_burst_clears_bleed_potency_when_bleed_count_hits_zero(self):
        enemy = Enemy(
            hp=100,
            max_hp=100,
            statuses={
                "tremor_type": "hemmorage",
                "tremor_potency": 6,
                "tremor_count": 1,
                "bleed_potency": 4,
                "bleed_count": 1,
            },
            sin_res={"Lust": 1.0},
        )
        env = Environment(enemy=enemy)

        env.on_tremor_burst()

        self.assertFalse(enemy.has_status("bleed_count"))
        self.assertFalse(enemy.has_status("bleed_potency"))


if __name__ == "__main__":
    unittest.main()
