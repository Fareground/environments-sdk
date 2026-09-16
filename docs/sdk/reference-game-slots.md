# game / slots

### `game.slots`
Worker placement: capacity-limited action spaces. Generates a `<name>_place` tool whose `space` lists only open spaces, runs each space's effects when claimed, resets claims and workers every round, and shows the board. Functions: $open_spaces, $claims.

Config:
- `who` (required): Agent type that places workers.
- `spaces` (required): {space name: {capacity, description, when, do}}.
- `per_round` (default 1): Workers each player places per round (number or expression over $it).
- `once_per_space` (default true): A player may claim each space at most once per round.
- `stage` (default null): Offer the tool in this declared stage; default: a generated placement stage.
- `views` (default true): Generate the board view.

Nested config:
**SpaceConfig** — One action space.
- `capacity`: int | text = 1 — Workers it holds per round (number or expression).
- `description`: text — What placing a worker here does (shown on the board).
- `when`: text — Who may use it ($actor), e.g. "$actor.wood >= 2".
- `do`: effects — Effects when a worker is placed ($actor).

Actions of the `game` op:
- `place` — takes `space`, `who` (needs `space`): {"game": "farm", "action": "place", "space": "$params.space"}  (claim a space for $actor or `who`; fails when it is full)

```json
{"mechanisms": {"my_slots": {"kind": "game", "mode": "slots", "who": "farmer", "per_round": 2, "spaces": {"forest": {"capacity": 1, "description": "+2 wood", "do": ["$actor.wood += 2"]}, "market": {"capacity": 2, "when": "$actor.wood >= 1", "do": ["$actor.wood -= 1", "$actor.coins += 3"]}}}}}
```
