# Codebase Documentation

Poise is tracked entirely in the unit status dictionary:
- `statuses["poise"]` is the actual Poise amount and contributes `5%` base critical chance per point.
- `statuses["poise_count"]` is the consumable number of Poise uses and decreases by one when a critical hit consumes it.
- When a unit gains Poise Count while `statuses["poise"]` is zero, Poise is initialized to `1`.
- When Poise Count reaches zero, `statuses["poise"]` is removed.
### Core formula

`damage = max(floor(current_power × effective_static × total_dynamic), 1)`

### Formula components

- `current_power` starts at the skill's base power and increases when coins flip heads, gain coin power, or receive other power buffs.

	env.unit.set_status("poise_count", new_count)
env.base = skill.base_power

if env.coin_result == "heads":
	env.current_power += env.coin_power
		env.unit.set_status("poise", 1)
	env.unit.set_status("poise_count", new_count)

- `effective_static` starts from the skill's recomputed static value and can be modified by crits, offense/defense level differences, resistances, observation level, clash count, and stagger overrides.

```python
	poise_count = int(unit.get_status("poise_count", 0))
	new_count = max(0, poise_count - 1)
	if new_count == 0:
		unit.remove_status("poise_count")
		unit.remove_status("poise")
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

- Offense level vs. enemy defense level contributes an OL multiplier.
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

### Environment fields used by damage

- References: `skill`, `unit`, `enemy`
- Multipliers: `static`, `dynamic`, `p_res_mod`, `s_res_mod`, `ol`, `def_level_mod`
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
2. Broadcast `turn_start` across all skills and passives.
3. Refresh tremor decay effects on all entities.
4. Sort skills by speed (descending).
5. Broadcast `combat_start` across all skills and passives.
6. Resolve each skill in order (creates its own `Environment`).
7. Broadcast `turn_end` across all skills and passives.
8. Apply end-of-turn status processing (burn, tremor, charge, poise, stagger ticks, and cleanup list).

```python
def run_turn(self) -> list[dict]:
	self.results.clear()
	self.envs.clear()

	for entity in self._all_entities():
		entity.reset_passives()

	all_skills = self._all_skills()
	self._broadcast_phase(SkillPhase.TURN_START, all_skills)

	for entity in self._all_entities():
		entity.refresh_tremor_decay_effect()

	ordered = sorted(all_skills, key=lambda s: s.speed, reverse=True)
	self._broadcast_phase(SkillPhase.COMBAT_START, all_skills)

	for skill in ordered:
		self.results.append(self._resolve_skill(skill, all_skills))

	self._broadcast_phase(SkillPhase.TURN_END, all_skills)
	self._process_turn_end_statuses()
	return self.results
```

### Per-skill flow

- `before_use` → `on_use`
- Optional clash phases: `clash_start`, `clash_win` / `clash_lose`
- `before_attack` → `on_unopposed_attack`
- Recompute static after pre-attack effects
- Resolve coins (see below)
- `on_kill` (skill-level) if target died
- `after_attack`

```python
def _resolve_skill(self, skill, all_skills) -> dict:
	owner = self._find_owner(skill)
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
		"status_damages": dict(env.status_damages),
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
- A cleanup registry (`TURN_END_ENEMY_EFFECTS_TO_CLEAR`) removes transient enemy statuses last.

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

	for enemy in self.enemies:
		for effect_name in TURN_END_ENEMY_EFFECTS_TO_CLEAR:
			if enemy.has_status(effect_name):
				enemy.remove_status(effect_name)
```

## Status Effects

Status effects are tracked on `Enemy.statuses` (and `Unit.statuses` for shared effects like charge/burn/tremor). Some effects are applied on hit, some tick at turn end, and some trigger special handlers.

### Rupture

Fixed damage on hit, consumes count.

```python
rupture_potency = env.enemy.get_status("rupture_potency", 0)
rupture_count = env.enemy.get_status("rupture_count", 0)
if rupture_potency > 0 and rupture_count > 0:
	rupture_dmg = env.enemy.take_damage(rupture_potency)
	env.total += rupture_dmg
	new_count = max(0, rupture_count - 1)
	if new_count == 0:
		env.enemy.remove_status("rupture_count")
	else:
		env.enemy.set_status("rupture_count", min(new_count, 99))
	env.enemy.set_status("rupture_potency", min(rupture_potency, 99))
