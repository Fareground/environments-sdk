# Build your first environment

Run a retailer for four weeks. Each week, the retailer orders stock, customers buy what is available, and the engine records cash and inventory. This example requires no API key.

For a compact example with editable objects and multiple entities, use
`fg-env guide authoring` after installing. Its known-answer test checks the
actual output, and the [authoring workflow](authoring.md) explains how to adapt it.

Prefer to describe it? `fg-env author brief.md --model anthropic:<model>` has a model write, check, preview and
run the contract for you with the SDK's own tools, and saves the latest version that checks clean and runs
(`--model openai:<model>` for OpenAI, or OpenRouter and other OpenAI-compatible servers through `OPENAI_BASE_URL`;
`fg_env.author(...)` from Python). This page builds one by hand, so you know what it wrote.

## 1. Install

```bash
python -m pip install fg-env
```

Use Python 3.11 or later. Save the following contract as `inventory.json`:

```json
{
  "name": "Weekly inventory decision",
  "brief": {
    "situation": "A retailer serves weekly demand from one stock pool.",
    "rules": "Choose a replenishment quantity before demand arrives. Orders arrive immediately in this introductory model."
  },
  "clock": {
    "rounds": 4,
    "unit": "week"
  },
  "inputs": {
    "weekly_demand": {
      "type": "int",
      "default": 6,
      "min": 0
    },
    "unit_price": {
      "type": "number",
      "default": 10,
      "min": 0
    },
    "unit_cost": {
      "type": "number",
      "default": 4,
      "min": 0
    }
  },
  "types": {
    "retailer": {
      "agent": true,
      "props": {
        "stock": 10,
        "cash": 100.0,
        "sold": 0,
        "lost": 0
      }
    }
  },
  "entities": {
    "shop": {
      "type": "retailer",
      "name": "Shop"
    }
  },
  "actions": {
    "replenish": {
      "by": "retailer",
      "description": "Purchase inventory before this week\u2019s demand.",
      "params": {
        "qty": {
          "type": "int",
          "min": 0,
          "max": 20
        }
      },
      "when": {
        "expr": "$actor.cash >= $params.qty * $inputs.unit_cost",
        "why": "The order exceeds available cash."
      },
      "do": [
        "$actor.cash -= $params.qty * $inputs.unit_cost",
        "$actor.stock += $params.qty"
      ]
    }
  },
  "events": [
    {
      "phase": "end",
      "each": "retailer",
      "do": [
        "$units = $min($it.stock, $inputs.weekly_demand)",
        "$it.stock -= $units",
        "$it.sold += $units",
        "$it.lost += $inputs.weekly_demand - $units",
        "$it.cash += $units * $inputs.unit_price"
      ]
    }
  ],
  "views": {
    "position": {
      "for": "retailer",
      "show": "Stock: {stock}. Cash: {cash|money}. Demand each week: {$inputs.weekly_demand}."
    }
  },
  "invariants": [
    "$entity(shop).stock >= 0",
    "$entity(shop).cash >= 0"
  ],
  "metrics": {
    "stock": "$entity(shop).stock",
    "cash": "$entity(shop).cash"
  },
  "outputs": {
    "units_sold": "$entity(shop).sold",
    "lost_sales": "$entity(shop).lost",
    "closing_cash": "$entity(shop).cash"
  },
  "policies": {
    "steady": {
      "rules": [
        {
          "do": "replenish",
          "with": {
            "qty": 6
          }
        }
      ]
    }
  },
  "arms": {
    "baseline": {},
    "busy": {
      "inputs": {
        "weekly_demand": 9
      }
    }
  }
}
```

## 2. Check and preview

```bash
fg-env check inventory.json
fg-env preview inventory.json shop
```

`check` validates the contract, then plays it for a few rounds with random agents, with agents that never act (a missed turn must not break the rules) and with each declared policy. `preview` shows the retailer's brief, current information, and available tools. A successful smoke check is an authoring aid; it does not establish the model's business accuracy.

## 3. Run a repeatable policy

Save this as `run_inventory.py` beside the contract:

```python
import fg_env

result = fg_env.run("inventory.json", {"retailer": "policy:steady"}, seed=7)
print(result.outputs)
assert result.outputs == {"units_sold": 24, "lost_sales": 0, "closing_cash": 244.0}
```

```bash
python run_inventory.py
fg-env run inventory.json --seed 7 --agent retailer=policy:steady --json
```

The policy orders six units each week. Opening stock is 10, purchases total 24, and sales total 24. Closing stock stays 10. Closing cash is `100 - 24 × 4 + 24 × 10 = 244`.

## 4. Change the scenario

```python
result = fg_env.run(
    "inventory.json",
    {"retailer": "policy:steady"},
    inputs={"weekly_demand": 9},
    seed=7,
)
print(result.outputs)
assert result.outputs["lost_sales"] == 2
```

The same policy now sells 34 units and loses two sales over four weeks. This is a useful known-answer test: demand increased while supply stayed fixed.

## Assumptions in this example

There is one product, fixed demand, immediate replenishment, no storage cost, and no credit. `closing_cash` is cash, not accounting profit. Real use cases may need delivery delays, multiple products, returns, price responses, or capacity constraints. Add those only when the scenario needs them; see [business modeling](business-modeling.md).

## Next steps

[Translate your brief into a contract](authoring.md), [connect a participant](integration.md), or explore [complete examples](examples.md).
