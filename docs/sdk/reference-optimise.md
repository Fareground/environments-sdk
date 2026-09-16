# optimise

## Optimise: the best decision, feasible with confidence and checked on fresh seeds

`fg_env.optimise` searches the inputs you control for the best objective under constraints. Every decision runs on
the same seeds. Each constraint is judged with a stated `confidence` (default 0.9): a decision is **feasible with
confidence** when every constraint's one-sided bound clears it, **infeasible** when one clearly misses, **borderline**
in between. The search asks for more room than the confidence (the cheapest decisions it finds are the ones its seeds
flattered); its best are confirmed on new seeds, with more seeds for a borderline one until it settles, and the choice
is made and reported there; then the choice and the runner-up run on fresh seeds again, so luck is flagged.

```python
r = fg_env.optimise("centre.json",
    decisions={"agents": {"length": 24, "low": 3, "high": 40, "step": 1, "start": manager_plan}},
    objective="minimise staffing_cost",
    constraints=["each sl_by_interval >= 0.8", "sl >= 0.8 in 90% of runs with 95% confidence"],
    runs=30, budget=300, workers=8, inputs={"date": "2026-09-14"})
print(r.summary())        # the decision, its verdict, objective and constraints with 95% intervals, fresh seeds
r.best, r.verdict, r.feasible, r.estimates, r.holdout, r.sensitivity, r.history; print(r.report()); r.to_dict()
```

Decisions (`{input: spec}`): a range `{"low": 0, "high": 3, "step": 0.25}` (int inputs step by 1; no step on a number
input is continuous; bounds default to the input's min/max) · choices `["fifo", "priority"]` (bool and enum inputs
offer theirs) · a vector: a list input `{"length": 24, "low": 3, "high": 40, "step": 1}` or a map input
`{"keys": ["brakes", "wipers"], "low": 0, "high": {"brakes": 3, "wipers": 2}}`, with optional
`"monotone": "decreasing"` or `"sum": 100` / `{"min": 90, "max": 110}` · `"start"`: where a local search begins.

Objective: `"maximise margin"`, `"minimise p90 of cost"` (statistic over runs: mean by default, median, p1…p99; p10 of
a profit is a risk-averse choice), or an expression `"maximise $outputs.revenue - $outputs.holding_cost"` ($outputs,
$metrics, $inputs; over a map or list: `"maximise $min($values($outputs.fill_by_category))"`).

Constraints: `"fill_rate >= 0.95"` (mean over runs) · `"p10 of fill_rate >= 0.9"` · `"sl >= 0.8 in 90% of runs"`
(share of runs) · per key of a list or map output: `"each fill_by_category >= 0.95"`, `"at least 20 of sl_by_interval
>= 0.8"`, `"at most 2 of sl_by_interval < 0.8"` (each key on its own; the rows list every key and the `binding` ones)
· any of them `"… with 95% confidence"`. Confidence needs runs: "in 90% of runs" with 90% confidence needs every one of
about 15 runs to hold, and "in 100% of runs" can never be shown. When nothing is feasible, `best` is the closest
decision and the summary says by how much each constraint misses.

Methods (`method=`, `budget` = most distinct decisions searched, each on `runs` seeds):
| method | for | tradeoff |
|---|---|---|
| `grid` | few stepped decisions | exhaustive; the grid must fit the budget |
| `random`, `lhs` | a first broad look | no refinement; Latin hypercube spreads points evenly |
| `local` | integers and vectors (staffing, reorder points, prices per grade) | coordinate descent with step halving from `start`, then blocks of positions moved together, spikes smoothed and pairs traded; a local optimum |
| `race` | large spaces with a noisy objective | successive halving: few seeds for many decisions, all seeds for the best |
| `nelder_mead`, `cross_entropy` | a few continuous decisions | calibration's searches on a penalised objective; cross-entropy suits rugged ones but needs budget |
| `frontier` | a Pareto frontier in a large space | half the budget sampled, the rest spent on the frontier's neighbours |
`auto` (default): grid when it fits the budget, Nelder–Mead for continuous decisions, else local (frontier for Pareto).

Honesty: `r.holdout` has the fresh-seed objective, constraints and `verdict`, the paired difference to the runner-up
with a 95% interval, `still_wins` and `seed_luck` with its reasons (`holdout_seeds=`, default `runs`). `r.sensitivity`
moves each decision one step down and up from the best. `uncertainty=` (a calibration, points or priors) draws
parameters per run so the decision holds across what is not known.

Pareto: `objective=["maximise margin", "minimise end_inventory_value"]` (two or three; grid, random, lhs or frontier)
returns `r.frontier` — decisions no other beats on every objective — with `r.table()`, each marked whether it stays on
the frontier on fresh seeds.

CLI: `fg-env optimise store.json --decision service_z='{"keys": ["brakes", "wipers"], "low": 0, "high": 3, "step": 0.25}'
--objective "minimise total_cost" --constraint "each fill_by_category >= 0.95" --confidence 0.9 --runs 20 --budget 80
--workers 4 [--json]`.

