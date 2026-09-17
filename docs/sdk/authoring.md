# Author scenarios from a brief

The shortest reliable path is **brief → contract → checks → known-answer tests → run → review**. Start with the smallest model that preserves the decision the user wants to make.

## Start with the executable guide

Run `fg-env guide authoring` (or `fg_env.guide("authoring")`) for the compact, tested
starting path: configurable objects and tables, dynamic entities, shared capacity,
round timing and an exact known-answer check. Hosts can include this installed-SDK
guide directly in an authoring agent’s starting context.

## 1. Make the decision explicit

Write down the decision, horizon, actors, controllable inputs and outputs before writing rules. For example: “Should a distributor raise safety stock for seasonal products over the next 12 weeks, given supplier delays and a fixed cash budget?”

Distinguish supplied facts from assumptions. If a brief does not specify lead time, mark your chosen value as an assumption and expose it as an input. Do not bury it in an expression.

## 2. Map the causal structure

For each outcome, identify what changes it. Stock depends on opening inventory, deliveries, sales and returns. Sales depend on demand and available stock. Demand may depend on seasonality, price and promotion. Cash depends on receipts, payments and timing.

Represent objects with their own lifecycle as entities: an order that can arrive late or be canceled usually deserves an identity. Use maps and lists for simpler structured values; use records for history.

Entity `id`, `name`, `type`, `alive` and `at` are built-in fields, not custom properties. Put display labels in the entity or population entry’s `name`, outside `props`. Within `create.props`, `$it` refers to the new entity; capture values from the enclosing loop in local variables before creating it.

Choose input types from the business units, not the example values. Money, effort and rates can be fractional (`type: "number"`); counts of indivisible items use `int`. An example of two hours per job does not imply whole-hour work. Defaults do not define minimums or maximums. Derive processing and bucket sizes from configured data instead of constraining customer inputs to match a hardcoded implementation. Input bounds reject values; they are not just the visual scale of a control. Do not invent a maximum merely to use a slider or knob. Use a number control unless the brief or domain justifies a finite range, and explain necessary modeling limits.

## Exact monetary budgets

Use `metrics` for per-round expressions and `outputs` for final results. `format`
belongs on outputs, not metrics. For per-entity rows, use an output expression
such as `$map(product, {name: $it.name, stock: $it.stock})`.

Use integer minor units for accounting that must be exact. Dollar inputs and
outputs can remain ordinary numbers: convert them to cents once, perform budget
checks and whole-unit ratios in cents, then divide by 100 for reporting. A money
format changes presentation, not numeric arithmetic. The `money` formatter takes
dollars in outputs, views and action receipts. Convert every cent-valued expression
there, not only the final report. State the rounding policy.
Configuration inputs and action parameters have different schemas. On `inputs`,
`multiple_of: 0.01` validates cents and `step` is a display hint. On action
`params`, `step: 0.01` validates increments from `min` (or zero); describe them
with `description`, not input-only `label` or `display`. Do not put `multiple_of`
on action parameters. For whole cents, choose a cent-aligned minimum.
This example rejects fractional cents before changing the budget.

```python
import fg_env

money = {
    "name": "Exact budget", "clock": {"rounds": 1},
    "inputs": {
        "budget": {"type": "number", "default": 0.30, "min": 0, "step": 0.01, "multiple_of": 0.01,
                   "description": "Dollars in whole-cent increments; fractional cents are rejected"},
        "unit_cost": {"type": "number", "default": 0.10, "min": 0.01, "step": 0.01, "multiple_of": 0.01,
                      "description": "Dollars in whole-cent increments; fractional cents are rejected"}
    },
    "world": {"available_cents": "$round($inputs.budget * 100)",
              "unit_cents": "$round($inputs.unit_cost * 100)", "spent_cents": 0},
    "types": {"operator": {"agent": True}},
    "entities": {"manager": {"type": "operator"}},
    "stages": [{"name": "allocate", "max_actions": 4}],
    "actions": {"spend": {
        "by": "operator",
        "params": {"amount": {"type": "number", "min": 0.01, "step": 0.01,
                              "max": "$world.available_cents / 100",
                              "description": "Amount in dollars, in whole cents."}},
        "do": ["$cost_cents = $round($params.amount * 100)",
               "$world.available_cents -= $cost_cents", "$world.spent_cents += $cost_cents"],
        "outcome": "Paid {$cost_cents / 100|money}."
    }},
    "outputs": {"spent": "$world.spent_cents / 100",
                "units": "$world.spent_cents // $world.unit_cents"}
}
def spend_in_parts(wake):
    assert not wake.call("spend", {"amount": 0.105}).ok
    for amount, receipt in [(0.10, "Paid $0.10."), (0.20, "Paid $0.20.")]:
        paid = wake.call("spend", {"amount": amount})
        assert paid.ok and paid.text == receipt
    assert not wake.call("spend", {"amount": 0.01}).ok
    wake.end()

result = fg_env.run(money, spend_in_parts)
assert result.ok and result.outputs == {"spent": 0.30, "units": 3}
```

