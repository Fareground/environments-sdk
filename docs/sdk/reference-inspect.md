# inspect

## Inspecting a run

```python
import fg_env
fg_env.new("market", "market.json")            # a cookbook recipe to look at; any contract file works
result = fg_env.run("market.json", seed=1)     # random agents; {"baker": my_agent} for yours
print(result.summary())                         # status, winner, outputs, issues, diagnostics, series, end state
result.outputs, result.winner, result.ended_by, result.series["price"]
```
Summaries show numbers to 4 decimals; an output's `"format": "money"` (any template format) shows it that way.
Stored values stay exact. A summary ends with each series output's last values and the state the run left: world props and
the first few entities of each type with every prop (`result.state`), so you can look without adding outputs.

`result.diagnostics` is `[{code, path, message, fix}]`: logic problems the run revealed. It reports:
* a tool offered when none of its choices could succeed;
* sealed choices that overwrite each other's values;
* an agent type that never had an action it could take (`agents_never_able_to_act`, degrading: over two rounds, or a
  whole run in which no agent ever could), and a run in which no agent had a single turn (`nobody_played`,
  degrading);
* a coded policy rule whose call was refused every time it was tried (`policy_rule_never_acted`), quoting the refusal,
  and a `repeat` policy's rule that was refused after it had acted (`policy_repeat_refused`);
* agents all of whose attempts went wrong, or too many of whose turns failed — more than a tenth for a model
  participant, half for others (`agents_never_acted`, `agents_often_failed`, both degrading), any other failed turns of
  a model participant (or any participant out of time), with their rate (`some_turns_failed`), and turns an LLM
  participant ended out of `max_steps` (`out_of_steps`);
* a stage that can never run, or a measure that reads only what no rule changes;
* host answers that were the contract's fallback stand-ins because no host was bound (`host_fallback`), and a run its
  budget cut short (`budget_cut`) — both degrade the run; requests a host gave no usable answer to, also when asked
  again, each refused — a judged text left unscored, a game master's attempt refused (`host_unusable`, degrading when
  an answer was outside the protocol or more than a tenth of the host's requests failed; a few declines are
  `host_sometimes_unusable`);
* with model participants, an action that was mostly refused; an action a model or coded policy chose several times
  and was refused every time (`action_never_succeeded`, degrading: what it does, and any mechanism it feeds, never ran
  — a policy's arguments the tool does not accept count as refused calls; random agents' blind calls do not count).

`fg-env check` plays 12 rounds (fewer when the run is shorter; more to reach the last round an event's `when` names,
`$round == 30` or a market's resolution) with random agents and again with each policy on every agent type, and reports
what those plays reveal: crashes as errors (naming the policy that ran into one), diagnostics (including each policy's
always-refused rules) as warnings. A stage whose `until` random agents never let hold is reported only when no policy
play lets it hold either: random agents cannot be expected to agree or get ready, a panel playing its policies can. Every check plays the same rounds; a time guard stops only a contract too slow to
play, and says so. Before a policy rule acts, the later rules whose action is legal are evaluated too, so a broken rule
is reported even when an earlier one always wins. A population that grows fast enough (agents creating agents) to pass
the engine's ceiling of 1,000,000 living entities before the run ends is a warning: a run fails when it reaches it.
`--rounds 30` plays exactly that many for more evidence.

`result.events` is the log in order: `{seq, round, stage, kind, actor, text, data}`. Its kinds are `action`,
`outcome` (a sealed choice's result), `record`, `news`, `timeout` and `end`. For example:
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

