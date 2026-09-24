# host / game_master

### `host.game_master`
Free-text attempts resolved by a host game master: agents get an `attempt(text)` tool; the host proposes effects and the engine applies them only when every one fits `allow` (kinds, targets, properties, bounds, amounts, destinations) — atomically, or refuses with the reason. Attempts, narration and changes go to the record <name>; the actor is told the result; answers are recorded for replay.

Config:
- `who` (required): Agent type(s) that may attempt things.
- `allow` (required): Every change the game master may make.
- `host` (default "game_master"): Host game master name.
- `model` (default null): A name for the kind of model wanted (e.g. "strong"), which the host maps to one of its own models (LLMHost(..., models={...})); a host that does not map it uses its own model.
- `tool` (default "attempt"): Name of the free-text tool.
- `description` (default ""): Tool description (default explains the tool).
- `max_chars` (default 500): Longest attempt text, in characters.
- `rules` (default ""): How the world works, for the game master (plain text).
- `context` (default {}): {name: expression over $actor} values shown to the game master.
- `max_effects` (default 4): Most changes one attempt may cause.
- `per_turn` (default 1): Attempts per turn.
- `terminal` (default true): An attempt ends the turn.
- `visible` (default "all"): Who reads the attempt log: 'all' or an expression over $viewer and $it.
- `fallback` (default null): Without a host: refuse every attempt (default: stop with an error).

Nested config:
**AllowRule** — One kind of change a game master may make.
- `effect`: any (required) — The change it allows.
- `target`: text = "actor" — set/move: 'actor' or an expression over $actor giving the entities it may touch.
- `prop`: text — set/set_world/transfer: the property.
- `min`: number — Lowest value a number may become.
- `max`: number — Highest value a number may become.
- `delta`: number — Largest change of a number in one attempt.
- `values`: [any] — The only values it may set.
- `max_chars`: int = 200 — Longest text it may set or spread as news.
- `from`: text = "actor" — transfer: 'actor' or an expression giving who may give.
- `to`: text — transfer: expression giving who may receive ('actor' works); move: 'adjacent' or an expression over $actor and $it giving places.
- `amount`: number — transfer: most that may move from one giver in one attempt.
- `description`: text — Shown to the game master.

Actions of the `host` op:
- `resolve` — takes `text`, `attach` (needs `text`): {"host": "gm", "action": "resolve", "text": "$params.text"}  (the game master resolves the actor's attempt; changes apply only within its allow-list, and $actor.gm_told says what happened; `attach` gives it files)

```json
{"mechanisms": {"my_game_master": {"kind": "host", "mode": "game_master", "who": "adventurer", "rules": "A small tavern. Be fair and terse.", "allow": [{"effect": "set", "prop": "health", "min": 0, "max": 10, "delta": 3}, {"effect": "transfer", "prop": "gold", "to": "$filter(adventurer, $it.id != $actor.id)", "amount": 5}, {"effect": "move", "to": "adjacent"}, {"effect": "news", "max_chars": 160}]}}}
```
