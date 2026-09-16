# flow / order

### `flow.order`
Turn order: initiative by an expression, rotating first player, snake order, skip conditions and extra turns. Use $turn_rank($it, name) as any stage's order, or give `stage` to declare the stage. $turn_order(name) is this round's order.

Config:
- `who` (required): Agent type whose order this is (subtypes included).
- `by` (default null): Initiative ($it): highest first (see ascending); seats break ties.
- `ascending` (default false): Lowest initiative first.
- `rotate` (default false): The first seat moves one place each round.
- `snake` (default false): Reverse the order every other round (snake draft).
- `skip` (default null): Agents who sit out ($it): folded, bankrupt, eliminated.
- `stage` (default null): Declare a stage (ordinary stage fields) using this order; its name defaults to the mechanism's.
- `extra_turns` (default false): Also declare <name>_extra after the stage for extra turns granted with the extra_turn action.
- `max_extra` (default 3): Most extra turns one agent can chain in a round.
- `views` (default true): Show agents this round's order.

Actions of the `flow` op:
- `extra_turn` — takes `who` (needs `who`): {"flow": "initiative", "action": "extra_turn", "who": "$actor"}  (the agent takes another turn this round)

```json
{"mechanisms": {"my_order": {"kind": "flow", "mode": "order", "who": "player", "by": "$it.speed", "skip": "$it.folded", "rotate": true, "stage": {"actions": ["bet", "fold"], "turns": "sequential"}}}}
```
