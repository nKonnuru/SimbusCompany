"""Terminal battle frontend for manual turn-by-turn combat simulation."""

from __future__ import annotations

import argparse
from datetime import datetime
import os
import random
import sys
from dataclasses import dataclass
from typing import Callable


# Support direct script execution: `python src/terminal_frontend.py`
if __package__ in {None, ""}:
    project_root = os.path.dirname(os.path.dirname(__file__))
    if project_root not in sys.path:
        sys.path.insert(0, project_root)

import src.characters as characters
from src.action import Action
from src.enemy import Enemy
from src.game_loop import GameLoop
from src.skill import Skill
from src.team import Team
from src.unit import Unit


@dataclass
class LoadedUnit:
    """Named unit factory record used by the frontend loader."""

    key: str
    label: str
    builder: Callable[[], Unit]


def _make_sample_enemy(level: int = 60) -> Enemy:
    """Create a baseline enemy for frontend playtesting."""
    hp = 9999
    return Enemy(
        name="Training Drone",
        base_level=level,
        defense_level=0,
        hp=hp,
        max_hp=hp,
        phys_res={"Slash": 1.0, "Pierce": 1.0, "Blunt": 1.0},
        sin_res={"Wrath": 1.0, "Lust": 1.0, "Pride": 1.0, "Envy": 1.0},
        statuses={},
        passives=[],
        stagger_thresholds=[int(hp * 0.99), int(hp * 0.98), int(hp * 0.97)],
    )


def _load_available_units(level: int) -> list[LoadedUnit]:
    """Return all currently available sample unit builders."""
    loaders: list[LoadedUnit] = []
    builder_names = sorted(
        name for name in characters.__all__ if name.startswith("make_")
    )

    for index, builder_name in enumerate(builder_names, start=1):
        builder = getattr(characters, builder_name)
        preview = builder(level=level)
        label = f"{preview.name} - {preview.id_name}"
        loaders.append(
            LoadedUnit(
                key=str(index),
                label=label,
                builder=lambda builder=builder: builder(level=level),
            )
        )

    return loaders


def _format_statuses(entity: Enemy) -> str:
    """Return a compact status string for a combat entity."""
    if not entity.statuses:
        return "none"
    items = sorted((str(k), entity.statuses[k]) for k in entity.statuses)
    return ", ".join(f"{k}={v}" for k, v in items)


def _get_player_skill_options(unit: Unit) -> list[tuple[str, str, Skill]]:
    """Map menu labels to the first configured skill in each slot."""
    options: list[tuple[str, str, Skill]] = []
    slot_names = [
        ("1", "Skill 1"),
        ("2", "Skill 2"),
        ("3", "Skill 3"),
        ("defense", "Defense"),
    ]
    for slot, label in slot_names:
        forms = unit.get_skill_forms(slot)
        if forms:
            options.append((slot, label, forms[0]))
    return options


def _unit_label(unit: Unit) -> str:
    """Return ``Name - Identity`` when the unit carries an identity name."""
    id_name = getattr(unit, "id_name", "")
    return f"{unit.name} - {id_name}" if id_name else unit.name


def _living_members(team: Team) -> list[Unit]:
    """Return the team members still standing, in team order."""
    return [unit for unit in team if unit.is_alive]


# ── team selection ───────────────────────────────────────────────────


def _select_team(loaders: list[LoadedUnit]) -> Team | None:
    """
    Prompt for an ordered team roster.

    The order the keys are typed in *is* the team order — position 1
    first. Repeating a key is allowed: builders are factories, so the
    same Identity twice yields two independent units. Returns ``None``
    if stdin closes.
    """
    print("Available IDs:")
    for loader in loaders:
        print(f"  {loader.key}: {loader.label}")
    print("\nEnter IDs in team order, separated by spaces (e.g. \"2 1\").")
    print("Team order does not set turn order (speed does), but it breaks speed ties.")

    by_key = {loader.key: loader for loader in loaders}

    while True:
        raw = _safe_input(f"Select team (default {loaders[0].key}): ")
        if raw is None:
            print("\nInput stream closed during team selection. Exiting.")
            return None

        keys = raw.split()
        if not keys:
            keys = [loaders[0].key]

        unknown = [key for key in keys if key not in by_key]
        if unknown:
            print(f"Invalid selection(s): {', '.join(unknown)}")
            continue

        team = Team(members=[by_key[key].builder() for key in keys])
        print("\nTeam set:")
        for position, unit in enumerate(team, start=1):
            print(f"  Pos {position}: {_unit_label(unit)}")
        return team


