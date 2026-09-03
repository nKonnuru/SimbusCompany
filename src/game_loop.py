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

from src.coin import Coin
from src.effect import CoinPhase, SkillPhase
from src.enemy import Enemy
from src.environment import CoinEnvironment, Environment
from src.passive import Passive
from src.skill import Skill
from src.status_effects import process_on_hit_statuses, reset_turn_status_effects
from src.unit import Unit
from src.utils import TURN_END_ENEMY_EFFECTS_TO_CLEAR


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

    # Optional: predetermined coin flip sequence and debug flag.
    # When set, these are forwarded to every Environment created.
    sequence: list[str | None] | None = None
    is_debugging: bool = False
    is_clashing: bool = False
    clash_won: bool | None = None
    clash_count: int = 0

    # Stash the per-skill Environments for post-run inspection.
    envs: list[Environment] = field(default_factory=list)
    _status_proc_counts: dict[tuple[int, str], int] = field(default_factory=dict, repr=False)

    # A "shared" env for turn-wide broadcasts (turn_start / turn_end).
    # Per-skill resolution creates its own Environment.
    _broadcast_env: Environment = field(default_factory=Environment)

    # ── public API ───────────────────────────────────────────────────

    def run_turn(self) -> list[dict]:
        """Execute a complete turn and return per-skill result dicts."""
        self.results.clear()
        self.envs.clear()

        # Reset passive proc counters for every entity
        for entity in self._all_entities():
            entity.reset_passives()
        reset_turn_status_effects(self._status_proc_counts)

        all_skills = self._all_skills()

        # ── Turn Start ──────────────────────────────────────────────
        for entity in self._all_entities():
            entity.apply_queued_statuses()

        self._broadcast_phase(SkillPhase.TURN_START, all_skills)
        
        for entity in self._all_entities():
            entity.refresh_tremor_decay_effect()

        # ── Set Skill Order Based on Speed ──────────────────────────
        ordered = sorted(all_skills, key=lambda s: s.speed, reverse=True)

        # ── Combat Start ────────────────────────────────────────────
        self._broadcast_phase(SkillPhase.COMBAT_START, all_skills)

        # ── Per-Skill Resolution ────────────────────────────────────
        for skill in ordered:
            result = self._resolve_skill(skill, all_skills)
            self.results.append(result)

        # ── Turn End ────────────────────────────────────────────────
        self._broadcast_phase(SkillPhase.TURN_END, all_skills)

        # ── Turn-end status processing ──────────────────────────────
        self._process_turn_end_statuses()

        return self.results

    # ── entity / skill aggregation ───────────────────────────────────

    def _all_skills(self) -> list[Skill]:
        """Merge explicitly-provided skills with those from units."""
        merged = list(self.skills)
        for unit in self.units:
            merged.extend(unit.skills)
        return merged

    def _all_entities(self) -> list[Enemy]:
        """Return every entity (units + enemies) for passive collection."""
        entities: list[Enemy] = []
        entities.extend(self.units)
        entities.extend(self.enemies)
        return entities

    def _find_owner(self, skill: Skill) -> Unit | None:
        """Return the Unit that owns *skill*, or None."""
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
        """
        for entity in self._all_entities():
            for passive in sorted(entity.passives, key=lambda p: p.priority):
                passive.execute_phase(phase, env)

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
        env.log.append(f"[broadcast] {phase.value}")
        for effect in Skill.collect_effects(skills, phase):
            effect.execute(env)
        self._fire_passives(phase, env)

    # ══════════════════════════════════════════════════════════════════    #  Turn-end status processing
    # ══════════════════════════════════════════════════════════════

    def _process_turn_end_statuses(self) -> None:
        """
        Process all turn-end status effects for every entity:

        - **Burn**: deal potency as damage, count − 1.
        - **Tremor**: count − 1; if count reaches 0, potency expires.
        - **Poise** (units only): count − 1; if count reaches 0, potency expires.
        - **Charge**: count − 1; potency persists even at 0 count.
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
                new_count = max(0, burn_count - 1)
                if new_count == 0:
                    enemy.remove_status("burn_count")
                    enemy.remove_status("burn_potency")
                    log.append(f"[turn_end] {enemy.name}: [burn] expired")
                else:
                    enemy.set_status("burn_count", min(new_count, 99))

            # ── Tremor ─────────────────────────────────────────────────
            tremor_count = enemy.get_status("tremor_count", 0)
            if tremor_count > 0:
                new_count = max(0, tremor_count - 1)
                if new_count == 0:
                    enemy.remove_status("tremor_count")
                    enemy.remove_status("tremor_potency")
                    log.append(f"[turn_end] {enemy.name}: [tremor] count 0 → expired")
                else:
                    enemy.set_status("tremor_count", new_count)
                    log.append(
                        f"[turn_end] {enemy.name}: [tremor] count {tremor_count} → {new_count}"
                    )

            # ── Charge ─────────────────────────────────────────────────
            charge_count = enemy.get_status("charge_count", 0)
            if charge_count > 0:
                new_count = max(0, charge_count - 1)
                if new_count == 0:
                    enemy.remove_status("charge_count")
                    # potency persists
                    log.append(
                        f"[turn_end] {enemy.name}: [charge] count 0 (potency kept)"
                    )
                else:
                    enemy.set_status("charge_count", new_count)
                    log.append(
                        f"[turn_end] {enemy.name}: [charge] count {charge_count} → {new_count}"
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
                new_count = max(0, poise_count - 1)
                if new_count == 0:
                    unit.remove_status("poise_count")
                    unit.remove_status("poise")
                    log.append(f"[turn_end] {unit.name}: [poise] count 0 → expired")
                else:
                    unit.set_status("poise_count", new_count)
                    log.append(
                        f"[turn_end] {unit.name}: [poise] count {old} → {new_count}"
                    )

            # ── Charge (units can also have charge) ────────────────────
            charge_count = unit.get_status("charge_count", 0)
            if charge_count > 0:
                new_count = max(0, charge_count - 1)
                if new_count == 0:
                    unit.remove_status("charge_count")
                    log.append(
                        f"[turn_end] {unit.name}: [charge] count 0 (potency kept)"
                    )
                else:
                    unit.set_status("charge_count", new_count)
                    log.append(
                        f"[turn_end] {unit.name}: [charge] count {charge_count} → {new_count}"
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
                new_count = max(0, burn_count - 1)
                if new_count == 0:
                    unit.remove_status("burn_count")
                    unit.remove_status("burn_potency")
                    log.append(f"[turn_end] {unit.name}: [burn] expired")
                else:
                    unit.set_status("burn_count", min(new_count, 99))

            # ── Tremor ─────────────────────────────────────────────────
            tremor_count = unit.get_status("tremor_count", 0)
            if tremor_count > 0:
                new_count = max(0, tremor_count - 1)
                if new_count == 0:
                    unit.remove_status("tremor_count")
                    unit.remove_status("tremor_potency")
                    log.append(f"[turn_end] {unit.name}: [tremor] count 0 → expired")
                else:
                    unit.set_status("tremor_count", new_count)
                    log.append(
                        f"[turn_end] {unit.name}: [tremor] count {tremor_count} → {new_count}"
                    )

        # ---- Final cleanup pass (enemy-only) -------------------------------
        # This is intentionally last: remove transient enemy effects only
        # after all other turn-end behavior has resolved.
        if TURN_END_ENEMY_EFFECTS_TO_CLEAR:
            for enemy in self.enemies:
                for effect_name in TURN_END_ENEMY_EFFECTS_TO_CLEAR:
                    if enemy.has_status(effect_name):
                        enemy.remove_status(effect_name)
                        log.append(
                            f"[turn_end] {enemy.name}: [{effect_name}] removed by turn-end cleanup"
                        )

    # ══════════════════════════════════════════════════════════════    #  Per-skill resolution  (creates an Environment)
    # ══════════════════════════════════════════════════════════════════

    def _resolve_skill(
        self, skill: Skill, all_skills: list[Skill]
    ) -> dict:
        """
        Walk through all per-skill and per-coin phases for *skill*.

        A fresh ``Environment`` is built from the skill, its owner, and
        the target enemy.  The environment is the single mutable state
        object every effect callback mutates.
        """
        owner = self._find_owner(skill)
        target = self._pick_target()

        # Build per-skill Environment
        if owner is not None and target is not None:
            env = Environment.from_skill(
                skill, owner, target,
                sequence=self.sequence,
                is_debugging=self.is_debugging,
            )
        else:
            # Fallback: lightweight env (no resist / OL computation)
            env = Environment(skill=skill, unit=owner, enemy=target)
            env.base = skill.base_power
            env.current_power = skill.base_power
            env.is_debugging = self.is_debugging

        env.log.append(f"=== Resolving skill: {skill.name} ===")

        # Propagate clash flags from GameLoop to env
        env.is_clashing = self.is_clashing
        env.clash_won = self.clash_won
        env.clash_count = self.clash_count
        # Bleed uses clash_count as a per-skill budget, consumed across coins.
        env.global_state["_bleed_clash_remaining"] = max(0, env.clash_count)
        env.global_state["units"] = self.units
        env.global_state["enemies"] = self.enemies
        env.global_state["_status_proc_counts"] = self._status_proc_counts

        # ── Before Use ──────────────────────────────────────────────
        self._skill_phase(skill, SkillPhase.BEFORE_USE, env)

        # ── On Use ──────────────────────────────────────────────────
        self._skill_phase(skill, SkillPhase.ON_USE, env)

        # ── Clash phases ────────────────────────────────────────────
        if env.is_clashing:
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
            "total_damage": env.total,
            "final_damage": env.total,  # backward compat alias
            "coin_damages": list(env.coin_damages),
            "status_damages": dict(env.status_damages),
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
        3.  Crit roll
        4.  Mid update → cancel checks
        5.  Damage = max(floor(power × static × (dynamic + coin_dynamic)), 1)
        6.  Hit → HP deduction
        7.  Status-on-hit: Rupture, Sinking, Bleed
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

        # ── 3. Crit Roll ────────────────────────────────────────────
        poise_potency = int(owner.get_status("poise", 0)) if owner else 0
        poise_count = int(owner.get_status("poise_count", 0)) if owner else 0
        crit_odds = (poise_potency * 0.05 * env.crit_odds_mult) + env.crit_odds_bonus

        if poise_potency > 0 and poise_count > 0 and random.random() < crit_odds:
            env.did_crit = True
            if env.CONSUME_POISE and owner is not None:
                new_count = max(0, int(owner.get_status("poise_count", 0)) - 1)
                if new_count == 0:
                    owner.remove_status("poise_count")
                    owner.remove_status("poise")
                else:
                    owner.set_status("poise_count", new_count)
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
            if env.CONSUME_RUPTURE and not env.target_killed:
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
                    new_count = max(0, rupture_count - 1)
                    if new_count == 0:
                        env.enemy.remove_status("rupture_count")
                        env.log.append(f"     [rupture] count reached 0 → removed")
                    else:
                        env.enemy.set_status("rupture_count", min(new_count, 99))
                    env.enemy.set_status(
                        "rupture_potency", min(rupture_potency, 99)
                    )
                    if not env.enemy.is_alive:
                        env.target_killed = True
                        env.log.append(f"     [kill] target killed by rupture!")

                    # ── 7a-DR. Deathrite【Haste】 ───────────────────
                    # Triggers when rupture is consumed by a coin hit
                    # from an attacker with 10+ Speed.
                    if (
                        not env.target_killed
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
                            dr_rc = env.enemy.get_status("rupture_count", 0)
                            env.enemy.set_status("rupture_count", min(dr_rc + 1, 99))
                            env.log.append(
                                f"     [deathrite] +1 Rupture Count → "
                                f"{env.enemy.get_status('rupture_count', 0)}"
                            )
                            if dr_stacks <= 0:
                                # At 0 stacks: Gluttony damage = rupture potency, then expire
                                env.enemy.remove_status("deathrite_haste")
                                dr_pot = env.enemy.get_status("rupture_potency", 0)
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

            # ── 7b. Sinking (gloom fixed damage on hit) ────────────────
            if env.CONSUME_SINKING and not env.target_killed:
                sink_potency = env.enemy.get_status("sinking_potency", 0)
                sink_count = env.enemy.get_status("sinking_count", 0)
                if sink_potency > 0 and sink_count > 0:
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
                    new_count = max(0, sink_count - 1)
                    if new_count == 0:
                        env.enemy.remove_status("sinking_count")
                        env.log.append(f"     [sinking] count reached 0 → removed")
                    else:
                        env.enemy.set_status("sinking_count", min(new_count, 99))
                    env.enemy.set_status(
                        "sinking_potency", min(sink_potency, 99)
                    )
                    if not env.enemy.is_alive:
                        env.target_killed = True
                        env.log.append(f"     [kill] target killed by sinking!")

            # ── 7c. Bleed (potency × clash_count, capped at count) ─────
            if env.CONSUME_BLEED and not env.target_killed:
                bleed_potency = env.enemy.get_status("bleed_potency", 0)
                bleed_count = env.enemy.get_status("bleed_count", 0)
                bleed_remaining = max(0, int(env.global_state.get("_bleed_clash_remaining", 0)))
                if bleed_potency > 0 and bleed_count > 0 and bleed_remaining > 0:
                    effective = min(bleed_remaining, bleed_count)
                    bleed_dmg_raw = bleed_potency * effective
                    bleed_dmg = env.enemy.take_damage(bleed_dmg_raw)
                    env.total += bleed_dmg
                    env.status_damages["bleed"] = env.status_damages.get("bleed", 0) + bleed_dmg
                    env.log.append(
                        f"     [bleed] {bleed_dmg} damage "
                        f"(potency={bleed_potency} × {effective} hits) → "
                        f"enemy HP {env.enemy.hp}/{env.enemy.max_hp}"
                    )
                    new_count = max(0, bleed_count - effective)
                    if new_count == 0:
                        env.enemy.remove_status("bleed_count")
                        env.enemy.remove_status("bleed_potency")
                        env.log.append(f"     [bleed] count reached 0 → removed")
                    else:
                        env.enemy.set_status("bleed_count", min(new_count, 99))

                    env.global_state["_bleed_clash_remaining"] = max(0, bleed_remaining - effective)

                    if not env.enemy.is_alive:
                        env.target_killed = True
                        env.log.append(f"     [kill] target killed by bleed!")

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
                f"sp={u.sp}  poise={u.get_status('poise', 0)}/{u.get_status('poise_count', 0)}  "
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