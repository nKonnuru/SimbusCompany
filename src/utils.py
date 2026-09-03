"""Reusable effect helpers for coin and passive apply callbacks."""

from __future__ import annotations

import math


# Status names in this registry are purged from enemies at turn end,
# after all other turn-end processing has completed.
TURN_END_ENEMY_EFFECTS_TO_CLEAR: set[str] = {
    "slash_fragility",
    "fragility",
}


def add_status(env, status_name: str, amount: int, cap: int = 99) -> None:
    """Add ``amount`` to a count-style status on the unit or enemy."""
    target = env.unit if env.unit is not None else env.enemy
    if target is None:
        return

    current = int(target.get_status(status_name, 0))
    new_value = max(0, current + amount)
    if new_value == 0:
        target.remove_status(status_name)
    else:
        target.set_status(status_name, min(new_value, cap))


def add_poise_count(env, amount: int, cap: int = 99) -> None:
    """Add Poise Count and keep Poise potency/count synchronized on a unit."""
    if env.unit is None:
        return

    current_count = max(0, int(env.unit.get_status("poise_count", 0)))
    new_count = min(max(0, current_count + amount), cap)

    if new_count == 0:
        env.unit.remove_status("poise_count")
        env.unit.remove_status("poise")
        return

    if int(env.unit.get_status("poise", 0)) <= 0:
        env.unit.set_status("poise", 1)
    env.unit.set_status("poise_count", new_count)


def queue_status(env, status_name: str, amount: int) -> None:
    """Queue a status on the acting unit to be applied next turn."""
    target = env.unit if env.unit is not None else env.enemy
    if target is None:
        return
        
    current_queued = target.next_turn_statuses.get(status_name, 0)
    target.queue_next_turn_status(status_name, current_queued + amount)


def set_status(env, status_name: str, amount: int, cap: int = 99) -> None:
    """Set a count-style status on the unit or enemy."""
    target = env.unit if env.unit is not None else env.enemy
    if target is None:
        return

    if amount <= 0:
        target.remove_status(status_name)
        return

    target.set_status(status_name, min(amount, cap))


def add_dynamic(env, amount: float) -> None:
    """Add a direct dynamic modifier to the current effect context."""
    env.dynamic += amount


def check_count(env, status_name: str, minimum_value: int) -> bool:
    """Return True when the active unit/enemy has at least ``minimum_value`` of a status."""
    target = env.unit if env.unit is not None else env.enemy
    if target is None:
        return False
    return int(target.get_status(status_name, 0)) >= int(minimum_value)


def add_coin_power(env, amount: int) -> None:
    """Add a direct coin-power modifier to the current effect context."""
    env.coin_power += amount


def add_enemy_status(env, status_name: str, amount: int, cap: int = 99) -> None:
    """Add ``amount`` to a count-style status on the enemy."""
    if env.enemy is None:
        return

    current = int(env.enemy.get_status(status_name, 0))
    new_value = max(0, current + amount)
    if new_value == 0:
        env.enemy.remove_status(status_name)
    else:
        env.enemy.set_status(status_name, min(new_value, cap))


def queue_enemy_status(env, status_name: str, amount: int) -> None:
    """Queue a status on the target enemy to be applied next turn."""
    if env.enemy is None:
        return
        
    current_queued = env.enemy.next_turn_statuses.get(status_name, 0)
    env.enemy.queue_next_turn_status(status_name, current_queued + amount)


def check_enemy_hp_below(env, hp_ratio: float) -> bool:
    """Return True when enemy HP ratio is below ``hp_ratio``."""
    if env.enemy is None or env.enemy.max_hp <= 0:
        return False
    return (env.enemy.hp / env.enemy.max_hp) < float(hp_ratio)


def deal_bonus_damage_from_current(env, multiplier: float, source: str = "bonus_damage") -> None:
    """Deal bonus damage based on current coin damage (floor(current_damage * multiplier))."""
    if env.enemy is None or env.target_killed:
        return

    bonus_damage = max(math.floor(env.current_damage * multiplier), 0)
    if bonus_damage <= 0:
        return

    dealt = env.enemy.take_damage(bonus_damage)
    env.total += dealt
    env.status_damages[source] = env.status_damages.get(source, 0) + dealt
    if not env.enemy.is_alive:
        env.target_killed = True


def check_speed_advantage(env, difference_per_step: int = 1, max_steps: int = 1) -> int:
    """Return amount of steps of advantage the unit has over the target"""
    if env.unit is None or env.enemy is None:
        return False
    difference = env.unit.speed - env.enemy.speed
    return min(difference // difference_per_step if difference > 0 else 0, max_steps)


def apply_speed_based_coin_power(env, difference_per_step: int = 1, max_steps: int = 1, power_per_step: int = 1) -> None:
    """Applies coin power scaling with speed steps."""
    steps = check_speed_advantage(env, difference_per_step, max_steps)
    if steps > 0:
        env.coin_power += (steps * power_per_step)
        if env.is_debugging:
            env.log.append(f"     [speed_bonus] +{steps * power_per_step} Coin Power ({steps} steps)")