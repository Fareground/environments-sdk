# functions / conditions

## Functions: conditions

- `$ability_text(entity, mechanism)` — The entity's limited actions as text: 'fireball ready; heal 1/2 charges, next in 3 rounds'.
- `$can_enter(entity, position)` — True when the terrain's entry rules let the entity enter the position.
- `$channeling(entity)` — The channel the entity has in progress ({action, params, started, completes}), or null.
- `$charges(entity, action)` — Charges of the action available now (null when it has no charges).
- `$cooldown_left(entity, action)` — Rounds until the action can be used again (0 = now, -1 = never).
- `$effective(entity, prop)` — The property with every active modifier applied: (base + adds) × multipliers from statuses and terrain, kept within the property's min/max, e.g. $effective($actor, 'armor').
- `$has_status(entity, status)` — True when the entity has the status, e.g. $has_status($actor, 'stun').
- `$ready(entity, action)` — True when a cooldown-limited action can be used now, e.g. $ready($actor, 'fireball').
- `$status_rounds(entity, status)` — Rounds the status lasts after this one (0 = it ends this round); null when permanent or absent.
- `$status_stacks(entity, status)` — Stacks of the status on the entity (0 when it has none).
- `$status_text(entity, mechanism)` — The entity's statuses as text: 'poison ×2 (1 more round), stun (ends this round)'.
- `$terrain(position_or_entity, mechanism?)` — The place at a position or under an entity as {name, description, ...props}, or null; e.g. $terrain($actor).cover.
