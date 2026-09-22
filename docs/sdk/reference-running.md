# running

## Running (Python)

```python
import fg_env
env = fg_env.load("shop.json", inputs={"budget": 50}, seed=7, arm=None)
print(env.preview("shopper_1"))                   # brief, update, tools, token estimates
result = env.run({"shopper": "policy:thrifty", "owner": my_agent})
result.outputs, result.metrics, result.series, result.stats, result.events, result.summary()
snap = env.snapshot(); env2 = fg_env.Env.restore("shop.json", snap)   # between rounds; JSON-safe
exp = fg_env.experiment("shop.json", runs=20, arms=["control", "promo"]); print(exp.table())
exp.deltas("control")   # paired promo − control per output: mean, sd, ci95, clear (CI excludes 0)
print(fg_env.report(exp, contract="shop.json", objective="max:profit", require={"fill_rate": ">= 0.95"}))
# plain words for an owner: the choice and its outcome with 80% ranges, drivers, risks, assumptions, fit to data
# (audience="analyst" adds the method; validation=, a run, a sweep or a validation also work; fg-env report file.json)
fg_env.sweep("shop.json", {"price": {"low": 1, "high": 5, "steps": 5}}, runs=10).table()   # also sensitivity, calibrate, backtest
# every check, experiment and analysis reads data files beside the contract file (or data_dir=) and takes hosts=
v = fg_env.validate("shop.json", [{"name": "Q1", "inputs": {"start": "2026-01-05"}, "actuals": {"units_by_sku": {...}}}],
                    runs=20, season=4); print(v.report())   # bias, MAPE/WAPE per key, interval coverage, baselines
cal = fg_env.calibrate("shop.json", cases, {"demand_scale": {"low": 0.5, "high": 2}})   # cases: {name, inputs, targets}
# a rate per case: {"value": 0.03, "count": calls} weighs it by its data; "pool": true matches the cases together
fg_env.validate("shop.json", cases, uncertainty=cal)   # also experiment, sweep, backtest: draw params per run
fg_env.behavior_checks("shop.json")   # constant outputs, inputs that change nothing, actions and stages never used
fg_env.tournament("duel.json", {"greedy": "policy:greedy", "llm": my_agent}, games=20).summary()
# seats rotate and share seeds; Elo with intervals, Glicko-2, Nash average, α-Rank, votes, cost per entrant
d = fg_env.describe("duel.json"); d.markdown, d.metadata   # ODD description; turns, chance, information, players, length

```

A participant is any callable taking a `Wake`:
```python
def my_agent(wake):
    wake.brief, wake.update, wake.tools            # read
    result = wake.call("buy", {"offer": "latte", "qty": 1})   # result.ok, result.text, result.ended
    wake.end()
```
`Wake`: `entity_id name type round stage reason me` (own props), `brief`, `update`, `tools` (each a
`ToolSpec`: name, description, input_schema, kind act|look|end, terminal), `tools_for("anthropic"|"openai")`,
`call(name, args)` → `ToolResult(ok, text, ended, data)` (`data.error` is `invalid` or `rejected`),
`end()`, `done`, `calls_left`, `actions_left`. In a simultaneous stage a choice is tried at submit, so
a choice that could not happen is refused immediately and does not use up the turn.
Async participants: an `async def` (or an object with an async `__call__`, or a function that returns an
awaitable) works everywhere, and a simultaneous stage runs them concurrently with the same deterministic
result. Inside an event loop use `result = await env.arun(participants, ...)`: participants run on that loop,
so clients bound to it work. `wake.time_limit` and `wake.time_left` give the turn's deadline.
`fg_env.load(..., exposures=True)` records what every agent was shown on every wake in `result.exposures`,
`{"texts": {hash: text}, "wakes": [...], "chance": [...]}`: brief, update and view hashes and sizes, news event sequence
numbers, tools offered, every call with its arguments and result, timeouts and undone turns — every text
stored once. `$seen(agent, item)` asks whether an agent was shown an event, a record entry or a view by name;
a contract that uses it records exposures automatically. `experiment`, `tournament`, `evaluate` and `run_jobs`
take `exposures=True` too, every run keeping its own (`--exposures` with `--json`). `result.frames` and
`env.spectate()` give the spectator views (`fg-env run file.json --frames frames.json` saves them).

