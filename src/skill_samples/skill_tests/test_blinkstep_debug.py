"""
Debug test: run Blinkstep against a neutral enemy with all coins
predetermined as heads.  Prints skill description, initial state,
then per-coin combat log with embedded entity/env snapshots.
"""

from __future__ import annotations

from dataclasses import fields

from ...enemy import Enemy
from ...environment import Environment
from ...game_loop import GameLoop
from ..blinkstep import make_blinkstep
from ...unit import Unit


def _dump_env(env: Environment, label: str) -> None:
    """Pretty-print every field of the Environment."""
    print(f"\n{'=' * 60}")
    print(f"  {label}")
    print(f"{'=' * 60}")
    for f in fields(env):
        if f.name.startswith("_"):
            continue
        val = getattr(env, f.name)
        # Shorten long / unhelpful reprs
        if f.name in ("skill", "unit", "enemy"):
            val = getattr(val, "name", val) if val is not None else None
        elif f.name == "current_coin":
            val = getattr(val, "name", val) if val is not None else None
        elif f.name == "effects" and isinstance(val, dict):
            val = {str(k): v for k, v in val.items()} or "(empty)"
        elif f.name == "log":
            val = f"({len(val)} entries)"
        print(f"  {f.name:24s} = {val}")
    print(f"{'=' * 60}")


def _dump_entity(entity, label: str) -> None:
    """Print key stats from a Unit or Enemy."""
    print(f"\n  [{label}]")
    print(f"    name            = {entity.name}")
    print(f"    base_level      = {entity.base_level}")
    print(f"    defense_level   = {entity.defense_level}")
    print(f"    effective_def   = {entity.effective_defense}")
    print(f"    hp / max_hp     = {entity.hp} / {entity.max_hp}")
    print(f"    speed           = {entity.speed}")
    print(f"    phys_res        = {entity.phys_res}")
    print(f"    sin_res         = {entity.sin_res}")
    print(f"    observation_lvl = {entity.observation_level}")
    print(f"    statuses        = {entity.statuses}")
    if hasattr(entity, "sp"):
        print(f"    sp              = {entity.sp}")
        print(f"    poise           = {entity.get_status('poise', 0)}")
        print(f"    poise_count     = {entity.get_status('poise_count', 0)}")
        print(f"    skills          = {[s.name for s in entity.skills]}")


def main() -> None:
    # ── Build entities ───────────────────────────────────────────────
    skill = make_blinkstep()

    unit = Unit(
        name="Blinkstep User",
        base_level=40,
        defense_level=0,
        hp=200,
        max_hp=200,
        speed=10,      # 10 → reuse WILL trigger; 5+ over enemy → speed advantage
        sp=0,          # neutral sanity → 50 % flip
        skills=[skill],
    )

    enemy = Enemy(
        name="Neutral Target",
        base_level=40,
        defense_level=0,
        hp=500,
        max_hp=500,
        speed=5,
        phys_res={"Slash": 1.0},
        sin_res={"Sloth": 1.0},
        observation_level=0,
        statuses={"rupture_potency": 10, "rupture_count": 5},
    )

    # ── Banner ───────────────────────────────────────────────────────
    print("\n" + "#" * 60)
    print("  BLINKSTEP DEBUG RUN  (clash win, rupture on enemy, 1.5× Sloth weak)")
    print("#" * 60)

    # ── Skill description (with numerical amounts) ───────────────────
    print(f"\n  Skill: {skill.name}")
    print(f"    base_power     = {skill.base_power}")
    print(f"    coin_power     = {skill.coin_power}")
    print(f"    offense_level  = {skill.offense_level}")
    print(f"    damage_type    = {skill.damage_type}")
    print(f"    coins          = {[c.name for c in skill.coins]}")
    for c in skill.coins:
        print(f"      {c.name}:  coin_power={c.coin_power}")
        for phase, effs in c.effects.items():
            for e in effs:
                print(f"        {phase.value}: {e.name}")
        for rc in c.reuse_conditions:
            print(f"        REUSE: {rc.name} (max={rc.max_reuses})")
    for phase, effs in skill.effects.items():
        for e in effs:
            cond_str = " [conditional]" if e.condition else ""
            print(f"    {phase.value}: {e.name}{cond_str}")

    # ── Initial state ────────────────────────────────────────────────
    _dump_entity(unit, "UNIT (initial)")
    _dump_entity(enemy, "ENEMY (initial)")

    # ── Run through the GameLoop ─────────────────────────────────────
    loop = GameLoop(
        units=[unit],
        enemies=[enemy],
        sequence=["heads", "heads", "heads"],  # 3rd entry for reuse coin
        is_debugging=True,
        is_clashing=True,
        clash_won=True,
        clash_count=1,  # won 1 clash → +3% static, enables bleed calc
    )

    # Snapshot the environment before resolution
    env_pre = Environment.from_skill(
        skill, unit, enemy,
        sequence=["heads", "heads", "heads"],
        is_debugging=True,
    )
    env_pre.is_clashing = True
    env_pre.clash_won = True
    env_pre.clash_count = 1
    env_pre.recompute_static()
    _dump_env(env_pre, "ENVIRONMENT (before resolution)")

    results = loop.run_turn()

    # ── Environment after ────────────────────────────────────────────
    env_post = loop.envs[0]
    _dump_env(env_post, "ENVIRONMENT (after resolution)")

    # ── Entity snapshots after ───────────────────────────────────────
    _dump_entity(unit, "UNIT (after)")
    _dump_entity(enemy, "ENEMY (after)")

    # ── Combat Log (includes per-coin snapshots) ─────────────────────
    print(f"\n{'─' * 60}")
    print("  COMBAT LOG")
    print(f"{'─' * 60}")
    for line in results[0]["log"]:
        print(f"  {line}")

    # ── Final result summary ─────────────────────────────────────────
    print(f"\n{'─' * 60}")
    print("  RESULT")
    print(f"{'─' * 60}")
    r = results[0]
    print(f"  Skill           : {r['skill']}")
    print(f"  Total damage    : {r['total_damage']}")
    print(f"  Enemy HP after  : {enemy.hp} / {enemy.max_hp}")
    print(f"  Enemy statuses  : {enemy.statuses}")
    print(f"  Coin reuse count: {[c.reuse_count for c in skill.coins]}")

    # ── Per-coin damage breakdown ────────────────────────────────────
    coin_dmgs = r.get("coin_damages", [])
    skill_total = sum(coin_dmgs)
    print(f"\n  Per-coin damage (skill only):")
    for i, dmg in enumerate(coin_dmgs):
        print(f"    Coin {i + 1}: {dmg}")
    print(f"    Subtotal: {skill_total}")

    # ── Status damage breakdown ──────────────────────────────────────
    status_dmgs = r.get("status_damages", {})
    status_total = sum(status_dmgs.values())
    if status_dmgs:
        print(f"\n  Status damage breakdown:")
        for name, dmg in status_dmgs.items():
            print(f"    {name:12s}: {dmg}")
        print(f"    {'Subtotal':12s}: {status_total}")
    else:
        print(f"\n  Status damage: 0")

    print(f"\n  Grand total   : {skill_total + status_total}  "
          f"(skill {skill_total} + status {status_total})")
    print()


if __name__ == "__main__":
    main()
