# conditions / channeling

### `conditions.channeling`
Multi-round actions: taking a listed action starts a channel that resolves `rounds` later with the original arguments, keeps the channeler busy, and breaks when `interrupt` holds (or with the `interrupt` action). A `fail` in resolve fizzles it with every change rolled back. $channeling(entity) is the channel in progress ({action, params, started, completes}) or null.

Config:
- `actions` (required): {action: {rounds, resolve, interrupt, on_interrupt, say, interrupt_say}}. Taking the action starts the channel (its own `do` runs at once: costs); `resolve` runs at the start of the round `rounds` later with the same $actor and $params.
- `busy` (default "all"): Actions the channeler cannot take meanwhile: all (every action of its type declared so far) or a list.
- `views` (default true): Show a channeling agent what it is channeling and when it completes.

Nested config:
**ChannelDef** — A channeled action.
- `rounds`: int | text (required) — Rounds until it completes (number or expression over $actor, $params).
- `resolve`: [any] — Effects when it completes, with the original $actor and $params.
- `interrupt`: text — Expression over $actor, $params checked each round: when true the channel breaks.
- `on_interrupt`: [any] — Effects when it breaks ($actor, $params).
- `say`: text — News when it completes (template over $actor, $params).
- `interrupt_say`: text — News when it breaks (template over $actor, $params).

Actions of the `conditions` op:
- `interrupt` — takes `who` (needs `who`): {"conditions": "spells", "action": "interrupt", "who": "$params.target"}  (break the entity's channel now: on_interrupt runs)

```json
{"mechanisms": {"my_channeling": {"kind": "conditions", "mode": "channeling", "actions": {"meteor": {"rounds": 2, "resolve": ["$params.target.hp -= 12"], "interrupt": "$actor.hp < 5", "say": "A meteor strikes!"}}}}}
```
