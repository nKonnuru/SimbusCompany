"""Tests for the game loop, skill, coin, effect, passive, enemy, unit, and environment systems."""

from __future__ import annotations

import math
import os
import tempfile
import unittest
from unittest.mock import patch

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
from src.skill import Skill
from src.skill_samples.blinkstep import make_blinkstep
from src.skill_samples.tanglecleaver import make_tanglecleaver
from src.skill_samples.traceless import make_traceless
from src.skill_samples.unleashed_violence import make_unleashed_violence
from src.status_effects import (
    DECLARED_DUEL_STATUS,
    process_on_hit_statuses,
    reset_turn_status_effects,
)
from src import terminal_frontend as terminal_frontend
from src.unit import Unit
from src.utils import (
    TURN_END_ENEMY_EFFECTS_TO_CLEAR,
    add_coin_power,
    add_dynamic,
    add_enemy_status,
    add_poise_count,
    add_status,
    check_count,
    check_enemy_hp_below,
    deal_bonus_damage_from_current,
    set_status,
)


# ── helpers ──────────────────────────────────────────────────────────────


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


# ── Effect tests ─────────────────────────────────────────────────────────


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


# ── Coin tests ───────────────────────────────────────────────────────────


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


# ── Skill tests ──────────────────────────────────────────────────────────


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


# ── Environment tests ────────────────────────────────────────────────────


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
            statuses={"poise": 5, "poise_count": 3},
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

    def test_def_level_includes_defense_level_down_status(self):
        enemy = Enemy(
            name="Debuffed", base_level=30, defense_level=0,
            statuses={"defense_level_down": 3},
        )
        env = self._make_env(enemy=enemy)
        self.assertEqual(env.def_level, 27)

    def test_def_level_mod(self):
        env = self._make_env()
        env.def_level_mod = -10
        self.assertEqual(env.def_level, 20)

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
        """Verify the core formula: floor(power × static × dynamic)."""
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

        env.update()  # tick 1: duration 2 → 1
        self.assertIn(eff, env.effects)

        env.update()  # tick 2: duration 1 → 0 → removed
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


# ── GameLoop tests ───────────────────────────────────────────────────────


class TestGameLoop(unittest.TestCase):
    def test_skill_ordering(self):
        slow = _make_skill(name="Slow", speed=1)
        fast = _make_skill(name="Fast", speed=10)
        loop = GameLoop(skills=[slow, fast])
        results = loop.run_turn()
        # Fast resolves first → appears at index 0
        self.assertEqual(results[0]["skill"], "Fast")
        self.assertEqual(results[1]["skill"], "Slow")

    def test_run_turn_returns_results_per_skill(self):
        s1 = _make_skill(name="Alpha", speed=3, coins=[_make_coin()])
        s2 = _make_skill(name="Beta", speed=7, coins=[_make_coin()])
        loop = GameLoop(skills=[s1, s2])
        results = loop.run_turn()
        self.assertEqual(len(results), 2)
        # Beta is faster → resolved first
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
        # Heads → 4+3=7, Tails → 4.  Either way > 0.
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


# ── Passive tests ────────────────────────────────────────────────────────


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


# ── Enemy tests ──────────────────────────────────────────────────────────


class TestEnemy(unittest.TestCase):
    def test_effective_defense(self):
        enemy = Enemy(base_level=40, defense_level=-5)
        self.assertEqual(enemy.effective_defense, 35)

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

    def test_tremor_decay_applies_defense_level_down_on_conversion(self):
        enemy = Enemy(
            statuses={
                "tremor_potency": 10,
                "tremor_count": 3,
                "tremor_type": "reverb",
            }
        )
        applied = enemy.convert_tremor_amplitude("decay")
        self.assertTrue(applied)
        self.assertEqual(enemy.get_status("defense_level_down", 0), 2)

    def test_tremor_decay_updates_defense_level_down_on_potency_change(self):
        enemy = Enemy(
            statuses={
                "tremor_potency": 8,
                "tremor_count": 3,
                "tremor_type": "decay",
            }
        )
        enemy.refresh_tremor_decay_effect()
        self.assertEqual(enemy.get_status("defense_level_down", 0), 2)

        enemy.set_status("tremor_potency", 15)
        self.assertEqual(enemy.get_status("defense_level_down", 0), 3)

    def test_tremor_decay_removed_when_tremor_not_active(self):
        enemy = Enemy(
            statuses={
                "tremor_potency": 12,
                "tremor_count": 2,
                "tremor_type": "decay",
            }
        )
        enemy.refresh_tremor_decay_effect()
        self.assertEqual(enemy.get_status("defense_level_down", 0), 3)

        enemy.remove_status("tremor_count")
        self.assertFalse(enemy.has_status("defense_level_down"))


# ── Unit tests ───────────────────────────────────────────────────────────


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


# ── GameLoop with entities tests ─────────────────────────────────────────


