# conditions / terrain

### `conditions.terrain`
Terrain on the space: places with static props ($terrain(position)), modifiers to occupants ($effective), entry requirements checked by the `enter` action (and $can_enter), on_enter effects, and tick effects on occupants each round.

Config:
- `who` (required): Type(s) affected by the terrain (subtypes included).
- `places` (required): {name: {at | area, description, props, modifiers, enter, tick, on_enter}}. Modifiers and ticks apply to occupants standing there; `enter` rules guard the enter action.
- `views` (default true): Tell agents which terrain they stand on.

Nested config:
**PlaceDef** — One kind of terrain.
- `description`: text
- `at`: [any] — Positions: grid cells [row, col], graph node names or plane points [x, y].
- `area`: [[int | number]] — A rectangle [[row0, col0], [row1, col1]] (or [[x0, y0], [x1, y1]]), corners included.
- `props`: object — Static properties, read with $terrain(position).x.
- `modifiers`: object — {prop: add | {add, mul}} for occupants, read with $effective.
- `enter`: [Condition] — Requirements to enter ($it): text or {expr, why}.
- `tick`: [any] — Effects on each occupant at the start of every round ($it).
- `on_enter`: [any] — Effects when an entity enters with the enter action ($it).
**ModifierSpec** — How a status or a place changes a property: ``(base + add) * mul`` (per stack for statuses).
- `add`: number | text = 0.0 — Added to the property (number or expression over $it).
- `mul`: number | text = 1.0 — Multiplies the property (number or expression over $it).
**Condition** — A requirement: ``"$actor.cash > 0"`` or ``{"expr": ..., "why": "You have no money."}``.
- `expr`: text (required)
- `why`: text

Actions of the `conditions` op:
- `enter` — takes `who`, `to` (needs `who`, `to`): {"conditions": "terrain", "action": "enter", "who": "$actor", "to": "$params.cell"}  (move there if every terrain's entry rules allow; runs on_enter)

```json
{"mechanisms": {"my_terrain": {"kind": "conditions", "mode": "terrain", "who": "unit", "places": {"forest": {"area": [[0, 0], [1, 1]], "modifiers": {"armor": 1}}, "lava": {"at": [[2, 2]], "tick": ["$it.hp -= 3"], "enter": [{"expr": "$it.fireproof", "why": "Too hot."}]}}}}}
```
