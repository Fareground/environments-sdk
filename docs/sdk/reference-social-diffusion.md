# social / diffusion

### `social.diffusion`
Items (rumors, ideas, products) spreading over a relation by independent cascade or linear threshold, with per-agent states (unaware, exposed, adopted, rejected) and exposure counts in the world prop `<name>`. Steps every round (in `phase`) or on demand with the `step` action; `on_adopt` effects run per adopter. Read it with $reach(item), $adopter_count(item), $spread_state(agent, item), $exposures(agent, item), $heard(agent).

Config:
- `who` (required): Entity type the items spread among (subtypes included).
- `over` (required): Relation the items travel along (e.g. follows).
- `flow` (default "both"): along: from → to; against: to → from (followers hear the followed); both.
- `model` (default "cascade"): cascade (independent cascade) | threshold (linear threshold).
- `p` (default 0.1): Cascade: chance one adopter convinces one neighbour (number or expression over $from, $to, $item).
- `persistent` (default false): Cascade: every adopter keeps trying to convince its neighbours at every step (default: one chance, right after adopting).
- `threshold` (default 0.5): Threshold: share of informing neighbours needed (number, expression over $it and $item, or "random").
- `weighted` (default false): Threshold: weigh neighbours by link value.
- `seeds` (default {}): Items adopted from the start: {item: [ids]}.
- `phase` (default "end"): Spread every round in this phase; null: only through the `step` action.
- `steps` (default 1): Spread steps per round.
- `on_adopt` (default []): Effects for each new adopter ($it, $item, and $from for a cascade).

Actions of the `social` op:
- `step` — takes `item`: {"social": "rumor", "action": "step", "item": "moon"}  (spread every item (or only `item`) one step now)
- `seed` — takes `item`, `who` (needs `item`, `who`): {"social": "rumor", "action": "seed", "item": "moon", "who": "$params.who"}  (the agents in `who` hold the item from now on, without running on_adopt)
- `adopt` — takes `item`, `who` (needs `item`, `who`): {"social": "rumor", "action": "adopt", "item": "moon", "who": "$params.who"}  (the agents in `who` adopt the item (unless they rejected it), running on_adopt)
- `reject` — takes `item`, `who` (needs `item`, `who`): {"social": "rumor", "action": "reject", "item": "moon", "who": "$params.who"}  (the agents in `who` reject the item: they stop holding and passing it on)
- `expose` — takes `item`, `who` (needs `item`, `who`): {"social": "rumor", "action": "expose", "item": "moon", "who": "$params.who"}  (count one more exposure of each agent in `who` who does not hold the item)

```json
{"entities": {"u1": {"type": "account"}, "u2": {"type": "account"}}, "relations": {"follows": {"links": [{"from": "u2", "to": "u1"}]}}, "mechanisms": {"my_diffusion": {"kind": "social", "mode": "diffusion", "who": "account", "over": "follows", "flow": "against", "model": "cascade", "p": 0.1, "seeds": {"rumor": ["u1"]}}}}
```
