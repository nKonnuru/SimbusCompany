"""
Game Loop:
- Turn Start
- Set Skill Order Based on Speed
- Combat Start
- Per Skill Effects:
    - Persistent Conditionals and Dynamics
    - Before Use
    - On Use
    - Clash Start
    - Clash Win
    - Clash Lose
    - Before Attack
    - On Unopposed Attack
    - On Kill
    Per Coin Effects
        - Coin Start
        - On Hit
            - Heads
            - Tails
            - With Cracking
            - Without Cracking
        - On Crit
            - Heads
            - Tails
        - On Kill
        - Reuse
    - After Attack
- Turn End
"""

from __future__ import annotations

import math
import random
from dataclasses import dataclass, field
from typing import Any

from src.action import Action
from src.coin import Coin
from src.effect import CoinPhase, SkillPhase
from src.enemy import Enemy
from src.environment import CoinEnvironment, Environment
from src.passive import Passive
from src.pre_combat import PRE_COMBAT_CHECKS, PreCombatCheck, TurnPlan
from src.skill import Skill
from src.status_effects import process_on_hit_statuses, reset_turn_status_effects
from src.team import Team
from src.unit import Unit
from src.utils import TURN_END_EFFECTS_TO_CLEAR


@dataclass
class GameLoop:
    """
    Orchestrates one full turn of combat.

    Usage
    -----
    >>> loop = GameLoop(units=[unit_a], enemies=[enemy_b])
    >>> results = loop.run_turn()

    For each skill an ``Environment`` is created via
    ``Environment.from_skill()``, carrying all multiplier and state
    tracking.  The loop fires every phase from the game-loop outline,
    collecting effects from skills, unit passives, and enemy passives.
    """

    skills: list[Skill] = field(default_factory=list)
    units: list[Unit] = field(default_factory=list)
    enemies: list[Enemy] = field(default_factory=list)
    results: list[dict] = field(default_factory=list)

    # The player's ordered roster. When set, ``units`` is populated from
    # it, so every entity-wide pass (passives, turn-start/turn-end status
    # processing) works unchanged. Team order does not decide turn order —
    # speed does — but it breaks speed ties (see _order_actions).
    team: Team | None = None

    # Optional explicit turn manifest: one Action per acting unit, built
    # fresh by the caller each turn. When set, this — not unit.skills —
    # determines what resolves this turn and who owns each skill (no
    # ownership lookup needed; see _turn_actions). Unit.skills stays the
    # unit's permanent kit either way. When empty, falls back to the
    # legacy behavior of resolving whatever is currently in each unit's
    # skills list (kept for backward compatibility).
    actions: list[Action] = field(default_factory=list)

    # Checks run across the whole skill selection between Turn Start and
    # Combat Start. Defaults to the module-level registry when unset;
    # pass an explicit list to override it without touching global state.
    pre_combat_checks: list[PreCombatCheck] | None = None

    # Optional: predetermined coin flip sequence and debug flag.
    # When set, these are forwarded to every Environment created —
    # unless an individual Action overrides them for its own skill.
    sequence: list[str | None] | None = None
    is_debugging: bool = False
    is_clashing: bool = False
    clash_won: bool | None = None
    clash_count: int = 0

    # Stash the per-skill Environments for post-run inspection.
    envs: list[Environment] = field(default_factory=list)

    # Published by the pre-combat checks each turn (TurnPlan.state) and
    # merged into every skill's env.global_state, so effects and passives
    # can read what the checks decided. Rebuilt from scratch each turn.
    _turn_state: dict[str, Any] = field(default_factory=dict, repr=False)
    _status_proc_counts: dict[tuple[int, str], int] = field(default_factory=dict, repr=False)

    # A "shared" env for turn-wide broadcasts (turn_start / turn_end).
    # Per-skill resolution creates its own Environment.
    _broadcast_env: Environment = field(default_factory=Environment)

    def __post_init__(self) -> None:
        # A team is the roster; ``units`` is what every entity-wide pass
        # iterates. Populate one from the other so callers can pass
        # either. An explicit ``units`` list wins if both are given.
        if self.team is not None and not self.units:
            self.units = list(self.team.members)

    # ── public API ───────────────────────────────────────────────────

    def run_turn(self) -> list[dict]:
        """Execute a complete turn and return per-skill result dicts."""
        self.results.clear()
        self.envs.clear()
        self._turn_state.clear()
        # _broadcast_env outlives a single turn, so its context must be
        # dropped too — otherwise a second run_turn would see last
        # turn's resonance at Turn Start, before checks have re-run.
        self._broadcast_env.global_state.clear()

        # Reset passive proc counters for every entity
        for entity in self._all_entities():
            entity.reset_passives()
        reset_turn_status_effects(self._status_proc_counts)

        # The turn's manifest, derived once. Everything downstream reads
        # from it, so a pre-combat check that changes the selection is
        # reflected in the later broadcasts too.
        ordered = self._turn_actions()
        all_skills = [action.skill for action in ordered]

        # ── Turn Start ──────────────────────────────────────────────
        for entity in self._all_entities():
            entity.apply_queued_statuses()

        self._process_turn_start_statuses()

        self._broadcast_phase(SkillPhase.TURN_START, all_skills)

        # ── Set Skill Order Based on Speed ──────────────────────────
        # An owned skill orders by its owner's effective_speed (so Haste/
        # Bind apply); ties break by team order. See _order_actions.
        ordered = self._order_actions(ordered)

        # ── Pre-Combat Checks (across all selected skills) ──────────
        # Checks see the plan already in resolution order, and the list
        # is re-sorted after they run — so a check can both read the
        # intended order and legitimately change it (by granting Haste,
        # say). Two sorts over a handful of actions is free; keep both.
        ordered = self._run_pre_combat_checks(ordered)
        ordered = self._order_actions(ordered)
        all_skills = [action.skill for action in ordered]

        # ── Combat Start ────────────────────────────────────────────
        self._broadcast_phase(SkillPhase.COMBAT_START, all_skills)

        # ── Per-Skill Resolution ────────────────────────────────────
        for action in ordered:
            result = self._resolve_action(action, all_skills)
            self.results.append(result)

        # ── Turn End ────────────────────────────────────────────────
        self._broadcast_phase(SkillPhase.TURN_END, all_skills)

        # ── Turn-end status processing ──────────────────────────────
        self._process_turn_end_statuses()

        return self.results

    @property
    def resonance(self):
        """
        This turn's ``ResonanceResult``, or ``None`` if none was computed.

        Populated by ``resonance_check`` during the pre-combat phase and
        valid after ``run_turn`` returns.
        """
        return self._turn_state.get("resonance")

    # ── entity / skill aggregation ───────────────────────────────────

    def _all_skills(self) -> list[Skill]:
        """Every skill resolving this turn, in declaration order."""
        return [action.skill for action in self._turn_actions()]

    def _turn_actions(self) -> list[Action]:
        """
        The canonical manifest of what resolves this turn, as Actions.

        Covers all three supported modes:

        - ``self.actions`` set → used directly. Each Action's skill is
          this turn's contribution from its unit (``unit.skills`` — the
          permanent kit — is not scanned), and carries its own config.
        - otherwise → one synthesized Action per skill sitting in each
          unit's ``skills`` list (legacy convention, still used by tests
          and older callers).
        - plus one ownerless Action per entry in ``self.skills``, for
          standalone skills with no owning unit.

        Synthesized Actions leave every config field unset, so they fall
        back to the turn-wide ``GameLoop`` values — preserving the exact
        behavior these two legacy paths had before per-action config.
        """
        actions: list[Action] = []
        if self.actions:
            actions.extend(self.actions)
        else:
            for unit in self.units:
                actions.extend(
                    Action(unit=unit, skill=skill) for skill in unit.skills
                )
        actions.extend(Action(unit=None, skill=skill) for skill in self.skills)
        return actions

    def _team_index(self, action: Action) -> int:
        """
        Team-order position of *action*'s owner — the speed tiebreaker.

        Falls back to the unit's index in ``self.units`` when no Team was
        supplied, and sorts an ownerless action last. Every mode returns
        a stable integer, so equal-speed actions never order randomly.
        """
        if action.unit is None:
            return len(self.units)
        if self.team is not None:
            return self.team.position_of(action.unit)
        for index, unit in enumerate(self.units):
            if unit is action.unit:
                return index
        return len(self.units)

    def _order_actions(self, actions: list[Action]) -> list[Action]:
        """
        Sort *actions* into resolution order: fastest first, then by
        team order.

        Ascending on ``(-speed, team_index)`` puts the highest speed
        first and, among equal speeds, the earliest team position first.
        Python's sort is stable, so actions that tie on both keys (one
        unit acting twice at the same speed) keep declaration order.
        """
        return sorted(
            actions,
            key=lambda a: (-a.resolve_speed(), self._team_index(a)),
        )

    def _run_pre_combat_checks(self, ordered: list[Action]) -> list[Action]:
        """
        Run every registered check against the full turn plan and return
        the resulting action list.

        The returned list is read back off the plan rather than assumed
        to be the one passed in, so a check may either mutate
        ``plan.actions`` in place or replace it wholesale.
        """
        checks = (
            self.pre_combat_checks
            if self.pre_combat_checks is not None
            else PRE_COMBAT_CHECKS
        )
        if not checks:
            return ordered

        plan = TurnPlan(
            actions=ordered,
            team=self.team,
            enemies=self.enemies,
            log=self._broadcast_env.log,
        )
        for check in checks:
            check(plan)

        # Whatever the checks published travels to every Environment.
        self._turn_state.update(plan.state)
        return list(plan.actions)

    def _populate_global_state(self, env: Environment) -> None:
        """
        Give *env* the turn-wide context every effect can read.

        Used for both per-skill Environments and the shared broadcast
        env, so a turn_start / combat_start / turn_end effect sees the
        same context a mid-resolution one does. ``_turn_state`` carries
        whatever the pre-combat checks published (notably Sin Resonance)
        and is empty until they have run — so Turn Start legitimately
        has no resonance, while Combat Start onward does.
        """
        env.global_state["units"] = self.units
        env.global_state["enemies"] = self.enemies
        env.global_state["_status_proc_counts"] = self._status_proc_counts
        env.global_state.update(self._turn_state)

    def _all_entities(self) -> list[Enemy]:
        """Return every entity (units + enemies) for passive collection."""
        entities: list[Enemy] = []
        entities.extend(self.units)
        entities.extend(self.enemies)
        return entities

    def _find_owner(self, skill: Skill) -> Unit | None:
        """
        Return the Unit that owns *skill*, or None.

        Only used as a fallback by ``_resolve_action`` when the Action
        carries no owner (e.g. a direct ``_resolve_skill`` call). The
        primary path (``run_turn``) never reaches this — every Action in
        the manifest already names its unit.
        """
        if self.actions:
            for action in self.actions:
                if action.skill is skill:
                    return action.unit
            return None
        for unit in self.units:
            if skill in unit.skills:
                return unit
        return None

    def _pick_target(self) -> Enemy | None:
        """Pick the first living enemy as a default target."""
        for enemy in self.enemies:
            if enemy.is_alive:
                return enemy
        return None

    # ── passive helper ───────────────────────────────────────────────

    @staticmethod
    def _sorted_entity_passives(entity: Enemy | None) -> list[Passive]:
        """Return an entity's passives sorted by passive priority."""
        if entity is None:
            return []
        return sorted(entity.passives, key=lambda p: p.priority)

    def _execute_merged_phase_effects(
        self,
        *,
        phase: SkillPhase | CoinPhase,
        env: Environment,
        base_effects: list,
    ) -> None:
        """
        Execute phase effects with owner/target passives merged into one stream.

        Skill/coin effects are treated as the base list; passive effects are
        appended and then globally sorted by effect priority.
        """
        owner = env.unit
        target = env.enemy

        merged: list[tuple[Any, Passive | None]] = [(eff, None) for eff in base_effects]

        # Passive kill effects resolve on coin-level kill only.
        # Use enum type + identity check because SkillPhase/CoinPhase share
        # string values (e.g. "on_kill").
        if isinstance(phase, SkillPhase) and phase is SkillPhase.ON_KILL:
            merged.sort(key=lambda item: item[0].priority)
            for effect, passive in merged:
                if passive is None:
                    effect.execute(env)
            return

        for entity in (owner, target):
            for passive in self._sorted_entity_passives(entity):
                for eff in passive.get_effects(phase):
                    merged.append((eff, passive))

        # Stable sort keeps append order for ties while honoring effect priority.
        merged.sort(key=lambda item: item[0].priority)

        passive_state: dict[int, str] = {}
        executed_passives: dict[int, Passive] = {}

        for effect, passive in merged:
            if passive is None:
                effect.execute(env)
                continue

            passive_id = id(passive)
            state = passive_state.get(passive_id)
            if state == "skip":
                continue

            if state is None:
                if not passive.can_proc(env):
                    passive_state[passive_id] = "skip"
                    continue
                passive_state[passive_id] = "run"

            if effect.execute(env):
                executed_passives[passive_id] = passive

        for passive in executed_passives.values():
            passive._proc_count += 1  # noqa: SLF001 - internal game-loop integration
            phase_proc_count = passive.get_phase_proc_count(phase)
            phase_proc_limit = passive.get_phase_proc_limit(phase)
            env.log.append(
                f"[passive] {passive.name} proc'd "
                f"({phase_proc_count}/{phase_proc_limit or '∞'})"
            )

    def _fire_passives(
        self, phase: SkillPhase | CoinPhase, env: Environment
    ) -> None:
        """
        Fire passive effects from **all** entities for *phase*.

        Passives are iterated in entity order (units first, then enemies),
        sorted by priority within each entity.

        This is used for turn-wide broadcasts (``TURN_START``/``TURN_END``/
        ``COMBAT_START``), where *env* is the shared ``_broadcast_env`` with
        no skill-specific unit/enemy set. Effects (e.g. a unit's own
        turn-end self-buff) commonly resolve "self" as ``env.unit`` if set,
        else ``env.enemy`` — so ``env.unit``/``env.enemy`` are temporarily
        pointed at each passive's owning entity while its effects run, then
        restored, so self-targeting effects work the same as they do
        during per-skill/per-coin resolution.
        """
        prev_unit, prev_enemy = env.unit, env.enemy
        try:
            for entity in self._all_entities():
                if isinstance(entity, Unit):
                    env.unit, env.enemy = entity, None
                else:
                    env.unit, env.enemy = None, entity
                for passive in sorted(entity.passives, key=lambda p: p.priority):
                    passive.execute_phase(phase, env)
        finally:
            env.unit, env.enemy = prev_unit, prev_enemy

    # ── combined phase helpers ───────────────────────────────────────

    def _skill_phase(
        self, skill: Skill, phase: SkillPhase, env: Environment
    ) -> None:
        """Execute a skill phase with owner/target passives appended."""
        env.log.append(f"  [phase] {phase.value}")
        self._execute_merged_phase_effects(
            phase=phase,
            env=env,
            base_effects=skill.get_effects(phase),
        )

    def _coin_phase(
        self, coin: Coin, phase: CoinPhase, env: Environment
    ) -> None:
        """Execute a coin phase with owner/target passives appended."""
        env.log.append(f"     [coin-phase] {phase.value}")
        self._execute_merged_phase_effects(
            phase=phase,
            env=env,
            base_effects=coin.get_effects(phase),
        )

    # ── broadcast ────────────────────────────────────────────────────

    def _broadcast_phase(
        self,
        phase: SkillPhase,
        skills: list[Skill],
    ) -> None:
        """
        Fire *phase* effects across **all** skills, then fire passives.

        Uses the lightweight ``_broadcast_env`` since no skill-specific
        Environment exists during turn-wide phases.
        """
        env = self._broadcast_env
        self._populate_global_state(env)
        env.log.append(f"[broadcast] {phase.value}")
        for effect in Skill.collect_effects(skills, phase):
            effect.execute(env)
        self._fire_passives(phase, env)

    # ══════════════════════════════════════════════════════════════════
    #  Turn-start status processing
    # ══════════════════════════════════════════════════════════════════

    def _process_turn_start_statuses(self) -> None:
        """
        Grant Charge Barrier's shield at the start of the turn.

        While an entity holds ``charge_barrier``, it gains ``3 ×
        charge_barrier`` Shield (additive — a fresh grant stacks with
        whatever Shield is left over). The barrier itself is converted
        into Charge Count and removed at turn end (see
        ``_process_turn_end_statuses``).
        """
        log = self._broadcast_env.log
        for entity in self._all_entities():
            barrier = int(entity.get_status("charge_barrier", 0))
            if barrier <= 0:
                continue
            gained_shield = barrier * 3
            entity.shield += gained_shield
            log.append(
                f"[turn_start] {entity.name}: [charge_barrier] +{gained_shield} Shield "
                f"(barrier={barrier}) → shield={entity.shield}"
            )

    # ══════════════════════════════════════════════════════════════════    #  Turn-end status processing
    # ══════════════════════════════════════════════════════════════

    def _process_turn_end_statuses(self) -> None:
        """
        Process all turn-end status effects for every entity:

        - **Burn**: deal potency as damage, count − 1.
        - **Tremor**: count − 1; if count reaches 0, potency expires.
        - **Poise**: count − 1; if count reaches 0, potency expires.
        - **Charge**: count − 1; potency persists even at 0 count.
        - **Charge Barrier**: converted into Charge Count (equal to its
          value) and removed. Its Shield was already granted at turn
          start by ``_process_turn_start_statuses``.
        """
        log = self._broadcast_env.log

        # ---- Process enemies -------------------------------------------------
        for enemy in self.enemies:
            if not enemy.is_alive:
                continue

            # ── Burn ───────────────────────────────────────────────────
            burn_potency = enemy.get_status("burn_potency", 0)
            burn_count = enemy.get_status("burn_count", 0)
            if burn_potency > 0 and burn_count > 0:
                burn_dmg = enemy.take_damage(burn_potency)
                log.append(
                    f"[turn_end] {enemy.name}: [burn] {burn_dmg} damage "
                    f"(potency={burn_potency}) → HP {enemy.hp}/{enemy.max_hp}"
                )
                new_count = enemy.reduce_status("burn_count", 1)
                if new_count == 0:
                    log.append(f"[turn_end] {enemy.name}: [burn] expired")

            # ── Tremor ─────────────────────────────────────────────────
            tremor_count = enemy.get_status("tremor_count", 0)
            if tremor_count > 0:
                new_count = enemy.reduce_status("tremor_count", 1)
                if new_count == 0:
                    log.append(f"[turn_end] {enemy.name}: [tremor] count 0 → expired")
                else:
                    log.append(
                        f"[turn_end] {enemy.name}: [tremor] count {tremor_count} → {new_count}"
                    )

            # ── Poise (enemies can also carry Poise) ────────────────────
            poise_count = enemy.get_status("poise_count", 0)
            if poise_count > 0:
                new_count = enemy.reduce_status("poise_count", 1)
                if new_count == 0:
                    log.append(f"[turn_end] {enemy.name}: [poise] count 0 → expired")
                else:
                    log.append(
                        f"[turn_end] {enemy.name}: [poise] count {poise_count} → {new_count}"
                    )

            # ── Charge ─────────────────────────────────────────────────
            charge_count = enemy.get_status("charge_count", 0)
            if charge_count > 0:
                new_count = enemy.reduce_status("charge_count", 1, cleanup=False)
                if new_count == 0:
                    log.append(
                        f"[turn_end] {enemy.name}: [charge] count 0 (potency kept)"
                    )
                else:
                    log.append(
                        f"[turn_end] {enemy.name}: [charge] count {charge_count} → {new_count}"
                    )

            # ── Charge Barrier (convert to Charge Count, then expire) ───
            barrier = int(enemy.get_status("charge_barrier", 0))
            if barrier > 0:
                enemy.add_status("charge_count", barrier)
                enemy.remove_status("charge_barrier")
                log.append(
                    f"[turn_end] {enemy.name}: [charge_barrier] converted to "
                    f"+{barrier} Charge Count, removed"
                )

            # ── Stagger duration ──────────────────────────────────────
            if enemy.stagger_turns_remaining > 0:
                old_stagger_turns = enemy.stagger_turns_remaining
                enemy.tick_stagger_duration()
                if enemy.is_staggered:
                    log.append(
                        f"[turn_end] {enemy.name}: [stagger] turns {old_stagger_turns} → {enemy.stagger_turns_remaining}"
                    )
                else:
                    log.append(
                        f"[turn_end] {enemy.name}: [stagger] expired"
                    )

        # ---- Process units ---------------------------------------------------
        for unit in self.units:
            if not unit.is_alive:
                continue

            # ── Poise (status dictionary) ──────────────────────────────
            poise_count = int(unit.get_status("poise_count", 0))
            if poise_count > 0:
                old = poise_count
                new_count = unit.reduce_status("poise_count", 1)
                if new_count == 0:
                    log.append(f"[turn_end] {unit.name}: [poise] count 0 → expired")
                else:
                    log.append(
                        f"[turn_end] {unit.name}: [poise] count {old} → {new_count}"
                    )

            # ── Charge (units can also have charge) ────────────────────
            charge_count = unit.get_status("charge_count", 0)
            if charge_count > 0:
                new_count = unit.reduce_status("charge_count", 1, cleanup=False)
                if new_count == 0:
                    log.append(
                        f"[turn_end] {unit.name}: [charge] count 0 (potency kept)"
                    )
                else:
                    log.append(
                        f"[turn_end] {unit.name}: [charge] count {charge_count} → {new_count}"
                    )

            # ── Charge Barrier (convert to Charge Count, then expire) ───
            barrier = int(unit.get_status("charge_barrier", 0))
            if barrier > 0:
                unit.add_status("charge_count", barrier)
                unit.remove_status("charge_barrier")
                log.append(
                    f"[turn_end] {unit.name}: [charge_barrier] converted to "
                    f"+{barrier} Charge Count, removed"
                )

            # ── Burn (units can also have burn) ────────────────────────
            burn_potency = unit.get_status("burn_potency", 0)
            burn_count = unit.get_status("burn_count", 0)
            if burn_potency > 0 and burn_count > 0:
                burn_dmg = unit.take_damage(burn_potency)
                log.append(
                    f"[turn_end] {unit.name}: [burn] {burn_dmg} damage "
                    f"(potency={burn_potency}) → HP {unit.hp}/{unit.max_hp}"
                )
                new_count = unit.reduce_status("burn_count", 1)
                if new_count == 0:
                    log.append(f"[turn_end] {unit.name}: [burn] expired")

            # ── Tremor ─────────────────────────────────────────────────
            tremor_count = unit.get_status("tremor_count", 0)
            if tremor_count > 0:
                new_count = unit.reduce_status("tremor_count", 1)
                if new_count == 0:
                    log.append(f"[turn_end] {unit.name}: [tremor] count 0 → expired")
                else:
                    log.append(
                        f"[turn_end] {unit.name}: [tremor] count {tremor_count} → {new_count}"
                    )

        # ---- Final cleanup pass (all entities) -----------------------------
        # This is intentionally last: remove transient effects only after
        # all other turn-end behavior has resolved. Applies to both units
        # and enemies since these buffs/debuffs can land on either side.
        if TURN_END_EFFECTS_TO_CLEAR:
            for entity in self._all_entities():
                for effect_name in TURN_END_EFFECTS_TO_CLEAR:
                    if entity.has_status(effect_name):
                        entity.remove_status(effect_name)
                        log.append(
                            f"[turn_end] {entity.name}: [{effect_name}] removed by turn-end cleanup"
                        )

    # ══════════════════════════════════════════════════════════════    #  Per-skill resolution  (creates an Environment)
    # ══════════════════════════════════════════════════════════════════

    def _resolve_skill(
        self, skill: Skill, all_skills: list[Skill], owner: "Unit | None" = None
    ) -> dict:
        """
        Resolve a bare skill with no per-action config.

        Retained for callers that hold a Skill rather than an Action —
        direct invocations and the skill-sample debug scripts. Wraps the
        skill in an Action (leaving every config field unset, so all of
        it falls back to the turn-wide GameLoop values) and delegates.
        """
        return self._resolve_action(
            Action(unit=owner, skill=skill), all_skills
        )

    def _resolve_action(self, action: Action, all_skills: list[Skill]) -> dict:
        """
        Walk through all per-skill and per-coin phases for *action*.

        A fresh ``Environment`` is built from the skill, its owner, and
        the target enemy.  The environment is the single mutable state
        object every effect callback mutates.

        Every configurable input — target, clash state, coin sequence —
        comes from *action*, which falls back to the turn-wide
        ``GameLoop`` value wherever the action leaves it unset.
        """
        skill = action.skill
        owner = action.unit
        if owner is None:
            owner = self._find_owner(skill)
        # This action's target, else the turn's default (first living enemy).
        target = action.target if action.target is not None else self._pick_target()
        sequence = action.sequence if action.sequence is not None else self.sequence
        is_clashing, clash_won, clash_count = action.resolve_clash(
            self.is_clashing, self.clash_won, self.clash_count
        )

        # Build per-skill Environment
        if owner is not None and target is not None:
            env = Environment.from_skill(
                skill, owner, target,
                sequence=sequence,
                is_debugging=self.is_debugging,
            )
        else:
            # Fallback: lightweight env (no resist / OL computation)
            env = Environment(skill=skill, unit=owner, enemy=target)
            env.base = skill.base_power
            env.current_power = skill.base_power
            env.is_debugging = self.is_debugging

        env.log.append(f"=== Resolving skill: {skill.name} ===")

        # Propagate this action's clash config to env
        env.is_clashing = is_clashing
        env.clash_won = clash_won
        env.clash_count = clash_count
        self._populate_global_state(env)
        env.chain_index = action.chain_index

        # Sin Resonance's Offense Level. from_skill seeds env.ol as
        # skill.offense_level + unit.base_level, so folding the bonus in
        # here keeps the skill's own offense_level — the permanent kit —
        # untouched. (defense_level_bonus is recorded on the Action but
        # has nowhere to land yet; see Action's docstring.)
        if action.offense_level_bonus:
            env.ol += action.offense_level_bonus
            env.log.append(
                f"  [resonance] +{action.offense_level_bonus} Offense Level "
                f"→ ol={env.ol}"
            )
            env.recompute_static()

        # ── Before Use ──────────────────────────────────────────────
        self._skill_phase(skill, SkillPhase.BEFORE_USE, env)

        # ── On Use ──────────────────────────────────────────────────
        self._skill_phase(skill, SkillPhase.ON_USE, env)

        # ── Clash phases ────────────────────────────────────────────
        if env.is_clashing:
            # Clash Bleed: each participant throws coins during the clash, so
            # each procs its own Bleed min(clash_count, own bleed_count) times
            # before any attack coin resolves. Independent per participant.
            if owner is not None:
                env.proc_bleed(owner, env.clash_count, is_self=True)
                if not owner.is_alive:
                    env.CANCEL_ATTACK = True
            if env.enemy is not None:
                env.proc_bleed(env.enemy, env.clash_count, is_self=False)
                if not env.enemy.is_alive:
                    env.target_killed = True
                    env.CANCEL_ATTACK = True
                    env.log.append("  [kill] target killed by clash bleed!")

            self._skill_phase(skill, SkillPhase.CLASH_START, env)
            if env.clash_won is True:
                self._skill_phase(skill, SkillPhase.CLASH_WIN, env)
            elif env.clash_won is False:
                self._skill_phase(skill, SkillPhase.CLASH_LOSE, env)

        # ── Before Attack ───────────────────────────────────────────
        self._skill_phase(skill, SkillPhase.BEFORE_ATTACK, env)

        # ── On Unopposed Attack ─────────────────────────────────────
        if not env.is_clashing:
            self._skill_phase(skill, SkillPhase.ON_UNOPPOSED_ATTACK, env)

        # Recompute static after pre-attack effects may have changed
        # OL, def_level, or resists.
        env.recompute_static()
        env.log.append(
            f"  [recompute] static={env.static:.4f}  "
            f"(coin_power={env.coin_power}, current_power={env.current_power})"
        )

        # ── Per-Coin resolution ─────────────────────────────────────
        for i, coin in enumerate(skill.coins):
            if env.CANCEL_ATTACK:
                break
            self._resolve_coin(coin, i, env, owner)
            if env.target_killed:
                env.log.append("  [skill-stop] target defeated, skipping remaining coins")
                break

        # ── On Kill (skill-level) ──────────────────────────────────
        if env.target_killed:
            self._skill_phase(skill, SkillPhase.ON_KILL, env)

        # ── After Attack ────────────────────────────────────────────
        self._skill_phase(skill, SkillPhase.AFTER_ATTACK, env)

        # ── Drain apply queue one final time ────────────────────────
        env.update_apply_queue()

        # Stash env for post-run inspection
        self.envs.append(env)

        return {
            "skill": skill.name,
            "unit": owner.name if owner is not None else None,
            "slot": action.slot,
            "total_damage": env.total,
            "final_damage": env.total,  # backward compat alias
            "coin_damages": list(env.coin_damages),
            "status_damages": dict(env.status_damages),
            "self_damage": dict(env.self_damage),
            "log": list(env.log),
        }

    # ══════════════════════════════════════════════════════════════════
    #  Per-coin resolution
    # ══════════════════════════════════════════════════════════════════

    def _resolve_coin(
        self,
        coin: Coin,
        index: int,
        env: Environment,
        owner: "Unit | None",
    ) -> None:
        """
        Resolve a single coin following the per-coin lifecycle:

        0.  Create CoinEnvironment + fire PERSISTENT per-coin
        1.  Coin Start / early update → cancel checks
        2.  Coin flip (heads/tails)
        2b. Bleed on throw: the thrower takes bleed_potency, −1 bleed_count
        3.  Crit roll
        4.  Mid update → cancel checks
        5.  Damage = max(floor(power × static × (dynamic + coin_dynamic)), 1)
        6.  Hit → HP deduction
        7.  Status-on-hit: Rupture, Sinking
        8.  Coin-specific on-hit effects (ON_HIT, heads/tails, crit …)
        9.  Late update
        10. Tick durations
        11. Megalate update
        """
        env.advance_coin(coin, index)
        coin_stagger_level_start = int(env.enemy.stagger_level) if env.enemy is not None else 0

        # ── 0. CoinEnvironment + Persistent ─────────────────────────
        env.coin_env = CoinEnvironment(
            is_reuse=coin.reuse_count > 0,
            reuse_count=coin.reuse_count,
        )
        env.log.append(f"  -- Coin {index + 1}: {coin.name} --")

        # Fire PERSISTENT conditionals / dynamics (per-coin)
        if env.skill is not None:
            self._skill_phase(env.skill, SkillPhase.PERSISTENT, env)

        # ── 1. Coin Start (early update) ────────────────────────────
        self._coin_phase(coin, CoinPhase.COIN_START, env)

        env.log.append(f"     [early_update] active effects: {len(env.effects)}")
        for effect in list(env.effects):
            if hasattr(effect, "early_update"):
                effect.early_update(effect, env)
        env.update_apply_queue()

        if env.CANCEL_ATTACK or env.CANCEL_COIN:
            env.log.append(f"     (cancelled)")
            return

        # ── 2. Coin Flip ────────────────────────────────────────────
        head_odds = 50 + (owner.sp if owner else 0)

        # Ensure the sequence is long enough for reuse coins that
        # extend beyond the original coin count.
        while len(env.sequence) <= index:
            env.sequence.append(None)

        predetermined = env.sequence[index]

        if predetermined == "heads":
            env.coin_result = "heads"
            env.current_power += env.coin_power
        elif predetermined == "tails":
            env.coin_result = "tails"
        else:
            roll = random.randint(1, 100)
            if roll <= head_odds:
                env.coin_result = "heads"
                env.current_power += env.coin_power
            else:
                env.coin_result = "tails"

        # Always record the result (works for original + reuse coins)
        env.sequence[index] = env.coin_result

        env.log.append(
            f"     [flip] result={env.coin_result}  "
            f"(power now={env.current_power}, coin_power={env.coin_power})"
        )

        # ── 2b. Bleed on throw ─────────────────────────────────────
        # The unit throwing this coin bleeds itself: bleed_potency damage,
        # −1 bleed_count. Every coin (heads or tails, reuse included). If it
        # kills the thrower, stop before the hit lands.
        if owner is not None:
            env.proc_bleed(owner, 1, is_self=True)
            if not owner.is_alive:
                env.log.append("     [skill-stop] thrower killed by bleed, aborting attack")
                env.CANCEL_ATTACK = True
                return

        # ── 3. Crit Roll ────────────────────────────────────────────
        poise_potency = int(owner.get_status("poise_potency", 0)) if owner else 0
        poise_count = int(owner.get_status("poise_count", 0)) if owner else 0
        crit_odds = (poise_potency * 0.05 * env.crit_odds_mult) + env.crit_odds_bonus

        if poise_potency > 0 and poise_count > 0 and random.random() < crit_odds:
            env.did_crit = True
            if env.CONSUME_POISE and owner is not None:
                owner.reduce_status("poise_count", 1)
            env.log.append(f"     CRIT!")
        else:
            env.did_crit = False
            env.log.append(
                f"     [crit] no crit (poise_potency={poise_potency}, "
                f"poise_count={poise_count}, odds={crit_odds:.2%})"
            )

        # ── 4. Mid update ───────────────────────────────────────────
        env.log.append(f"     [mid_update]")
        for effect in list(env.effects):
            if hasattr(effect, "mid_update"):
                effect.mid_update(effect, env)
        env.update_apply_queue()

        if env.CANCEL_ATTACK or env.CANCEL_COIN:
            env.log.append(f"     (cancelled after mid-update)")
            return

        # ── 5. Damage calculation ───────────────────────────────────
        damage = env.compute_coin_damage()
        _, _, total_dyn = env.get_dynamic_breakdown()
        effective_static = env.get_effective_static()
        stagger_debug = env.get_stagger_debug()
        stagger_preview = env.get_stagger_preview_debug()
        env.log.append(
            f"     damage={damage}  "
            f"(power={env.current_power}, "
            f"static={effective_static:.3f}, dynamic={total_dyn:.3f}, {stagger_debug}, {stagger_preview})"
        )

        if env.is_debugging:
            env.log.append(f"     {env.debug_coin(index)}")

        # Record per-coin skill damage
        env.coin_damages.append(damage)

        # ── 6. Hit → HP deduction ───────────────────────────────────
        if env.enemy is not None:
            actual = env.enemy.take_damage(damage)
            env.log.append(
                f"     [hit] {actual} damage dealt → "
                f"enemy HP {env.enemy.hp}/{env.enemy.max_hp}"
            )
            if not env.enemy.is_alive:
                env.target_killed = True
                env.log.append(f"     [kill] target killed!")

            # ── 7a. Rupture (fixed damage on hit) ──────────────────
            # Damage always lands; CONSUME_RUPTURE only gates whether the
            # count is spent (and, with it, the Deathrite rider).
            if not env.target_killed:
                rupture_potency = env.enemy.get_status("rupture_potency", 0)
                rupture_count = env.enemy.get_status("rupture_count", 0)
                if rupture_potency > 0 and rupture_count > 0:
                    rupture_dmg = env.enemy.take_damage(rupture_potency)
                    env.total += rupture_dmg
                    env.status_damages["rupture"] = env.status_damages.get("rupture", 0) + rupture_dmg
                    env.log.append(
                        f"     [rupture] {rupture_dmg} fixed damage "
                        f"(potency={rupture_potency}) → "
                        f"enemy HP {env.enemy.hp}/{env.enemy.max_hp}"
                    )
                    if env.CONSUME_RUPTURE:
                        new_count = env.enemy.reduce_status("rupture_count", 1)
                        if new_count == 0:
                            env.log.append(f"     [rupture] count reached 0 → removed")
                    if not env.enemy.is_alive:
                        env.target_killed = True
                        env.log.append(f"     [kill] target killed by rupture!")

                    # ── 7a-DR. Deathrite【Haste】 ───────────────────
                    # Triggers when rupture is consumed by a coin hit
                    # from an attacker with 10+ Speed.
                    if (
                        env.CONSUME_RUPTURE
                        and not env.target_killed
                        and env.unit is not None
                        and env.unit.speed >= 10
                    ):
                        dr_stacks = env.enemy.get_status("deathrite_haste", 0)
                        if dr_stacks > 0:
                            dr_stacks -= 1
                            env.log.append(
                                f"     [deathrite] triggered (stacks {dr_stacks + 1} → {dr_stacks})"
                            )
                            # Gain +1 Rupture Count
                            env.enemy.add_status("rupture_count", 1)
                            env.log.append(
                                f"     [deathrite] +1 Rupture Count → "
                                f"{env.enemy.get_status('rupture_count', 0)}"
                            )
                            if dr_stacks <= 0:
                                # At 0 stacks: Gluttony damage = rupture potency, then expire.
                                # Use the potency captured before this hit's consumption
                                # (rupture_potency may already be gone by now: reduce_status
                                # removes it the moment rupture_count above hit 0).
                                env.enemy.remove_status("deathrite_haste")
                                dr_pot = rupture_potency
                                glut_res = env.enemy.sin_res.get("Gluttony", 1.0)
                                glut_mod = glut_res - 1.0
                                if glut_mod < 0:
                                    glut_mod /= 2  # weakness halved
                                dr_dmg_raw = max(math.floor(dr_pot * (1.0 + glut_mod)), 1)
                                dr_dmg = env.enemy.take_damage(dr_dmg_raw)
                                env.total += dr_dmg
                                env.status_damages["deathrite"] = (
                                    env.status_damages.get("deathrite", 0) + dr_dmg
                                )
                                env.log.append(
                                    f"     [deathrite] 0 stacks → Gluttony damage {dr_dmg} "
                                    f"(rupture_pot={dr_pot}, glut_mod={glut_mod:+.2f}) → "
                                    f"enemy HP {env.enemy.hp}/{env.enemy.max_hp}"
                                )
                                env.log.append(f"     [deathrite] expired")
                                if not env.enemy.is_alive:
                                    env.target_killed = True
                                    env.log.append(f"     [kill] target killed by deathrite!")
                            else:
                                env.enemy.set_status("deathrite_haste", dr_stacks)

            # ── 7b. Sinking (gloom damage, or sanity reduction if the ──
            #        target has sanity) ─────────────────────────────────
            if env.CONSUME_SINKING and not env.target_killed:
                sink_potency = env.enemy.get_status("sinking_potency", 0)
                sink_count = env.enemy.get_status("sinking_count", 0)
                if sink_potency > 0 and sink_count > 0:
                    if env.enemy.has_sanity:
                        old_sp = env.enemy.sp
                        new_sp = env.enemy.adjust_sp(-sink_potency)
                        env.log.append(
                            f"     [sinking] sanity {old_sp} → {new_sp} "
                            f"(potency={sink_potency})"
                        )
                    else:
                        # Apply gloom resistance to the fixed damage
                        gloom_res = env.enemy.sin_res.get("Gloom", 1.0)
                        gloom_mod = gloom_res - 1.0
                        if gloom_mod < 0:
                            gloom_mod /= 2  # weakness halved
                        sink_dmg_raw = max(math.floor(sink_potency * (1.0 + gloom_mod)), 1)
                        sink_dmg = env.enemy.take_damage(sink_dmg_raw)
                        env.total += sink_dmg
                        env.status_damages["sinking"] = env.status_damages.get("sinking", 0) + sink_dmg
                        env.log.append(
                            f"     [sinking] {sink_dmg} gloom damage "
                            f"(potency={sink_potency}, gloom_mod={gloom_mod:+.2f}) → "
                            f"enemy HP {env.enemy.hp}/{env.enemy.max_hp}"
                        )
                        if not env.enemy.is_alive:
                            env.target_killed = True
                            env.log.append(f"     [kill] target killed by sinking!")

                    # Consumption bookkeeping is shared by both payloads.
                    new_count = env.enemy.reduce_status("sinking_count", 1)
                    if new_count == 0:
                        env.log.append(f"     [sinking] count reached 0 → removed")

            # (Bleed is not consumed here: it procs on the unit throwing coins —
            # see the clash-Bleed block in _resolve_skill and step 2b above.)

        # ── 8. On-hit effects (after damage + statuses) ─────────────
        self._coin_phase(coin, CoinPhase.ON_HIT, env)

        if env.coin_result == "heads":
            self._coin_phase(coin, CoinPhase.ON_HIT_HEADS, env)
        else:
            self._coin_phase(coin, CoinPhase.ON_HIT_TAILS, env)

        if env.is_cracking:
            self._coin_phase(coin, CoinPhase.ON_HIT_WITH_CRACKING, env)
        else:
            self._coin_phase(coin, CoinPhase.ON_HIT_WITHOUT_CRACKING, env)

        if env.did_crit:
            self._coin_phase(coin, CoinPhase.ON_CRIT, env)
            if env.coin_result == "heads":
                self._coin_phase(coin, CoinPhase.ON_CRIT_HEADS, env)
            else:
                self._coin_phase(coin, CoinPhase.ON_CRIT_TAILS, env)

        # Status handlers remain outside the attack loop; this dispatcher
        # processes statuses applied by the current hit as well.
        process_on_hit_statuses(env)

        # ── 9. Late update ──────────────────────────────────────────
        env.log.append(f"     [late_update]")
        for effect in list(env.effects):
            if hasattr(effect, "late_update"):
                effect.late_update(effect, env)
        env.update_apply_queue()

        # ── 10. Tick durations ──────────────────────────────────────
        env.log.append(f"     [tick_durations]")
        env.update()

        # ── 11. Megalate update ─────────────────────────────────────
        env.log.append(f"     [megalate_update]")
        for effect in list(env.effects):
            if hasattr(effect, "megalate_update"):
                effect.megalate_update(effect, env)
        env.update_apply_queue()

        # ── Debug: per-coin state snapshot ───────────────────────────
        if env.is_debugging:
            self._debug_coin_snapshot(env, index, coin_stagger_level_start)

        # ── On Kill (coin-level) ────────────────────────────────────
        if env.target_killed:
            self._coin_phase(coin, CoinPhase.ON_KILL, env)

        # ── Reuse ───────────────────────────────────────────────────
        self._coin_phase(coin, CoinPhase.REUSE, env)
        reused = coin.check_reuse(env)
        if reused:
            env.log.append(f"     [reuse] {coin.name} will resolve again (reuse #{coin.reuse_count})")

    # ══════════════════════════════════════════════════════════════════
    #  Debug helpers
    # ══════════════════════════════════════════════════════════════════

    @staticmethod
    def _debug_coin_snapshot(
        env: Environment,
        index: int,
        stagger_level_start: int,
    ) -> None:
        """Append a compact unit / enemy / env summary to the log."""
        log = env.log
        log.append(f"     ┌─── snapshot after Coin {index + 1} ───")
        if env.unit is not None:
            u = env.unit
            log.append(
                f"     │ UNIT  hp={u.hp}/{u.max_hp}  speed={u.speed}  "
                f"sp={u.sp}  poise={u.get_status('poise_potency', 0)}/{u.get_status('poise_count', 0)}  "
                f"statuses={u.statuses}"
            )
        if env.enemy is not None:
            e = env.enemy
            log.append(
                f"     │ ENEMY hp={e.hp}/{e.max_hp}  speed={e.speed}  "
                f"statuses={e.statuses}"
            )
            stagger_level_end = int(e.stagger_level)
            stagger_changed = stagger_level_end != stagger_level_start
            log.append(
                f"     │ STAGGER level={stagger_level_end}  turns_left={e.stagger_turns_remaining}  "
                f"changed_this_coin={stagger_changed}  start={stagger_level_start}"
            )
        _, _, total_dyn = env.get_dynamic_breakdown()
        effective_static = env.get_effective_static()
        log.append(
            f"     │ ENV   total={env.total}  current_power={env.current_power}  "
            f"coin_power={env.coin_power}  static={effective_static:.4f}  "
            f"dynamic={total_dyn:.3f}"
        )
        if env.coin_env is not None:
            ce = env.coin_env
            log.append(
                f"     │ COIN  is_reuse={ce.is_reuse}  "
                f"reuse_count={ce.reuse_count}  flags={ce.flags}"
            )
        log.append(f"     └{'─' * 40}")