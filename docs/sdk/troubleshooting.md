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
| `check` warns a policy rule was refused every time (`policy_rule_never_acted` in `result.diagnostics`) | The refusal it quotes; the rule's `with` builds arguments the action never accepts |
| A choice depends on another argument | The schema lists every candidate; a call with a combination the `where` rules out is refused with the valid choices given the other arguments |
| Output stays constant | Whether any rule changes its dependencies; run behavior checks |
| Simultaneous decisions lose updates | Whether effects overwrite shared state; inspect settlement semantics |
| Everyone sees confidential data | Public announcements, views, record visibility and recorded exposures |
| Summary says `DEGRADED` (`result.degraded` lists why; `result.ok` is false and `fg-env run` exits 3) | The run does not show how the environment plays: `action_always_faulted` (a rule broken for every choice — `check` reports it as an error), `agents_never_acted` (an agent replied only in text, called unknown tools or was always refused), `agents_often_failed` (more than a tenth of a model agent's turns — half of another's — ended with no action after invalid or refused calls, refusals, replies cut off or its model calls used up, or had a model reply refused or cut off), `agents_never_able_to_act` (an agent type was woken but never had an action to take), `turns_forfeited`, `budget_cut` (the run's budget ended it early or idled its agents: its outputs are those of an unfinished game), `host_fallback` (host answers were the contract's fallback stand-ins because no host was bound), `output_failed` (an output raised an error: `result.output_issues` says which and why) |
| Turns forfeited (`turns_forfeited`) | The model provider kept failing after retries; rerun or raise `retries` |
| A stage played its every pass with `until` still false (`stage_until_capped`) | What `until` waits for had not happened when the stage stopped (in every round, or only some): set what it reads in an action or event, allow more `passes`, or set `passes` to the passes it should always play |
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
trace = fg_env.analysis.trace(result)
print(trace.overview())
print(trace.invalid())
result.save("run.jsonl")
```

Diagnostics include a code, path, message and suggested fix. They cover common authoring problems but cannot detect every missing requirement. Treat warnings as items to investigate.

## Replay and privacy

`fg_env.analysis.trace("run.jsonl").replay("inventory.json")` replays recorded calls and reports a divergence. Keep traces within the appropriate access boundary: they may contain participant-visible private data and host responses.

## Errors

`ContractError` identifies invalid contracts; `InputError` identifies bad inputs; `InvariantViolation` reports a declared rule broken by world logic; `RunError` covers execution failures outside agents' actions (events, triggers, physics, and failing model providers); `fg_env.run` raises it for a failed run, with the run in `error.result`. `SnapshotError` covers restoration problems. A rule that fails, or an invariant that breaks, while an agent's action applies does not raise: that action is refused and undone and the run's diagnostics report it. Preserve error details with the source version and a minimal reproduction.

For field-level help, use `fg-env guide <section>`. For full details, see [inspection](reference-inspect.md) and [running](reference-running.md).