Traces: a run with `exposures=True` is a trace (`fg-env run file.json --trace run.jsonl`); `result.save("run.json")`
or `.jsonl`, `fg_env.RunResult.load(path)`. `t = fg_env.trace(result_or_file)`: `t.overview()` (per agent: turns,
calls, invalid rate, timeouts, tokens), `t.turn(7)` or `t.turn("ana", 3)` (what the agent read, the tools offered,
every call with its result), `t.timeline("ana")`, `t.search("bribe")` (in what agents read or wrote), `t.invalid()`
(refused calls with the correction given), `t.agent("ana")`; each has `.data` and prints as text
(`fg-env trace run.jsonl turn ana 3`). `t.replay("shop.json")` runs the contract again offline with the recorded
calls (`fg_env.participants.replay(t)`) and host answers, and reports the first divergence — a turn, the brief or
update text, the tools offered, a call result, an event or the ending; `fallback="policy:x"` plays on after it
(`fg-env trace run.jsonl replay shop.json`, exit 1 on a divergence). Each wake's `steps` are what it replays.
Outcomes a `chance=` chooser picked are recorded (`exposures.chance`) and replayed without it (a changed chance
node is a divergence); a forked run replays from the snapshot it continued from (`exposures.start`).
Evaluation: `fg_env.evaluate(suite, focal=my_agent, background="policy:reciprocate", seats="villager",
score="$outputs.cash[$seat]", modes={"resident": 0.75, "visitor": 0.25}, runs=20).summary()` runs every scenario and
mode with `focal` in a seeded draw of the seats and again with `baseline` (default: the background) in the same seats
on the same seed: focal score per focal seat, the baseline's, the paired difference with a 95% interval and cost, per
scenario, mode, tag, held out vs in sample and overall. A suite is a contract, a list of scenarios
`{contract, name, inputs, arm, seats, score, background, baseline, modes, tags, held_out}`, or a
`{"scenarios": [...]}` file (`fg-env evaluate suite.json --focal policy:x --mode visitor=0.25`).
Budgets: `env.run(..., budget={"tokens": 200000, "calls": 500, "host_calls": 50, "seconds": 600,
"on_exhaust": "end"})` caps a run: reported input + output tokens, tool calls, host answers on the tape, wall-clock
seconds. It is checked before every round, stage, pass and turn (a turn in progress finishes): `end` ends the run
(`ended_by: "budget"`), `idle` lets it finish with every agent idle. `result.budget` has the limits, use and the
limit that ran out; snapshots keep it. `experiment` (with `branch_at` the shared rounds count toward each arm),
`tournament`, `evaluate` and `run_jobs` give every run the whole budget, as `--budget tokens=200000` does on
`fg-env run`, `experiment`, `tournament` and `evaluate`. Usage reported after a turn ran out of time still counts.
`env.step(participants)` runs one round; `env.run(participants, rounds=N)` runs N more (an unfinished
run returns provisional outputs). `env.run(..., stop=lambda env: ...)` is checked before every round,
stage, pass and sequential turn; the next `run` continues exactly where it stopped (finishing that
round counts as one of `rounds`). Snapshots are taken between rounds. A participant that raises fails
the run with its entity id; experiments keep such runs as `status="failed"` and carry on. Read state with `env.entity(id)`, `env.entities(type)`, `env.props`,
`env.result()`, `env.finished`. `env.preview(id)` plays the start of the next round on a copy and shows
exactly the turn the agent will get.