# ── per-turn display ─────────────────────────────────────────────────


def _print_state(turn: int, team: Team, enemy: Enemy) -> None:
    """Print HP and status overview for every team member and the enemy."""
    print("\n" + "=" * 72)
    print(f"Turn {turn}")
    print("-" * 72)
    for position, unit in enumerate(team, start=1):
        if not unit.is_alive:
            print(f"  [{position}] {_unit_label(unit)} | DEFEATED")
            continue
        print(
            f"  [{position}] {_unit_label(unit)} | HP {unit.hp}/{unit.max_hp} | "
            f"Speed {unit.effective_speed} | Statuses: {_format_statuses(unit)}"
        )
    print(
        f"Enemy : {enemy.name} | HP {enemy.hp}/{enemy.max_hp} | "
        f"Staggered: {enemy.is_staggered} | Statuses: {_format_statuses(enemy)}"
    )


def _print_skill_menu(unit: Unit, options: list[tuple[str, str, Skill]]) -> None:
    """Print selectable skill actions for one unit."""
    print(f"\nActions for {_unit_label(unit)} (Speed {unit.effective_speed}):")
    for slot, label, skill in options:
        coin_count = len(skill.coins)
        print(
            f"  {slot}: {label} - {skill.name} "
            f"(base={skill.base_power}, coin={skill.coin_power}, coins={coin_count})"
        )
    print("  history: show turn summaries")
    print("  log: show full battle log")
    print("  status: show current statuses")
    print("  quit: exit battle")


def _prompt_team_actions(
    team: Team, enemy: Enemy, history: list[dict]
) -> list[Action] | None:
    """
    Prompt one skill per living member, in team order.

    Returns ``None`` to end the battle — either the player quit or stdin
    closed, each having already printed its own message. Informational
    commands (history/log/status) are handled inline and re-prompt the
    same member.
    """
    actions: list[Action] = []

    for unit in _living_members(team):
        options = _get_player_skill_options(unit)
        if not options:
            print(f"{_unit_label(unit)} has no usable skills; skipping.")
            continue

        while True:
            _print_skill_menu(unit, options)
            raw_action = _safe_input("Choose action: ")
            if raw_action is None:
                print("\nInput stream closed. Ending battle.")
                return None
            raw = raw_action.strip().lower()

            if raw == "quit":
                print("Battle exited by user.")
                return None
            if raw == "history":
                _print_battle_history(history)
                continue
            if raw == "log":
                _print_full_log(history)
                continue
            if raw == "status":
                for member in team:
                    print(f"{_unit_label(member)} statuses : {_format_statuses(member)}")
                print(f"Enemy statuses: {_format_statuses(enemy)}")
                continue

            selected = next(
                (skill for slot, _label, skill in options if raw == slot), None
            )
            if selected is None:
                print("Invalid action.")
                continue

            actions.append(Action(unit=unit, skill=selected, slot=raw))
            break

    return actions


def _run_team_turn(
    team: Team, enemy: Enemy, actions: list[Action]
) -> tuple[list[dict], list[str], str]:
    """
    Resolve one full turn for the team.

    Every Action leaves its clash/target/sequence config unset, so those
    fall back to the turn-wide GameLoop values. Ordering is by speed
    with team order breaking ties — handled inside ``run_turn``.

    Returns the per-action results, the broadcast log, and the turn's
    Sin Resonance summary.
    """
    # Frontend mode assumption: all flips resolve as heads.
    loop = GameLoop(
        team=team,
        enemies=[enemy],
        actions=actions,
        is_debugging=True,
        sequence=["heads"] * 64,
    )
    results = loop.run_turn()
    resonance = loop.resonance.summary() if loop.resonance is not None else "none"
    return results, list(loop._broadcast_env.log), resonance  # noqa: SLF001


