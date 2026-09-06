# Environment & Damage Calculation Guide

This document explains the `Environment` class and the full damage calculation pipeline in `backend.py`.

---

## Table of Contents

- [Environment Class](#environment-class)
  - [Constructor](#constructor)
  - [Multiplier Fields](#multiplier-fields)
  - [Combat State Fields](#combat-state-fields)
  - [Control Flags](#control-flags)
  - [Properties & Helpers](#properties--helpers)
  - [Effect Management](#effect-management)
  - [Event Hooks](#event-hooks)
- [Damage Calculation](#damage-calculation)
  - [Phase 0 — Setup](#phase-0--setup)
  - [Phase 1 — Entry Effects](#phase-1--entry-effects)
  - [Phase 2 — Status on_skill_start](#phase-2--status-on_skill_start)
  - [Phase 3 — Recalculate Static & Skill Conditions](#phase-3--recalculate-static--skill-conditions)
  - [Phase 4 — Per-Coin Loop](#phase-4--per-coin-loop)
  - [Phase 5 — Cleanup](#phase-5--cleanup)
  - [The Damage Formula](#the-damage-formula)
  - [Per-Coin Lifecycle Summary](#per-coin-lifecycle-summary)

---

## Environment Class

`Environment` is a **single-use combat context** created fresh for every skill simulation. It holds every modifier, flag, and accumulator, acting as the central hub that `Skill.calculate_damage()` and all `SkillEffect` callbacks operate on.

### Constructor

```python
class Environment:
    def __init__(self, skill: Skill, unit: Unit, enemy: Enemy, is_debugging=False) -> None:
```

Takes a `Skill`, `Unit`, `Enemy`, and optional debug flag. Immediately computes all baseline modifiers.

### Multiplier Fields

These are the two halves of the damage formula:

| Field | Type | Initial Value | Role |
|-------|------|---------------|------|
| `static` | `int\|float` | Computed from resists, OL, observation | Additive multiplier — built from defense stats |
| `dynamic` | `int\|float` | `1.0` | Multiplicative multiplier — modified by effects at runtime |

#### Resistance Calculation

```python
p_type, s_type = skill.type  # e.g., ("Slash", "Wrath")

self.p_res_mod = enemy.phys_res[p_type] - 1
if self.p_res_mod < 0: self.p_res_mod /= 2   # weakness (negative resist) is halved

self.s_res_mod = enemy.sin_res[s_type] - 1
if self.s_res_mod < 0: self.s_res_mod /= 2
```

- Physical and sin resistance are looked up from the enemy.
- A resistance of `1.0` = neutral (mod = 0). Values above 1.0 mean the enemy resists; below 1.0 means weakness.
- **Weakness is halved** — if the enemy has 0.5 physical resist, the mod is `(0.5 - 1) / 2 = -0.25`, not `-0.5`.

#### Offense Level vs Defense Level

```python
self.ol = skill.offense_level + unit.base_level
ol_diff = self.effective_ol - self.def_level
ol_mult = ol_diff / (abs(ol_diff) + 25)
```

Both sides are **properties**, not the raw fields. `effective_ol` layers the `off_lvl_up`/`off_lvl_down` statuses onto `self.ol` (which itself holds `skill.offense_level + unit.base_level` plus any Sin Resonance bonus); `def_level` layers `def_lvl_up`/`def_lvl_down`, `def_level_mod`, and Tremor Decay's derived reduction onto the enemy's `effective_defense`.

This is a **sigmoid-shaped** scaling curve. Large OL advantages give diminishing returns. For example:
- OL 10 above def: `10 / (10 + 25) = +0.286`
- OL 50 above def: `50 / (50 + 25) = +0.667`
- OL 100 above def: `100 / (100 + 25) = +0.800`

#### Static Multiplier Assembly

```python
self.static = 1.00 + ol_mult + self.p_res_mod + self.s_res_mod + (enemy.observation_level * 0.03)
```

All additive: base 1.0 + OL modifier + physical resist + sin resist + 3% per observation level.

### Combat State Fields

| Field | Type | Purpose |
|-------|------|---------|
| `base` | `int` | Base power of the skill (copied from `skill.base`) |
| `coin_power` | `int` | Power added per Heads flip (copied from `skill.coin_power`) |
| `current_power` | `int` | Running power, starts at `base`, grows with each Heads |
| `current_damage` | `int` | Damage dealt by the current coin |
| `total` | `int` | Accumulated damage across all coins — the return value |
| `crit_bonus` | `float` | `0.20` — added to static on crit |
| `crit_odds_mult` | `float` | `1.0` — multiplier on crit chance |
| `crit_odds_bonus` | `float` | `0.0` — flat bonus to crit chance |
| `effects` | `dict[SkillEffect, Any]` | Active effects with `[data, duration]` tracking |
| `apply_queue` | `list[SkillEffect]` | Effects queued mid-resolution (avoids mutating dict during iteration) |
| `sequence` | `list[str\|None]` | Per-coin flip results: `"Heads"`, `"Tails"`, or `None` (random) |
| `did_crit` | `bool` | Whether the current coin crit |
| `current_coin_index` | `int` | Which coin is being resolved (0-based, starts at -1) |
| `ignore_fixed_damage` | `bool` | `True` — flag for fixed-damage coins |
| `global_state` | `AttrStrDict` | Arbitrary key/value bag for cross-effect communication |

### Control Flags

| Flag | Default | Effect |
|------|---------|--------|
| `CONSUME_RUPTURE` | `True` | If `False`, Rupture still deals its on-hit damage but `rupture_count` is not consumed (and the Deathrite rider does not fire) |
| `CONSUME_BLEED` | `True` | If `False`, Bleed still deals its damage but `bleed_count` is not consumed |
| `CONSUME_SINKING` | `True` | If `False`, Sinking is not applied on hit |
| `CONSUME_POISE` | `True` | If `False`, Poise Count is not consumed on crit |
| `CANCEL_ATTACK` | `False` | If set `True` by an effect, the entire attack ends after the current coin |
| `CANCEL_COIN` | `False` | If set `True` by an effect, the current coin is skipped (no damage) |

### Properties & Helpers

#### `def_level` (property)

```python
@property
def def_level(self) -> int:
    if self.enemy is None:
        return 0
    up = max(0, int(self.enemy.get_status("def_lvl_up", 0)))
    down = max(0, int(self.enemy.get_status("def_lvl_down", 0)))
    return (
        self.enemy.effective_defense
        + self.def_level_mod
        + up
        - down
        - self.enemy.tremor_decay_def_level_down()
    )
```

Returns the target's defense level, summing four independent sources: the enemy's own `effective_defense` (`base_level + defense_level`), the per-resolution `def_level_mod` an effect can write, the volatile `def_lvl_up`/`def_lvl_down` statuses, and Tremor Decay's reduction. Decay is derived on read rather than stored as a status, so it can never disagree with the Tremor state it comes from and is untouched by the turn-end status sweep.

#### `effective_ol` (property)

```python
@property
def effective_ol(self) -> int:
    if self.unit is None:
        return self.ol
    up = max(0, int(self.unit.get_status("off_lvl_up", 0)))
    down = max(0, int(self.unit.get_status("off_lvl_down", 0)))
    return self.ol + up - down
```

The attacker-side mirror. `ol` stays the raw figure so Sin Resonance can add to it once per skill; the statuses layer on at read time and are swept at turn end. Returns `self.ol` unchanged when there is no attacker — the lightweight-env path and turn-wide passive broadcasts both leave `unit` as `None`.

#### `get_target(name)`

Routes a dotted name to the correct object:

| Prefix | Target |
|--------|--------|
| `"unit."` | `self.unit` |
| `"enemy."` | `self.enemy` |
| `"skill."` | `self.skill` |
| (none) | `self` (the Environment) |

#### `get(name)` / `set(name, val)` / `add(name, val)`

Generic attribute access using chained dot-notation strings. Powers the data-driven effect system:

```python
env.add("unit.charge", 3)       # equivalent to unit.charge += 3
env.get("enemy.rupture")        # reads enemy.rupture
env.set("dynamic", 1.5)         # sets env.dynamic to 1.5
```

### Effect Management

#### `update()`

Ticks down all active effect durations by 1. When an effect's duration reaches 0, calls its `remove` callback and purges it from `self.effects`.

```python
def update(self):
    for effect in self.effects:
        info = self.effects[effect]
        info[1] -= 1                    # decrement duration
        if info[1] == 0:
            effect.remove(effect, self)  # cleanup callback
            effects_to_del.append(effect)
    # ... delete expired effects
```

#### `update_apply_queue()`

Drains `apply_queue` in LIFO order — processes effects that were queued by other effects during resolution. A `updating_queue` guard prevents re-entrant calls.

```python
def update_apply_queue(self):
    if self.updating_queue: return
    self.updating_queue = True
    while self.apply_queue:
        self.skill.apply_effect_if_cond(self.apply_queue[-1], self, self.skill.condition_state)
        self.apply_queue.pop()
    self.apply_queue.clear()
    self.updating_queue = False
```

### Event Hooks

Three broadcast events that iterate all active effects:

| Method | Fired When | Signature |
|--------|-----------|-----------|
| `on_poise_gained(potency, count)` | Poise is added to the unit | `(effect, env, potency, count)` |
| `on_status_applied(status_name, potency, count)` | Any status is applied to the enemy | `(effect, env, status_name, potency, count)` |
| `on_tremor_burst()` | Tremor bursts on the enemy | `(effect, env)` |

Each calls `update_apply_queue()` afterward to process any newly queued effects.

---

## Damage Calculation

The `Skill.calculate_damage()` method orchestrates the entire damage simulation. It proceeds through five major phases.

```python
def calculate_damage(self, owner: Unit, enemy: Enemy, debug=False,
                     sequence: list[str|None] = None,
                     clash_count: int = 0,
                     ignore_fixed_damage: bool = True,
                     entry_effects: list[SkillEffect]|None = None):
```

### Phase 0 — Setup

```python
env = Environment(self, owner, enemy, debug)
env.sequence = sequence            # pre-determined coin flips, or None = random
env.ignore_fixed_damage = ignore_fixed_damage
condition_state = self.condition_state  # list[bool] set by rotation
```

A fresh `Environment` is created. The `sequence` list lets callers force `"Heads"`/`"Tails"` per coin (used for averaging), or leave `None` for RNG. `condition_state` is the boolean list set by the rotation code (e.g., `[True, False]` for "is clashing = yes, has 7+ charge = no").

### Phase 1 — Entry Effects

```python
for effect in owner.effects + entry_effects:
    self.apply_effect_if_cond(effect, env, condition_state)
env.update_apply_queue()
```

Unit-level passive effects (from EGO passives, support passives stored on the `Unit`) and any `entry_effects` passed in (one-off buffs from the rotation) are applied first. Each goes through the **3-way condition gate**:

1. **Callable condition** — `effect.condition(effect, env)` is called; if truthy, `effect.apply(effect, env)` fires.
2. **Negative int** (`condition < 0`) — unconditional, always applies.
3. **Index** — `condition_state[effect.condition]` is checked; if truthy, applies.

### Phase 2 — Status on_skill_start

```python
enemy.on_skill_start(env, is_defending=True)
owner.on_skill_start(env, is_defending=False)
env.update_apply_queue()
```

Both the enemy and unit fire `on_skill_start` on all their active `StatusEffect`s. This is where statuses like Rupture, Sinking, Burn, etc. do their pre-attack setup (e.g., Rupture's defense down).

### Phase 3 — Recalculate Static & Skill Conditions

```python
ol_diff = env.effective_ol - env.def_level
ol_mult = ol_diff / (abs(ol_diff) + 25)
env.static = 1 + env.p_res_mod + env.s_res_mod + ol_mult
            + (enemy.observation_level * 0.03) + (clash_count * 0.03)

for i, effect in enumerate(self.conditions):
    self.apply_effect_if_cond(effect, env, condition_state)
env.update_apply_queue()
```

Static is **recomputed** because Phase 1/2 effects may have changed `env.ol`, `env.def_level`, resist mods, or the Offense/Defense Level statuses those two properties read. Note `clash_count * 0.03` — each clash win adds +3% to the static multiplier.

Then the skill's own `self.conditions` effects are applied — these are skill-level conditional buffs defined in the SKILLS dictionary (e.g., "if target has 7+ Rupture, gain +2 coin power").

### Phase 4 — Per-Coin Loop

For each coin in `self.coins`:

#### 4a. Early Update

```python
for effect in env.effects:
    if effect.early_update:
        effect.early_update(effect, env)
env.update_apply_queue()
```

Active effects act **before** the coin flip. `CANCEL_ATTACK` and `CANCEL_COIN` flags are checked — if set, the attack ends or this coin is skipped.

#### 4b. Coin Flip

```python
head_odds = 50 + owner.sp
if sequence[i] == "Heads":
    env.current_power += env.coin_power
elif sequence[i] == "Tails":
    pass
else:
    rand_val = randint(1, 100)
    if rand_val <= head_odds:
        env.current_power += env.coin_power
        sequence[i] = "Heads"
    else:
        sequence[i] = "Tails"
```

- **Heads**: `current_power += coin_power` (coin power stacks additively across coins)
- **Tails**: nothing added
- **Unset (`None`)**: random roll based on `50 + SP`

`current_power` accumulates — after coin 3 with all Heads, it equals `base + 3 × coin_power`.

#### 4c. Crit Check

```python
crit_odds = (owner.poise_potency * 0.05 * env.crit_odds_mult) + env.crit_odds_bonus
```

- Each point of Poise Potency = **5% crit chance** (before multiplier/bonus)
- Can be forced via `self.crits[i]` (`True`/`False`) or rolled randomly
- On crit, **1 Poise Count is consumed** (unless `CONSUME_POISE` is `False`)
- If Poise Potency ≤ 0 and the skill doesn't ignore no-poise, crit is impossible

#### 4d. Apply Coin-Specific Effects

```python
for effect in coin:
    self.apply_effect_if_cond(effect, env, condition_state)
```

Each coin has its own list of effects (e.g., "On Hit: +2 Rupture Potency"). These are the per-coin on-hit effects defined in the SKILLS dictionary.

#### 4e. Mid Update

```python
for effect in env.effects:
    if effect.mid_update:
        effect.mid_update(effect, env)
env.update_apply_queue()
```

Active effects fire their mid-update — after coin effects but before damage. Another `CANCEL_ATTACK`/`CANCEL_COIN` check follows.

#### 4f. Damage Calculation (per coin)

```python
ol_diff = env.effective_ol - env.def_level
ol_mult = ol_diff / (abs(ol_diff) + 25)
env.static = 1.00 + env.s_res_mod + ol_mult + env.p_res_mod
            + (enemy.observation_level * 0.00) + (clash_count * 0.03)
if did_crit: env.static += env.crit_bonus

val = max(floor(env.current_power * env.static * env.dynamic), 1)
env.current_damage += val
```

Static is recomputed **per coin** because effects during the loop may have changed OL, def_level, or resists. This is also what lets an Offense/Defense Level status applied mid-skill take effect from the next coin onward.

> **Note:** The observation level coefficient is `0.00` in the per-coin recalc (zeroed out). Observation level only contributes to the Phase 3 static, not the per-coin recalc.

Crit adds the `crit_bonus` (+0.20) to static.

#### 4g. Hit & Late Update

```python
enemy.hit(env.current_damage, env)     # subtract HP, check stagger, fire status on_hit
env.total += env.current_damage         # accumulate running total

for effect in env.effects:
    if effect.late_update:
        effect.late_update(effect, env)
```

`enemy.hit()` deducts HP, triggers part breaks if HP ≤ 0, and fires `on_hit` on every active enemy status (e.g., Rupture consuming itself for bonus damage).

#### 4h. Duration Tick & Megalate Update

```python
env.update()                           # tick down effect durations, remove expired
enemy.update_stagger_level()           # check if stagger threshold crossed

for effect in env.effects:
    if effect.megalate_update:
        effect.megalate_update(effect, env)
env.update_apply_queue()
```

### Phase 5 — Cleanup

```python
for effect in env.effects:
    effect.on_skill_end(effect, env)
env.update_apply_queue()

enemy.on_skill_end(env, is_defending=True)
owner.on_skill_end(env, is_defending=False)
```

All active effects and statuses get an `on_skill_end` callback (used for things like "at end of attack, apply X stacks of status"). The environment is deleted and `env.total` is returned as the final damage number.

---

### The Damage Formula

The core formula applied **per coin** is:

```
damage = max( floor( current_power × static × dynamic ), 1 )
```

Where:

| Component | How it's built |
|-----------|----------------|
| **current_power** | `base + (heads_count × coin_power)` + any BasePower/CoinPower buffs from effects |
| **static** | `1.0 + ol_mult + phys_res_mod + sin_res_mod + (0.03 × clash_count) + [0.20 if crit]` |
| **dynamic** | Starts at `1.0`, modified by effects (e.g., DynamicBonus, Rupture damage bonus) |

- **Minimum damage per coin is always 1**, even if multipliers would reduce it to 0 or negative.
- **Total damage** is the sum of all per-coin damage values (`env.total`).

#### OL Multiplier Detail

```
ol_diff = effective_ol - def_level
ol_mult = ol_diff / (|ol_diff| + 25)
```

| OL advantage | ol_mult |
|-------------|---------|
| +5 | +0.167 |
| +10 | +0.286 |
| +25 | +0.500 |
| +50 | +0.667 |
| 0 (even) | 0.000 |
| -10 | -0.286 |
| -25 | -0.500 |

#### Crit Detail

- Crit chance: `(poise_potency × 0.05 × crit_odds_mult) + crit_odds_bonus`
- Crit effect: `+0.20` added to static multiplier for that coin
- Poise Count consumed: 1 per crit (unless `CONSUME_POISE = False`)

---

### Per-Coin Lifecycle Summary

| Order | Hook | What Happens |
|-------|------|-------------|
| 1 | `early_update` | Pre-flip effects, CANCEL_ATTACK/CANCEL_COIN checks |
| 2 | Coin flip | Heads → add `coin_power` to `current_power` |
| 3 | Crit roll | Poise-based, adds +0.20 to static if crit |
| 4 | Coin effects | Per-coin on-hit buffs/debuffs from skill definition |
| 5 | `mid_update` | Post-effect / pre-damage hooks, CANCEL checks |
| 6 | **Damage calc** | `floor(power × static × dynamic)`, min 1 |
| 7 | `enemy.hit()` | HP deduction, stagger check, status `on_hit` callbacks |
| 8 | `late_update` | Post-damage hooks |
| 9 | `env.update()` | Tick durations, remove expired effects |
| 10 | `megalate_update` | Final per-coin hooks |

---

### Full Attack Lifecycle (All Phases)

```
1. Create Environment
2. Apply unit passives + entry effects
3. enemy.on_skill_start / owner.on_skill_start (status hooks)
4. Recompute static, apply skill conditions
5. FOR EACH COIN:
   a. early_update → cancel checks
   b. Coin flip (Heads/Tails)
   c. Crit roll
   d. Coin-specific effects
   e. mid_update → cancel checks
   f. DAMAGE = max(floor(power × static × dynamic), 1)
   g. enemy.hit() → HP loss, stagger, status on_hit
   h. late_update
   i. env.update() (tick durations)
   j. megalate_update
6. on_skill_end for all effects
7. enemy.on_skill_end / owner.on_skill_end
8. Return env.total
```

---

## How to Use — Practical Guide

You **never create an `Environment` directly**. It is built internally by `Skill.calculate_damage()`. Your job from a test or rotation script is to set up the **inputs** that feed into it: a `Unit`, an `Enemy`, conditions, effects, and `calculate_damage()` parameters.

### Step 1 — Get a Unit and Enemy

Units and enemies are looked up from the global registries by name:

```python
from backend import Unit, Enemy, Skill

unit = Unit.get_unit("Butler Faust")   # looks up from UNITS dict
enemy = Enemy.get_enemy("Test")         # looks up from ENEMIES dict

unit.sp = 45                            # set sanity (affects coin flip odds)
```

Skills are automatically attached to the unit: `unit.skill_1`, `unit.skill_2`, `unit.skill_3`, and sometimes `unit.extra_skills[n]`.

### Step 2 — Set Up Pre-Existing State

Before calling `calculate_damage()`, configure any combat state the rotation needs:

```python
# Apply statuses to the enemy
enemy.apply_status('Sinking', 5, 3)        # 5 potency, 3 count
enemy.apply_status('Rupture', 10, 8)

# Apply statuses to the unit
unit.poise = {"Potency": 5, "Count": 3}    # for crit chance

# Clear state between simulation runs
enemy.clear_effects()
unit.clear_effects()
unit.clear_statuses()
```

### Step 3 — Apply Unit-Level Effects (Passives)

Unit effects persist across coins and are applied in Phase 1 of the damage calc. Two methods:

```python
# apply_effect: adds an effect to unit.effects (can stack)
unit.apply_effect(backend.skc.OffenseLevelUp(3))
unit.apply_effect(backend.SkillEffect.new("DynamicBonus", 0.1))

# apply_unique_effect: keyed by name — won't duplicate
unit.apply_unique_effect('passive', backend.skc.MidDonPassive(), True)
unit.apply_unique_effect('passive2', backend.skc.TypedDamageUp('Envy', 3), True)
```

These are the effects that get processed in `for effect in owner.effects` during Phase 1.

### Step 4 — Set Conditions (`set_conds`)

Conditions are per-skill boolean lists. Effects with an index-based condition check `condition_state[i]`:

```python
# Skill 2 has one conditional effect that requires "is clashing"
unit.skill_2.set_conds([True])    # condition index 0 = True → effect applies

# Skill 3 has two conditional effects
unit.skill_3.set_conds([True, False])  # index 0 = True, index 1 = False
```

This maps directly to the indexed branch in `apply_effect_if_cond`:
```python
elif effect.condition < len(condition_state):
    if condition_state[effect.condition]:  # checks condition_state[0], [1], etc.
        effect.apply(effect, env)
```

### Step 5 — Call `calculate_damage()`

```python
result = unit.skill_1.calculate_damage(unit, enemy, debug=False)
```

Full signature with all optional parameters:

```python
result = skill.calculate_damage(
    owner=unit,
    enemy=enemy,
    debug=False,                    # print per-coin breakdown
    sequence=None,                  # list of "Heads"/"Tails"/None per coin
    clash_count=0,                  # number of clash wins (+3% static each)
    ignore_fixed_damage=True,       # whether to skip fixed-damage coins
    entry_effects=None              # one-shot effects for this attack only
)
```

#### Key parameters explained:

**`debug=True`** — prints per-coin power, damage, static, dynamic, and resist breakdown:
```
Coin 1: power = 7, damage = 8, static = 1.29, dynamic = 1.0
phys_res = 0, sin_res = 0, ol_mult = 0.286
```

**`sequence`** — force coin outcomes for deterministic testing:
```python
result = skill.calculate_damage(unit, enemy, sequence=["Heads", "Heads", "Tails"])
```

**`clash_count`** — each count adds `+0.03` to the static multiplier:
```python
result = skill.calculate_damage(unit, enemy, clash_count=3)  # +9% static
```

**`entry_effects`** — one-shot effects applied only for this single attack (not stored on the unit):
```python
result = skill.calculate_damage(unit, enemy,
    entry_effects=[backend.skc.OffenseLevelUp(5)])

result = skill.calculate_damage(unit, enemy,
    entry_effects=[backend.skc.DynamicBonus(0.1)])

# Multiple entry effects
effects = [backend.skc.CoinPower(2), backend.skc.DynamicBonus(0.1)]
result = skill.calculate_damage(unit, enemy, entry_effects=effects)
```

### Step 6 — Run in a Loop (Monte Carlo)

Since coin flips and crits are random, simulations run many iterations and average:

```python
total = 0
count = 2000

for _ in range(count):
    total += rotations.butler_faust_rotation(unit, enemy, False, (5, 3), 2, True)

print(f"Average damage: {total / count:.0f}")
```

### Complete Minimal Example

```python
import backend
from backend import Unit, Enemy

# 1. Get unit and enemy
unit = Unit.get_unit("Butler Faust")
enemy = Enemy.get_enemy("Test")
unit.sp = 45

# 2. Pre-apply statuses
enemy.apply_status('Sinking', 5, 3)

# 3. Set conditions (skill 2 has a clash conditional)
unit.skill_2.set_conds([True])

# 4. Run damage calc
result = unit.skill_2.calculate_damage(unit, enemy, debug=True)
print(f"Damage: {result}")
```

### Complete Rotation Example

```python
def my_rotation(unit: Unit, enemy: Enemy, debug=False, does_clash=True):
    bag = [1, 1, 1, 2, 2, 3]
    shuffle(bag)
    skills = {1: unit.skill_1, 2: unit.skill_2, 3: unit.skill_3}
    total = 0

    for i in range(6):
        # Clear per-turn state
        unit.clear_effects()
        unit.on_turn_start()
        enemy.on_turn_start()

        # Pick a skill from the bag
        decision = max(bag[0], bag[1])

        # Set conditions based on rotation parameters
        if decision >= 2:
            skills[decision].set_conds([does_clash])

        # Optionally add passives
        if random() < 0.5:
            unit.apply_effect(backend.skc.SomePassive())

        # Calculate and accumulate
        result = skills[decision].calculate_damage(unit, enemy, debug=debug)
        total += result

        bag.remove(decision)
        if len(bag) < 2:
            bag += get_bag()

    return total
```

### What You Control vs What Environment Handles

| You set up (caller side) | Environment handles internally |
|--------------------------|-------------------------------|
| `Unit` with SP, statuses, level | `static` / `dynamic` multiplier computation |
| `Enemy` with resists, statuses, HP, def_level | Resistance mod, OL mult calculation |
| `set_conds([True, False])` | Condition gate dispatch |
| `unit.apply_effect(...)` | Effect lifecycle (apply → update → remove) |
| `entry_effects=[...]` | One-shot effect application in Phase 1 |
| `clash_count=N` | +3% static per clash win |
| `sequence=["Heads", ...]` | Deterministic coin resolution |
| `debug=True` | Per-coin diagnostic output |