Copies, forks, games and gyms:
```python
with wake.clone() as branch:          # inside a turn: a private copy paused right here (fresh luck; same_luck=True)
    branch.call("buy", {"offer": "latte", "qty": 2}); outcome = branch.run("random")   # the real run never changes
twin = env.clone()                    # between rounds or stopped mid-round: continues exactly like env
what_if = env.fork(arm="promo", patch={...}, effects=["$world.tax = 0.2"])   # between rounds; refuses what cannot follow
game = fg_env.game("kuhn_poker.json"); state = game.new_initial_state()    # OpenSpiel-style
state.current_player(), state.legal_actions(), state.chance_outcomes(), state.child(action), state.returns()
state.information_state(seat), state.observation(seat, "struct"), state.apply_actions({0: a, 1: b})
env = fg_env.gym("nim.json", "a", others="random"); obs, info = env.reset(seed=1)
obs, reward, terminated, truncated, info = env.step({"tool": "take", "args": {"count": 2}})
fg_env.conformance("kuhn_poker.json", sims=20).summary()   # legal calls, chance, clone, serialize, returns, replay, resume, leaks
print(fg_env.playthrough("kuhn_poker.json", seed=1))       # every seat's reading at every decision: a golden text to diff
from fg_env.sdk.game.algorithms import CFRSolver, exploitability, minimax, MCTSBot
policy = CFRSolver(game, plus=True).iterate(1000).average_policy(); exploitability(game, policy)
fg_env.run("tic_tac_toe.json", {"x": "mcts:200", "o": "minimax"})   # also "ismcts:200", "cfr:policy.json", "cfr:1000"
aec = fg_env.pettingzoo_aec("kuhn_poker.json", seed=1)     # PettingZoo AEC (reward since last turn); pettingzoo_parallel too
```
Games transform into ordinary contracts: `fg_env.sdk.game.repeated(contract, 10)`, `misere`, `zerosum`;
`game.start_at(steps)` starts part-way. Known-answer games live in `examples/contracts/games`.
CLI: `fg-env conformance file.json --sims 50`, `fg-env playthrough file.json --seed 1 [--check golden.txt]`,
`fg-env bench --game file.json`.
A copy is rebuilt from the run's base and replays what its participants did, so it is exact (state, random
streams, turn numbers, log, recorded host answers) and costs a restore plus the round so far; turn time
limits never run out in a copy. It holds the whole world, hidden state included. Game action ids are fixed
when the game is created (one per combination of listed argument values; free text and lists are
parametric: apply them as `{"tool", "args"}`). `fg_env.load(..., chance=callable)` chooses chance outcomes.

LLM participants: `fg_env.participants.anthropic(anthropic.Anthropic(), "claude-sonnet-5")` or
`fg_env.participants.openai(client, model)`; they cache the brief and loop over tool calls, retry rate
limits, timeouts and server errors (`retries=4`), then fail the run or, with `on_error="end_turn"`,
forfeit the turn. Both take `max_tokens` (openai also `reasoning_effort`); a reply cut off at the limit
counts in `truncated` and, when it called no tool, is asked once for a short tool call (`retry_truncated`).
Every truncated reply wastes its whole output: for frequent decisions use `reasoning_effort="low"` (in a Hold'em
evaluation it cut cost by 38% with no visible loss in play), or keep the default effort with a larger `max_tokens`
(6,000 was cut off 9 times in 96 turns).
Their real token usage is in `result.stats` (`llm_calls`, `input_tokens`, `output_tokens`,
`cache_read_tokens`, `cache_write_tokens`, `llm_retries`, `forfeits`, `truncated`); your own
participants can add theirs with `wake.record_usage(...)`.
Built-ins: `"random"`, `"idle"`, `"policy:<name>"`, and game algorithms `"mcts:N"`, `"ismcts:N"`, `"minimax[:depth]"`, `"cfr:<policy.json|iterations>"`.

`result.events` is the ordered log: `{seq, round, kind, text, actor, to, stage, data}` where kind is
`action` (data: action, params, success), `outcome` (a sealed action's result, to its actor),
`record` (data: record, entry, fields), `news` (event `say`), any `emit` name, or `end` (data: ended_by, winner).
`result.winner` is set by `end` conditions or effects that give `winner`.

CLI: `fg-env check file.json` (static check, then up to 12 rounds with random agents and with each policy; `--rounds 0` for static only),
`fg-env preview file.json agent_id --rounds 5 --agent trader=policy:quote` (see a mid-run turn),
`fg-env bench [files] --rounds 20` (ms per round, rounds per second and time per phase; no files: the
reference agent-based models), `fg-env check|run|preview|experiment|tournament|evaluate|trace|guide|schema` (`fg-env run file.json --seed 1
--input budget=50 --agent shopper=policy:thrifty --json`).

