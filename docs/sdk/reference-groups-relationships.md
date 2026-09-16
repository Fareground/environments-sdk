# groups / relationships

### `groups.relationships`
Relations (trust, affinity, rivalry) that drift back toward a baseline every round and fire threshold events (news and effects with $from, $to, $value) when crossed. Change them with the `relate` action; read them with $relation(a, b, kind).

Config:
- `relations` (required): {relation: {baseline, decay, min, max, symmetric, thresholds}}.
- `phase` (default "end"): When relations decay each round.

Nested config:
**RelationDynamics** — A relation whose values drift back to a baseline.
- `baseline`: number = 0.0 — Where values settle (and where a new link starts).
- `decay`: number = 0.0 — Share of the gap to the baseline closed every round.
- `min`: number = -1.0
- `max`: number = 1.0
- `symmetric`: bool = false
- `description`: text
- `thresholds`: [Threshold]
**Threshold** — An event when a relation crosses a value.
- `at`: number (required)
- `direction`: any = "above" — Fires when the value rises to/above, or falls to/below, `at`.
- `say`: text — News text (template over $from, $to, $value).
- `to`: any = "both" — Who reads the news.
- `do`: effects — Effects when it fires ($from, $to, $value).
- `once`: bool = false — Fire at most once per pair; otherwise again after crossing back.

Actions of the `groups` op:
- `relate` — takes `relation`, `from`, `to`, `add`, `set` (needs `relation`, `from`, `to`): {"groups": "bonds", "action": "relate", "relation": "trust", "from": "$actor", "to": "$params.partner", "add": 0.2}  (change a relation by `add` or replace it with `set`; a missing link starts at its baseline; thresholds fire)

```json
{"mechanisms": {"my_relationships": {"kind": "groups", "mode": "relationships", "relations": {"trust": {"baseline": 0, "decay": 0.1, "thresholds": [{"at": 0.7, "say": "{$from.name} now trusts {$to.name}."}]}}}}}
```
