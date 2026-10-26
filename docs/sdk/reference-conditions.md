# conditions

## Mechanism family `conditions`

Conditions on entities: named, timed statuses (on leave, suspended, sick, under review) that tick, change properties and block actions.

Named the same in every mode:
- `who`: the types that carry the statuses
- `stage`: a declared stage the mechanism runs in (default: a stage it generates)
- `views`: generate the mechanism's views
- `phase`: start | end: when in the round the mechanism's own step runs

Modes (`"kind": "conditions", "mode": ...`; read one with `guide('conditions.<mode>')`):
- `status`: Named statuses on entities: timed or permanent, stacking, ticking effects each round, property modifiers ($effective), blocked actions, immunity, expiry news and cleansing.

Functions:
- `$effective(entity, prop)` — The property with every active modifier applied: (base + adds) × multipliers from statuses, kept within the property's min/max, e.g. $effective($actor, 'armor').
- `$has_status(entity, status)` — True when the entity has the status, e.g. $has_status($actor, 'stun').
- `$status_rounds(entity, status)` — Rounds the status lasts after this one (0 = it ends this round); null when permanent or absent.
- `$status_stacks(entity, status)` — Stacks of the status on the entity (0 when it has none).
- `$status_text(entity, mechanism)` — The entity's statuses as text: 'poison ×2 (1 more round), stun (ends this round)'.
