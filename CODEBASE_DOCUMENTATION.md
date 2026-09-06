# Codebase Documentation

Poise is tracked entirely in the unit status dictionary:
- `statuses["poise_potency"]` is the actual Poise amount and contributes `5%` base critical chance per point.
- `statuses["poise_count"]` is the consumable number of Poise uses and decreases by one when a critical hit consumes it.
- When a unit gains Poise Count while `statuses["poise_potency"]` is zero, Poise is initialized to `1`.
- When Poise Count reaches zero, `statuses["poise_potency"]` is removed.
### Core formula

`damage = max(floor(current_power × effective_static × total_dynamic), 1)`

### Formula components

- `current_power` starts at the skill's base power and increases when coins flip heads, gain coin power, or receive other power buffs.

	env.unit.set_status("poise_count", new_count)
env.base = skill.base_power

if env.coin_result == "heads":
	env.current_power += env.coin_power
		env.unit.set_status("poise_potency", 1)
	env.unit.set_status("poise_count", new_count)

- `effective_static` starts from the skill's recomputed static value and can be modified by crits, offense/defense level differences, resistances, observation level, clash count, and stagger overrides.

```python
	poise_count = int(unit.get_status("poise_count", 0))
	new_count = max(0, poise_count - 1)
	if new_count == 0:
		unit.remove_status("poise_count")
		unit.remove_status("poise_potency")
	else:
		unit.set_status("poise_count", new_count)
ol_mult = ol_diff / (abs(ol_diff) + 25) if (abs(ol_diff) + 25) != 0 else 0.0

env.p_res_mod = phys_res - 1.0
if env.p_res_mod < 0:
	env.p_res_mod /= 2  # weakness halved

env.s_res_mod = sin_res - 1.0
if env.s_res_mod < 0:
	env.s_res_mod /= 2

observation = enemy.observation_level * 0.03

env.static = (
	1.0
	+ ol_mult
	+ env.p_res_mod
	+ env.s_res_mod
	+ observation
	+ (env.clash_count * 0.03)
)

effective_static = env.static
if env.did_crit:
	effective_static += env.crit_bonus

if env.enemy.is_staggered:
	stagger_p_res_mod = env.enemy.get_stagger_physical_resistance() - 1.0
	effective_static += stagger_p_res_mod - env.p_res_mod
```

- `total_dynamic` is the sum of the skill-wide dynamic modifier, any per-coin dynamic modifier, and fragility-based bonuses.

```python
coin_dynamic = env.coin_env.dynamic if env.coin_env is not None else 0.0

fragility_bonus = 0.0
slash_fragility = int(enemy.get_status("slash_fragility", 0))
fragility_bonus += min(slash_fragility, 10) * 0.1

total_dynamic = env.dynamic + coin_dynamic + fragility_bonus
```

### Static multiplier inputs

- Offense level vs. enemy defense level contributes an OL multiplier: `ol_diff = env.effective_ol - env.def_level`, then `ol_mult = ol_diff / (abs(ol_diff) + 25)`. Both sides are properties, not raw fields — `effective_ol` layers the `off_lvl_up`/`off_lvl_down` statuses onto `env.ol`, and `def_level` layers `def_lvl_up`/`def_lvl_down`, `def_level_mod`, and Tremor Decay's derived reduction onto the enemy's `effective_defense`.
- Physical and sin resistances are converted into additive modifiers.
- Observation level adds `+3%` static per stack.
- Clash wins add `+3%` static per clash count.
- Critical hits add the crit bonus to static.
- If the enemy is staggered, physical damage types use stagger-based resistance instead of the enemy's normal physical resistance.

### Dynamic multiplier inputs

- `env.dynamic` is the skill-wide dynamic multiplier.
- `coin_env.dynamic` is per-coin dynamic.
- Slash fragility adds `+10%` dynamic per stack up to 10 stacks.

### Final damage calculation

```python
damage = max(
	math.floor(env.current_power * effective_static * total_dynamic),
	1,
)
```

### Execution order

1. The environment is built from the skill, acting unit, and target enemy.
2. Static is recomputed before resolution and again before each coin.
3. Coin flip and crit are resolved.
4. Coin effects can modify power, static-related state, enemy statuses, or dynamic modifiers.
5. Damage is computed and then applied to the target.
6. On-hit effects, kill checks, and post-hit updates run after damage is dealt.

### Environment construction

From `Environment.from_skill`:

```python
env.base = skill.base_power
env.coin_power = skill.coin_power
env.current_power = env.base
env.ol = skill.offense_level + unit.base_level

if sequence is not None:
	env.sequence = list(sequence)
else:
	env.sequence = [None] * len(skill.coins)

p_type, s_type = skill.damage_type
phys_res = enemy.phys_res.get(p_type, 1.0)
sin_res = enemy.sin_res.get(s_type, 1.0)

env.p_res_mod = phys_res - 1.0
if env.p_res_mod < 0:
	env.p_res_mod /= 2

env.s_res_mod = sin_res - 1.0
if env.s_res_mod < 0:
	env.s_res_mod /= 2

env._recompute_static()
```

`env.ol` is the *raw* offense level and stays that way — Sin Resonance adds to it in `_resolve_action`, but the `off_lvl_up`/`off_lvl_down` statuses are layered on at read time by the `effective_ol` property instead, so nothing has to unwind them when they are swept at turn end.

### Environment fields used by damage

- References: `skill`, `unit`, `enemy`
- Multipliers: `static`, `dynamic`, `p_res_mod`, `s_res_mod`, `ol`, `def_level_mod`
- Derived level properties: `effective_ol`, `def_level` — what `_recompute_static` actually reads
- Combat state: `base`, `coin_power`, `current_power`, `current_damage`, `total`
- Crit state: `crit_bonus`, `crit_odds_mult`, `crit_odds_bonus`, `did_crit`
- Coin tracking: `current_coin`, `current_coin_index`, `coin_result`, `coin_env`, `sequence`
- Flags and flow control: `is_clashing`, `clash_won`, `clash_count`, `is_cracking`, `target_killed`, `CANCEL_ATTACK`, `CANCEL_COIN`
- Effect bookkeeping: `effects`, `apply_queue`, `global_state`

The engine stores the final per-coin damage in `current_damage` and accumulates total skill damage in `total`.

## Game Loop Implementation

`GameLoop.run_turn()` executes one full combat turn and returns a result dict per skill.

### Turn-level flow

