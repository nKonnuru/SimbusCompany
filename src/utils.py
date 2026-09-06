"""Reusable effect helpers for coin and passive apply callbacks."""

from __future__ import annotations

import math


# Physical and Sin damage types, used to build per-type Fragility /
# Damage Up status names (e.g. "slash_fragility", "wrath_dmg_up").
PHYSICAL_DAMAGE_TYPES: tuple[str, ...] = ("slash", "pierce", "blunt")
SIN_DAMAGE_TYPES: tuple[str, ...] = (
    "wrath", "lust", "sloth", "gluttony", "gloom", "envy", "pride",
)
_ALL_DAMAGE_TYPES: tuple[str, ...] = PHYSICAL_DAMAGE_TYPES + SIN_DAMAGE_TYPES

# Status names in this registry are purged from every entity (units and
# enemies alike) at turn end, after all other turn-end processing has
# completed. These are buffs/debuffs that last only "for this turn":
# a generic Fragility ("fragility") and Damage Up ("dmg_up"), one
# type-specific variant of each per physical/sin damage type
# (e.g. "slash_fragility", "wrath_dmg_up"), Max/Min Speed Up,
# Haste/Bind, and Offense/Defense Level Up/Down.
#
# Tremor Decay's defense-level reduction is deliberately absent: it is
# derived on read (``Enemy.tremor_decay_def_level_down``), not a status,
# so it survives this sweep and lasts exactly as long as Decay Tremor.
TURN_END_EFFECTS_TO_CLEAR: set[str] = {
    "fragility",
    "dmg_up",
    "max_speed_up",
    "min_speed_up",
    "haste",
    "bind",
    "off_lvl_up",
    "off_lvl_down",
    "def_lvl_up",
    "def_lvl_down",
    *(f"{t}_fragility" for t in _ALL_DAMAGE_TYPES),
    *(f"{t}_dmg_up" for t in _ALL_DAMAGE_TYPES),
}


def add_status(env, status_name: str, amount: int, cap: int = 99) -> None:
    """
    Add (or, for a negative amount, reduce) ``amount`` of a count-style
    status on the unit or enemy (whichever the env is acting for).

    Delegates to Enemy.add_status / Enemy.reduce_status, so this picks
    up the same potency/count pairing behavior they provide (e.g.
    gaining "poise_count" auto-initializes "poise_potency" to 1 if it
    was 0; reducing either side of a pair to 0 removes both).
    """
    target = env.unit if env.unit is not None else env.enemy
    if target is None:
        return

    if amount < 0:
        target.reduce_status(status_name, -amount)
    else:
        target.add_status(status_name, amount, cap=cap)


def consume_charge_count(env, amount: int, cap: int = 99) -> int:
    """
    Spend up to ``amount`` Charge Count from the acting unit.

    Every unit tracks lifetime Charge consumed via
    ``Unit.charge_consumed_total`` regardless of identity. Units with
    ``gains_charge_potency_on_consume`` set additionally gain +1 Charge
    Potency for every 10 cumulative Charge Count consumed (fractional
    progress carries over across separate consumptions).

    Returns the amount actually consumed (may be less than ``amount``
    if the unit didn't have enough Charge Count).
    """
    if env.unit is None:
        return 0

    unit = env.unit
    current = max(0, int(unit.get_status("charge_count", 0)))
    consumed = min(max(0, amount), current)
    if consumed <= 0:
        return 0

    remaining = current - consumed
    if remaining == 0:
        unit.remove_status("charge_count")
    else:
        unit.set_status("charge_count", remaining)

    prior_total = unit.charge_consumed_total
    unit.charge_consumed_total = prior_total + consumed

    if getattr(unit, "gains_charge_potency_on_consume", False):
        prior_stacks = prior_total // 10
        new_stacks = unit.charge_consumed_total // 10
        gained = new_stacks - prior_stacks
        if gained > 0:
            current_potency = int(unit.get_status("charge_potency", 0))
            unit.set_status("charge_potency", min(current_potency + gained, cap))

    return consumed


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