```

### Bleed

Potency × clash count, capped by count.

```python
bleed_potency = env.enemy.get_status("bleed_potency", 0)
bleed_count = env.enemy.get_status("bleed_count", 0)
bleed_remaining = int(env.global_state.get("_bleed_clash_remaining", 0))
if bleed_potency > 0 and bleed_count > 0 and bleed_remaining > 0:
	effective = min(bleed_remaining, bleed_count)
	bleed_dmg = env.enemy.take_damage(bleed_potency * effective)
	env.total += bleed_dmg
	new_count = max(0, bleed_count - effective)
	if new_count == 0:
		env.enemy.remove_status("bleed_count")
		env.enemy.remove_status("bleed_potency")
	else:
		env.enemy.set_status("bleed_count", min(new_count, 99))
	env.global_state["_bleed_clash_remaining"] = max(0, bleed_remaining - effective)
```

### Sinking

Gloom fixed damage on hit, consumes count.

```python
sink_potency = env.enemy.get_status("sinking_potency", 0)
sink_count = env.enemy.get_status("sinking_count", 0)
if sink_potency > 0 and sink_count > 0:
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

### Poise

Poise is tracked on `Unit` fields, not as a standalone `statuses["poise"]` value:

- `poise_potency` is the actual Poise amount and contributes `5%` base critical chance per point.
- `poise_count` is the consumable number of Poise uses and decreases by one when a critical hit consumes it.
- The compatibility key `statuses["poise_count"]` is synchronized when Poise Count is gained, but the `Unit.poise_count` field is authoritative for combat.
- When a unit gains Poise Count while `poise_potency` is zero, potency is initialized to `1`.
- When Poise Count reaches zero, all Poise potency is removed.

The shared helper enforces these rules:

```python
def add_poise_count(env, amount: int, cap: int = 99) -> None:
	if env.unit is None:
		return

	new_count = min(max(0, env.unit.poise_count + amount), cap)
	env.unit.poise_count = new_count
	if new_count == 0:
		env.unit.poise_potency = 0
		env.unit.remove_status("poise_count")
		env.unit.remove_status("poise")
	elif env.unit.poise_potency <= 0:
		env.unit.poise_potency = 1
		env.unit.set_status("poise_count", new_count)
```

Turn-end processing also removes potency when the dedicated count decays to zero:

```python
if unit.poise_count > 0:
	unit.poise_count = max(0, unit.poise_count - 1)
	if unit.poise_count == 0:
		unit.poise_potency = 0
```

### Defense Level Down

Derived from tremor decay.

```python
tremor_type = str(enemy.get_status("tremor_type", "")).strip().lower()
if tremor_type == "decay" and enemy.has_tremor():
	tremor_potency = int(enemy.get_status("tremor_potency", 0))
	enemy.statuses["defense_level_down"] = tremor_potency // 4
else:
	enemy.remove_status("defense_level_down")
```

## Utils Helpers and Custom Extensions

[src/utils.py](src/utils.py) holds reusable helper functions used as `Effect.apply` callbacks and condition gates. These functions take an `Environment` and mutate it or query combat state in a consistent way.

Core helper categories:
- Status mutation helpers (`add_status`, `set_status`, `add_enemy_status`).
- Poise helper (`add_poise_count`) keeps Poise Count and Poise potency synchronized.
- Direct modifier helpers (`add_dynamic`, `add_coin_power`).
- Condition helpers (`check_count`, `check_enemy_hp_below`, `ddedr_condition`).
- Bonus damage helper (`deal_bonus_damage_from_current`).

Status cleanup registry:
```python
TURN_END_ENEMY_EFFECTS_TO_CLEAR: set[str] = {
	"slash_fragility",
	"fragility",
}
```

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

If a status should automatically clear at turn end, add its name to `TURN_END_ENEMY_EFFECTS_TO_CLEAR` so the game loop removes it after turn-end processing.

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

Available units are hard-coded in `_load_available_units` as a list of `LoadedUnit` records. Each record stores a menu key, display label, and a builder function that returns a `Unit`.

```python
def _load_available_units(level: int) -> list[LoadedUnit]:
	return [
		LoadedUnit(
			key="1",
			label="Ryoshu - W Corp. L3 Cleanup Agent",
			builder=lambda: make_ryoshu_w_corp_l3_cleanup_agent(level=level),
		)
	]
```

Unit selection loop (defaults to the first entry, accepts the key from the menu):

```python
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

### Resolving a selected action

Only one skill is resolved per turn. The frontend overrides the unit's `skills` list, forces the selected skill's `speed` to match the unit's roll, and runs the `GameLoop` in deterministic heads-only mode.

```python
def _run_selected_skill(unit: Unit, enemy: Enemy, skill: Skill) -> dict:
	old_skills = list(unit.skills)
	old_speed = skill.speed

	unit.skills = [skill]
	skill.speed = unit.speed

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

	selected_result["broadcast_log"] = list(loop._broadcast_env.log)
	return selected_result
```
