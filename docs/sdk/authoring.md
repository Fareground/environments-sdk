# Author scenarios from a brief

The shortest reliable path is **brief → contract → checks → known-answer tests → run → review**. Start with the smallest model that preserves the decision the user wants to make.

## 1. Make the decision explicit

Write down the decision, horizon, actors, controllable inputs and outputs before writing rules. For example: “Should a distributor raise safety stock for seasonal products over the next 12 weeks, given supplier delays and a fixed cash budget?”

Distinguish supplied facts from assumptions. If a brief does not specify lead time, mark your chosen value as an assumption and expose it as an input. Do not bury it in an expression.

## 2. Map the causal structure

For each outcome, identify what changes it. Stock depends on opening inventory, deliveries, sales and returns. Sales depend on demand and available stock. Demand may depend on seasonality, price and promotion. Cash depends on receipts, payments and timing.

Represent objects with their own lifecycle as entities: an order that can arrive late or be canceled usually deserves an identity. Use maps and lists for simpler structured values; use records for history.

## 3. Choose the timing

Name each round and stage. State whether delivery happens before demand, whether participants observe competitors' decisions, and when payment clears. These choices can change the answer more than a sophisticated demand equation.

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

> Build a rounds-based environment with the Environments SDK. Read `fg-env guide`, then the relevant sections. Map each requirement to contract rules and observable checks. Make uncertain assumptions explicit inputs. Use public SDK APIs and keep scenario logic in the contract. Check the contract, preview every role, run a deterministic baseline, and verify a small known-answer case. Report omissions and unsupported behavior. Deliver the contract, data, tests and run instructions.

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
`step` is a presentation increment, not rounding or a constraint on the engine.
Old contracts need no changes; existing `columns` table declarations still work.