def check_resonance(env, sin: str | None = None, minimum: int = 2) -> bool:
    """
    Return True when *sin* reached at least *minimum* Sin Resonance.

    ``sin=None`` means the resolving skill's own Affinity — the common
    case for "if my sin resonates this turn, do X".

    Returns False when the turn had no resonance computed at all (a
    skill resolved outside a resonance-aware loop simply doesn't
    trigger) rather than raising.
    """
    from src.resonance import sin_of  # local: src.resonance imports this module

    result = env.global_state.get("resonance")
    if result is None:
        return False
    target_sin = sin if sin is not None else sin_of(env.skill)
    if target_sin is None:
        return False
    return result.count(target_sin) >= int(minimum)


def get_resonance(env):
    """
    Return the turn's ``ResonanceResult``, or ``None`` if there is none.

    The escape hatch for effects whose arithmetic no helper anticipates:
    the result exposes every Reson. and A-Reson. line (``chains``,
    ``indices_of``, ``chain_at``), so a custom ``apply`` callback can
    compute whatever a skill needs.

    ``None`` at Turn Start — the chain is not final until the pre-combat
    checks have run — and from Combat Start onward it is populated.
    """
    return env.global_state.get("resonance")


def check_absolute_resonance(env, minimum: int = 3) -> bool:
    """
    Return True when the resolving skill's **own** A-Reson. run is at
    least *minimum* long.

    This is the "am I in a big Absolute Resonance?" question, and it
    reads the run this skill actually sits in — not the longest run
    elsewhere in the turn, and not the sum across runs. For those, see
    ``check_absolute_resonance_longest`` / ``_sum``.

    Returns False where there is no single resolving skill — notably the
    broadcast phases (turn_start / combat_start / turn_end), whose env
    has ``chain_index == -1``. Ask by Affinity there instead.
    """
    result = env.global_state.get("resonance")
    if result is None:
        return False
    chain = result.chain_at(getattr(env, "chain_index", -1))
    return chain is not None and chain.length >= int(minimum)


def check_absolute_resonance_sum(env, sin: str | None = None, minimum: int = 3) -> bool:
    """
    Return True when *sin*'s A-Reson. **summed across every run** reaches
    *minimum* — two separate runs of 3 give 6.

    ``sin=None`` means the resolving skill's own Affinity; pass an
    explicit Affinity to use this from a broadcast phase.
    """
    from src.resonance import sin_of  # local: src.resonance imports this module

    result = env.global_state.get("resonance")
    if result is None:
        return False
    target_sin = sin if sin is not None else sin_of(env.skill)
    if target_sin is None:
        return False
    return result.absolute_sum(target_sin) >= int(minimum)


def check_absolute_resonance_longest(
    env, sin: str | None = None, minimum: int = 3
) -> bool:
    """
    Return True when *sin*'s **longest** A-Reson. run reaches *minimum*.

    The dashboard rule: separate chains are counted separately rather
    than summed, so two runs of 3 read as 3. ``sin=None`` means the
    resolving skill's own Affinity.
    """
    from src.resonance import sin_of  # local: src.resonance imports this module

    result = env.global_state.get("resonance")
    if result is None:
        return False
    target_sin = sin if sin is not None else sin_of(env.skill)
    if target_sin is None:
        return False
    return result.absolute_longest(target_sin) >= int(minimum)


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
    difference = env.unit.effective_speed - env.enemy.effective_speed
    return min(difference // difference_per_step if difference > 0 else 0, max_steps)


def apply_speed_based_coin_power(env, difference_per_step: int = 1, max_steps: int = 1, power_per_step: int = 1) -> None:
    """Applies coin power scaling with speed steps."""
    steps = check_speed_advantage(env, difference_per_step, max_steps)
    if steps > 0:
        env.coin_power += (steps * power_per_step)
        if env.is_debugging:
            env.log.append(f"     [speed_bonus] +{steps * power_per_step} Coin Power ({steps} steps)")