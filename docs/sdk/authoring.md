# Authoring principles

**Start with `fg-env guide authoring`** (or `fg_env.guide("authoring")`; [the same page here](reference-authoring.md)).
It is the one page an author needs: the faithful-first principle, the write → check → preview → run loop, a complete
worked contract with a known-answer check, and the guide parts most briefs need. Give it to an authoring agent as its
starting context; `check` errors name the guide part to read next.

This page is the judgement around that loop: the shortest reliable path is **brief → contract → checks →
known-answer tests → run → review**, starting with the smallest model that preserves the decision the user wants to
make.

## 1. Make the decision explicit

Write down the decision, horizon, actors, controllable inputs and outputs before writing rules. For example: “Should a distributor raise safety stock for seasonal products over the next 12 weeks, given supplier delays and a fixed cash budget?”

Distinguish supplied facts from assumptions. If a brief does not specify lead time, mark your chosen value as an assumption and expose it as an input. Do not bury it in an expression.

## 2. Map the causal structure

For each outcome, identify what changes it. Stock depends on opening inventory, deliveries, sales and returns. Sales depend on demand and available stock. Demand may depend on seasonality, price and promotion. Cash depends on receipts, payments and timing.

Represent objects with their own lifecycle as entities: an order that can arrive late or be canceled usually deserves an identity. Use maps and lists for simpler structured values; use records for history.

Entity `id`, `name`, `type`, `alive` and `at` are built-in fields, not custom properties. Put display labels in the entity or population entry’s `name`, outside `props`. Within `create.props`, `$it` refers to the new entity; capture values from the enclosing loop in local variables before creating it.

Choose input types from the business units, not the example values. Money, effort and rates can be fractional (`type: "number"`); counts of indivisible items use `int`. An example of two hours per job does not imply whole-hour work. Defaults do not define minimums or maximums. Derive processing and bucket sizes from configured data instead of constraining inputs to match a hardcoded implementation. For money that must add up exactly, see [money and settlement](business-modeling.md#money-and-settlement).

Expose what a user should change as `inputs`, each defaulting to the brief's value, and read them with `$inputs`
wherever the contract needs them. Maps, tables and lists can declare the fields of their items. Bound an input only
where the brief or the domain limits it: a bound refuses values, it is not a display range. `fg-env guide inputs`
lists every field.

## 3. Choose the timing

Name each round and stage. State whether delivery happens before demand, whether participants observe competitors' decisions, and when payment clears. These choices can change the answer more than a sophisticated demand equation.

Check intermediate balances, not only final totals. A pending count includes all created items that have not settled, including items due before the horizon ends. The final pending count is the last snapshot of that same quantity. For an applicable process, assert created = settled + lost + pending each round.

Give every delayed effect a clear starting point and due time. Include explicit tie-breaking when processing queued orders or allocating scarce capacity.

## 4. Build and inspect

```bash
fg-env new blank scenario.json
fg-env guide authoring
fg-env guide actions
fg-env guide events
fg-env check scenario.json
fg-env preview scenario.json ada    # an agent's id: the blank template's agents are ada and bo
```

Read only the reference sections needed for the current task. Expand mechanisms when you need to inspect their generated rules: `fg-env expand scenario.json --mechanisms`.

## 5. Test meaning, not just syntax

For every important requirement, write one observable check. Useful cases include zero demand, no budget, a full warehouse, a late delivery, a refund after settlement, overlapping audiences and tied queue priorities.

Calculate at least one small run by hand. Assert exact inventory, cash or capacity balances after each important transition. Also test changes: higher demand should not create inventory, and doubling a product table should not silently drop half the products.

Check configured values without editing the contract's defaults:

<!-- not run: inputs.json holds your scenario's configured values -->
```bash
fg-env check scenario.json --inputs-file inputs.json --rounds 8
fg-env playtest scenario.json --inputs-file inputs.json --boundaries --runs 2 --rounds 8
```

The Python equivalents are `fg_env.check(..., inputs=values)` and
`fg_env.analysis.behavior_checks(..., inputs=values, boundaries=True)`. Boundary checks sample
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

> Build a rounds-based environment with the Environments SDK. Read `fg-env guide authoring`, then only the reference parts it points to when you need them. Implement every stated requirement and deliverable faithfully first, then make values configurable with the brief's values as defaults. Map each requirement to contract rules and observable checks. Make uncertain assumptions explicit inputs. Use public SDK APIs and keep scenario logic in the contract. Check the contract, preview every role, run a deterministic baseline, and verify a small known-answer case. Report omissions and unsupported behavior. Deliver the contract, data, tests and run instructions.

The agent should repair errors using their paths and suggested fixes, then rerun the affected checks. A clean checker result does not prove that the brief was captured faithfully.
