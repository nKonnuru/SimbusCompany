"""
Sample skill: **Unleashed Violence**

Spec implemented for simulator prototyping:
- Base power 3, coin power 5, offense level 3
- Damage type: ("Blunt", "Wrath")
- [Combat Start] Spend 5 Tremor Count (attack weight effect intentionally ignored)
- [Clash Lose] Lose 20 SP
- [Persistent] +6% damage per negative status type on target (max +30%)
- [On Kill] Gain +2 Tremor Count
- Coin 1/2/3 [On Hit]: Trigger tremor burst, then deal Wrath damage equal
  to 30% of final stagger-threshold raised by tremor burst (max 20)
- Coin 3 [On Hit]: additionally reduce Tremor Count by 3

Notes
-----
Tremor Burst is implemented via ``Environment.on_tremor_burst()``:
- Raises target stagger threshold by current ``tremor_potency``
- Stores actual raised amount in ``enemy.statuses['tremor_last_burst_raised']``
- Force-staggers if HP is below the newly raised threshold
"""

from __future__ import annotations

import math

from src.coin import Coin
from src.effect import CoinPhase, Effect, SkillPhase
from src.environment import Environment
from src.skill import Skill


# =====================================================================
#  Helpers
# =====================================================================


def _count_negative_status_types(env: Environment) -> int:
    """Count active negative effects by type on the enemy."""
    if env.enemy is None:
        return 0

    helper_keys = {
        "tremor_last_burst_raised",  # internal bookkeeping, not a debuff type
    }

    active_types: set[str] = set()
    for name, value in env.enemy.statuses.items():
        if name in helper_keys:
            continue

        is_active = value > 0 if isinstance(value, (int, float)) else bool(value)
        if not is_active:
            continue

        if name.endswith("_potency"):
            base_name = name[: -len("_potency")]
        elif name.endswith("_count"):
            base_name = name[: -len("_count")]
        else:
            base_name = name

        active_types.add(base_name)

    return len(active_types)


def _apply_wrath_fixed_damage(env: Environment, raw_damage: int, source: str) -> None:
    """Apply Wrath fixed damage with sin resistance handling, then log it."""
    if env.enemy is None or raw_damage <= 0 or env.target_killed:
        return

    wrath_res = env.enemy.sin_res.get("Wrath", 1.0)
    wrath_mod = wrath_res - 1.0
    if wrath_mod < 0:
        wrath_mod /= 2.0  # keep weakness handling consistent with other fixed status damage

    final_raw = max(math.floor(raw_damage * (1.0 + wrath_mod)), 1)
    dealt = env.enemy.take_damage(final_raw)
    env.total += dealt
    env.status_damages[source] = env.status_damages.get(source, 0) + dealt
    env.log.append(
        f"     [{source}] {dealt} Wrath damage "
        f"(raw={raw_damage}, wrath_mod={wrath_mod:+.2f}) -> "
        f"enemy HP {env.enemy.hp}/{env.enemy.max_hp}"
    )

    if not env.enemy.is_alive:
        env.target_killed = True
        env.log.append(f"     [kill] target killed by {source}!")


# =====================================================================
#  Skill-level callbacks
# =====================================================================


def _combat_start_spend_tremor_count(env: Environment) -> None:
    """Spend up to 5 Tremor Count from the user (attack weight intentionally ignored)."""
    if env.unit is None:
        return
    current = env.unit.get_status("tremor_count", 0)
    spent = min(5, max(0, current))
    remaining = max(0, current - spent)

    if remaining == 0:
        env.unit.remove_status("tremor_count")
    else:
        env.unit.set_status("tremor_count", min(remaining, 99))


def _clash_lose_lose_sp(env: Environment) -> None:
    """Lose 20 SP on clash lose."""
    if env.unit is None:
        return
    env.unit.sp = max(-45, env.unit.sp - 20)


def _persistent_negative_effect_damage_bonus(env: Environment) -> None:
    """
    +6% damage per active negative status type on target, max +30%.

    Idempotent per coin: remove prior applied bonus before re-applying.
    """
    prev = env.global_state.get("_uv_neg_status_bonus", 0.0)
    env.dynamic -= prev

    status_types = _count_negative_status_types(env)
    bonus = min(status_types * 0.06, 0.30)

    env.dynamic += bonus
    env.global_state["_uv_neg_status_bonus"] = bonus


