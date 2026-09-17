"""Compact, executable starting context for human and agent authors."""

AUTHORING = '''\
# Environments SDK: author a faithful scenario

An environment is one JSON contract. Keep domain rules in that contract; the SDK
executes them. Start from the user's decision, actors, inputs, timing and outcomes.
Separate supplied facts from assumptions. Preserve each requirement; do not silently
simplify it to make validation pass. No spatial model is needed for rounds-based work.

## Small complete example: allocate a shared resource

This example teaches composition, not a built-in scenario. Each editable table row
becomes an entity. One operator allocates a shared per-round capacity. The example
has a two-round horizon; real authors choose their own timing, decisions and rules.

```json
{
  "name": "Shared capacity",
  "clock": {"rounds": 2, "unit": "day"},
  "brief": {"rules": "Allocate capacity to work items. Capacity resets each day. Finish as much as possible."},
  "inputs": {
    "facility": {"type": "map", "display": "object", "default": {}, "fields": {
      "capacity": {"type": "int", "default": 4, "min": 0, "max": 100, "display": "knob", "label": "Daily capacity"}
    }},
    "items": {"type": "table", "display": "table", "fields": {
      "name": {"type": "text", "required": true},
      "quantity": {"type": "int", "min": 0, "required": true}
    }, "default": [{"name": "A", "quantity": 3}, {"name": "B", "quantity": 2}]}
  },
  "world": {"available": 0},
  "types": {
    "operator": {"agent": true},
    "item": {"props": {"pending": 0, "completed": 0}}
  },
  "entities": {"manager": {"type": "operator"}},
  "population": [{"type": "item", "from": "$inputs.items", "name": "{$row.name}", "props": {"pending": "$row.quantity"}}],
  "stages": [{"name": "allocate", "max_actions": 100, "max_calls": 110}],
  "events": [{"phase": "start", "do": "$world.available = $inputs.facility.capacity"}],
  "actions": {"allocate": {
    "by": "operator", "description": "Complete work using shared capacity.",
    "params": {
      "item": {"type": "entity", "of": "item", "where": "$it.pending > 0"},
      "quantity": {"type": "int", "min": 1, "max": "$min($world.available, $params.item.pending)"}
    },
    "when": ["$world.available > 0"],
    "do": ["$params.item.pending -= $params.quantity", "$params.item.completed += $params.quantity", "$world.available -= $params.quantity"]
  }},
  "views": {
    "capacity": {"show": "Available capacity: {$world.available}"},
    "items": {"of": "item", "show": "{$it.id}: {$it.name}, pending {$it.pending}, completed {$it.completed}"}
  },
  "metrics": {"completed": "$sum(item, $it.completed)"},
  "outputs": {"completed": "$sum(item, $it.completed)", "pending": "$sum(item, $it.pending)"},
  "invariants": [{"expr": "$world.available >= 0", "why": "Shared capacity cannot be overspent."}]
}
```

Save the JSON as `scenario.json`. Check syntax, inspect the participant's actual
information and tools, then run a known-answer case:

```python
import fg_env

assert fg_env.check("scenario.json", rounds=0) == []
print(fg_env.load("scenario.json").preview("manager"))

def fixed_policy(wake):
    calls = [("item_1", 3), ("item_2", 1)] if wake.round == 1 else [("item_2", 1)]
    for item, quantity in calls:
        result = wake.call("allocate", {"item": item, "quantity": quantity})
        assert result.ok, result.text
    wake.end()

result = fg_env.run("scenario.json", fixed_policy, seed=1)
assert result.ok
assert result.outputs == {"completed": 5, "pending": 0}
assert result.series["completed"] == [4, 5]
```

## Compose without losing fidelity

- Inputs are typed data and actual bindings, not labels. `map` + `fields` defines
  nested objects; `table` + `fields` defines rows; `list` + `items` defines lists.
  Bind `$inputs` in defaults, `population.from`, actions or events. `display` may be
  text, textarea, number, select, toggle, date, slider, knob, table, object, list or
  json. Slider/knob need numeric bounds. Hosts render hints using their existing UI.
- Editable collections must remain data-driven: `population.from` creates one
  entity per row when count is omitted. Never hardcode rows 0, 1, 2. Test empty,
  added, removed and reordered rows. Use stable input IDs as entity IDs when the
  domain needs persistent identity; duplicate IDs are invalid.
- Objects with independent lifecycles belong in entities (orders, accounts,
  cohorts); simple settings belong in maps. Use relations for links and records
  for history. Only decision-makers need `agent: true`; background processes use
  events. Use views to expose precisely what each role is allowed to know.
- Order matters: start events, stages, end events, metrics. A round advances after
  stages, not after each action. Set `max_actions` explicitly for repeated choices. A per-round cap must track
  the total across ALL calls in that round and reset once; a parameter maximum
  only limits one call. Test a second action that tries to exceed the remaining cap.
  Charge a shared budget/capacity once, in the same atomic action as the outcome.
  For money, record both cash received and cash paid. Write the conservation
  equation: closing cash = opening cash + receipts - payments. Money returned to
  a customer reduces cash. Refundable deposits create liabilities, not revenue.
  Define refunds and settlement timing. For delays, define the due round and whether arrivals precede decisions.
- Expressions read `$inputs`, `$world`, `$actor`, `$params`; `population` uses
  `$row`; loops use `$it`. `{$...}` substitutes only in template fields (brief,
  show, outcome, say), not ordinary stored strings. Read a focused guide when a
  feature is unfamiliar; don't invent syntax or reload the whole manual.
- Validation and random runs establish executability, not fidelity. For each
  important requirement, calculate an expected observable result from the brief
  BEFORE inspecting a run. State the opening balance and each receipt/payment;
  do not copy the contract expression or observed output as the expected answer. Check conservation (cash, stock, capacity), timing, zero cases and
  sensitivity. A declared input appearing only in an output is not a mechanism.
  Check deduplication/overlap explicitly when combining audiences or populations.
- Deliver assumptions, input controls, output meanings and tested limitations.
  A runnable model is not proof of predictive accuracy. Keep omissions visible.

## Focused references

`fg_env.guide(part)` or `fg-env guide PART`: inputs, population, actions, stages,
events, views, expressions, effects, invariants, records, relations, running.
`guide('core')` maps the full SDK; `guide('mechanisms')` lists reusable mechanisms.
Use them when their semantics fit. Extend the contract with ordinary rules when
needed; don't force a scenario into a preset. `fg_env.schema()` is the exact schema.
'''