## 3. Choose the timing

Name each round and stage. State whether delivery happens before demand, whether participants observe competitors' decisions, and when payment clears. These choices can change the answer more than a sophisticated demand equation.

Check intermediate balances, not only final totals. A pending count includes all created items that have not settled, including items due before the horizon ends. The final pending count is the last snapshot of that same quantity. For an applicable process, assert created = settled + lost + pending each round.

Give every delayed effect a clear starting point and due time. Include explicit tie-breaking when processing queued orders or allocating scarce capacity.

## 4. Build and inspect

```bash
fg-env new blank scenario.json
fg-env guide
fg-env guide actions
fg-env guide events
fg-env check scenario.json --rounds 5
fg-env preview scenario.json participant_id
```

Read only the reference sections needed for the current task. Expand mechanisms when you need to inspect their generated rules: `fg-env expand scenario.json --mechanisms`.

## 5. Test meaning, not just syntax

For every important requirement, write one observable check. Useful cases include zero demand, no budget, a full warehouse, a late delivery, a refund after settlement, overlapping audiences and tied queue priorities.

Calculate at least one small run by hand. Assert exact inventory, cash or capacity balances after each important transition. Also test changes: higher demand should not create inventory, and doubling a product table should not silently drop half the products.

Check configured values without editing the contract's defaults:

```bash
fg-env check scenario.json --inputs-file inputs.json --rounds 8
fg-env checks scenario.json --inputs-file inputs.json --boundaries --runs 2 --rounds 8
```

The Python equivalents are `fg_env.check(..., inputs=values)` and
`fg_env.behavior_checks(..., inputs=values, boundaries=True)`. Boundary checks sample
zero/min/max values, enum choices, empty/short collections, reordered tables, an added
duplicate table row, and fields in the first
row/item, with a default limit of 24 configurations. Reports state when that limit
is reached and give the input path, value, seed and runtime error for failures.
These checks do not cover every row or combination, behavior after the round cap,
or whether requested business rules and reports are complete. Keep the independent
known-answer tests. For time series, explicitly choose what missing future values
mean; `$get(schedule, $round - 1, 0)` uses zero after the list ends.

## 6. Hand off a complete environment

Ship the contract and its data together, with:

- A short brief and list of assumptions, units and timing rules.
- Input descriptions, defaults and supported ranges.
- At least one baseline policy and a reproducible seed.
- Known-answer checks and boundary cases.
- Output definitions and limitations of the model.
- A run command and the SDK version used.

## Instructions for an authoring agent

Use this as a starting instruction in your own authoring workflow:

> Build a rounds-based environment with the Environments SDK. Read `fg-env guide authoring`, then only the reference sections needed. Map each requirement to contract rules and observable checks. Make uncertain assumptions explicit inputs. Use public SDK APIs and keep scenario logic in the contract. Check the contract, preview every role, run a deterministic baseline, and verify a small known-answer case. Report omissions and unsupported behavior. Deliver the contract, data, tests and run instructions.

The agent should repair errors using their paths and suggested fixes, then rerun the affected checks. A clean checker result does not prove that the brief was captured faithfully.

## Configurable data and controls

Expose the data a user should change in `inputs`, and bind it into entity defaults,
population `from`, actions or events using `$inputs`. Input objects are data, not
new engine entity types. A `map` input can declare nested `fields`; a `table` can
declare the fields of each row, and a `list` can declare `items`. Each child uses
the same input specification, including types, defaults, required values and bounds.

```json
{"shop": {"type": "map", "label": "Store", "display": "object", "default": {},
  "fields": {
    "price": {"type": "number", "default": 25, "min": 5, "max": 100, "step": 1, "display": "slider", "unit": "USD"},
    "segment": {"type": "enum", "values": ["consumer", "business"], "default": "consumer", "display": "select"},
    "description": {"type": "text", "default": "", "display": "textarea"}
  }}}
```

The example is an `inputs` section. Read its price as `$inputs.shop.price`.
Hosts may render `display` hints using their own controls; the standalone SDK
validates and resolves values without requiring a UI. Supported displays are
`number`, `text`, `textarea`, `select`, `toggle`, `date`, `slider`, `knob`,
`table`, `object`, `list` and `json`. Sliders and knobs require numeric bounds.
For configuration inputs, `step` is a presentation increment, not rounding or
a validation constraint. Action parameters differ: their `step` is enforced.
Use `multiple_of` to validate numeric increments: `0.01` for whole-cent dollar
inputs or `6` for packs of six. It applies to defaults and supplied values,
including nested fields and list items, and is measured from zero independently
of `min`. Invalid values are rejected, never rounded. Ordinary floating-point
arithmetic noise within one billionth of an increment is tolerated.
Old contracts need no changes; existing `columns` table declarations still work.