def _on_kill_gain_tremor_count(env: Environment) -> None:
    """Gain +2 Tremor Count on kill."""
    if env.unit is None:
        return
    current = env.unit.get_status("tremor_count", 0)
    env.unit.set_status("tremor_count", min(current + 2, 99))


# =====================================================================
#  Coin callbacks
# =====================================================================


def _burst_then_wrath_30pct_of_raised(env: Environment) -> None:
    """
    Trigger Tremor Burst, then deal Wrath damage by 30% of burst raised amount.

    Uses Environment Tremor Burst logic:
    - Fires ``env.on_tremor_burst()``
    - Reads ``tremor_last_burst_raised`` from target
    """
    if env.enemy is None or env.target_killed:
        return

    env.on_tremor_burst()

    raised = env.enemy.get_status("tremor_last_burst_raised", 0)

    raised_int = max(0, int(raised))
    wrath_raw = min(math.floor(raised_int * 0.30), 20)
    _apply_wrath_fixed_damage(env, wrath_raw, source="unleashed_bonus_wrath")


def _coin3_reduce_tremor_count_by_3(env: Environment) -> None:
    """Coin 3 on-hit rider: reduce target Tremor Count by 3."""
    if env.enemy is None:
        return

    count = env.enemy.get_status("tremor_count", 0)
    new_count = max(0, count - 3)
    if new_count == 0:
        env.enemy.remove_status("tremor_count")
    else:
        env.enemy.set_status("tremor_count", min(new_count, 99))


# =====================================================================
#  Build function
# =====================================================================


def make_unleashed_violence() -> Skill:
    """Construct and return the Unleashed Violence skill."""

    coin1 = Coin(name="Unleashed Violence Coin 1", coin_power=5)
    coin1.add_effect(
        Effect(
            name="On Hit: Tremor Burst -> Wrath Damage",
            phase=CoinPhase.ON_HIT,
            apply=_burst_then_wrath_30pct_of_raised,
        )
    )

    coin2 = Coin(name="Unleashed Violence Coin 2", coin_power=5)
    coin2.add_effect(
        Effect(
            name="On Hit: Tremor Burst -> Wrath Damage",
            phase=CoinPhase.ON_HIT,
            apply=_burst_then_wrath_30pct_of_raised,
        )
    )

    coin3 = Coin(name="Unleashed Violence Coin 3", coin_power=5)
    coin3.add_effect(
        Effect(
            name="On Hit: Tremor Burst -> Wrath Damage",
            phase=CoinPhase.ON_HIT,
            apply=_burst_then_wrath_30pct_of_raised,
        )
    )
    coin3.add_effect(
        Effect(
            name="On Hit: -3 Tremor Count",
            phase=CoinPhase.ON_HIT,
            apply=_coin3_reduce_tremor_count_by_3,
        )
    )

    skill = Skill(
        name="Unleashed Violence",
        base_power=3,
        coin_power=5,
        offense_level=3,
        damage_type=("Blunt", "Wrath"),
    )
    skill.add_coin(coin1)
    skill.add_coin(coin2)
    skill.add_coin(coin3)

    skill.add_effect(
        Effect(
            name="Combat Start: Spend 5 Tremor Count",
            phase=SkillPhase.COMBAT_START,
            apply=_combat_start_spend_tremor_count,
        )
    )
    skill.add_effect(
        Effect(
            name="Clash Lose: Lose 20 SP",
            phase=SkillPhase.CLASH_LOSE,
            apply=_clash_lose_lose_sp,
        )
    )
    skill.add_effect(
        Effect(
            name="Persistent: +6% per negative status type (max 30%)",
            phase=SkillPhase.PERSISTENT,
            apply=_persistent_negative_effect_damage_bonus,
        )
    )
    skill.add_effect(
        Effect(
            name="On Kill: Gain +2 Tremor Count",
            phase=SkillPhase.ON_KILL,
            apply=_on_kill_gain_tremor_count,
        )
    )

    return skill
