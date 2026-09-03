# Status Reference

This file lists status keys currently used by the combat engine and sample skills in `src/`.

## Core Combat Statuses

- `rupture_potency`: Rupture fixed-damage strength.
- `rupture_count`: Rupture hit count consumed on hit.
- `sinking_potency`: Sinking fixed-damage strength.
- `sinking_count`: Sinking hit count consumed on hit.
- `burn_potency`: Burn turn-end damage strength.
- `burn_count`: Burn duration/count consumed at turn end.
- `bleed_potency`: Bleed per-hit damage strength.
- `bleed_count`: Bleed available hit count.
- `slash_fragility`: Extra Slash dynamic damage bonus.
- `fragility`: Generic fragility status key (registered for turn-end cleanup).
- `defense_level_down`: Reduces effective defense level.

## Charge / Resource Statuses

- `charge_count`: Charge stack/count used by multiple skills.
- `charge_potency`: Charge potency marker/supporting value.
- `charge_barrier`: Barrier granted by D.D.E.D.R. on kill condition.
- `haste_count`: Haste stacks granted by Leap on kill.

## Tremor Family Statuses

- `tremor_potency`: Tremor strength.
- `tremor_count`: Tremor count/stacks.
- `tremor_type`: Active tremor amplitude type (`decay`, `reverb`, `everlasting`, `chain`, `scorch`, `hemmorage`, etc.).
- `tremor_superposition`: Blocks amplitude conversion while active.
- `tremor_last_burst_raised`: Internal bookkeeping for the last burst threshold raise amount.

## Skill-Specific Statuses

- `deathrite_haste`: Deathrite stack tracker used by rupture interactions.
- `strider_mao`: Traceless self-buff stack/status.
- `tigermark_round`: Tanglecleaver resource consumed per coin.

## Notes

- This list is based on keys read/written through `get_status`, `set_status`, `remove_status`, and status helper utilities in `src/`.
- Unit attributes like `poise_potency`, `poise_count`, and `sp` are tracked as dedicated fields on `Unit`, not as entries in the status dictionary.
