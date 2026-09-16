# conditions / cooldowns

### `conditions.cooldowns`
Per-action cooldowns and charges with regeneration. Each listed action is offered only when ready, and starts its cooldown (spends a charge) when taken. Read with $ready(entity, action), $charges(entity, action), $cooldown_left(entity, action); the `reset` action makes one ready again.

Config:
- `actions` (required): {action: {cooldown, charges, recharge, start, why}}. cooldown N: after a use the action is unavailable for the next N rounds; charges: uses stored, one regained every `recharge` rounds.
- `views` (default true): Show each agent the state of its limited actions.

Nested config:
**CooldownDef** — Limits on one action.
- `cooldown`: int | text = 0 — Rounds the action is unavailable after each use (number or expression over $actor, $params).
- `charges`: int — Uses stored; each use spends one.
- `recharge`: int — Rounds to regain one spent charge.
- `start`: int — Charges at the start (default: full).
- `why`: text — Why the action is refused while not ready.

Actions of the `conditions` op:
- `reset` — takes `ability`, `who` (needs `ability`): {"conditions": "abilities", "action": "reset", "ability": "all", "who": "$params.ally"}  (an action, a list, or all: ready with full charges; who defaults to $actor)

```json
{"mechanisms": {"my_cooldowns": {"kind": "conditions", "mode": "cooldowns", "actions": {"fireball": {"cooldown": 2}, "heal": {"charges": 2, "recharge": 3}}}}}
```
