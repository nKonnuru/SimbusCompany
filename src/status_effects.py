"""Reusable status-effect handlers for combat event processing."""

from __future__ import annotations

from src.utils import queue_status

DECLARED_DUEL_STATUS = "Declared Duel - Sinclair"
DECLARED_DUEL_MAX_HASTE_PROCS = 4


def reset_turn_status_effects(proc_counts: dict[int, int]) -> None:
    """Reset status-effect proc counters at the start of a turn."""
    proc_counts.clear()


def apply_declared_duel(env) -> None:
    """Apply Declared Duel as a simple target status marker."""
    if env.enemy is None:
        return
    env.enemy.set_status(DECLARED_DUEL_STATUS, 1)


def _process_declared_duel_on_hit(
    env,
    status_name: str,
    proc_counts: dict[tuple[int, str], int],
) -> None:
    """Queue Haste when Sinclair hits a target with Declared Duel."""
    if env.unit is None or env.unit.name != "Sinclair":
        return

    target_key = (id(env.enemy), status_name)
    haste_procs = proc_counts.get(target_key, 0)
    if haste_procs >= DECLARED_DUEL_MAX_HASTE_PROCS:
        return

    queue_status(env, "haste", 1)
    proc_counts[target_key] = haste_procs + 1


_ON_HIT_STATUS_HANDLERS = {
    DECLARED_DUEL_STATUS: _process_declared_duel_on_hit,
}


def process_on_hit_statuses(env) -> None:
    """Dispatch status-driven on-hit behavior without branching in GameLoop."""
    if env.enemy is None:
        return

    for status_name, handler in _ON_HIT_STATUS_HANDLERS.items():
        if env.enemy.has_status(status_name):
            proc_counts = env.global_state.setdefault("_status_proc_counts", {})
            handler(env, status_name, proc_counts)
