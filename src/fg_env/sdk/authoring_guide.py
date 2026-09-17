"""Compact, executable starting context for human and agent authors."""

AUTHORING = '''\
# Environments SDK: author a faithful scenario

An environment is one JSON contract of rules. Start from the user's decision,
actors, inputs, timing and outcomes. Separate facts from assumptions. Preserve
requirements; do not simplify them to pass validation. Rounds need no spatial model.

## Small complete example: allocate a shared resource

Each editable row becomes an entity. One operator allocates shared capacity and
per-item allowances over a configurable horizon.

```json
{
  "name": "Shared capacity",
  "clock": {"rounds": "$inputs.horizon", "unit": "day"},
  "brief": {"rules": "Allocate capacity to work items. Shared and per-item allowances reset each day. Finish as much as possible.", "roles": {"operator": "Prioritize {$inputs.facility.priority}."}},
  "inputs": {
    "horizon": {"type": "int", "default": 2, "min": 1, "max": 30, "display": "slider", "label": "Days"},
    "facility": {"type": "map", "display": "object", "default": {}, "fields": {
      "capacity": {"type": "int", "default": 4, "min": 0, "max": 100, "display": "knob", "label": "Daily capacity"},
      "priority": {"type": "enum", "values": ["smallest backlog", "largest backlog"], "default": "largest backlog", "display": "select"}
    }},
    "items": {"type": "table", "display": "table", "fields": {
      "name": {"type": "text", "required": true},
      "quantity": {"type": "int", "min": 0, "required": true},
      "daily_limit": {"type": "int", "min": 0, "default": 2}
    }, "default": [{"name": "A", "quantity": 3}, {"name": "B", "quantity": 2}]}
  },
  "world": {"available": 0},
  "types": {
    "operator": {"agent": true},
    "item": {"props": {"pending": 0, "completed": 0, "daily_limit": 2, "remaining_today": 0}}
  },
  "entities": {"manager": {"type": "operator"}},
  "population": [{"type": "item", "from": "$inputs.items", "name": "{$row.name}", "props": {"pending": "$row.quantity", "daily_limit": "$row.daily_limit"}}],
  "stages": [{"name": "allocate", "max_actions": 100, "max_calls": 110}],
  "events": [{"phase": "start", "do": ["$world.available = $inputs.facility.capacity", {"each": "item", "do": "$it.remaining_today = $it.daily_limit"}]}],
  "actions": {"allocate": {
    "by": "operator", "description": "Complete work using shared capacity.",
    "params": {
      "item": {"type": "entity", "of": "item", "where": "$it.pending > 0 and $it.remaining_today > 0"},
      "quantity": {"type": "int", "min": 1, "max": "$min($world.available, $params.item.pending, $params.item.remaining_today)"}
    },
    "when": ["$world.available > 0"],
    "do": ["$params.item.pending -= $params.quantity", "$params.item.completed += $params.quantity", "$world.available -= $params.quantity", "$params.item.remaining_today -= $params.quantity"]
  }},
  "views": {
    "capacity": {"show": "Available capacity: {$world.available}"},
    "items": {"of": "item", "show": "{$it.id}: {$it.name}, pending {$it.pending}, completed {$it.completed}, allowance left today {$it.remaining_today}"}
  },
  "metrics": {"completed": "$sum(item, $it.completed)"},
  "outputs": {"completed": "$sum(item, $it.completed)", "pending": "$sum(item, $it.pending)"},
  "invariants": [{"expr": "$world.available >= 0", "why": "Shared capacity cannot be overspent."}]
}
```

Save as `scenario.json`. Check syntax, preview participant information/tools,
and run a known-answer case:

```python
import fg_env

assert fg_env.check("scenario.json", rounds=0) == []
print(fg_env.load("scenario.json").preview("manager"))

def fixed_policy(wake):
    calls = [("item_1", 1, True), ("item_1", 1, True),
             ("item_1", 1, False), ("item_2", 2, True)] if wake.round == 1 else [("item_1", 1, True)]
    for item, quantity, expected_ok in calls:
        result = wake.call("allocate", {"item": item, "quantity": quantity})
        assert result.ok == expected_ok, result.text
    wake.end()

result = fg_env.run("scenario.json", fixed_policy, seed=1)
assert result.ok
assert result.outputs == {"completed": 5, "pending": 0}
assert result.series["completed"] == [4, 5]
short = fg_env.run("scenario.json", fixed_policy, inputs={"horizon": 1}, seed=1)
assert short.ok and short.outputs == {"completed": 4, "pending": 1}
preview = fg_env.load("scenario.json", inputs={"facility": {"priority": "smallest backlog"}}).preview("manager")
assert "Prioritize smallest backlog." in preview["brief"]
```

## Compose without losing fidelity

- Inputs are typed data and actual bindings, not labels. `map` + `fields` defines
  nested objects; `table` + `fields` defines rows; `list` + `items` defines lists.
  Bind `$inputs` in defaults, `population.from`, actions or events. `display` may be
  text, textarea, number, select, toggle, date, slider, knob, table, object, list or
  json. Dropdowns use `type: "enum"`, `values: [...]`, `display: "select"`.
  Roles use `brief.roles.<type>`; put selected goals there so the participant reads
  their actual values. Action descriptions are static text, not templates.
  Slider/knob need numeric bounds. Bind a configurable duration with
  `"clock": {"rounds": "$inputs.horizon", "unit": "day"}` as above.
  Hosts render hints using their existing UI.
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
  `{"after": n, "do": [...]}` needs integer rounds `n >= 1`; due effects run
  before start events/decisions. For zero delay, branch to immediate effects
  or constrain the input to start at 1 if same-round delivery is unsupported.
- Property shorthand is a DEFAULT value: `"done": false` is Boolean; `"done":
  "bool"` is literal text. For explicit types use `{"type": "bool", "default": false}`.
- Expressions read `$inputs`, `$world`, `$actor`, `$params`; `population` uses
  `$row`; loops use `$it`. `{$...}` substitutes only in template fields (brief,
  show, outcome, say), not stored strings. Look up unfamiliar syntax in a focused
  guide; don't invent it or reload the manual.
- Validation/random runs establish executability, not fidelity. Derive expected
  results from the brief BEFORE running, never from the contract or its output.
  Show opening balances and receipts/payments. Check conservation, timing, zero
  cases, sensitivity and audience/population overlap. Inputs must change rules,
  not just reports.
- Record every requested per-round measure in `metrics` and final measure in
  `outputs`. Participant `views` are observations, not user reports.
- Deliver assumptions, input controls, output meanings and tested limitations.
  A runnable model is not proof of predictive accuracy. Keep omissions visible.

## Validate nested inputs

Use `items` for list elements; `required: true` rejects null. `display` selects
controls, not bounds. A horizon can outlast an editable schedule: choose and state
the missing-data policy. Here `$get(list, index, 0)` means no arrivals after the
list ends; direct indexing would fail. Test short/empty lists and longer horizons.

```python
import fg_env

input_example = {"name": "Typed schedules", "types": {}, "inputs": {
    "rows": {"type": "table", "display": "table", "default": [], "fields": {
        "name": {"type": "text", "required": True},
        "schedule": {"type": "list", "display": "list", "default": [],
                     "items": {"type": "int", "min": 0, "required": True}}
    }}
}}
loaded = fg_env.load(input_example, inputs={"rows": [{"name": "A", "schedule": [0, 2]}]})
assert loaded.inputs["rows"][0]["schedule"] == [0, 2]
input_example.update(clock={"rounds": 4}, world={"total": 0},
    events=[{"each": "$inputs.rows", "do": "$world.total += $get($it.schedule, $round - 1, 0)"}],
    outputs={"total": "$world.total"})
for schedule, total in (([0, 2], 2), ([], 0)):
    result = fg_env.run(input_example, inputs={"rows": [{"name": "A", "schedule": schedule}]})
    assert result.ok and result.outputs == {"total": total}
for invalid in ([-1], ["invalid"], [None]):
    try:
        fg_env.load(input_example, inputs={"rows": [{"name": "A", "schedule": invalid}]})
    except fg_env.ContractError:
        pass
    else:
        raise AssertionError("Invalid schedule was accepted")
```

Choose constraints from the scenario. Test zero, empty lists, duplicate names and
changed row counts. Define zero-price/delay behavior explicitly; do not hide it
behind an arbitrary nonzero divisor.

## Focused references

`fg_env.guide(part)` / `fg-env guide PART`: inputs, population, actions, stages,
events, views, expressions, effects, running. `core` maps the SDK; `mechanisms`
lists reusable rules. Use only mechanisms that fit, otherwise compose rules.
`fg_env.schema()` gives exact field definitions.
'''
