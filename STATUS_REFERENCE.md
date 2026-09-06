# Status Reference

This file lists status keys currently used by the combat engine and sample skills in `src/`.

# Main Status Effects

## Rupture
- `rupture_potency`: Rupture fixed-damage strength.
- `rupture_count`: Rupture hit count consumed on hit.

## Sinking
- `sinking_potency`: Sinking strength — Gloom fixed damage, or sanity reduction amount (see below).
- `sinking_count`: Sinking hit count consumed on hit (same lifecycle either way).

**Behavior depends on the target's `has_sanity`** (see Sanity below): with sanity, Sinking reduces `sp` by `sinking_potency` (flat, unresisted) via `Enemy.adjust_sp` instead of dealing damage; without sanity (the default for most enemies), it deals Gloom fixed damage as before, resisted by `sin_res["Gloom"]`. Either way `sinking_count`/`sinking_potency` consumption is unchanged. See `GameLoop._resolve_coin`'s "7b. Sinking" block.

## Burn
- `burn_potency`: Burn turn-end damage strength.
- `burn_count`: Burn duration/count consumed at turn end.

## Bleed
- `bleed_potency`: damage taken per proc.
- `bleed_count`: remaining procs.

**Bleed is consumed by the unit throwing coins, not the target of a hit.** One
proc per coin thrown (heads/tails/reuse): `bleed_potency` damage, −1 `bleed_count`
(step 2b of `GameLoop._resolve_coin`). During a clash, both participants first
proc `min(clash_count, bleed_count)` times before the coin loop (top of the
`is_clashing` block in `_resolve_skill`). A proc that would overkill spends only
the procs needed to kill (shield counts toward the threshold). A unit killed by
its own Bleed cancels the rest of its skill (`env.CANCEL_ATTACK`). The actor's
self-damage is booked to `env.self_damage["bleed"]` (not skill `total`); a clash
opponent's Bleed is enemy-directed (`env.status_damages["bleed"]` + `total`).
`env.CONSUME_BLEED` gates only the count decrement, not the damage. All via
`Environment.proc_bleed`. (Tremor `hemmorage` still consumes the target's
`bleed_count` separately — see below.)

## Tremor
- `tremor_potency`: Tremor strength.
- `tremor_count`: Tremor count/stacks.
- `tremor_type`: Active tremor amplitude type (`decay`, `reverb`, `everlasting`, `chain`, `scorch`, `hemmorage`, etc.).
- `tremor_superposition`: Blocks amplitude conversion while active.
- `tremor_last_burst_raised`: Internal bookkeeping for the last burst threshold raise amount.

**Tremor Decay's defense reduction is not a status.** While `tremor_type` is `decay` and Tremor is active, the target loses 1 defense level per 4 `tremor_potency`. `Enemy.tremor_decay_def_level_down()` derives that on read and `Environment.def_level` subtracts it, so it never appears in a status dict, needs no recompute after a Tremor change (whichever route that change took — `add_status`, `set_status`, `reduce_status`, or a constructor-supplied `statuses` dict), and cannot disagree with the Tremor state it comes from. It is untouched by the turn-end sweep and therefore applies for exactly as long as Decay Tremor does. It is independent of the `def_lvl_down` status: the two sum, and neither can overwrite the other.

## Charge
- `charge_count`: Charge stack/count used by multiple skills.
- `charge_potency`: Charge potency marker/supporting value. Grown by spending Charge Count — see below.
- `charge_barrier`: Barrier that gives (3 × status value) Shield at turn start and is removed at turn end to grant Charge Count equal to the barrier value. See `GameLoop._process_turn_start_statuses`/`_process_turn_end_statuses`.

**Charge Potency growth on consumption**: whenever Charge Count is deliberately spent via `utils.consume_charge_count` (not the passive turn-end decay), every unit accumulates the amount into `Unit.charge_consumed_total` — a lifetime counter kept regardless of identity. Units whose `Unit.gains_charge_potency_on_consume` flag is `True` additionally gain +1 `charge_potency` for every 10 cumulative Charge Count consumed (fractional progress carries over across separate consumptions). No built-in identity sets this flag yet — it's an opt-in trait a character builder can pass to `Character(...)`/`Unit(...)`.

