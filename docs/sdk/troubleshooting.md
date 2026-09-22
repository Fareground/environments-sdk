# Troubleshooting

## Start with the smallest evidence

```bash
fg-env check inventory.json
fg-env preview inventory.json shop
fg-env run inventory.json --seed 7 --agent retailer=policy:steady --trace run.jsonl
fg-env trace run.jsonl
```

| Symptom | What to inspect |
|---|---|
| Unknown property or root | The issue path, the type's properties, and the roots available in that section |
| Action never offered | Actor type, current stage, action requirements and argument bounds |
| Call refused | `ToolResult.text`; distinguish malformed arguments from a business constraint |
| Call refused with "Nothing changed" (divide by zero, number too large, a broken invariant) | A rule failed or an invariant broke while the action applied: `result.diagnostics` (`action_rule_failed`, `action_broke_invariant`) names the rule and `stats.faulted_actions` counts them; guard it with parameter bounds or a `when` with a `why` |
| `check` warns a policy rule was refused every time | The refusal it quotes; the rule's `with` builds arguments the action never accepts |
| A choice depends on another argument | The schema lists every candidate; a call with a combination the `where` rules out is refused with the valid choices given the other arguments |
| Output stays constant | Whether any rule changes its dependencies; run behavior checks |
| Simultaneous decisions lose updates | Whether effects overwrite shared state; inspect settlement semantics |
| Everyone sees confidential data | Public announcements, views, record visibility and recorded exposures |
| Turns forfeited (`turns_forfeited`) | The model provider kept failing after retries; rerun or raise `retries` |
| Model keeps making invalid calls | Tool descriptions, legal bounds, role brief and trace of rejected calls |
| Resume differs | Contract/version mismatch, external responses or different participant decisions |
| Results look plausible but wrong | Hand-check units, timing, balances, allocation and omitted causal effects |

## Inspect the run

```python
import fg_env

result = fg_env.run(
    "inventory.json", {"retailer": "policy:steady"}, seed=7, exposures=True
)
print(result.summary())
print(result.diagnostics)
trace = fg_env.trace(result)
print(trace.overview())
print(trace.invalid())
result.save("run.jsonl")
```

Diagnostics include a code, path, message and suggested fix. They cover common authoring problems but cannot detect every missing requirement. Treat warnings as items to investigate.

## Replay and privacy

`fg_env.trace("run.jsonl").replay("inventory.json")` replays recorded calls and reports a divergence. Keep traces within the appropriate access boundary: they may contain participant-visible private data and host responses.

## Errors

`ContractError` identifies invalid contracts; `InputError` identifies bad inputs; `InvariantViolation` reports a declared rule broken by world logic; `RunError` covers execution failures outside agents' actions (events, triggers, physics, and failing model providers); `fg_env.run` raises it for a failed run, with the run in `error.result`. `SnapshotError` covers restoration problems. A rule that fails, or an invariant that breaks, while an agent's action applies does not raise: that action is refused and undone and the run's diagnostics report it. Preserve error details with the source version and a minimal reproduction.

For field-level help, use `fg-env guide <section>`. For full details, see [inspection](reference-inspect.md) and [running](reference-running.md).