def _print_turn_result(
    turn: int, results: list[dict], enemy: Enemy, resonance: str = "none"
) -> None:
    """Print compact turn resolution output, in resolution order."""
    print("\n" + "-" * 72)
    print(f"Resonance: {resonance}")
    print(f"Turn {turn} Result (resolution order, fastest first):")
    for position, result in enumerate(results, start=1):
        actor = result.get("unit") or "-"
        print(
            f"  {position}. {actor}: {result['skill']} dealt "
            f"{result['total_damage']} total damage "
            f"(coin hits={result['coin_damages']})"
        )
        if result.get("status_damages"):
            print(f"     Status damage: {result['status_damages']}")
        if result.get("self_damage"):
            print(f"     Self-inflicted: {result['self_damage']}")
    turn_total = sum(result["total_damage"] for result in results)
    print(f"  Turn total: {turn_total}")
    print(f"Enemy HP after turn: {enemy.hp}/{enemy.max_hp}")


def _print_battle_history(history: list[dict]) -> None:
    """Print condensed per-turn history."""
    if not history:
        print("No turns resolved yet.")
        return
    print("\nTurn history:")
    for entry in history:
        skills = ", ".join(
            f"{result.get('unit') or '-'}:{result['skill']}({result['total_damage']})"
            for result in entry["results"]
        )
        print(
            f"  T{entry['turn']}: {skills} | turn_damage={entry['turn_damage']} "
            f"| enemy_hp={entry['enemy_hp_after']}"
        )
        for name, hp, max_hp in entry["team_hp_after"]:
            print(f"      {name}: {hp}/{max_hp}")


def _print_full_log(history: list[dict]) -> None:
    """Print full detailed logs accumulated across turns."""
    if not history:
        print("No logs yet.")
        return

    print("\n" + "#" * 72)
    print("Battle log")
    print("#" * 72)
    for entry in history:
        print("\n" + f"[Turn {entry['turn']}]")
        for result in entry["results"]:
            actor = result.get("unit") or "-"
            print(f"  {actor} - {result['skill']}:")
            for line in result["log"]:
                print(f"    {line}")
        print("  Broadcast log:")
        for line in entry.get("broadcast_log", []):
            print(f"    {line}")


def _safe_input(prompt: str) -> str | None:
    """Read input and return None when stdin is closed."""
    try:
        return input(prompt)
    except EOFError:
        return None


def _workspace_root() -> str:
    """Return the project root path for this repository."""
    return os.path.dirname(os.path.dirname(__file__))


def _serialize_entity_metadata(entity: Enemy, label: str) -> list[str]:
    """Build human-readable metadata lines for a combat entity."""
    lines = [
        f"[{label}]",
        f"name={entity.name}",
        f"base_level={entity.base_level}",
        f"defense_level={entity.defense_level}",
        f"effective_defense={entity.effective_defense}",
        f"hp={entity.hp}",
        f"max_hp={entity.max_hp}",
        f"speed={entity.speed}",
        f"is_alive={entity.is_alive}",
        f"is_staggered={entity.is_staggered}",
        f"stagger_thresholds={entity.stagger_thresholds}",
        f"phys_res={entity.phys_res}",
        f"sin_res={entity.sin_res}",
        f"statuses={entity.statuses}",
    ]

    passives = [passive.name for passive in entity.passives]
    lines.append(f"passives={passives}")
    return lines


def _serialize_unit_skill_metadata(unit: Unit) -> list[str]:
    """Build unit-specific metadata lines for skill slots and current skills."""
    slot_map = {
        slot: [skill.name for skill in unit.get_skill_forms(slot)]
        for slot in ("1", "2", "3", "defense")
    }
    return [
        "[UNIT_SKILLS]",
        f"current_skills={[skill.name for skill in unit.skills]}",
        f"skill_slots={slot_map}",
    ]