1. Reset passive proc counters for all entities.
2. Build the turn manifest once as a list of `Action`s (`_turn_actions()`); `all_skills` is derived from it.
3. Apply queued next-turn statuses, then apply start-of-turn status processing (currently: Charge Barrier grants Shield).
4. Broadcast `turn_start` across all skills and passives.
5. Order the actions by speed, team order breaking ties (`_order_actions()`).
6. Run **pre-combat checks** across the whole selection, then re-order (a check may have changed a unit's speed).
7. Broadcast `combat_start` across all skills and passives.
8. Resolve each action in order (each creates its own `Environment`), reading its per-action config directly — no lookup.
9. Broadcast `turn_end` across all skills and passives.
10. Apply end-of-turn status processing (burn, tremor, charge, charge barrier, poise, stagger ticks, and cleanup list).

There is no turn-start Tremor Decay refresh step: Decay's defense reduction is derived on read (see "Tremor Decay" below), so there is nothing to re-assert.

```python
def run_turn(self) -> list[dict]:
	self.results.clear()
	self.envs.clear()

	for entity in self._all_entities():
		entity.reset_passives()

	ordered = self._turn_actions()
	all_skills = [action.skill for action in ordered]

	for entity in self._all_entities():
		entity.apply_queued_statuses()
	self._process_turn_start_statuses()

	self._broadcast_phase(SkillPhase.TURN_START, all_skills)

	ordered = self._order_actions(ordered)

	# Checks see the plan already in resolution order; the re-sort after
	# lets a check legitimately change that order (by granting Haste, say).
	ordered = self._run_pre_combat_checks(ordered)
	ordered = self._order_actions(ordered)
	all_skills = [action.skill for action in ordered]

	self._broadcast_phase(SkillPhase.COMBAT_START, all_skills)

	for action in ordered:
		self.results.append(self._resolve_action(action, all_skills))

	self._broadcast_phase(SkillPhase.TURN_END, all_skills)
	self._process_turn_end_statuses()
	return self.results
```

`_turn_actions()` is the canonical manifest of what resolves this turn, always as `Action`s. When `GameLoop.actions` is set it is used directly. Otherwise one `Action` is synthesized per skill sitting in each unit's `skills` list (legacy convention), plus one ownerless `Action` per entry in `GameLoop.skills`. Synthesized Actions leave every config field unset, so they fall back to the turn-wide `GameLoop` values — preserving the exact behavior those legacy paths always had.

### Turn order: speed first, team order as tiebreaker

`_order_actions()` sorts by `(-speed, team_index)` ascending — highest speed first, and among equal speeds the earliest team position first:

```python
sorted(actions, key=lambda a: (-a.resolve_speed(), self._team_index(a)))
```

`Action.resolve_speed()` returns an explicit `action.speed` if set (this is how one unit takes several independently-ordered actions in a turn), else the owner's `effective_speed` so Haste/Bind apply, else the skill's own vestigial `speed` field for an ownerless skill.

`_team_index()` reads `Team.position_of` when a `Team` was supplied, else the unit's index in `GameLoop.units`, else sorts last. Every mode returns a stable integer, so equal-speed actions never order randomly.

### Teams

`src.team.Team` is the player's ordered roster. Team order is **not** turn order — speed decides that — but it breaks speed ties, which is its mechanical role. `position_of` matches by identity rather than equality, since `Unit` is a mutable dataclass whose `__eq__` compares every field and the same Identity may legitimately appear on a team twice.

```python
loop = GameLoop(team=Team(members=[sinclair, ryoshu]), enemies=[enemy], actions=[...])
```

Passing `team=` populates `GameLoop.units` from it in `__post_init__`, so every entity-wide pass (passives, turn-start/turn-end status processing) works unchanged. An explicit `units=` list wins if both are given.

### Actions: declaring what each unit does this turn

`Unit.skills` is a unit's permanent kit and is never mutated to express "what's happening this turn." Instead, a caller builds one `src.action.Action` per acting unit and passes them to `GameLoop(actions=[...])`. An Action also carries **that skill's own configuration** for the turn:

| Field | Unset → falls back to |
|---|---|
| `target` | `GameLoop._pick_target()` (first living enemy) |
| `speed` | owner's `effective_speed`, then `skill.speed` |
| `is_clashing` / `clash_won` / `clash_count` | the `GameLoop` clash triple |
| `sequence` | `GameLoop.sequence` |
| `slot` | — metadata only (`"1"`/`"2"`/`"3"`/`"defense"`) |

```python
from src.action import Action

loop = GameLoop(
    team=Team(members=[yi_sang, faust]),
    enemies=[enemy, elite],
    actions=[
        Action(unit=yi_sang, skill=yi_sang.skills[0], slot="1"),
        Action(unit=faust, skill=faust.skills[2], slot="3",
               target=elite, is_clashing=True, clash_won=True, clash_count=2),
    ],
)
results = loop.run_turn()
```

Clash state resolves as a **single unit, not three independent fields**: `clash_won=None` legitimately means "clashing, no result yet", so it cannot also stand for "unset". `is_clashing` is therefore the sole discriminator — if an action declares it, that action's whole triple is used; otherwise the loop's whole triple is. See `Action.resolve_clash`.

`Action.skill` *is* the chosen form — a slot may hold several forms (`Unit.get_skill_forms`) and the caller picks one before building the Action. `slot` rides along for display and for pre-combat checks.

Actions are passed in no particular order (they're sorted inside `run_turn`). Omitting `actions` falls back to the legacy behavior of resolving whatever is currently sitting in each unit's `skills` list. `_resolve_skill(skill, all_skills, owner=...)` is retained as a thin wrapper for callers holding a bare Skill; it wraps it in a config-less Action and delegates to `_resolve_action`.

Each result dict carries `"unit"` (the owner's name, or `None`) and `"slot"` alongside `"skill"`, so a caller can label several results per turn.

### Pre-combat checks

`src.pre_combat` holds checks that run once per turn, **between Turn Start and Combat Start** — the point where every unit's skill for the turn is known but nothing has resolved. A check sees the entire selection at once, which is what distinguishes it from a skill/coin `Effect` (one skill) or a `Passive` (one entity).

```python
@dataclass
class TurnPlan:
    actions: list[Action]      # speed-ordered at the time checks run
    team: Team | None
    enemies: list[Enemy]
    log: list[str]             # the turn's broadcast log
    state: dict[str, Any]      # published into every env.global_state

PreCombatCheck = Callable[[TurnPlan], None]
PRE_COMBAT_CHECKS: list[PreCombatCheck] = [resonance_check]
```

`PRE_COMBAT_CHECKS` is the extension point — append a `(TurnPlan) -> None` function to register one. `GameLoop.pre_combat_checks` overrides the module registry when set, so tests can supply their own without touching global state.

A check may mutate `plan.actions` in place or replace it wholesale; both are honored. Because `run_turn` re-sorts after checks run, a check that grants Haste or Bind changes the actual resolution order. **Registry order matters for that reason**: Sin Resonance is derived from the ordering, so a speed-changing check must be registered *before* `resonance_check` or resonance is computed on a stale chain.

Anything a check writes to `plan.state` is merged into `env.global_state` via `GameLoop._populate_global_state` — for per-skill Environments **and** the shared broadcast env — which is how resonance reaches effects and passives from Combat Start onward. `units`, `enemies`, and `_status_proc_counts` travel the same way, so a turn-wide effect reading `global_state["units"]` now sees the roster rather than an empty list.

## Sin Resonance

[src/resonance.py](src/resonance.py) implements the main pre-combat check. Resonance keys on a skill's **Affinity** — `skill.damage_type[1]`, normalized `.strip().lower()` against `SIN_DAMAGE_TYPES`, since skills declare it capitalized (`("Slash", "Lust")`) while the type tuples in `utils` are lowercase.

- **Sin Resonance** ("Reson.") — 2+ skills of the same Affinity anywhere in the chain. **Adjacency is not required.** The bonus ramps by the skill's position among that Affinity's skills, so the rightmost gains the most.
- **Absolute Sin Resonance** ("A-Reson.") — 3+ skills of the same Affinity **consecutive in the full chain**; a different Affinity between them breaks the run. The bonus is keyed on the run's length and is flat across every skill in it.
- **The two never stack — a skill takes whichever grants more** (`_combine_bonus` = `max`).

The chain is **speed order** (resolution order, fastest first), which is what the check receives. Team order only breaks speed ties.

### Tables

Index 0 is unused; the last entry also serves 11+.

```python
#                        1  2  3  4  5  6  7  8  9 10 11+
RESONANCE_OL          = (0, 0, 1, 3, 3, 5, 5, 7, 7, 9, 9, 11)  # index = position
ABSOLUTE_RESONANCE_OL = (0, 0, 0, 3, 5, 5, 7, 7, 9, 9, 11, 11) # index = run length
```

### Worked examples

`Lust, Pride, Lust, Lust, Lust, Pride, Lust` — A-Reson. wins in the middle:

| idx | sin | Reson. pos | Reson. | A-Reson. | **final = max** |
|---|---|---|---|---|---|
| 0 | Lust | 1 | +0 | — | **+0** |
| 1 | Pride | 1 | +0 | — | **+0** |
| 2 | Lust | 2 | +1 | +3 (run 2-4) | **+3** |
| 3 | Lust | 3 | +3 | +3 | **+3** |
| 4 | Lust | 4 | +3 | +3 | **+3** |
| 5 | Pride | 2 | +1 | — | **+1** |
| 6 | Lust | 5 | +5 | — | **+5** |

Lust 0 and 2 are *not* adjacent — the Pride at index 1 breaks the run.

`Pride, Pride, Lust, Pride, Pride, Lust, Pride, Pride, Pride` — positional Reson. wins:

| idx | sin | Reson. pos | Reson. | A-Reson. | **final = max** |
|---|---|---|---|---|---|
| 0,1 | Pride | 1,2 | +0,+1 | — | **+0,+1** |
| 2 | Lust | 1 | +0 | — | **+0** |
| 3,4 | Pride | 3,4 | +3,+3 | — | **+3,+3** |
| 5 | Lust | 2 | +1 | — | **+1** |
| 6,7,8 | Pride | 5,6,7 | +5,+5,+7 | +3 (run 6-8) | **+5,+5,+7** |

7 Pride, but the only run of 3+ is the last three (runs 0-1 and 3-4 are length 2). They sit deep enough in the positional chain that Reson. beats the short run's flat +3 — this is the case that rules out "A-Reson. replaces Reson.".

### Every line is kept — three different A-Reson. numbers

`compute_resonance` records each qualifying run as its own `ResonanceChain` rather than collapsing them, because "the A-Reson. number" is three distinct quantities that only agree when an Affinity forms exactly one run:

```python
@dataclass(frozen=True)
class ResonanceChain:
    sin: str        # normalized, e.g. "envy"
    start: int      # first chain index, in speed order
    length: int
    bonus: int      # the flat OL/DL this run granted each member

    end / indices / contains(index)
```

On `Envy Envy Envy Pride Envy Envy Envy`:

| quantity | call | value |
|---|---|---|
| Reson. count | `result.count("envy")` | 6 |
| **longest** run — the dashboard rule | `result.absolute_longest("envy")` | 3 |
| **sum** across runs | `result.absolute_sum("envy")` | 6 |
| the run a given skill is in | `result.chain_at(env.chain_index)` | whichever of the two |

There is deliberately **no bare `result.absolute(sin)`** — an old call site raises `AttributeError` instead of quietly returning the longest when it meant the sum.

Other accessors: `result.chains(sin=None)` returns every line (all Affinities when `sin` is omitted, each carrying its own `.sin`); `result.indices_of(sin)` returns the Reson. line — every chain position that Affinity occupies. Every accessor normalizes the Affinity it is given, so `"Envy"` and `"envy"` are the same key, and a sin that never resonated yields `0`/`()` rather than raising.

- "A-Reson. also counts as Reson." needs no special handling — `count` is the Affinity's total and is always ≥ any run inside it.

### Which chain am I in?

`Action.chain_index` and `Environment.chain_index` carry a skill's position in the turn's chain (speed order), assigned by `resonance_check` in the same pass that computes resonance so the two can never disagree. That is what lets a skill ask about **its own** run:

```python
def _my_chain_is_big_envy(env) -> bool:
    result = get_resonance(env)
    if result is None:
        return False
    chain = result.chain_at(env.chain_index)
    return chain is not None and chain.sin == "envy" and chain.length >= 4
```

Broadcast envs have no single resolving skill, so `chain_index` stays `-1` there and own-chain queries return `False` — ask by Affinity instead.

### Phase availability

`GameLoop._populate_global_state` fills both per-skill Environments and the shared broadcast env, so turn-wide effects see the same context mid-resolution ones do.

| phase | resonance readable? |
|---|---|
| `turn_start` | **no** — checks have not run; the chain is not final (a Turn Start effect can grant Haste and reorder it) |
| `combat_start` | yes |
| per-skill / per-coin | yes, plus `env.chain_index` |
| `turn_end` | yes |

`run_turn` clears `_broadcast_env.global_state` each turn, so a second turn's Turn Start never sees the previous turn's result.

`resonance_check` writes each bonus onto its own `Action`: `offense_level_bonus` for an attack skill, `defense_level_bonus` for a defensive one (`is_offensive` = physical type in `PHYSICAL_DAMAGE_TYPES`; anything else, today only `"Evade"`, is defensive). Defensive skills carry a real Affinity — Ryoshu's Charged Evade is `("Evade", "Lust")` — so **they chain normally**. The defense bonus is currently recorded but not consumed: the engine has no defensive resolution path yet.

`_resolve_action` folds the offense bonus into `env.ol` and recomputes static, so `skill.offense_level` — the permanent kit — is never mutated:

```python
if action.offense_level_bonus:
    env.ol += action.offense_level_bonus
    env.recompute_static()
```

These are **internal** level adjustments: not statuses, never in the status dict, and not in `TURN_END_EFFECTS_TO_CLEAR` (a fresh `Action` is built each turn, so there is nothing to clear).

Do not confuse the resonance bonus with the `off_lvl_up`/`off_lvl_down` statuses, which *are* statuses and *are* swept at turn end. The two reach the formula at different layers and simply sum: resonance is added to `env.ol` once per skill here, while the statuses are read off the unit every time `effective_ol` is evaluated.

### Querying resonance from effects and passives

Four helpers in `utils`, gating effects the same way `check_count` does. All return `False` when no resonance was computed, so a skill resolved outside a resonance-aware loop simply doesn't trigger:

| helper | asks |
|---|---|
| `check_resonance(env, sin=None, minimum=2)` | Reson. count for an Affinity |
| `check_absolute_resonance(env, minimum=3)` | **this skill's own run** — no `sin`, since the run knows its own |
| `check_absolute_resonance_sum(env, sin=None, minimum=3)` | summed across that Affinity's runs |
| `check_absolute_resonance_longest(env, sin=None, minimum=3)` | longest run (dashboard rule) |

`sin=None` means the resolving skill's own Affinity. Pass an explicit Affinity to use these from a broadcast phase, where there is no resolving skill.

```python
skill.add_effect(Effect(
    name="Reson. 3+: +10% damage",
    phase=SkillPhase.PERSISTENT,
    apply=add_dynamic, args=(0.10,),
    condition=check_resonance, condition_args=("lust", 3),
))

skill.add_effect(Effect(
    name="[Combat Start] 4+ Envy A-Reson. sum",
    phase=SkillPhase.COMBAT_START,
    apply=_swap_skill_form,
    condition=check_absolute_resonance_sum, condition_args=("envy", 4),
))
```

`utils.get_resonance(env)` returns the raw `ResonanceResult` (or `None`) for effects whose arithmetic no helper anticipates.

`GameLoop.resonance` exposes the turn's `ResonanceResult` after `run_turn`; `ResonanceResult.summary()` renders the dashboard line (`Lust x5 (A-Reson 3) | Pride x2`).

### Per-skill flow

- `before_use` → `on_use`
- Optional clash phases: `clash_start`, `clash_win` / `clash_lose`
- `before_attack` → `on_unopposed_attack`
- Recompute static after pre-attack effects
- Resolve coins (see below)
- `on_kill` (skill-level) if target died
- `after_attack`

```python
def _resolve_skill(self, skill, all_skills, owner=None) -> dict:
	if owner is None:
		owner = self._find_owner(skill)  # fallback only; run_turn always passes owner
	target = self._pick_target()
	env = Environment.from_skill(skill, owner, target, sequence=self.sequence, is_debugging=self.is_debugging)

	self._skill_phase(skill, SkillPhase.BEFORE_USE, env)
	self._skill_phase(skill, SkillPhase.ON_USE, env)

	if env.is_clashing:
		self._skill_phase(skill, SkillPhase.CLASH_START, env)
		if env.clash_won is True:
			self._skill_phase(skill, SkillPhase.CLASH_WIN, env)
		elif env.clash_won is False:
			self._skill_phase(skill, SkillPhase.CLASH_LOSE, env)

	self._skill_phase(skill, SkillPhase.BEFORE_ATTACK, env)
	if not env.is_clashing:
		self._skill_phase(skill, SkillPhase.ON_UNOPPOSED_ATTACK, env)

	env.recompute_static()

	for i, coin in enumerate(skill.coins):
		if env.CANCEL_ATTACK:
			break
		self._resolve_coin(coin, i, env, owner)
		if env.target_killed:
			break

	if env.target_killed:
		self._skill_phase(skill, SkillPhase.ON_KILL, env)

	self._skill_phase(skill, SkillPhase.AFTER_ATTACK, env)
	env.update_apply_queue()

	self.envs.append(env)
	return {
		"skill": skill.name,
		"total_damage": env.total,
		"coin_damages": list(env.coin_damages),
		"status_damages": dict(env.status_damages),   # damage dealt to the enemy, per status
		"self_damage": dict(env.self_damage),         # damage the actor dealt to itself (e.g. Bleed); NOT in total
		"log": list(env.log),
	}
```

### Per-coin flow

- `coin_start`
- Coin flip (heads/tails) and crit roll
- `mid_update` effects and apply queue
- Damage calculation and hit application
- `on_hit` → `on_hit_heads`/`on_hit_tails` → `on_hit_with_cracking`/`on_hit_without_cracking`
- Optional crit phases: `on_crit` → `on_crit_heads`/`on_crit_tails`
- `late_update`, `tick_durations`, `megalate_update`
- `on_kill` (coin-level) if target died
- `reuse` phase and reuse check

```python
def _resolve_coin(self, coin, index, env, owner) -> None:
	env.advance_coin(coin, index)
	env.coin_env = CoinEnvironment(is_reuse=coin.reuse_count > 0, reuse_count=coin.reuse_count)

	self._coin_phase(coin, CoinPhase.COIN_START, env)
	# ... early updates and cancel checks ...

	# coin flip
	if env.coin_result == "heads":
		env.current_power += env.coin_power

	# crit roll and mid update
	# ...

	damage = env.compute_coin_damage()
	env.coin_damages.append(damage)

	if env.enemy is not None:
		actual = env.enemy.take_damage(damage)
		if not env.enemy.is_alive:
			env.target_killed = True

	self._coin_phase(coin, CoinPhase.ON_HIT, env)
	# heads/tails + cracking branches
	# crit branches

	# late update, tick durations, megalate update

	if env.target_killed:
		self._coin_phase(coin, CoinPhase.ON_KILL, env)

	self._coin_phase(coin, CoinPhase.REUSE, env)
```

### Passives and merged effects

- For each phase, base skill/coin effects are merged with owner/target passives and sorted by effect priority.
- Passive proc limits are enforced per passive, and logs record phase-level proc counts.
- Skill-level `on_kill` explicitly skips passive merge so kill passives only fire at coin `on_kill`.

```python
def _execute_merged_phase_effects(self, *, phase, env, base_effects) -> None:
	merged = [(eff, None) for eff in base_effects]

	if isinstance(phase, SkillPhase) and phase is SkillPhase.ON_KILL:
		merged.sort(key=lambda item: item[0].priority)
		for effect, passive in merged:
			if passive is None:
				effect.execute(env)
		return

	for entity in (env.unit, env.enemy):
		for passive in self._sorted_entity_passives(entity):
			for eff in passive.get_effects(phase):
				merged.append((eff, passive))

	merged.sort(key=lambda item: item[0].priority)

	for effect, passive in merged:
		if passive is None:
			effect.execute(env)
			continue
		if passive.can_proc(env) and effect.execute(env):
			passive._proc_count += 1
```

### Turn-end status processing

- Enemy and unit status decay is applied after the turn-end broadcast.
- A cleanup registry (`TURN_END_EFFECTS_TO_CLEAR`) removes transient statuses from **every entity** (units and enemies alike) last.

```python
def _process_turn_end_statuses(self) -> None:
	for enemy in self.enemies:
		if not enemy.is_alive:
			continue
		# burn, tremor, charge, stagger duration
		# ...

	for unit in self.units:
		if not unit.is_alive:
			continue
		# poise, charge, burn, tremor
		# ...

	for entity in self._all_entities():
		for effect_name in TURN_END_EFFECTS_TO_CLEAR:
			if entity.has_status(effect_name):
				entity.remove_status(effect_name)
```

## Status Effects

Status effects are tracked on `Enemy.statuses` (and `Unit.statuses` for shared effects like charge/burn/tremor). Some effects are applied on hit, some tick at turn end, and some trigger special handlers.

### Rupture

Fixed damage on each coin hit (step 7a of `_resolve_coin`). The damage always
lands; `env.CONSUME_RUPTURE` (default `True`) gates **only** whether
`rupture_count` is decremented — and, with it, the Deathrite【Haste】rider, which
only fires when a count was actually spent.

```python
if not env.target_killed:
	rupture_potency = env.enemy.get_status("rupture_potency", 0)
	rupture_count = env.enemy.get_status("rupture_count", 0)
	if rupture_potency > 0 and rupture_count > 0:
		rupture_dmg = env.enemy.take_damage(rupture_potency)
		env.total += rupture_dmg
		env.status_damages["rupture"] = env.status_damages.get("rupture", 0) + rupture_dmg
		if env.CONSUME_RUPTURE:
			env.enemy.reduce_status("rupture_count", 1)   # auto-removes rupture_potency at 0
		# ... target_killed / Deathrite (also gated on CONSUME_RUPTURE) ...
```

### Bleed

**Bleed is consumed by the unit that throws the coin — not the target of a hit.**

- **Per attack coin** (step 2b of `_resolve_coin`, right after the flip): the
  skill owner takes `bleed_potency` and loses 1 `bleed_count`. Every coin, heads
  or tails, reused coins included. Self-damage is booked to
  `env.self_damage["bleed"]` and is **not** added to `env.total` (it is not skill
  output).
- **During a clash** (top of the `if env.is_clashing:` block in `_resolve_skill`,
  before `CLASH_START`): both participants — the owner and `env.enemy` — proc
  `min(env.clash_count, own bleed_count)` times, before any attack coin resolves.
  The enemy's clash Bleed is enemy-directed → `env.status_damages["bleed"]` +
  `env.total`; the owner's → `env.self_damage["bleed"]`.
- Landing attack coins no longer touch the target's Bleed.
- A proc that would drop the target below 0 HP spends only the procs actually
  needed to kill (shield counts toward the threshold) — leftover
  `bleed_count` / `clash_count` is not wasted.
- `env.CONSUME_BLEED` (default `True`) gates **only** the `bleed_count` decrement;
  when `False`, Bleed still deals its damage.
- A `Unit` killed by its own Bleed mid-skill sets `env.CANCEL_ATTACK` — the
  remaining coins do not resolve.

All of this goes through the shared helper `Environment.proc_bleed(entity, procs,
*, is_self)`.

```python
# _resolve_skill, clash:
if env.is_clashing:
	if owner is not None:
		env.proc_bleed(owner, env.clash_count, is_self=True)
		if not owner.is_alive:
			env.CANCEL_ATTACK = True
	if env.enemy is not None:
		env.proc_bleed(env.enemy, env.clash_count, is_self=False)
		if not env.enemy.is_alive:
			env.target_killed = True
			env.CANCEL_ATTACK = True
	# ... CLASH_START / CLASH_WIN / CLASH_LOSE ...

# _resolve_coin, step 2b (per coin thrown):
if owner is not None:
	env.proc_bleed(owner, 1, is_self=True)
	if not owner.is_alive:
		env.CANCEL_ATTACK = True
		return
```

> Tremor **hemmorage** (`Environment.on_tremor_burst`) still reduces the target's
> `bleed_count` by 1 and deals Lust damage on a Tremor Burst — that is a separate
> Tremor-amplitude mechanic and is unchanged.

### Sinking

Behavior on hit depends on whether the target has sanity (`Enemy.has_sanity` — most enemies don't, `Unit` defaults it `True`): with sanity, Sinking reduces `sp` instead of dealing damage; without it, unchanged Gloom fixed damage. Either way, count/potency consumption is identical.

```python
sink_potency = env.enemy.get_status("sinking_potency", 0)
sink_count = env.enemy.get_status("sinking_count", 0)
if sink_potency > 0 and sink_count > 0:
	if env.enemy.has_sanity:
		env.enemy.adjust_sp(-sink_potency)  # flat, unresisted; clamped to [-45, 45]
	else:
		gloom_res = env.enemy.sin_res.get("Gloom", 1.0)
		gloom_mod = gloom_res - 1.0
		if gloom_mod < 0:
			gloom_mod /= 2
		sink_dmg_raw = max(math.floor(sink_potency * (1.0 + gloom_mod)), 1)
		sink_dmg = env.enemy.take_damage(sink_dmg_raw)
		env.total += sink_dmg

	new_count = max(0, sink_count - 1)
	if new_count == 0:
		env.enemy.remove_status("sinking_count")
	else:
		env.enemy.set_status("sinking_count", min(new_count, 99))
	env.enemy.set_status("sinking_potency", min(sink_potency, 99))
```

### Burn

Turn-end damage by potency, count decays.

```python
burn_potency = enemy.get_status("burn_potency", 0)
burn_count = enemy.get_status("burn_count", 0)
if burn_potency > 0 and burn_count > 0:
	burn_dmg = enemy.take_damage(burn_potency)
	new_count = max(0, burn_count - 1)
	if new_count == 0:
		enemy.remove_status("burn_count")
		enemy.remove_status("burn_potency")
	else:
		enemy.set_status("burn_count", min(new_count, 99))
```

### Tremor

Turn-end count decay plus tremor burst behavior.

```python
tremor_count = enemy.get_status("tremor_count", 0)
if tremor_count > 0:
	new_count = max(0, tremor_count - 1)
	if new_count == 0:
		enemy.remove_status("tremor_count")
		enemy.remove_status("tremor_potency")
	else:
		enemy.set_status("tremor_count", new_count)
```

```python
potency = int(env.enemy.get_status("tremor_potency", 0))
raised = env.enemy.raise_stagger_threshold(potency)
env.enemy.set_status("tremor_last_burst_raised", raised)

tremor_type = str(env.enemy.get_status("tremor_type", "")).strip().lower()
if tremor_type == "reverb":
	# Sloth damage = tremor potency
	...
elif tremor_type == "scorch":
	# Wrath damage = floor((tremor_potency + burn_potency) / 2)
	...
elif tremor_type == "hemmorage":
	# Lust damage = floor((tremor_potency + bleed_potency) / 2)
	...
```

### Charge

Count decays at turn end, potency persists.

```python
charge_count = unit.get_status("charge_count", 0)
if charge_count > 0:
	new_count = max(0, charge_count - 1)
	if new_count == 0:
		unit.remove_status("charge_count")
	else:
		unit.set_status("charge_count", new_count)
```

#### Charge Potency growth on consumption

Deliberately *spending* Charge Count (as opposed to it passively decaying) goes through `utils.consume_charge_count`, not a direct `set_status` call. Every unit tracks lifetime consumption via `Unit.charge_consumed_total`, regardless of identity; only units with `Unit.gains_charge_potency_on_consume = True` convert that into Charge Potency — +1 per 10 cumulative consumed, with fractional progress carrying over across separate consumptions:

```python
def consume_charge_count(env, amount: int, cap: int = 99) -> int:
	unit = env.unit
	current = max(0, int(unit.get_status("charge_count", 0)))
	consumed = min(max(0, amount), current)
	...
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
```

Ryoshu's D.D.E.D.R. (which spends up to 15 Charge Count for +5 Coin Power) is built on top of this helper, so she also accumulates `charge_consumed_total` — she just doesn't have `gains_charge_potency_on_consume` set, so she never gains Charge Potency from it. No built-in identity currently opts into the flag; it's meant to be set per-character in `Character(...)`/`Unit(...)`.

#### Charge Barrier / Shield

`charge_barrier` has its own two-part lifecycle, handled directly by `GameLoop` rather than the generic status-decay code above:

- **Turn start** (`_process_turn_start_statuses`): for every entity with `charge_barrier > 0`, grant `3 × charge_barrier` Shield (additive).
- **Turn end** (inside `_process_turn_end_statuses`, both the enemy and unit loops): convert `charge_barrier` 1:1 into Charge Count via `entity.add_status("charge_count", barrier)` (auto-initializes `charge_potency` to 1 if it was 0 — see "Potency/Count Status Helpers"), then remove the `charge_barrier` status.

```python
# turn start
barrier = int(entity.get_status("charge_barrier", 0))
if barrier > 0:
	entity.shield += barrier * 3

# turn end
barrier = int(entity.get_status("charge_barrier", 0))
if barrier > 0:
	entity.add_status("charge_count", barrier)
	entity.remove_status("charge_barrier")
```

**Shield** itself is a dedicated `shield` field on `Enemy` (inherited by `Unit`) — not a status dict entry — drained by `take_damage` before HP, and never decaying on its own:

```python
def take_damage(self, amount: int) -> int:
	old_hp = self.hp
	remaining = max(0, int(amount))
	if self.shield > 0 and remaining > 0:
		absorbed = min(self.shield, remaining)
		self.shield -= absorbed
		remaining -= absorbed
	actual = min(remaining, self.hp)
	self.hp -= actual
	self._update_stagger_from_hp(old_hp)
	return actual
```

### Poise

Poise is tracked entirely in the status dictionary — there are no dedicated `Unit`/`Enemy` fields for it. Both enemies and units can carry it:

- `statuses["poise_potency"]` is the actual Poise amount and contributes `5%` base critical chance per point.
- `statuses["poise_count"]` is the consumable number of Poise uses and decreases by one when a critical hit consumes it, and by one more at turn end for whichever entity has it (enemy or unit).
- When a unit gains Poise Count while `statuses["poise_potency"]` is zero, Poise is initialized to `1`.
- When Poise Count reaches zero, both `statuses["poise_count"]` and `statuses["poise_potency"]` are removed.

There's no dedicated Poise gain helper anymore — Sinclair's Poise-granting effects (Remise, Engagement) use the plain env-based `utils.add_status(env, "poise_count", N)`, which delegates to `Enemy.add_status` and picks up the gain-side rules above for free (see "Potency/Count Status Helpers" below). The former dedicated `add_poise_count` was removed once it became pure duplication of `add_status`/`reduce_status`.

Every reduction — turn-end decay (for both enemies and units) and crit-consumption in `GameLoop._resolve_coin` — goes through `Enemy.reduce_status`, which removes both `poise_count` and `poise_potency` together once either hits 0:

```python
poise_count = int(unit.get_status("poise_count", 0))
if poise_count > 0:
	new_count = unit.reduce_status("poise_count", 1)
```

See "Potency/Count Status Helpers" below for `reduce_status`/`add_status`.

### Offense / Defense Level

Four volatile statuses shift the level term of the damage formula by 1 per stack. `off_lvl_up`/`off_lvl_down` live on the attacker and are read by `Environment.effective_ol`; `def_lvl_up`/`def_lvl_down` live on the target and are read by `Environment.def_level`. Up and down are stored independently and netted on read, each clamped with `max(0, ...)`, mirroring `Enemy.effective_speed`'s Haste/Bind handling. All four are cleared from every entity at turn end via `TURN_END_EFFECTS_TO_CLEAR`.

```python
@property
def effective_ol(self) -> int:
	if self.unit is None:
		return self.ol
	up = max(0, int(self.unit.get_status("off_lvl_up", 0)))
	down = max(0, int(self.unit.get_status("off_lvl_down", 0)))
	return self.ol + up - down
```

`def_level` sums four independent sources — the enemy's `effective_defense`, the per-resolution `def_level_mod`, the two statuses, and Tremor Decay:

```python
return (
	self.enemy.effective_defense
	+ self.def_level_mod
	+ up
	- down
	- self.enemy.tremor_decay_def_level_down()
)
```

### Tremor Decay

Decay's defense reduction is **derived on read, not stored as a status**. While `tremor_type` is `decay` and Tremor is active, the target loses 1 defense level per 4 `tremor_potency`:

```python
def tremor_decay_def_level_down(self) -> int:
	tremor_type = str(self.get_status("tremor_type", "")).strip().lower()
	if tremor_type != "decay" or not self.has_tremor():
		return 0
	return max(0, int(self.get_status("tremor_potency", 0))) // 4
```

Because it is computed rather than written, it follows potency however that potency changed — `add_status`, `set_status`, `reduce_status`, or a constructor-supplied `statuses` dict that bypasses every accessor — with no recompute step anywhere. It cannot desync from the Tremor state it reads, it drops to 0 the instant the type converts away (mid-turn included), and the turn-end status sweep does not touch it, so it applies for exactly as long as Decay Tremor does.

It is fully independent of the `def_lvl_down` status: the two sum in `def_level`, and neither can overwrite the other. This replaces an earlier model where `Enemy.refresh_tremor_decay_effect()` wrote a `defense_level_down` status back on every Tremor mutation, which required a hook in `set_status`/`remove_status` and a turn-start sweep in `run_turn` to stay fresh. Both are gone.

### Fragility / Damage Up

Both are dynamic-multiplier bonuses read directly off the damage formula (`Environment.get_fragility_dynamic_bonus` / `get_damage_up_dynamic_bonus`), not consumed on hit. Each comes in a generic (any damage type) form plus one variant per physical type (`PHYSICAL_DAMAGE_TYPES`) and per sin type (`SIN_DAMAGE_TYPES`), each independently capped at 10 stacks (+100%) and summed together:

- **Fragility** lives on the target (`enemy`) and boosts whatever hits it: `fragility` (any attacker), `slash_fragility`/`pierce_fragility`/`blunt_fragility` (only that physical type), `wrath_fragility`/`lust_fragility`/etc. (only that sin type).
- **Damage Up** lives on the attacker (`unit`) and boosts their own outgoing damage: `dmg_up` (any skill), `pierce_dmg_up`/etc. (only that physical type), `envy_dmg_up`/etc. (only that sin type).

```python
def get_fragility_dynamic_bonus(self) -> float:
	bonus = _status_stack_bonus(self.enemy, "fragility")
	if phys_type in PHYSICAL_DAMAGE_TYPES:
		bonus += _status_stack_bonus(self.enemy, f"{phys_type}_fragility")
	if sin_type in SIN_DAMAGE_TYPES:
		bonus += _status_stack_bonus(self.enemy, f"{sin_type}_fragility")
	return bonus
```

Both families are removed unconditionally from every entity at turn end via `TURN_END_EFFECTS_TO_CLEAR` — they aren't consumed on hit or decayed by count like Rupture/Burn.

### Max Speed Up / Min Speed Up

Raise the ceiling/floor of a unit's speed roll for the turn. Applied in `Unit.roll_speed()`, and cleared at turn end via `TURN_END_EFFECTS_TO_CLEAR`:

```python
min_speed_bonus = max(0, int(self.get_status("min_speed_up", 0)))
max_speed_bonus = max(0, int(self.get_status("max_speed_up", 0)))
effective_min = self.speed_min + min_speed_bonus
effective_max = max(self.speed_max + max_speed_bonus, effective_min)
self.speed = roller.randint(effective_min, effective_max)
```

### Haste / Bind

Add/subtract directly from `speed` for the turn, rather than widening the roll range like Max/Min Speed Up above. Exposed as `Enemy.effective_speed`, computed on demand (raw `speed` is never mutated) and cleared via `TURN_END_EFFECTS_TO_CLEAR`:

```python
@property
def effective_speed(self) -> int:
    haste = max(0, int(self.get_status("haste", 0)))
    bind = max(0, int(self.get_status("bind", 0)))
    return max(1, self.speed + haste - bind)
```

`utils.check_speed_advantage` (and therefore `apply_speed_based_coin_power`, used by Sinclair's Remise/Engagement/Contre Attaque) reads `effective_speed` on both sides instead of raw `speed`, so Haste/Bind change those speed-advantage checks. Skill turn-ordering (`sorted(all_skills, key=lambda s: s.speed, ...)` in `GameLoop.run_turn()`) is unaffected — `Skill.speed` is a separate, caller-managed field (see the terminal frontend copying `skill.speed = unit.speed` before a turn) and isn't automatically synced from `effective_speed`.

Grants are queued for next turn via `utils.queue_status`, matching how other single-turn buffs are delivered (e.g. Sinclair's Remise/Declared Duel and Ryoshu's Leap on-kill effect all grant `haste` this way).

## Utils Helpers and Custom Extensions

[src/utils.py](src/utils.py) holds reusable helper functions used as `Effect.apply` callbacks and condition gates. These functions take an `Environment` and mutate it or query combat state in a consistent way.

Core helper categories:
- Status mutation helpers (`add_status`, `set_status`, `add_enemy_status`) — env-based: pick `env.unit` if present, else `env.enemy`. `add_status` delegates to `Enemy.add_status`/`Enemy.reduce_status` (see below) for a negative amount.
- Direct modifier helpers (`add_dynamic`, `add_coin_power`).
- Condition helpers (`check_count`, `check_enemy_hp_below`, `check_resonance`, `check_absolute_resonance`, `ddedr_condition`).
- Bonus damage helper (`deal_bonus_damage_from_current`).

### Potency/Count Status Helpers (`Enemy.add_status` / `Enemy.reduce_status`)

Rupture, Sinking, Bleed, Tremor, Burn, Poise, and Charge are each stored as a `{base}_potency`/`{base}_count` pair. These two methods live directly on `Enemy` (inherited by `Unit`), alongside `get_status`/`set_status`/`remove_status`/`has_status`, and are called on a specific entity you already have in hand (`env.enemy`, `env.unit`, `owner`, etc.) rather than through an env-based fallback:

```python
def add_status(self, status_name: str, amount: int, cap: int = 99, init_partner: bool = True) -> int:
    if self.has_status(status_name):
        new_value = self.get_status(status_name, 0) + amount
    else:
        new_value = amount
    new_value = min(new_value, cap)
    self.set_status(status_name, new_value)

    if new_value > 0 and init_partner:
        if status_name.endswith("_potency"):
            partner = status_name[: -len("_potency")] + "_count"
        elif status_name.endswith("_count"):
            partner = status_name[: -len("_count")] + "_potency"
        else:
            partner = None
        if partner is not None and self.get_status(partner, 0) <= 0:
            self.set_status(partner, 1)

    return new_value

def reduce_status(self, status_name: str, amount: int, cleanup: bool = True) -> int:
    current = self.get_status(status_name, 0)
    new_value = max(0, current - amount)

    if new_value <= 0:
        self.remove_status(status_name)
        if cleanup:
            if status_name.endswith("_potency"):
                partner = status_name[: -len("_potency")] + "_count"
            elif status_name.endswith("_count"):
                partner = status_name[: -len("_count")] + "_potency"
            else:
                partner = None
            if partner is not None:
                self.remove_status(partner)
    else:
        self.set_status(status_name, new_value)

    return new_value
```

`reduce_status` always removes `status_name` once it reaches 0 (the same "decrement, remove at 0" contract every status reduction site already followed). When `cleanup` is `True` (the default), it additionally removes the paired key by swapping `status_name`'s own `_potency`/`_count` suffix — there's no registry of paired status names; a status simply shouldn't be named with one of these suffixes unless it's meant to pair. Charge is the one `cleanup=False` case: Charge Count is still removed at 0, but Charge Potency is left alone so it keeps tracking independently (see `GameLoop._process_turn_end_statuses`). Every Rupture/Sinking/Bleed/Tremor/Burn/Poise reduction site in `game_loop.py`, `environment.py`, and the `skill_samples/*.py` files goes through `reduce_status`; every hand-rolled `+N potency/count` gain effect that targets `env.enemy`/`env.unit` directly goes through `add_status`.

`add_status` mirrors this on the gain side: when the status just gained ends with `_potency`/`_count` and comes out positive, its partner (swap the suffix, same as `reduce_status`) is initialized to `1` if it's currently `0`/absent — so a skill that grants only one side of a pair (e.g. Potency only) doesn't leave the other side missing. Pass `init_partner=False` to suppress this for a gain that's meant to stay one-sided. This rule replaced two duplicate hand-rolled versions of it: `Enemy.add_charge_count` and `utils.add_poise_count`, both since removed — every former caller now calls `add_status`/`reduce_status` directly (the entity method where one is already in hand, or the env-based `utils.add_status`, which also delegates to them, at existing `Effect.apply` sites).

Status cleanup registry — cleared from every entity (units and enemies) at turn end. Covers a generic Fragility (`fragility`) and Damage Up (`dmg_up`), one type-specific variant of each per physical/sin damage type (built from `PHYSICAL_DAMAGE_TYPES`/`SIN_DAMAGE_TYPES`, e.g. `slash_fragility`, `wrath_dmg_up`), Max/Min Speed Up, Haste/Bind, and Offense/Defense Level Up/Down:
```python
PHYSICAL_DAMAGE_TYPES: tuple[str, ...] = ("slash", "pierce", "blunt")
SIN_DAMAGE_TYPES: tuple[str, ...] = (
	"wrath", "lust", "sloth", "gluttony", "gloom", "envy", "pride",
)
_ALL_DAMAGE_TYPES: tuple[str, ...] = PHYSICAL_DAMAGE_TYPES + SIN_DAMAGE_TYPES

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
```

Tremor Decay's defense reduction is deliberately absent: it is derived on read (`Enemy.tremor_decay_def_level_down`) rather than stored, so it survives this sweep and lasts exactly as long as Decay Tremor.

### Creating custom apply helpers

Custom helpers are plain functions that accept `env` and mutate the environment, unit, or enemy. Use `env.total` and `env.status_damages` if you apply additional damage, and set `env.target_killed` if the damage kills the enemy.

Example: add a new enemy status with a cap
```python
def add_enemy_status(env, status_name: str, amount: int, cap: int = 99) -> None:
	if env.enemy is None:
		return

	current = int(env.enemy.get_status(status_name, 0))
	new_value = max(0, current + amount)
	if new_value == 0:
		env.enemy.remove_status(status_name)
	else:
		env.enemy.set_status(status_name, min(new_value, cap))
```

Example: apply bonus damage based on current coin damage
```python
def deal_bonus_damage_from_current(env, multiplier: float, source: str = "bonus_damage") -> None:
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
```

### Creating custom condition helpers

Condition helpers return `True` or `False` and are used to gate effects. They should be read-only and avoid mutating state unless explicitly intended.

Example: check a minimum stack count
```python
def check_count(env, status_name: str, minimum_value: int) -> bool:
	target = env.unit if env.unit is not None else env.enemy
	if target is None:
		return False
	return int(target.get_status(status_name, 0)) >= int(minimum_value)
```

Example: special gate that can also apply a penalty before returning
```python
def ddedr_condition(env) -> bool:
	if env.unit is None:
		return False

	charge_count = int(env.unit.get_status("charge_count", 0))
	if 7 <= charge_count <= 14:
		percent = 3 * (15 - charge_count)
		hp_loss = max(0, math.floor(env.unit.max_hp * (percent / 100.0)))
		if hp_loss > 0:
			env.unit.take_damage(hp_loss)
		return True

	if charge_count >= 15:
		return True

	return False
```

### Registering turn-end cleanup

If a status should automatically clear at turn end, add its name to `TURN_END_EFFECTS_TO_CLEAR` so the game loop removes it (from both units and enemies) after turn-end processing.

## Character Implementation

Character definitions live under `src/characters/` and are constructed as `Character` objects (a `Unit` subclass). A character builder typically:

1. Defines helper functions for stats (HP, stagger thresholds).
2. Builds skills by assembling coins + skill-level effects.
3. Builds passives as phase-tagged effects.
4. Returns a `Character` with skill slots and passives filled.

Character payload wiring (`Character.__post_init__`):

```python
def __post_init__(self) -> None:
	self.skill_slots["1"] = list(self.skill_1)
	self.skill_slots["2"] = list(self.skill_2)
	self.skill_slots["3"] = list(self.skill_3)
	self.skill_slots["defense"] = list(self.defense)
	self.sync_skills_from_slots()

	if self.passive_effects:
		self.passives = list(self.passive_effects)
	else:
		self.passive_effects = list(self.passives)

	super().__post_init__()
```

### Example: Ryoshu (W Corp. L3 Cleanup Agent)

Ryoshu’s builder shows the standard pattern for skills, coins, and passives.

#### Skills

Each skill is a `Skill` with base stats and a list of `Coin` objects. Skill-level effects (e.g., `SkillPhase.PERSISTENT`, `SkillPhase.ON_USE`, `SkillPhase.ON_KILL`) are attached via `skill.add_effect`.

Skill 1 (E.C.) defines three coins and two persistent charge-based damage bonuses:

```python
coin1 = Coin(name="E.C. Coin 1", coin_power=2)
coin1.add_effect(
	Effect(
		name="On Hit: Gain +2 Charge Count",
		phase=CoinPhase.ON_HIT,
		apply=add_status,
		args=("charge_count", 2),
	)
)

coin2 = Coin(name="E.C. Coin 2", coin_power=2)
coin2.add_effect(
	Effect(
		name="On Hit: Gain +2 Charge Count",
		phase=CoinPhase.ON_HIT,
		apply=add_status,
		args=("charge_count", 2),
	)
)

coin3 = Coin(name="E.C. Coin 3", coin_power=2)

skill = Skill(
	name="E.C.",
	base_power=3,
	coin_power=2,
	offense_level=5,
	damage_type=("Slash", "Lust"),
)
skill.add_coin(coin1)
skill.add_coin(coin2)
skill.add_coin(coin3)

skill.add_effect(
	Effect(
		name="Persistent: +10% damage at 10+ Charge",
		phase=SkillPhase.PERSISTENT,
		apply=add_dynamic,
		args=(0.10,),
		condition=check_count,
		condition_args=("charge_count", 10),
	)
)
skill.add_effect(
	Effect(
		name="Persistent: +10% damage at 15+ Charge",
		phase=SkillPhase.PERSISTENT,
		apply=add_dynamic,
		args=(0.10,),
		condition=check_count,
		condition_args=("charge_count", 15),
	)
)
```

Skill 2 (Leap) illustrates common phase usage:

- `SkillPhase.ON_USE` (gain charge immediately)
- `SkillPhase.PERSISTENT` (coin power boost while charge is high)
- `SkillPhase.ON_KILL` (grant haste)

#### Coins per skill

Coins are `Coin` objects attached to a skill in order. Each coin can attach its own phase effects (e.g., `CoinPhase.ON_HIT`, `CoinPhase.ON_HIT_HEADS`, `CoinPhase.COIN_START`).

Skill 3 (D.D.E.D.R.) uses four coins and a special per-coin tracker to scale the final coin:

```python
coin1 = Coin(name="D.D.E.D.R. Coin 1", coin_power=2)
coin1.add_effect(
	Effect(
		name="Heads Hit: Track +10% for final coin",
		phase=CoinPhase.ON_HIT_HEADS,
		apply=_ddedr_track_heads_hit_for_last_coin,
	)
)

coin2 = Coin(name="D.D.E.D.R. Coin 2", coin_power=2)
coin2.add_effect(
	Effect(
		name="Heads Hit: Track +10% for final coin",
		phase=CoinPhase.ON_HIT_HEADS,
		apply=_ddedr_track_heads_hit_for_last_coin,
	)
)

coin3 = Coin(name="D.D.E.D.R. Coin 3", coin_power=2)
coin3.add_effect(
	Effect(
		name="Heads Hit: Track +10% for final coin",
		phase=CoinPhase.ON_HIT_HEADS,
		apply=_ddedr_track_heads_hit_for_last_coin,
	)
)

coin4 = Coin(name="D.D.E.D.R. Coin 4", coin_power=2)
coin4.add_effect(
	Effect(
		name="Coin Start: Apply tracked final-coin dynamic",
		phase=CoinPhase.COIN_START,
		apply=_ddedr_apply_tracked_dynamic_on_last_coin,
	)
)
coin4.add_effect(
	Effect(
		name="On Kill: +7 Charge Barrier (if consumed 15)",
		phase=CoinPhase.ON_KILL,
		apply=_ddedr_on_kill_charge_barrier,
	)
)
```

#### Passives

Passives are `Passive` objects with phase-tagged effects. They are merged into skill/coin phases by the game loop, subject to `max_procs` limits.

Ryoshu’s passive (`Dimensional Demon Edge`) fires on coin kill up to three times:

```python
passive = Passive(
	name="Dimensional Demon Edge",
	condition=_dimensional_demon_edge_placeholder_condition,
)
passive.add_effect(
	Effect(
		name="On Kill: Gain +3 Charge Count",
		phase=CoinPhase.ON_KILL,
		apply=add_status,
		args=("charge_count", 3),
		max_procs=3,
	)
)
```

#### Character assembly

The builder returns a `Character` with skills grouped by slot and static stats set directly. Skill slots are lists to allow future variants per slot.

```python
def make_ryoshu_w_corp_l3_cleanup_agent(level: int = 60) -> Character:
	hp = _compute_hp(level)
	return Character(
		name="Ryoshu",
		id_name="W Corp. L3 Cleanup Agent",
		base_level=level,
		skill_1=[_make_skill_1_ec()],
		skill_2=[_make_skill_2_leap()],
		skill_3=[_make_skill_3_ddedr()],
		defense=[_make_defense_charged_evade()],
		hp=hp,
		max_hp=hp,
		speed_min=3,
		speed_max=6,
		defense_level=-4,
		stagger_thresholds=_compute_stagger_thresholds(hp),
		phys_res={
			"Slash": 0.5,
			"Pierce": 1.0,
			"Blunt": 2.0,
		},
		sin_res={},
		passives=[_make_passive_dimensional_demon_edge()],
	)
```

## Terminal Frontend Loading

The terminal frontend lives in [src/terminal_frontend.py](src/terminal_frontend.py). It is a single-file interactive loop that loads one unit, spawns a sample enemy, and runs one selected skill per turn.

Entry point and CLI args:

```python
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
```

### Unit loading

`_load_available_units` discovers every `make_*` builder re-exported by `src.characters` and wraps each in a `LoadedUnit` record storing a menu key, display label, and the builder itself. Builders are factories, so calling one twice yields two independent units.

```python
def _load_available_units(level: int) -> list[LoadedUnit]:
	loaders: list[LoadedUnit] = []
	builder_names = sorted(
		name for name in characters.__all__ if name.startswith("make_")
	)
	for index, builder_name in enumerate(builder_names, start=1):
		builder = getattr(characters, builder_name)
		preview = builder(level=level)
		loaders.append(LoadedUnit(
			key=str(index),
			label=f"{preview.name} - {preview.id_name}",
			builder=lambda builder=builder: builder(level=level),
		))
	return loaders
```

### Team selection

`_select_team` prompts once for a space-separated list of keys. **The order they are typed in is the team order** — there is no separate reorder step. Repeating a key is allowed and produces two independent units of the same Identity.

```
Enter IDs in team order, separated by spaces (e.g. "2 1").

Select team (default 1): 2 1

Team set:
  Pos 1: Sinclair - Cinq Assoc. South Section 4 Director
  Pos 2: Ryoshu - W Corp. L3 Cleanup Agent
```

### Enemy loading

The frontend always spawns a single training target via `_make_sample_enemy`. It sets high HP, neutral resistances, and dense stagger thresholds for quick testing.

```python
def _make_sample_enemy(level: int = 60) -> Enemy:
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
```

### Skill menu construction

Menu options are generated from the first configured skill in each slot (`1`, `2`, `3`, `defense`) using `_get_player_skill_options`.

```python
def _get_player_skill_options(unit: Unit) -> list[tuple[str, str, Skill]]:
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
```

### Resolving a team turn

`_prompt_team_actions` walks the living members **in team order**, printing each one's skill menu and collecting one `Action` per member. Informational commands (`history`/`log`/`status`) are handled inline and re-prompt the same member; `quit` and EOF both return `None` to end the battle.

`_run_team_turn` then resolves the whole turn at once. Each Action leaves its clash/target/sequence config unset, so those fall back to the turn-wide values; ordering by speed (team order breaking ties) happens inside `run_turn`. `unit.skills` — the permanent kit — is never touched.

```python
def _run_team_turn(team, enemy, actions) -> tuple[list[dict], list[str]]:
	# Frontend mode assumption: all flips resolve as heads.
	loop = GameLoop(
		team=team,
		enemies=[enemy],
		actions=actions,
		is_debugging=True,
		sequence=["heads"] * 64,
	)
	results = loop.run_turn()
	return results, list(loop._broadcast_env.log)
```

Results come back in resolution order, so prompting order (team) and output order (speed) deliberately differ — a turn where a back-line unit acts first reads correctly. Each history entry holds a list of per-action results plus `turn_damage`, `team_hp_after`, and `enemy_hp_after`; the battle report grows a `[TEAM_ORDER]` block and one `[UNIT_n]` metadata block per member. The battle ends when **all** members are defeated, not one.
