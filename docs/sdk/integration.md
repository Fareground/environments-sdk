# Participants and hosting

## A participant is a callable

A participant reads a `Wake` and calls the tools it receives. The following participant works with the inventory quickstart:

```python
import fg_env

def replenish(wake):
    result = wake.call("replenish", {"qty": 6})
    if not result.ok:
        print(result.text)
    wake.end()

result = fg_env.run("inventory.json", {"retailer": replenish}, seed=7)
print(result.outputs)
```

Map participants by entity ID or type. Built-in choices include `random`, `idle` and `policy:<name>`. Random participants are useful for smoke checks, not a substitute for a business baseline.

## The wake interface

| Member | Purpose |
|---|---|
| `brief`, `update` | Static context and the current participant-visible picture |
| `tools` | Tool names, descriptions and argument schemas |
| `tools_for(provider)` | Provider-shaped tool declarations |
| `call(name, args)` | Execute or submit a call and return a `ToolResult` |
| `end()` | Finish this turn |
| `done`, `calls_left`, `actions_left` | Turn state and remaining allowances |
| `entity_id`, `round`, `stage`, `me` | Participant identity and current context |
| `record_usage(...)` | Record usage from a custom model integration |

Inspect `result.ok`, `result.text`, `result.ended` and `result.data`. A rejected call is feedback to the participant, not proof that the environment failed.

## Async applications

Async callables are supported. Within an existing event loop, use `await env.arun(participants)` so loop-bound clients remain on that loop. The [running reference](reference-running.md) documents timing, budgets and concurrency.

## Language-model participants

The SDK accepts caller-owned provider clients. Choose a model available to your account:

```python
import os
import anthropic
import fg_env

participant = fg_env.participants.anthropic(
    anthropic.Anthropic(), os.environ["SIMULATION_MODEL"]
)
result = fg_env.run("inventory.json", {"retailer": participant}, seed=7)
```

Install the provider client separately (`python -m pip install anthropic`) and configure its credentials through your normal secret management. Environment seeds do not make remote model responses deterministic.

## Host responsibilities

A hosting application stores the contract and data, chooses participant implementations, runs the environment, and displays outputs and traces. It also owns authentication, tenant boundaries, resource limits, credential handling and artifact access. The SDK itself is not a sandbox for arbitrary host code.

Generated environments should pass contract checks and requirement-level tests before a customer run. Preview roles, cap workloads, and retain the exact contract, inputs and version. Run budgets are checked at execution boundaries; an in-progress turn can finish before a limit is applied.

## Save and resume

```python
import fg_env

env = fg_env.load("inventory.json", seed=7)
env.step({"retailer": "policy:steady"})
snapshot = env.snapshot()
restored = fg_env.Env.restore("inventory.json", snapshot)
result = restored.run({"retailer": "policy:steady"})
```

Take snapshots between rounds and retain the matching contract. For replay of external participant decisions, enable exposures and use the trace APIs; see [troubleshooting](troubleshooting.md).

## Using another agent library

The participant boundary is a callable and typed tools. Neither SDK needs to import the other. An integration should expose only the wake's available tools, route calls back through `wake.call`, and stop when the turn ends. Do not bypass the environment by editing its state from the agent.
