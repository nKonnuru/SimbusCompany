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
from src.enemy import Enemy
from src.game_loop import GameLoop
from src.skill import Skill
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


def _print_state(turn: int, unit: Unit, enemy: Enemy) -> None:
    """Print HP and status overview for current turn."""
    print("\n" + "=" * 72)
    print(f"Turn {turn}")
    print("-" * 72)
    print(
        f"Unit  : {unit.name} | HP {unit.hp}/{unit.max_hp} | Speed {unit.speed} | "
        f"Statuses: {_format_statuses(unit)}"
    )
    print(
        f"Enemy : {enemy.name} | HP {enemy.hp}/{enemy.max_hp} | "
        f"Staggered: {enemy.is_staggered} | Statuses: {_format_statuses(enemy)}"
    )


def _print_skill_menu(options: list[tuple[str, str, Skill]]) -> None:
    """Print selectable skill actions."""
    print("\nActions:")
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


def _run_selected_skill(unit: Unit, enemy: Enemy, skill: Skill) -> dict:
    """Resolve one selected skill as one combat turn."""
    old_skills = list(unit.skills)
    old_speed = skill.speed

    # Frontend uses one selected action per turn.
    unit.skills = [skill]
    skill.speed = unit.speed

    # Frontend mode assumption: all flips resolve as heads.
    loop = GameLoop(
        units=[unit],
        enemies=[enemy],
        is_debugging=True,
        sequence=["heads"] * 64,
    )
    results = loop.run_turn()

    unit.skills = old_skills
    skill.speed = old_speed

    selected_result = results[0] if results else {
        "skill": skill.name,
        "total_damage": 0,
        "coin_damages": [],
        "status_damages": {},
        "log": [],
    }

    # Include turn-wide logs (turn start/end + status processing) for completeness.
    selected_result["broadcast_log"] = list(loop._broadcast_env.log)  # noqa: SLF001
    return selected_result


def _print_turn_result(turn: int, result: dict, enemy: Enemy) -> None:
    """Print compact turn resolution output."""
    print("\n" + "-" * 72)
    print(
        f"Turn {turn} Result: {result['skill']} dealt {result['total_damage']} total damage "
        f"(coin hits={result['coin_damages']})"
    )
    if result.get("status_damages"):
        print(f"Status damage breakdown: {result['status_damages']}")
    print(f"Enemy HP after turn: {enemy.hp}/{enemy.max_hp}")


def _print_battle_history(history: list[dict]) -> None:
    """Print condensed per-turn history."""
    if not history:
        print("No turns resolved yet.")
        return
    print("\nTurn history:")
    for entry in history:
        print(
            f"  T{entry['turn']}: {entry['skill']} | damage={entry['total_damage']} "
            f"| enemy_hp={entry['enemy_hp_after']} | unit_hp={entry['unit_hp_after']}"
        )


def _print_full_log(history: list[dict]) -> None:
    """Print full detailed logs accumulated across turns."""
    if not history:
        print("No logs yet.")
        return

    print("\n" + "#" * 72)
    print("Battle log")
    print("#" * 72)
    for entry in history:
        print("\n" + f"[Turn {entry['turn']}] {entry['skill']}")
        print("  Skill/Coin log:")
        for line in entry["log"]:
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
    unit: Unit,
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

    lines.extend(_serialize_entity_metadata(unit, "UNIT"))
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
                f"T{entry['turn']} skill={entry['skill']} damage={entry['total_damage']} "
                f"coin_damages={entry['coin_damages']} "
                f"enemy_hp_after={entry['enemy_hp_after']} unit_hp_after={entry['unit_hp_after']}"
            )
            if entry.get("status_damages"):
                lines.append(f"  status_damages={entry['status_damages']}")
    lines.append("")

    lines.append("[DETAILED_LOG]")
    if not history:
        lines.append("none")
    else:
        for entry in history:
            lines.append("")
            lines.append(f"Turn {entry['turn']} - {entry['skill']}")
            lines.append("  Skill/Coin log:")
            for log_line in entry["log"]:
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
    print("Available units:")
    for loader in loaders:
        print(f"  {loader.key}: {loader.label}")

    selected_loader = loaders[0]
    while True:
        raw_choice = _safe_input("Select unit (default 1): ")
        if raw_choice is None:
            print("\nInput stream closed during unit selection. Exiting.")
            return 0
        choice = raw_choice.strip().lower()
        if choice in {"", selected_loader.key}:
            break
        matched = [loader for loader in loaders if loader.key == choice]
        if matched:
            selected_loader = matched[0]
            break
        print("Invalid selection.")

    unit = selected_loader.builder()
    enemy = _make_sample_enemy()
    history: list[dict] = []

    turn = 1
    while unit.is_alive and enemy.is_alive:
        unit.roll_speed()
        _print_state(turn, unit, enemy)

        options = _get_player_skill_options(unit)
        _print_skill_menu(options)

        raw_action = _safe_input("Choose action: ")
        if raw_action is None:
            print("\nInput stream closed. Ending battle.")
            break
        raw = raw_action.strip().lower()

        if raw == "quit":
            print("Battle exited by user.")
            break
        if raw == "history":
            _print_battle_history(history)
            continue
        if raw == "log":
            _print_full_log(history)
            continue
        if raw == "status":
            print(f"Unit statuses : {_format_statuses(unit)}")
            print(f"Enemy statuses: {_format_statuses(enemy)}")
            continue

        selected_skill: Skill | None = None
        for slot, _label, skill in options:
            if raw == slot:
                selected_skill = skill
                break

        if selected_skill is None:
            print("Invalid action.")
            continue

        result = _run_selected_skill(unit, enemy, selected_skill)
        history.append(
            {
                "turn": turn,
                "skill": result["skill"],
                "total_damage": result["total_damage"],
                "coin_damages": list(result["coin_damages"]),
                "status_damages": dict(result["status_damages"]),
                "log": list(result["log"]),
                "broadcast_log": list(result.get("broadcast_log", [])),
                "unit_hp_after": unit.hp,
                "enemy_hp_after": enemy.hp,
            }
        )

        _print_turn_result(turn, result, enemy)
        turn += 1

    print("\n" + "=" * 72)
    if enemy.is_alive and not unit.is_alive:
        print("Defeat: unit was defeated.")
    elif unit.is_alive and not enemy.is_alive:
        print("Victory: enemy defeated.")
        _print_full_log(history)
    else:
        print("Battle ended.")

    _print_battle_history(history)
    report_path = _save_battle_report(
        unit=unit,
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
