# game / status

### `game.status`
Named statuses on entities: timed or permanent, stacking, ticking effects each round, property modifiers ($effective), blocked actions, immunity, expiry news and cleansing. Apply with the `apply` action, remove with `cleanse`; read with $has_status, $status_stacks, $status_rounds. State is the map property <name> on each carrier.

Config:
- `who` (required): Type(s) that can carry these statuses (subtypes included).
- `statuses` (required): {name: {duration, stacking, max_stacks, tick, modifiers, blocks, blocked_why, immune, unless, on_apply, on_expire, say, expire_say, cleansable}}. Durations count rounds after the round of application; tick effects see $it (carrier), $stacks and $source.
- `phase` (default "start"): When statuses tick: start (before agents act) or end of the round. Expiry is always at the end.
- `views` (default true): Show every agent who is affected by what.

Nested config:
**StatusDef** — One named status.
- `description`: text
- `duration`: int = 1 — Rounds it lasts after the round it is applied in; null = until cleansed.
- `stacking`: any = "refresh" — Applying it again: refresh (add a stack, reset the timer), extend (add a stack and the duration), independent (each application its own timer), ignore (no effect while active).
- `max_stacks`: int = 1 — Most stacks it can have at once.
- `tick`: [any] — Effects each round it is active ($it, $stacks, $source).
- `modifiers`: object — {prop: add | {add, mul}} per stack, read with $effective(entity, prop).
- `blocks`: any | [text] — Actions the carrier cannot take while it is active.
- `blocked_why`: text — Why a blocked action is refused.
- `immune`: [text] — Statuses that cannot be applied while this one is active.
- `unless`: expression — Expression over $it: when true the status cannot be applied to it.
- `on_apply`: [any] — Effects each time it is applied ($it, $stacks, $source).
- `on_expire`: [any] — Effects when it runs out ($it, $stacks, $source).
- `say`: text — News when it is applied (template over $it, $stacks).
- `expire_say`: text — News when it runs out (template over $it).
- `cleansable`: bool = true — Removed by a cleanse of 'all' (a cleanse naming it always removes it).
**ModifierSpec** — How a status changes a property: ``(base + add) * mul`` per stack.
- `add`: number | text = 0.0 — Added to the property (number or expression over $it).
- `mul`: number | text = 1.0 — Multiplies the property (number or expression over $it).

Actions of the `game` op:
- `apply` — takes `status`, `who`, `rounds`, `stacks`, `source` (needs `status`, `who`): {"game": "conditions", "action": "apply", "status": "poison", "who": "$params.target", "rounds": 3, "stacks": 1}  (rounds and stacks are optional; source defaults to $actor)
- `cleanse` — takes `status`, `who` (needs `status`, `who`): {"game": "conditions", "action": "cleanse", "status": "poison", "who": "$params.ally"}  (a status, a list, or "all" for every cleansable one; on_expire does not run)

```json
{"types": {"unit": {"agent": true, "props": {"hp": 20, "armor": 0}}}, "actions": {"attack": {"by": "unit", "do": "$actor.hp += 0"}}, "mechanisms": {"my_status": {"kind": "game", "mode": "status", "who": "unit", "statuses": {"poison": {"duration": 3, "max_stacks": 3, "tick": ["$it.hp -= 2 * $stacks"]}, "stun": {"duration": 1, "blocks": ["attack"], "blocked_why": "you are stunned"}, "shield": {"duration": 2, "modifiers": {"armor": 3}, "immune": ["poison"]}}}}}
```