**Shield**: `charge_barrier`'s payoff is a dedicated `shield` field on `Enemy`/`Unit` (not a status), drained by `take_damage` before HP. It does not decay on its own — only combat damage consumes it.

## Poise
- `poise_potency`: Poise potency; contributes 5% base critical chance per point.
- `poise_count`: Consumable Poise uses; decreases by one when a critical hit consumes it. Now decays at turn end for enemies as well as units (previously units only).

## Sanity
- `sp`: Sanity — dedicated field on `Enemy` (inherited by `Unit`), not a status-dict entry. Shifts coin-flip odds for whoever resolves a skill (base 50 + sp, see `GameLoop._resolve_coin`). Range is `[-45, 45]`, enforced by `Enemy.adjust_sp(delta)` rather than by individual effects clamping it themselves — anything that changes sanity should go through it.
- `has_sanity`: Dedicated bool field on `Enemy`, default `False` (most enemies don't track sanity); `Unit` overrides the default to `True`. Effects like Sinking branch on this to decide their behavior — specific enemies can opt in individually via `Enemy(..., has_sanity=True)`.


# Volatile Statuses
These are statuses that are not tied to a specific main status.

## Offense/Defense Level

Each stack shifts the level term of the damage formula by 1. `static` includes `ol_mult = ol_diff / (abs(ol_diff) + 25)`, where `ol_diff = Environment.effective_ol - Environment.def_level` — so a stack is worth progressively less the further apart the two levels already are. All four are cleared from every entity at turn end via `TURN_END_EFFECTS_TO_CLEAR`.

**Offense Level** — lives on the **attacker** (`env.unit`), read by `Environment.effective_ol`:
- `off_lvl_up`: Offense level increase.
- `off_lvl_down`: Offense level decrease.

**Defense Level** — lives on the **target** (`env.enemy`), read by `Environment.def_level`:
- `def_lvl_up`: Defense level increase.
- `def_lvl_down`: Defense level decrease.

Up and down are stored independently and netted on read (`+ up - down`), each clamped with `max(0, ...)` so a negative stack contributes nothing — the same shape as `Enemy.effective_speed`'s Haste/Bind handling. They are not capped beyond `add_status`'s default 99, and the netted result is deliberately unfloored: `ol_mult` is well-behaved for a negative `ol_diff`.

`env.ol` itself stays the raw figure (`skill.offense_level + unit.base_level`, plus any Sin Resonance bonus), so the statuses layer on at read time and never have to be unwound. `Environment.def_level` sums four sources: the target's `effective_defense`, `def_level_mod`, these statuses, and Tremor Decay's derived reduction.

Apply them with the existing helpers — `utils.add_status` for `off_lvl_*` on the acting unit, `utils.add_enemy_status` for `def_lvl_*` on the target. Because `_recompute_static` runs at the top of every `compute_coin_damage`, a mid-skill application takes effect from the next coin onward.

**`off_lvl_*` on an `Enemy` is inert.** `env.unit` is always a `Unit` or `None` — the engine has no enemy-attacks-unit path — so `utils.add_status`'s fallback to `env.enemy` would land the status where nothing reads it.

## Fragility / Damage Up Family

Each is +10% dynamic damage per stack, capped at 10 stacks (+100%). All variants are cleared from every entity (units and enemies) at turn end via `TURN_END_EFFECTS_TO_CLEAR`.

**Fragility** — lives on the target being hit (`enemy`), boosts incoming damage from whoever attacks it:
- `fragility`: Generic — boosts any attacker regardless of damage type.
- `slash_fragility`, `pierce_fragility`, `blunt_fragility`: Boosts only attacks of that physical type.
- `wrath_fragility`, `lust_fragility`, `sloth_fragility`, `gluttony_fragility`, `gloom_fragility`, `envy_fragility`, `pride_fragility`: Boosts only attacks of that sin type.

**Damage Up** — lives on the attacker (`unit`), boosts their own outgoing damage:
- `dmg_up`: Generic — boosts any skill regardless of damage type.
- `slash_dmg_up`, `pierce_dmg_up`, `blunt_dmg_up`: Boosts only skills of that physical type.
- `wrath_dmg_up`, `lust_dmg_up`, `sloth_dmg_up`, `gluttony_dmg_up`, `gloom_dmg_up`, `envy_dmg_up`, `pride_dmg_up`: Boosts only skills of that sin type.

## Speed Modifiers

- `max_speed_up`: Raises a unit's speed-roll ceiling (`speed_max`) for the turn. Cleared at turn end.
- `min_speed_up`: Raises a unit's speed-roll floor (`speed_min`) for the turn. Cleared at turn end.
- `haste`: Adds directly to `speed` for the turn (granted by Sinclair's Remise/Declared Duel and Ryoshu's Leap on kill — all queued for next turn via `queue_status`).
- `bind`: Subtracts directly from `speed` for the turn. No built-in identity inflicts it yet.
- Turn order: `GameLoop.run_turn()` sorts each turn's actions by `(-speed, team_index)` — highest `effective_speed` first, ties broken by team order — so Haste/Bind can reorder who acts first (see `Action`/`Team`/`GameLoop._order_actions` in `CODEBASE_DOCUMENTATION.md`).

## Sin Resonance (not a status)

Sin Resonance and Absolute Sin Resonance grant Offense Level (Defense Level for defensive skills) to skills sharing an Affinity in the same turn. Listed here only to be explicit that they are **not statuses**: the bonus is an internal level adjustment written onto the turn's `Action` and folded into `env.ol`, so it never appears in a status dict, is not `off_lvl_up`, and is not in `TURN_END_EFFECTS_TO_CLEAR` (there is nothing to clear — a fresh `Action` is built each turn).

The two reach the damage formula at different layers and simply sum: resonance is added to `env.ol` (the raw field) once per skill in `_resolve_action`, while `off_lvl_up`/`off_lvl_down` are read off the unit every time `effective_ol` is evaluated.

Because Haste/Bind change `effective_speed` and the chain is read in speed order, they can also change *which* resonance bonus a skill receives. Query it with `utils.check_resonance` / `check_absolute_resonance`; see §"Sin Resonance" in `CODEBASE_DOCUMENTATION.md` for the tables.



# Notes

- This list is based on keys read/written through `get_status`, `set_status`, `remove_status`, and status helper utilities in `src/`.
- `poise_potency` and `poise_count` are tracked as entries in the status dictionary (`statuses["poise_potency"]` / `statuses["poise_count"]`), not as dedicated `Unit` fields. `sp` (sanity) and `has_sanity` are the exception — both are dedicated fields on `Enemy` (inherited by `Unit`, which overrides `has_sanity`'s default).
- **Potency/Count pairing**: Rupture, Sinking, Bleed, Tremor, Burn, Poise, and Charge are each stored as a `{base}_potency`/`{base}_count` pair. `Enemy.reduce_status(status_name, amount, cleanup=True)` reduces either side by `amount` (floored at 0, removing the key at 0) and, by default, also removes its paired key the moment either side hits 0 — a pair should never linger with only one side zeroed. `Enemy.add_status(status_name, amount, cap=99, init_partner=True)` mirrors this on the gain side: when the resulting value is positive, it initializes the partner to `1` if the partner is currently 0/absent, so a gain that only grants one side of a pair doesn't leave the other side missing. Pairing on both methods is resolved purely by swapping the `_potency`/`_count` suffix on `status_name` itself, not by a registry — which is why a status that is *not* half of a pair must not be named with either suffix. None of the Volatile Statuses are (`def_lvl_down`, not `def_lvl_down_count`), so they pass through both methods untouched; a key named with a trailing `_count` would silently spawn a `_potency` partner on first gain. `cleanup=False` / `init_partner=False` are the opt-outs — Charge's turn-end reduction uses `cleanup=False` so Charge Potency persists independently of Charge Count. Both methods are inherited by `Unit`.
