# inspect

## Inspecting a run

```python
result = fg_env.run("game.json", seed=1)     # random agents; {"player": my_agent} for yours
print(result.summary())                       # status, winner, outputs, issues, diagnostics, metrics, end state
result.outputs, result.winner, result.ended_by, result.metrics, result.series["price"]
```
Summaries show numbers to 4 decimals; an output's `"format": "money"` (any template format) shows it that way.
Stored values stay exact. A summary ends with each metric's last values and the state the run left: world props and
the first few entities of each type with every prop (`result.state`), so you can look without adding outputs.

`result.diagnostics` is `[{code, path, message, fix}]`: logic problems the run revealed. It reports:
* a tool offered when none of its choices could succeed;
* sealed choices that overwrite each other's values;
* an agent type that never had an action it could take;
* a coded policy rule whose call was refused every time it was tried (`policy_rule_never_acted`), quoting the refusal,
  and a `repeat` policy's rule that was refused after it had acted (`policy_repeat_refused`);
* agents that never acted, or most of whose turns ended with no action after failed calls (`agents_never_acted`,
  `agents_mostly_failed`), any turns of a model participant (or any participant out of time) that ended so, with
  their rate (`some_turns_failed`), and turns an LLM participant ended out of `max_steps` (`out_of_steps`);
* a stage that can never run, or a measure that reads only what no rule changes;
* host answers that were the contract's fallback stand-ins because no host was bound (`host_fallback`), and a run its
  budget cut short (`budget_cut`) — both degrade the run;
* with model participants, an action that was mostly refused.

`fg-env check` plays up to 12 rounds with random agents and again with each policy, and reports what those plays
reveal: crashes as errors (naming the policy that ran into one), diagnostics (including each policy's always-refused
rules) as warnings. Before a policy rule acts, the later rules whose action is legal are evaluated too, so a broken rule
is reported even when an earlier one always wins. A population that grows fast enough (agents creating agents) to pass
the engine's ceiling of 1,000,000 living entities before the run ends is a warning: a run fails when it reaches it.
`--rounds 30` plays exactly that many for more evidence.

`result.events` is the log in order: `{seq, round, stage, kind, actor, text, data}`. Its kinds are `action`,
`outcome` (a sealed choice's result), `record`, `news`, `refused`, `timeout` and `end`. For example:
`[e.get("text") for e in result.events if e["round"] == 3]` (keys without a value are left out).

What agents saw:
* `env.preview("ann")` shows the next turn exactly as ann will get it.
* A recorded run holds every turn: `result = fg_env.run(c, seed=1, exposures=True)`, then `t = fg_env.analysis.trace(result)`.
* `t.overview()` gives turns, calls, invalid rate and tokens per agent.
* `t.turn("ann", 3)` shows what ann read, the tools she was offered, and every call with its result.
* `t.invalid()` lists refused calls with the correction given; `t.search("bribe")` searches the text.

Reproducing: the same seed and participants give the same run. `result.save("run.jsonl")` and
`fg_env.RunResult.load(path)` keep a run. `t.replay("game.json")` runs the contract again with the recorded calls
and names the first divergence.

CLI: `fg-env run game.json --seed 1 --events --trace run.jsonl`, `fg-env trace run.jsonl turn ann 3`.