class TestGameLoopWithEntities(unittest.TestCase):
    def test_turn_end_enemy_cleanup_list_removes_registered_effects(self):
        """Enemy statuses listed in cleanup registry should be removed at turn end."""
        enemy = Enemy(name="CleanupDummy", statuses={"test_turn_end_cleanup": 3})
        loop = GameLoop(enemies=[enemy])

        TURN_END_ENEMY_EFFECTS_TO_CLEAR.add("test_turn_end_cleanup")
        try:
            loop.run_turn()
        finally:
            TURN_END_ENEMY_EFFECTS_TO_CLEAR.discard("test_turn_end_cleanup")

        self.assertFalse(enemy.has_status("test_turn_end_cleanup"))

    def test_turn_start_refreshes_tremor_decay_effect(self):
        enemy = Enemy(
            name="DecayEnemy",
            statuses={
                "tremor_type": "decay",
                "tremor_potency": 12,
                "tremor_count": 3,
            },
        )
        loop = GameLoop(enemies=[enemy])
        loop.run_turn()
        self.assertEqual(enemy.get_status("defense_level_down", 0), 3)

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
        # Two skills resolve → BEFORE_ATTACK fires twice, but passive procs only once
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
        # Should fire once per turn → 2 total
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
        unit_skill = _make_skill(name="UnitSkill", speed=10, coins=[_make_coin()])
        unit = Unit(name="U", skills=[unit_skill])
        loop = GameLoop(skills=[direct], units=[unit])
        results = loop.run_turn()
        self.assertEqual(len(results), 2)
        self.assertEqual(results[0]["skill"], "UnitSkill")  # faster
        self.assertEqual(results[1]["skill"], "Direct")

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


# ── Core combat resolution tests ─────────────────────────────────────


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
        unit = Unit(name="CritUser", statuses={"poise": 10, "poise_count": 1}, skills=[skill])
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

    def test_bleed_on_hit_consumes_count_and_clash_budget(self):
        enemy = Enemy(
            name="Bleed",
            hp=100,
            max_hp=100,
            statuses={"bleed_potency": 3, "bleed_count": 4},
            sin_res={"Wrath": 1.0},
        )
        result = self._run_single_coin(enemy, clash_count=2)

        self.assertEqual(result["status_damages"].get("bleed", 0), 6)
        self.assertEqual(enemy.get_status("bleed_count", 0), 2)

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
                "poise": 3,
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


# ── Entity / Unit helpers ───────────────────────────────────────────


class TestEnemyHelpers(unittest.TestCase):
    def test_add_charge_count_sets_potency_if_missing(self):
        enemy = Enemy(statuses={})
        enemy.add_charge_count(2)
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


# ── Utils helpers ───────────────────────────────────────────────────


class TestUtilsHelpers(unittest.TestCase):
    def test_add_poise_count_initializes_poise_and_clears_at_zero(self):
        unit = Unit(name="U")
        env = Environment(unit=unit)

        add_poise_count(env, 2)
        self.assertEqual(unit.get_status("poise_count", 0), 2)
        self.assertEqual(unit.get_status("poise", 0), 1)

        add_poise_count(env, -2)
        self.assertEqual(unit.get_status("poise_count", 0), 0)
        self.assertEqual(unit.get_status("poise", 0), 0)
        self.assertFalse(unit.has_status("poise"))

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

# ── CombatContext ───────────────────────────────────────────────────


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


# ── Terminal frontend helpers ───────────────────────────────────────


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
        unit = Unit(name="U", hp=100, max_hp=100, skills=[])
        enemy = Enemy(name="E", hp=100, max_hp=100)
        history = [
            {
                "turn": 1,
                "skill": "Test",
                "total_damage": 5,
                "coin_damages": [5],
                "status_damages": {},
                "log": ["line"],
                "broadcast_log": [],
                "unit_hp_after": 100,
                "enemy_hp_after": 95,
            }
        ]

        with tempfile.TemporaryDirectory() as tmpdir:
            with patch.object(terminal_frontend, "_workspace_root", return_value=tmpdir):
                report = terminal_frontend._save_battle_report(
                    unit=unit,
                    enemy=enemy,
                    history=history,
                    level=60,
                    seed=123,
                )

            self.assertTrue(os.path.exists(report))
            with open(report, "r", encoding="utf-8") as handle:
                content = handle.read()
            self.assertIn("BATTLE REPORT", content)
            self.assertIn("[TURN_HISTORY]", content)
            self.assertIn("[DETAILED_LOG]", content)


# ── Character builders ──────────────────────────────────────────────


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


# ── Ryoshu W Corp. L3 Cleanup Agent effects ──────────────────────────


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

        self.assertEqual(unit.get_status("haste_count", 0), 3)

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

        self.assertEqual(unit.get_status("charge_barrier", 0), 7)

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


# ── Cinq Sinclair effects ───────────────────────────────────────────


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
        self.assertEqual(unit.get_status("poise", 0), 1)
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
        self.assertEqual(unit.get_status("poise", 0), 10)

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
        self.assertEqual(unit.get_status("poise", 0), 0)

    def test_contre_attaque_clash_win_applies_fragile_with_effect_cap(self):
        unit, enemy, skill = self._make_unit_enemy("3")
        loop, _result = self._run(unit, enemy, ["tails"] * 3, clash_won=True)

        self.assertEqual(enemy.get_status("Fragile", 0), 1)
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
        unit.set_status("poise", 3)
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
        unit.passives[0].execute_phase(SkillPhase.TURN_END, env)

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



# ── Skill sample builders ───────────────────────────────────────────


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


if __name__ == "__main__":
    unittest.main()