def _save_battle_report(
    *,
    team: Team,
    enemy: Enemy,
    history: list[dict],
    level: int,
    seed: int | None,
) -> str:
    """Write battle metadata + logs to a timestamped text file."""
    output_dir = os.path.join(_workspace_root(), "battle_logs")
    os.makedirs(output_dir, exist_ok=True)

    timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    filename = f"battle_{timestamp}.txt"
    file_path = os.path.join(output_dir, filename)

    lines: list[str] = []
    lines.append("BATTLE REPORT")
    lines.append("=" * 72)
    lines.append(f"generated_at={datetime.now().isoformat(timespec='seconds')}")
    lines.append(f"configured_level={level}")
    lines.append(f"seed={seed}")
    lines.append(f"turns_resolved={len(history)}")
    lines.append("")

    lines.append("[TEAM_ORDER]")
    for position, unit in enumerate(team, start=1):
        lines.append(f"pos_{position}={_unit_label(unit)}")
    lines.append("")

    for position, unit in enumerate(team, start=1):
        lines.extend(_serialize_entity_metadata(unit, f"UNIT_{position}"))
        lines.append("")
        lines.extend(_serialize_unit_skill_metadata(unit))
        lines.append("")

    lines.extend(_serialize_entity_metadata(enemy, "ENEMY"))
    lines.append("")

    lines.append("[TURN_HISTORY]")
    if not history:
        lines.append("none")
    else:
        for entry in history:
            lines.append(
                f"T{entry['turn']} turn_damage={entry['turn_damage']} "
                f"enemy_hp_after={entry['enemy_hp_after']} "
                f"resonance={entry.get('resonance', 'none')}"
            )
            for result in entry["results"]:
                lines.append(
                    f"  {result.get('unit') or '-'} skill={result['skill']} "
                    f"damage={result['total_damage']} "
                    f"coin_damages={result['coin_damages']}"
                )
                if result.get("status_damages"):
                    lines.append(f"    status_damages={result['status_damages']}")
                if result.get("self_damage"):
                    lines.append(f"    self_damage={result['self_damage']}")
            for name, hp, max_hp in entry["team_hp_after"]:
                lines.append(f"  hp_after {name}={hp}/{max_hp}")
    lines.append("")

    lines.append("[DETAILED_LOG]")
    if not history:
        lines.append("none")
    else:
        for entry in history:
            lines.append("")
            lines.append(f"Turn {entry['turn']}")
            for result in entry["results"]:
                lines.append(f"  {result.get('unit') or '-'} - {result['skill']}:")
                for log_line in result["log"]:
                    lines.append(f"    {log_line}")
            lines.append("  Broadcast log:")
            for log_line in entry.get("broadcast_log", []):
                lines.append(f"    {log_line}")

    with open(file_path, "w", encoding="utf-8") as handle:
        handle.write("\n".join(lines) + "\n")

    return file_path


def run_terminal_frontend(level: int = 60, seed: int | None = None) -> int:
    """Run an interactive terminal battle simulation session."""
    if seed is not None:
        random.seed(seed)

    loaders = _load_available_units(level=level)
    team = _select_team(loaders)
    if team is None:
        return 0

    enemy = _make_sample_enemy()
    history: list[dict] = []

    turn = 1
    while _living_members(team) and enemy.is_alive:
        for unit in _living_members(team):
            unit.roll_speed()
        _print_state(turn, team, enemy)

        actions = _prompt_team_actions(team, enemy, history)
        if actions is None:
            break
        if not actions:
            print("No actions selected.")
            continue

        results, broadcast_log, resonance = _run_team_turn(team, enemy, actions)
        history.append(
            {
                "turn": turn,
                "resonance": resonance,
                "results": [
                    {
                        "skill": result["skill"],
                        "unit": result.get("unit"),
                        "slot": result.get("slot"),
                        "total_damage": result["total_damage"],
                        "coin_damages": list(result["coin_damages"]),
                        "status_damages": dict(result["status_damages"]),
                        "self_damage": dict(result.get("self_damage", {})),
                        "log": list(result["log"]),
                    }
                    for result in results
                ],
                "turn_damage": sum(result["total_damage"] for result in results),
                "broadcast_log": list(broadcast_log),
                "team_hp_after": [
                    (unit.name, unit.hp, unit.max_hp) for unit in team
                ],
                "enemy_hp_after": enemy.hp,
            }
        )

        _print_turn_result(turn, results, enemy, resonance)
        turn += 1

    print("\n" + "=" * 72)
    if enemy.is_alive and not _living_members(team):
        print("Defeat: the whole team was defeated.")
    elif _living_members(team) and not enemy.is_alive:
        print("Victory: enemy defeated.")
        _print_full_log(history)
    else:
        print("Battle ended.")

    _print_battle_history(history)
    report_path = _save_battle_report(
        team=team,
        enemy=enemy,
        history=history,
        level=level,
        seed=seed,
    )
    print(f"Saved battle report: {report_path}")
    return 0


def _parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Terminal battle frontend")
    parser.add_argument("--level", type=int, default=60, help="Player unit level")
    parser.add_argument(
        "--seed",
        type=int,
        default=None,
        help="Optional random seed for reproducible speed/flip rolls",
    )
    return parser.parse_args()


def main() -> int:
    args = _parse_args()
    return run_terminal_frontend(level=args.level, seed=args.seed)


if __name__ == "__main__":
    raise SystemExit(main())
