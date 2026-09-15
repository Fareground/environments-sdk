# Changelog

All notable changes to this project will be documented in this file.

The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.1.0/),
and this project adheres to [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

## [Unreleased]

### Environment SDK (`fg-env`)

#### Composition and agent choices
- Waiting procedure response windows now settle items whose remaining responders have departed.
  Living responders still block resolution; generated/custom windows, snapshots and rollback retain
  their existing semantics. No new authoring setting is required.
- Exact author-only record permissions reject another reader before constructing an expression context,
  reducing event/news observation cost. Matching authors still use the evaluator; additional conditions
  and function-based rules keep normal evaluation. Proven mismatches consume no expression work.
- Author-only record reads use a derived author index for the exact `$viewer.id == $it.author`
  predicate (either operand order). Posting, retention and rollback maintain it; snapshot restore
  and fast world copies rebuild it. Direct record views and `$records` avoid scanning unrelated authors' entries. Other
  visibility rules retain normal evaluation. Work budgets charge the indexed candidates and results,
  so cheaper private reads can fit budgets that previously expired while scanning unrelated records.
- Record permissions that use only reader/entry roots and no function calls avoid constructing unused
  world context. Permissions are still evaluated on every read with the same expression budgets;
  rules with function calls or other roots retain full context.
- Reader-scoped `$events` enforces record visibility and retention as well as notification recipients;
  `$actor` is the reader when no `$viewer` is supplied. Omniscient analysis retains the full event log.
  Truncated news counts exclude records the reader cannot access, preventing private activity from
  appearing in the omitted-item count. Visibility evaluations share the expression work budget.
- `$median(list)` now accepts a numeric list directly, consistent with `$avg` and `$stdev`; nulls are
  skipped and empty/all-null lists return null. Existing projected and filtered entity forms are unchanged.
- Unknown `$mean(...)` expressions suggest `$avg`, the arithmetic mean, rather than the similarly spelled
  `$median`. Static checks and both runtime expression paths share the same suggestion logic; unknown
  functions remain errors and explicitly authored definitions retain their behavior.
- Procedure validation warns at `all_did` transitions shared by multiple procedures: successful actions
  are counted across stages during each phase, so overlapping procedures can share completion evidence.
  The warning explains independent-action and state-based alternatives without changing runtime behavior.
- Unnamed procedure stages now use `<procedure>_<phase>` (with a position suffix for further stages),
  allowing independent workflows to reuse phase names. Explicit stage names are unchanged. Callers
  referring to an automatically generated stage by its previous bare phase name must use the scoped
  name or declare that old stage name explicitly.
- Repeating seasonal profiles select slots using the reduced time before division, preventing exact
  cycle boundaries from slipping into the previous slot; fitting and runtime share the same selection.
  Regenerated the affected contact-centre example fit and saved staffing plan.
- Backtests reject missing, non-finite or incorrectly typed forecast outputs instead of turning them into
  negative events, synthetic categories or silently reduced ensembles; threshold arguments are validated.
- Procedure checks warn when `all_did` is paired with actor-specific action conditions, explaining that
  all declared actor types remain required and suggesting filtered completion for a smaller cohort.
- Added a declarative regional-rollout acceptance composition: nested macros instantiate independent
  approval/delivery/sales workflows sharing funding and stock, with inheritance and checkpoint regressions.
- Money totals use accurate summation across holders, and conservation tolerates floating-point roundoff
  instead of a fraction of the total supply. Payments, minting and burning fail explicitly when a nonzero
  change cannot move a balance in the requested direction at the chosen monetary scale.
- Ledger conservation rejects non-finite aggregate holdings and non-finite or non-numeric supply values,
  preventing floating-point overflow from falsely certifying an accounting balance.
- Static checks identify invalid inventory literals at their authored holder/property/item path, including
  defaults and generated populations. Nested expression strings suggest an expression for the whole map.
- Added a declarative business acceptance composition for overlapping campaign audiences, private customer
  preferences, purchases constrained by budgets/stock, delayed fulfillment, and control-based incrementality.
- Weighted threshold diffusion respects explicit flow direction when reciprocal links have different weights.
- Threshold diffusion accumulates contacts instead of overwriting prior outreach exposures. Each step counts
  active informing neighbours until adoption/rejection; unique reach and the adoption threshold remain separate.
- Diffusion publishes each adoption batch before its hooks. Hooks see current adoption state, nested diffusion
  changes survive, and later steps use those changes. Refused hooks still roll back the whole action.
- Inspection listings with fixed visibility can be reused across ordinary viewers until journaled state changes.
  Dynamic permissions and private self-views keep individual evaluation; rollback and run copies invalidate reuse,
  and returned schemas are isolated from each other.
- Inspect choices reuse inherited type metadata within each listing and short-circuit visible-value checks,
  reducing actor-turn overhead while retaining current permissions, private-field rules and entity order.
- Guest exit abandons pending bookings without counting service or leaving permanent queue entries. Freed
  reservations promote eligible waiters after expired waits are cleared. Departure retains payments; explicit
  cancellation still applies the configured refund once.
- Installments with inactive parties follow the configured breach/termination policy without stopping unrelated
  deals. Breach hooks still run for retained inactive participants; automatic penalties require active parties.
- Removing a subscription provider ends due agreements as withdrawn instead of failing the run. Unavailable
  plans are excluded from generated choices and views; custom subscription attempts abort without side effects.
- Subscription choices and plan views account for each actor’s prior trials. Rejoining requires an affordable
  payment after a used trial, including when other agreements draw on the same budget.

#### Numerical fidelity and timing
- Demand-count sampling and stockout-conditioned fitting recover from probability underflow. Large Poisson and
  negative-binomial counts invert their distributions instead of using a clipped normal approximation, preserving
  skew and same-seed ordering across volume changes. Numerical searches have explicit convergence limits.
- Expression-driven effect loops correctly shadow enclosing item types during checking.
- Three business acceptance contracts cover perishable retail, refurbished-product returns/substitution, and
  component-constrained bundles, with demand censoring, price responses, forecasting and accounting checks.
- Recommendations exclude options with failed, unfinished or budget-exhausted runs, or missing decision measurements,
  and explain the exclusion beside the decision. Explicit experiment/sweep windows remain eligible once reached;
  successful-only results remain available as descriptive evidence.
- Mapped sums and averages use the existing compiled expression loops, preserving filtering order, indices,
  errors, work budgets and random draws while avoiding repeated per-item scope construction.
- Coupled world and entity dynamics share integration states, removing declaration-order dependence.
- Drift uses bounded adaptive refinement; general stochastic dynamics reuse Brownian paths during refinement,
  with exact transitions for supported independent affine processes. Accuracy/work-limit failures stop the run.
- Noise streams are stable by entity and variable identity. Failed intervals roll back state and writebacks.
- Continuous clocks integrate elapsed state before boundary interventions, stop at intervening scheduled events,
  and include the final fractional interval. Decimal ticks and wake delays no longer accumulate an extra turn.
- `physics.rtol`, `atol` and `noise_rtol` expose accuracy targets without adding a solver-selection API.

#### Added
- **Optimisation you can act on** (`fg_env.optimise`): every constraint is judged with a stated confidence
  (`confidence=0.9`, or `"sl >= 0.8 in 90% of runs with 95% confidence"`) by one-sided bounds, so a recommendation is
  *feasible with confidence*, *borderline* or *infeasible* (`result.verdict`, also on the held-out seeds). The search
  asks each constraint to clear its confidence bound by (1 + √2) times that distance, so fresh seeds agree; a borderline
  finalist gets more confirmation seeds (doubling, up to four times `runs`) until it settles. Per-key constraints over
  list and map outputs — `"each fill_by_category >= 0.95"`, `"at least 20 of …"`, `"at most 2 of sl_by_interval < 0.8"`
  — report every key with its slack and the binding keys. Local search adds block moves (a run of a vector's positions
  moved together) and smoothing moves before pair moves, and `method="frontier"` refines a Pareto frontier from the
  neighbours of its undominated decisions. `fg-env optimise --confidence`.
- **Calibrating rates recorded per case** (`fg_env.calibrate`): a number target may give `"count"` (the trials behind
  a rate: its error counts in that rate's standard errors, so a quiet day's noisy rate weighs as little as its data) or
  `"pool": true` (matched over the cases together); `result.pooled` compares every per-case target with its pooled level
  and notes when a per-case fit drifts from it. Cases now draw their own seeds (derived from each case's name; every
  candidate still meets the same seeds within a case): with shared seeds, 40 replayed days on 4 seeds carried the noise
  of 4 runs, and the contact centre's patience fit moved between 166 s and 240 s with the seeds alone.
- **Owner reports** (`fg_env.report(result, audience="owner")`, `fg-env report`): a run, an experiment, a sweep or a
  validation told in short sentences and a few tables, in the clock's own terms — the recommended option (by
  `objective="min:cost"` and `require={"service_level": ">= 0.8"}`, or a service queue's own target: the cheapest
  staffing that meets it) with its expected outcome and 80% ranges, the staffing plan by time of day, what drives it
  (the pattern factors behind the busiest time, clear paired differences between options, sweep effects, a typical
  run's notable moments), risks (options and runs that miss the requirement, intervals below target, the data check's
  warnings), what the model assumes (its queue, inputs marked assumed, fitted parameters) and how well it matched
  the data (error, bias and interval coverage in plain words). `audience="analyst"` adds the method, every output of
  every option and the full validation and sweep reports; `Report.markdown`, `to_dict()` and `save("r.md" | "r.json")`
  export it. An optimisation (`fg_env.optimise`), as the source or as `optimisation=` next to the experiment that plays
  its decision, is told as the plan with its expected objective, how often each constraint held (with intervals), the
  fresh-seed check, a seed-luck warning and what one step either way breaks.
- **Service queues** (`"kind": "operations", "mode": "queue"`): a contact centre, clinic, counter or repair crew
  played natively, interval by interval — arrivals per channel from any expression (patterns, data, an outage),
  service and patience distributions, server pools with skills, shifts as staff per interval
  (`"$inputs.staffing[$interval]"`, a vector an optimiser can search), priorities, callbacks served when nobody waits,
  and retries. Queue operations are heaps (O(log n)); arrivals and each customer's durations come from streams of their
  own, so arms with different staffing see the same customers. Per-interval records and totals give service level at
  each channel's threshold, speed of answer, abandonment, utilisation, queue length, paid hours with shrinkage and
  cost, on round and continuous clocks. A stationary M/M/c case matches Erlang C and one with patience Erlang A.
  Callbacks can keep servers free for live customers (`reserve`); per-interval guarantees can be constrained through
  `<name>_intervals_below_target` and `<name>_worst_interval_service_level`, and handle times are measured
  (`<name>_aht`, and per interval in the records).
- **Example `contact_centre`**: a day of calls at a broadband provider's contact centre on the queue mode, with a
  bundled eight-week half-hourly history its `truth` arm generates (`examples/contact_centre_history.py`). Arrivals
  (day-of-week and time-of-day indexes, base volume) are fitted with `fit_patterns` on six weeks; handle time, outage
  uplift and patience (by the simulated method of moments) are estimated from the same weeks; the last two weeks
  validate it (daily calls within 2%, service level within 2%). `examples/contact_centre_plan.py` searches a staffing
  vector with `fg_env.optimise` under a per-half-hour constraint, keeps the result in `contact_centre/plan.json`, and
  prints the owner report with the outage and callback arms.
- **World patterns** (`patterns`): the world's own regularities declared once, like its physics, and read anywhere as
  values — `$pattern.winter`, `$pattern.price_effect($it.price)`, `$pattern.season($it.category)`. Kinds for time
  (trend, seasonal, calendar, cycle, lifecycle, step, series), random paths from each pattern's own seeded stream
  (random walk, mean reversion, autoregression, volatility clustering, regimes, shocks, noise, weather), responses
  (elasticity, cross-price substitution, promotions with a dip after, saturation, thresholds, reference prices,
  learning curves, network effects, hazards), population (draws, segments, Bass diffusion, habit and fatigue),
  observation (negative-binomial counts, measurement error, censoring, missing values), memory (carry-over and lags)
  and composition (product, sum). Parameters are fixed for a run and may read `$inputs`, so sweeps, arms,
  sensitivity, calibration and forks apply to them; keys come from an entity type, a list or a table of per-key
  parameters; `record` makes a pattern a metric; `uncertainty` draws parameters around their estimates each run.
  `guide("patterns")` teaches them; the former `patterns` part (recipes) is now `guide("recipes")`.
- **Fitting patterns from data** (`fg_env.fit_patterns(contract, data_dir=...)`): every pattern with a `fit` block is
  estimated from its rows — least squares, harmonic and log-log regression, the method of moments, grid search and
  maximum likelihood, one documented estimator per kind — and the estimates are written back as inputs with their
  standard errors, which become the pattern's `uncertainty` (scaled by the input `parameter_uncertainty`). A `product`
  fits its base per key, profiles, trend, price elasticity and promotion lift together in one count regression, so a
  promotion is not mistaken for price response; stockout rows marked `censored` are fitted as lower bounds, and a
  singular design names the factors the data cannot tell apart. `result.report()` gives each fit's method, error and
  what was assumed; `result.priors` gives the number estimates as normal priors for `uncertainty=`. The checker warns
  when a fitted input is also tuned by the load-time `calibration` section. `fg_env.decompose(contract, "demand",
  key=...)` shows what each factor of a product adds, and `fg_env.describe` lists every pattern in plain words.
- **Demand** (`economy.demand`): customers' demand for stocked items drawn from patterns — `rate` × `factors` (a
  season, trend, price elasticity with its driver, promotion, cross-price substitution, any expression) with count
  noise — and served from stock, keeping true demand, lost sales, substitution to other items, backorders, segments
  (bulk buyers, channels) with their own prices, items and returns, revenue into a ledger account, totals by item,
  group and segment (`$demand_totals`), and a history record in the columns `fit_patterns` reads. Stock changes only
  through sales, returns and the `receive`/`remove` actions (`$stock_conserved`).
- **Replenishment** (`economy.replenishment`): inventory policies for a demand's items — (s, S), (s, Q), order-up-to
  (base stock or periodic review), a service-level target whose safety stock comes from the forecast's error and the
  lead time's spread, a custom expression, or agents' own orders — with lead times drawn per order from a noise
  pattern (and recorded, to fit that pattern from purchase orders), case packs, minimum and maximum orders, capacity,
  a budget spent most-urgent first, payment from a ledger account, and holding, ordering, stockout and backorder
  costs (`$replenishment_totals`). Every policy parameter is an expression, so a per-category map input makes it a
  decision `fg_env.optimise` can search.
- **Example `auto_parts_store`**: twelve SKUs on the demand and replenishment modes — per-category seasons, growth,
  price elasticity, promotions with a dip after them, substitution between tiers, stockouts with lost and spilled
  demand, lead times drawn per order and negative-binomial sales — fitted from bundled three-year sales and
  purchase-order histories that its `truth` arm generates (`examples/auto_parts_history.py`). Arms compare the store's
  lean reorder rule with a service-level policy; `examples/auto_parts_policies.py` compares service levels, optimises
  one per category under a fill-rate constraint and validates the forecast on held-out quarters.
- **Example `phone_reseller`**: a bulk reseller of used iPhones (four models × four grades) on the same modes — value
  from price new × resale share × grade, decaying with age and stepping down at every later launch (a lifecycle
  pattern); retail and wholesale channels as segments with their own demand, prices and return rates; weekly lot
  buying within a lot minimum, case packs and a budget, with delivery and grading times drawn per lot. Demand, the
  markup elasticity and lead times are fitted from sales and lot histories its `truth` arm records;
  `examples/phone_reseller_study.py` compares channels, forks a run into a new-model launch and validates on
  held-out weeks.

#### Changed
- **Fitting is faster**: a product's joint count regression solves by Cholesky from a design built once, computes
  standard errors only for the final fit and restarts dispersion refits from the previous fit — 35,568 SKU-weeks of
  business history fit in 11 s instead of 92 s with every estimate the same to about 1e-6. A negative-binomial step
  is now judged by its own deviance.

#### Fixed
- **Fitted uncertainty you can trust** (`fg_env.fit_patterns`, joint product fits): estimates were already unbiased
  over 400 simulated auto-parts histories and synthetic designs with promotions tied to the trend, the season or price
  cuts, but their errors were not. A demand scale's error was its first seasonal slot's (1.7× too wide) and a profile's
  first slot had none; both now come from the profile rescaled to average 1, and cover the truth 94–95% of the time at
  95%. Censored fits read each row's observed information less what the stockout leaves unknown (lift and elasticity
  errors were 10–17% too narrow), and dispersion is estimated with the fitted parameters' degrees of freedom (it ran
  4–9% high). The report names parameters the history can hardly tell apart — a promotion that is always a price cut
  — with how much the overlap widens their errors and their 95% ranges.
- **Reading never costs the action**: `look` and `inspect` have their own free allowance (`max_calls` per turn, said in
  the update and both tool descriptions); a read past it is refused without spending a call ("You have used your 4
  free reads this turn; nothing was read. Act or end your turn."), and only a participant that keeps reading after
  more refusals than the turn has calls has its turn ended. In the second evaluation reads past the allowance used up
  calls: the werewolf seer read eight times and never voted, and 14 social-network turns ended with no action. The
  same read twice in a turn answers "Unchanged since you read it earlier this turn."; `inspect` offers only entities
  with something to show (the fact desk and the town-hall chair showed a name and nothing else) and leaves out
  properties without a value. A turn that ends with its calls used up while an action was still open is reported as
  `Ben did not act.` in any stage, not only a `must_act` one.
- **Text limits models keep to**: a limit's word hint is characters ÷ 8 ("Up to 400 characters (about 50 words).")
  — at ÷ 6.5, 44% of werewolf speeches were over on the first try. A text parameter may set `"overflow": "truncate"`:
  longer text is cut after the last full sentence that fits and the result says so ("(Your text was cut to 380 of 450
  characters; the rest was not said.)"). Werewolf `say` uses it: 11 of its 31 refused speeches were never said,
  because the speaker gave up and ended its turn.
- **Refusals in the form the tools take**: with `tools: one`, an unknown tool name points to the shared tool ("Use
  hall with action: speak, yield or amend.") instead of action names that are not tools there; with nothing open,
  "No actions are available now — call end_turn." An entity parameter with too many choices for an enum lists their
  ids compactly ("One of: u1–u150."); the social network's brief says the fact desk is not an account and cannot be
  followed (16 refused follows). The town hall's `raise_hand` says to end the turn and wait to be woken with the floor
  (17 same-reply speeches were refused). The participants guide recommends `reasoning_effort="low"` for frequent
  decisions, or a larger `max_tokens` at the default effort.
- **Discarded game states and copies do no work during garbage collection**: collecting a stepped state closed its
  waiting turn there — evaluating legality rules on whatever thread collected, inside whatever it was evaluating, and
  charging that evaluation's budget — and collecting a `Branch` joined its copy's thread in the middle of the
  collection. A discarded round now closes nothing, and a collected copy only asks its thread to stop (it unwinds on its
  own thread); `Branch.close()` and `GameState.close()` still wait for the thread. Running Python code inside a
  collection is the trigger for a CPython 3.11.4 use-after-free (gh-106092), the likely cause of rare segfaults seen
  in piloted game tests and while copying runs.
- **Narratives a reader can follow**: rounds are named in the clock's terms everywhere — `half-hour` for a clock of 30
  minutes, `09:30–10:00` (with the weekday and date when a run spans days) on a dated sub-day clock, `Week 7
  (2026-10-12)` on a weekly one — through `fg_env.sdk.clock_words`, which `RunResult.unit`, `period` and `summary`
  now use ("completed after 24 half-hours"). A narrative tells one measure's moment once (a peak is not repeated next
  to the reversal it belongs to), leaves out moments no more unusual than the run's usual ups and downs (a short run
  still tells its largest changes, and says why), names what happened with a move — other measures that moved in the
  same round and the world's news in that round or the one before — and shows results in their declared formats.
- **LLM turns that ran out of words**: a reply cut off at the output limit counts in `stats["truncated"]` (per
  wake in exposures and `agent_stats`) and, with no tool call, is asked once for a short tool call
  (`retry_truncated=False` ends the turn). The nudge names the tools offered and never asks for an `end_turn` that
  is not offered; in a must-act stage the participant no longer calls a refused `end_turn` (no fake invalid call) and
  the engine logs `Ben did not act.` (the pot says what the timeout did: `Ben folds.`). `openai(...)` takes
  `max_tokens` and `reasoning_effort`.
- **Calls after the turn ended**: LLM participants stop running a reply's tool calls once one ends the turn; the
  leftovers are answered without reaching the engine. `look` and `inspect` are free reads (up to `max_calls` per
  turn), so reading never spends the calls needed to act; a stage allowing fewer calls than the default states the
  budget in the update, and tool results count down the calls once they barely cover the actions left
  (`(Calls left: 2.)`).
- **Inspect ids**: `inspect.id` offers the ids it accepts (an enum, or a compact listing like `u1–u150`), finds a
  unique name, and a refusal suggests the closest id. Entities an agent may inspect read `Moderator [chair]` in its
  update and views; deliberation motions are numbered (`motion 1`), not bracketed.
- **Limits stated up front**: text parameters say `Up to 400 characters (about 60 words).`; `per_turn` / `per_round`
  caps are in the tool description (`Once per turn.`). `tools: one` keeps each action's constraints (one merged
  schema of the common type, enums joined, per-action ranges and choices in the description), lists every action on
  its own line with its arguments, and says only those actions are available now.
- **Example and mechanism wording**: `$poker_hand(hole, board)` says whether a hand uses the hole cards or is on
  the board (Hold'em's view uses it); the town hall floor refusals say what to do (and that a raised hand waits);
  the ballot count shows only while the vote is open; social replies, reposts and reactions accept trending posts;
  posted-market sellers see their cash and can sponsor only the rounds they can pay for; Kuhn poker spells out the
  betting (`pass, bet`) and tic-tac-toe shows a 3-row board with cell numbers; werewolf chat is quoted once, and
  `check` warns when a template wraps a placeholder in «» itself.
- **Data files everywhere**: a parsed contract remembers its data folder (the contract file's folder, or `data_dir=`),
  so `check`, `experiment`, `run_jobs` workers, `calibrate`, `backtest`, `precision`, `sweep`, `sensitivity`,
  `behavior_checks` and `chain` read `source` inputs instead of failing. Each takes `data_dir=` and `hosts=` (runs
  answered by hosts stay in this process); the CLI's `check` and analysis commands take `--data-dir`.
- **A property named `make` is data**: an object is a macro only when it has `for` and `make` (or only macro fields);
  a macro missing `for`, or data with both fields, says to rename the field.
- **World defaults read other world properties** (`"plan": "$map($world.rates, $it * 2)"`), evaluated in dependency
  order (after the entities when a property they read needs them); defaults reading each other in a circle are an
  error naming the circle. A local named like a reserved root (`$row = …`) says to rename it.

#### Changed
- **Batches pay less for worker processes.** `experiment`, the analyses, `optimise`, `tournament` and `evaluate` keep
  one pool of worker processes for the life of the process (started on first use, closed at exit or by
  `fg_env.sdk.workers.shutdown_workers()`; `FG_ENV_KEEP_WORKERS=0` gives every batch its own pool as before). A batch
  whose runs are measured to finish before workers could start stays in this process until the time spent so would
  have paid for starting them, many short runs travel to a worker per round trip, and each worker parses a contract
  once. Results are unchanged whichever way a batch runs.
- **The `dynamics` mechanism family is gone: the world's own changes are `patterns`.** A drift rule becomes a trend,
  seasonal, random-walk or mean-reversion pattern (applied to state by an event, or `physics`, where agents change
  the same property); a shock becomes a `shocks` pattern an event reads; a prior becomes a `draw` pattern. Declaring a
  `dynamics` mechanism (or `drift`, `shocks`, `priors`) says which pattern replaces it. `epidemic_shocks` and the
  flagship exchange's calibration are migrated; their golden runs changed because the draws now come from pattern
  streams, while their behaviour did not (200 seeds of `epidemic_shocks`: no output mean differs by more than 1.3
  standard errors; 40 seeds of the flagship before and after: no stylized fact's distribution differs, Mann-Whitney
  p 0.2–0.9, and one session meets every bound in 24 of 40 against 22 of 40).
- **The flagship's stylized-facts test is statistical, not a pinned seed.** Its tape is bimodal across seeds —
  sessions where stop-loss cascades start are volatile and fat-tailed, the rest calm — so one session proves
  nothing. The default test holds each fact's median over 6 sessions inside its bound and needs one session meeting
  every bound; `FG_ENV_SLOW=1` adds 24 sessions with strict medians and at least 8 meeting every bound. Thresholds
  come from the measured distribution (a healthy set fails 2.6% and 0.3% of the time).
- **A short core guide**: `fg_env.guide()` / `fg-env guide` is a ~2.8K-token core — the model, a quickstart that
  runs with defaults, the essential sections and fields, expression and effect basics, the mechanism families and a
  map of every other part. Each contract section (`guide("actions")`, with the `$` roots available there), function
  group (`guide("functions.stats")`), topic, family and mode is its own small part; `guide("all")` is everything; an
  unknown part suggests the closest. The `reference` part is replaced by the section parts; a family's page lists its
  modes and functions, and each mode is read on its own.
- **The run's winner**: outputs and `game.returns` read `$result.winner` (the winner an `end` gave, as an entity) and
  `$result.ended_by`. The ranking helper `$winner(items, by, ties)` is renamed `$best`. `result` is a reserved root.
- **Actions**: a `when` requirement may read `$params`; it refuses a call that breaks it, with its `why`. A tool whose
  required number has no valid value right now (min above max) or whose enum has no values is not offered.
- **Shorter contracts**: `do`, `otherwise`, `then`, `else`, stage hooks and `when` accept one item where a list of one
  is meant; the types `integer`, `string`, `boolean` and `float` read as `int`, `text`, `bool` and `number`.
- **Errors**: structural errors say what a field must be, what it got and how to write it (no library wording);
  syntax errors hint `==` for `=`; a call or root written without `$` says what to write; parameter bounds that are
  not numbers, literal bounds no value meets and plain entity ids that cannot exist are static errors; a failed name
  lookup suggests the closest name or lists the choices; a missing mechanism field says what it is for.
- **`fg_env.check` plays one smoke round by default** (`rounds=0` for static checks only); `fg-env check` says what
  it checked, when `clock.rounds` was left at its default, and what each mechanism generated.
- **Examples**: one per mechanic. The mechanism versions of the duplicate pairs keep the plain names (`beer_game`,
  `checkers`, `connect_four`, `civil_trial`, `werewolf`); redundant `terminal: true` flags are gone (golden runs
  unchanged).

- **Mechanism families**: mechanisms are declared as `"kind": <family>, "mode": <variant>` (`market`,
  `economy`, `agreements`, `decision`, `game`, `flow`, `groups`, `social`, `mind`, `conditions`,
  `host`). Each mode keeps its own strict config: a field that does not belong to it is an
  error naming the mode, listing its fields and suggesting the closest one. An old kind name is refused
  with the new `kind` and `mode`. A family has one effect op, `{"<family>": "<mechanism>", "action": ...}`,
  checked per action. The guide lists the families first; `guide("market")` and `guide("market.auction")`
  read one family or mode.

#### Added
- **An optimiser** (`fg_env.optimise(contract, decisions, objective, constraints, runs=, budget=, method=, workers=,
  holdout_seeds=, uncertainty=)`, `fg-env optimise`): searches decisions — ranges (stepped or continuous), choices and
  vectors (a list or map input per half hour or category, with per-position bounds, a monotone order or a sum) — for
  `"maximise margin"` / `"minimise p90 of cost"` (or an expression over `$outputs`) under constraints such as
  `"fill_rate >= 0.95"` or `"sl >= 0.8 in 90% of runs"`. Methods: grid, random, Latin hypercube, local (coordinate
  descent with step halving, then paired moves along constraints), race (successive halving) and calibration's
  Nelder–Mead and cross-entropy; `auto` picks one. Every decision runs on common seeds; the search's best are
  confirmed on new seeds, where the choice is made and reported; the choice and the runner-up then run on fresh seeds
  with a paired difference and a plain seed-luck flag. When nothing meets the constraints, the closest decision and
  each shortfall; sensitivity one step around the best; two or three objectives trace a Pareto frontier checked on
  fresh seeds. `guide("optimise")`. On a copy of the business study's contact centre ("service level 80% in 90% of
  runs"), the cheapest plan that passed on four seeds met it in 55% of 60 fresh runs; the optimiser's plan, judged on
  30 seeds, in 87% (within noise of 90%), for 4% less than the manager's rule.
- **Validation that tells the truth** (`fg_env.validate(contract, cases, runs=, levels=(0.8, 0.95), season=, test=)`):
  each case's `actuals` — a number, a map per key (product, category) or a list — checked against the run ensembles:
  bias, MAPE, WAPE, RMSE and CRPS overall, per key and on held-out cases; interval coverage at each nominal level with
  a loud warning when intervals clearly hold fewer actual values than they claim; and a comparison with the last
  value, the earlier mean and the value one season back, with a warning when a baseline wins. Plain `report()` and
  structured `to_dict()`. `backtest` notes the same coverage warning.
- **Parameter uncertainty in runs**: `uncertainty=` on `experiment`, `sweep`, `backtest` and `validate` draws
  parameters per run from a calibration's plausible points (`CalibrationResult.plausible`), a list of points or
  priors (`normal`, `lognormal`, `uniform`, `triangular`, `values`, clipped by `min`/`max`); run *i* draws the same in
  every arm, cell and case. Forecasting 10 quarters with p treated as known, 80% intervals held 1 of 10 actual values;
  drawing p held at least 8. A contract's load-time `calibration` report records its plausible points too, and
  `uncertainty=` takes that report or the loaded session (`env`) itself.
- **Scan lint**: `check` warns, with a fix, when work that repeats per entity or row re-reads a whole input table
  (events and `each` over a type, type hooks, population rows: use `$lookup`) or when a block that schedules itself
  again through `after` reads every entity of a type (keep a world list such as a queue of ids), defs followed. Once-per-
  round scans and per-entity pairing are not reported; the shipped examples raise none.
- **Negotiation settlement**: the `agreements` `negotiation` mode takes `transfers` (unique entities a signed deal
  hands over, `{"items": "$filter(phone, $it.owner == $proposer.id)", "count": "$terms.units", "to": "$acceptor"}`,
  listed in `<name>_deal.items`) and `on_sign` effects (`$deal`, `$proposer`, `$acceptor`, `$parties`, `$terms`). A
  settlement that cannot happen (too few items, a `fail`) refuses the acceptance, so one lot is never sold twice.
- **Keyed table lookups**: `$lookup(table, field, key)` gives the rows whose field equals the key and
  `$lookup_one(table, field, key, default?)` the first; an input table is indexed once per run (other lists per call),
  fields and keys may be lists. Deriving each of 228 SKUs' base demand from a 35,568-row sales history at load went
  from 26.9 s (`$filter`) to 0.3 s.
- **Calendar functions**: `$date_add(date, n, unit?)` (day, week, month, quarter, year, hour, minute; months keep the
  day or take the month's last), `$days_between(a, b)`, `$date_part(date, part)` (year, quarter, month, day, weekday,
  ISO week, day of year, hour, minute, weekday and month names) and `$is_holiday(date, dates)`. `clock.start` may
  read an input (`"$inputs.start"`), readable as `$clock.start`; `RunResult.clock` records the clock, and summaries,
  highlights and narratives name rounds in its unit with their date (`Week 3 (2026-09-14): …`).
- **Order-book venue rules as expressions** (`guide('market.order_book')`): `tick_size`, `lot_size`, maker/taker fees,
  `collar_pct`, `price_band_pct`, `halt_pct`, `halt_rounds`, `halt_window`, `short_limit`, `max_short_leverage`,
  `order_ttl`, `max_orders` and `bar_rounds` accept expressions over `$inputs`, resolved once when the world is built
  into `$world.<name>_rules` (each checked against its limits, naming the field), so a tick can follow the price level
  and fees can be swept or calibrated. Snapshots, clones and forks carry the resolved values.
- **Bars of several rounds and a bar-aware circuit breaker**: `bar_rounds` makes `<name>_bars` record one OHLCV bar
  (with vwap, trades, whether a halt tripped and aggressive flow by trader kind) every N rounds, and
  `$book(name).bar` is the bar in progress. The breaker measures from `halt_reference` (`round_open`, `bar_open` or
  `rolling` with `halt_window`), looks on every trade or at `round_end` (the mid), and halts for `halt_rounds` or to
  the `bar_end`. The book's `open`/`close` actions run once per round, so an author end event can close the round
  first and read the bar just recorded.
- **Stage `passes` and event `every` as expressions** over `$inputs`, checked statically and resolved at load.
- **Calibration at load** (`guide('calibration')`): a contract's `calibration` section fits inputs with short pilot
  sessions whenever a session loads (targets may be expressions read from the built world); the session runs with
  the fitted values, reproducible from its seed, and `env.calibration` reports the fit and its cost. A load that sets
  a fitted input (the pilot sessions themselves, a sweep, `fg_env.check`'s smoke round via `calibrate=False`)
  skips it.
- **Files and media** (`guide('assets')`): a contract's `assets` section declares files and folders beside it —
  images (png, jpg, webp, gif), PDFs, text and markdown, audio (wav, mp3) and other files — read once at load,
  confined to the contract's folder (no `..`, absolute paths, hidden files or links out), checked against their
  extension and size limits, and hashed. Properties and record fields of type `asset`, table input columns of type
  `asset` (paths), and `$asset(ref)` (`name type media_type size hash caption alt tags text`) reference them.
  Agents receive files only through what they may see — a view's `attach`, a record entry's asset fields, an
  event addressed to them, `brief.attach`, their own action's `attach` and `inspect` (never another entity's
  private asset) — as a compact reference in the text and as `wake.attachments` / `ToolResult.attachments`
  (`read()`, `text()`). `participants.anthropic` and `participants.openai` send real image, document, file and
  audio parts (`media=` chooses the types; `media=()` for text-only models). A `file` parameter (`kinds`,
  `max_bytes`) takes a file from the agent (base64 `data`, `text`, or a submitted `asset` id; `wake.upload` for
  coded participants), stored as an untrusted asset recognised from its bytes. The judge and game master take
  `attach` (a judged entry brings its files), an asset's `describe` host writes a caption and text at build
  (`host.Describer`, `host.stubs.StubDescriber`), and the reference host adapters send attachments as multimodal
  content. Snapshots, clones, forks, the tape and the exposure log (`assets: [{id, hash, in}]`) hold ids and
  hashes, never bytes; a replay checks the files each wake received; `result.save` writes the run's files to
  `<name>.assets/` and `RunResult.load` provides them again (`fg_env.sdk.assets.provide(folder)` elsewhere).
  Examples: `court_exhibits.json` (sealed then revealed exhibits, a judge host receiving them) and
  `product_listing.json` (a CSV catalogue with photos, sellers relisting with a submitted photo).
- **Flagship exchange** (`examples/contracts/exchange_flagship.json`): the platform Exchange rebuilt as a contract — a
  calibrated crowd of market makers, momentum, mean-reversion, fundamental, noise and passive traders on one order book,
  one round per pass through a bar, bars, a circuit breaker on the bar's open, scheduled news, sentiment, volume and
  volatility controllers, stop-loss liquidations, model-trader seats and a realism score against a seed history.
- **Order book** (`market.order_book`): `passive` coded strategy; a `stop_loss` strategy parameter that liquidates at
  market; `$book(name).flow` (last round's aggressive quantity by trader kind) and `.liquidations`; `max_short_leverage`;
  `flow_scale` and `sentiment` expressions; `base_qty` and `volatility` may be expressions and `measure_volatility:
  false` makes strategies assume a calibrated volatility; crowd `params` may be expressions; `value_rounds` and
  `side_rounds` hold a fundamentalist's value estimate or a passive side for several rounds; `conserve` takes `action`,
  `round` or `end`. Books write only the sides an order changed, without copying resting orders.
- **`fg-env new <template> [file]` / `fg_env.new(template, path)`**: a ready-to-run contract from `blank`, `game`,
  `market`, `simulation` or `social`, each checking without errors and running with random agents.
- **What mechanisms generate**: `fg-env check` and `fg-env preview` list, per mechanism, the actions, stages, views,
  outputs and other parts it added (`fg-env expand --mechanisms` shows them in full).
- **Shared tools** (`actions.<name>.tool`): actions naming the same tool are offered as one flat tool whose
  required `action` argument lists the actions legal now; each call is routed to its action, whose own
  arguments, conditions and limits apply, and the log keeps the action's own name. Mechanisms that generate
  several tools take `tools: each | one | auto` (`each` by default; `auto` shares one tool only when every
  action takes the same arguments).
- **Spaces for agent-based models**: grid `neighborhood` (`von_neumann`, `moore`, `hex` in axial
  coordinates) and `torus`; plane `torus`; sizes and graph nodes/edges may be expressions over `$inputs`;
  `space.capacity` (per cell, or per type) refuses a `move` or `create` into a full cell. Positions are
  indexed (kept current under rollback and restore): `$at`, `$near`, `$nearest`, `$cells`, `$empty`,
  `$random_empty`. `diagonal` is replaced by `neighborhood: moore`.
- **Layers** (`space.layers`): values on every cell or place, read with `$layer(name, position)` and changed
  by the `layer` effect — one cell (`at`), every cell reading the old values (`set` with `$cell`/`$value`),
  `diffuse` and `decay`; kept in snapshots.
- **Synchronous and ordered events**: `events[].sync` (every item reads the state before the event and all
  property and layer-cell writes land together; conflicting writes are an error) and `events[].order`
  (`random` or an expression).
- **`fg-env bench`** (`fg_env.sdk.bench.bench`): build time, ms per round, rounds per second and exclusive
  time per phase, for given contracts or the reference models; new examples `schelling`,
  `boltzmann_wealth`, `game_of_life`, `forest_fire`, `wolf_sheep`, `sugarscape_lite` with checks of their
  known results.
- **Crowd scale**: `invariants[].check` (`action` default, `round`, `end`); an invariant already found to
  hold in the same state is not evaluated again. Type members are indexed (no world scan per
  `$choice(type)`/`$count(type)`/`each`), a condition opening with `$it.field == value` skips entities whose
  field differs without evaluating them, and a coded policy's entity argument is validated without listing
  every candidate. Boltzmann 20 000 agents: 11.8 s → 0.48 s per round.
- **Traces** (`fg_env.trace(result_or_file)`, `fg-env run --trace run.jsonl`, `fg-env trace FILE ...`): read a run
  recorded with `exposures=True` — `overview()` per agent (turns, calls, invalid rate, timeouts, tokens), `turn()` in
  full (what the agent read, the tools offered, every call with its result), `timeline()`, `search()`, `invalid()`
  (refused calls with their corrections), `agent()`. `result.save(path)` / `RunResult.load(path)` write and read JSON
  or JSON lines; each exposure wake keeps its turn's `steps` from the engine tape, and a recorded result carries
  `host_tape`.
- **Replay** (`trace.replay(contract)`, `fg_env.participants.replay(trace)`, `fg-env trace FILE replay CONTRACT`): a
  recorded LLM and host run plays again offline from its steps (timeouts included) and host answers, checked turn by
  turn; the first
  divergence (turn, brief or update text, tools offered, call result, event, ending) is reported precisely, and a
  `fallback` participant can play on after it.
- **Evaluation** (`fg_env.evaluate(suite, focal=..., background=..., baseline=..., seats=..., score=..., modes=...)`,
  `fg-env evaluate`): a focal participant in a seeded share of a scenario's seats among background agents, paired
  with a baseline in the same seats on the same seeds; focal score per focal seat, paired difference with a 95%
  interval and cost, per scenario, mode, tag, held-out split and overall.
- **Run budgets** (`env.run(..., budget={"tokens", "calls", "host_calls", "seconds", "on_exhaust": "end" | "idle"})`):
  checked at every safe point; a run that runs out ends with `ended_by: "budget"` or idles its agents;
  `result.budget` and snapshots record it.
- **Chance nodes** (`chance` effect): `{"chance": [{"p": 0.5, "label": "heads", "do": [...]}, ...], "as": "coin"}`
  or `{"chance": "deal", "outcomes": "$world.deck", "weight": "...", "as": "card", "do": [...]}` picks one
  outcome from a listed distribution and logs it as a `chance` event. Sampled from the seed by default;
  `fg_env.load(..., chance=callable)` chooses outcomes (fixed deals, duplicate formats).
- **Game section** (`game`): seats (`players`, `seat`), per-seat `returns` and optional `rewards`, and the
  `utility` class (zero_sum, constant_sum with `total`, general_sum, identical) checked on every finished run.
  Every `RunResult` carries `returns` per seat. Mechanisms may fill the section in. Claims written there
  (`dynamics`, `information`, `chance_mode`, player counts, `max_rounds`, action space) are verified by
  `check` against `fg_env.describe`'s derivation; `describe` reports the utility class from the returns, and
  tournaments score seats by their returns when no `score` is given.
- **Copies of a run at any moment**: `wake.clone()` copies the run paused inside the agent's turn (fresh luck
  by default, `same_luck=True` for the real run's streams) to try calls and play forward on a `Branch`;
  `env.clone()` copies a run between rounds or stopped part-way through one. Copies are rebuilt from the
  run's base and replay a tape of what participants did, so they are exact — state, streams, turn
  numbers, log, exposures, frames, recorded host answers and timeouts — and never touch the original.
- **Forks**: `env.fork(arm=..., inputs=..., patch=..., contract=..., seed=..., effects=[...])` and
  `fg_env.fork(contract, snapshot, ...)` continue a run under changes; a compatibility check lists everything
  the state cannot follow with its fix, and intervention effects are applied atomically and logged as a
  `fork` event. `fg_env.experiment(..., branch_at=N)` plays each run's first N rounds once and continues
  every arm from that shared state.
- **Games** (`fg_env.game(contract)`): an OpenSpiel-style game over any contract — seats, stable integer
  action ids with legal masks (parametric actions for free text and lists; a param `step` makes numbers
  enumerable), chance nodes with enumerable outcomes, joint simultaneous nodes or `as_turn_based()`,
  `clone`/`child`, returns and rewards, per-seat observation text and structure, perfect-recall
  information states, state keys and serialization. Decisions go through the same tool calls as LLM agents.
- **Gym** (`fg_env.gym(contract, agent, others=...)`): a Gymnasium-style `reset`/`step` over one agent's tool
  calls with rewards from `game.returns`; a `gymnasium.Env` when gymnasium is installed.
- **Turn time limits**: stage `time_limit` (seconds, or an expression over `$actor`) and `on_timeout`
  effects, plus a run-wide default (`env.run(..., time_limit=30)`). A participant past its deadline loses
  the turn — later calls are refused, a `timeout` event and `stats["timeouts"]` record it — and a hung one
  never hangs the run. The update and `env.preview` show the limit; `wake.time_limit` / `wake.time_left`.
- **Async participants**: `async def` participants (or async `__call__`, or functions returning an
  awaitable) work in `env.run`, run concurrently in simultaneous stages with deterministic results, and
  natively inside an event loop with `await env.arun(...)`.
- **Exposure log** (`fg_env.load(..., exposures=True)`): `result.exposures` records, per wake, the brief,
  update and views shown (texts stored once by hash), news delivered, tools offered and every call with its
  arguments and result; kept in snapshots. `$seen(agent, item)` asks whether an agent was shown an event,
  a record entry or a view (a contract that uses it records exposures automatically).
- **Spectator views** (`"for": "spectator"`): an omniscient picture for UIs and reports, rendered each round
  into `result.frames` and on demand with `env.spectate()`, never shown to an agent.
- **Atomic turns**: stage `atomic` and `valid` — a turn's actions apply together, triggers, reactions and
  invariants wait for the turn, and a turn that breaks `valid` is undone whole with a correction
  (`stats["undone_turns"]`). Example: `examples/contracts/hopscotch_race.json`.

- **Per-entity dynamics** (`physics.per.<type>`): every entity of a type integrates its own ODEs over its
  number props (viral load, firm capital, habit strength), reading its own props, per-entity `read`s, the
  type's `params` and world physics values; `where` limits who steps, `write` sets other props. Stepped by
  the same clock right after world physics; thousands of entities per step.
- **Stochastic terms** (`noise`, Euler–Maruyama) on world physics variables and per-entity variables, drawn
  from streams derived from the run seed, so adding noise never shifts any other random draw.
- **Link fields** (`relations.<kind>.props`): typed fields on every link (defaults may read `$from`/`$to`),
  read with `$link(a, b, kind).field` and listed with `$links(entity, kind, where?)`; set by `link` with
  `props`, by assignment (`"$link($actor, $it, trusts).since = $round"`), in generated `links` and from
  `rows` columns; removed with the link, journaled, and carried by snapshots.
- **Entity lifecycle hooks** (`types.<type>.on_create` / `on_remove`, `$it` = the entity): run for every
  creation and removal — effects, mechanisms, other hooks — atomically with the change that caused them,
  inherited through `extends` (ancestors first) and guarded against endless recursion. Entities made at
  build run `on_create` once the whole world exists; `on_create_at_build: false` opts a type out.
- **External data feeds** (`feeds: {name: {host, into, query, every, when, fallback}}`): live or historical
  values (prices, news, weather) written into `world.<prop>` or `records.<record>` at the start of a due
  round, answered by a host adapter implementing the new `Feed` protocol (`fetch(request)`), recorded on the
  host tape so snapshots, restores and replays never ask again; host text is marked untrusted. A declared
  `fallback` answers without a host, drawing randomness from its own seeded stream. New `StubFeed` stub and
  `adapters.historical(rows, at=, value=)` for backtests.
- **Delivery latency and lossy channels**: `delay` on `post` and `emit` delivers the message rounds (or clock
  time) later with the content it had when sent; `drop` on `post`, `emit` and `wake` loses it with a chance
  rolled from the run's seed. Pending deliveries are journaled (a refused action sends nothing) and carried
  by snapshots with their provenance. `delay` and `drop` are now reserved record field names.
- Example `examples/contracts/outbreak_network.json`: per-resident viral load and immunity with noise, a
  contact network whose links carry a setting and closeness, newcomers wired in by `on_create`, a weather
  feed with a seeded fallback, and advisories that arrive a day late and are sometimes lost.
- **Tournaments** (`fg_env.tournament(contract, entrants={name: participant})`, `fg-env tournament`): round
  robin, all-play-all (every seat order) or Swiss pairing; entrants rotate through every seat and game *g*
  replays the same seed at every table (duplicate deals). Games are scored by the winner, an output, an
  expression over `$outputs` and `$seat`, or a function. Standings carry maximum-likelihood Elo with 95%
  intervals, Glicko-2, win/draw/loss, points and score means; `evaluation` adds the margin matrix, the
  maximum-entropy Nash average, α-Rank and a Schulze vote. The result also has head-to-head records, every
  entrant's score in every seat, and cost per entrant (turns, calls, invalid calls, model tokens).
- **Self-description** (`fg_env.describe(contract)`, `fg-env describe`, `fg-env info`): an ODD-protocol
  markdown document (purpose, entities and state variables, process and scheduling, design concepts,
  initialisation, inputs, submodels) and game metadata derived from the contract, each with its
  evidence: dynamics (sequential, simultaneous, scheduled, mixed), chance mode and when it acts,
  information (perfect, imperfect or unknown), utility, players at start and their bounds, longest game
  in rounds and decisions, action-space size or parametric, observations and concepts. What the contract
  does not settle is reported as unknown with the reason. `fg_env.sdk.describe.check_claims` turns
  claims a contract makes about itself into errors (contradicted) or warnings (unverifiable).
- **Held-out cases in calibration and backtests**: `calibrate` accepts a list of cases, each with its own
  fixed inputs and targets, and fits one set of params to all of them; `test=` (a share or case names)
  returns the fit to the other cases with its error on the held-out ones, and `folds=` adds a k-fold
  cross-validated error to the fit on every case. `backtest(test=|folds=)` scores held-out cases against a
  climatology built only from the other cases (out-of-sample Brier or CRPS and skill). Splits are drawn
  from the analysis seed. CLI: `calibrate --cases --test --folds`, `backtest --test --folds`.
- **Per-agent run statistics**: `RunResult.agent_stats` splits `stats` by agent entity id (turns, calls,
  invalid calls, committed actions, model usage), and snapshots keep the split.
- `Job.participants`: a job in `run_jobs` can carry its own participants; participants given by name still
  run in worker processes.
- **Linear algebra in expressions**: `$dot`, `$matmul`, `$transpose`, `$identity`, `$inverse`, `$det`
  (exact for whole numbers) and `$linsolve` on nested lists, with errors that name the bad shape or a
  singular matrix, and work charged against the evaluation budget.
- **Correlated draws**: `$mvnormal(means, cov)` samples a multivariate normal from the run's seeded
  generator (Cholesky; the covariance is checked to be symmetric positive semi-definite). Draw once in a
  population list prop and read the parts through `$it` for correlated traits.

#### Changed
- **BREAKING (template API):** the template-based kernel API moved off the top level: import it from
  `fg_env.legacy` (`from fg_env.legacy import simulate, Kernel, compile_template, registry`) and run its commands
  as `fg-env legacy <command>` (`fg-env legacy compile template.json`). `fg_env` and `fg-env --help` now show only
  the Environment SDK (46 names, 20 commands; were 120 and 31).
- **Replays of chosen chance and forks**: outcomes a `chance=` chooser picked are recorded (`exposures.chance`) and
  replayed without the chooser (a changed chance node is a divergence); a forked run records the snapshot it
  continued from (`exposures.start`, its exposure log as counts) and replays from there. `.jsonl` results keep both.
- **Budgets and recordings in every batch**: `experiment(..., budget=, exposures=)` (with `branch_at` the shared
  rounds count toward every arm), `tournament(..., budget=, exposures=)`, `evaluate(..., exposures=)` (every run in
  `result.results`) and `run_jobs(..., exposures=)`; each run has the whole budget, and a recorded run keeps its
  events. CLI: `fg-env run --exposures --frames FILE`, `fg-env experiment|tournament --budget --exposures`,
  `fg-env evaluate --exposures` (`--exposures` needs `--json`).
- A host-tool answer that lands after its turn timed out is taken off the host tape, and a call made after the
  deadline is no step on the engine tape, so replays of timed-out turns stay exact.
- Model usage reported after a turn's deadline counts toward `stats`, `agent_stats` and the budget (the wake records it
  as `late_usage`); the late participant still cannot act.
- `Env.restore` refuses a snapshot whose seed, arm or inputs were edited, and explains a contract mismatch,
  pointing to `fg_env.fork` (an edited arm used to be ignored silently).
- Async participants are awaited instead of refused.
- `link` without `value` keeps an existing link's value (it used to reset it to 1); a new link gets the
  relation's `default` (previously ignored), or 1.

## [0.3.0]

### Environment SDK (`fg-env`)

The package is renamed `fg-env-kernel` → **`fg-env`** (import `fg_env`) and gains the
contract-driven Environment SDK: define an environment as one JSON contract, and the engine runs it.

#### Added
- **One contract for any environment** (`fg_env.load(contract).run(participants)`): typed `inputs`,
  static `brief`, `clock`, `space`, global `world` props, `types` with inheritance (`extends`),
  named `entities`, sampled `population` (weighted rows, distributions), `relations` and generated
  `links` networks, continuous `physics` (RK4) bound to the world, `records`, `actions` as typed tools,
  `stages` (sequential or sealed simultaneous turns, `until`, `quiet`), declared `views`, `events`
  (scheduled, periodic, conditional, random, per arm), coded `policies`, `metrics`, typed `outputs`,
  `end` conditions, experiment `arms`, `invariants`, reusable `defs` (expressions) and `blocks` (effects).
- **One strict, safe expression language** with aggregates (`$count`, `$sum`, `$top`, `$median`,
  `$quantile`, `$dict` …), randomness from the run's seed tree, map literals, `$outer` in nested items.
- **LLM-native turns**: a cacheable brief, a compact update (why now, what changed since the last
  turn, ranked views), one JSON-Schema tool per legal action, correction text for invalid calls,
  atomic actions with rollback, participant text kept «untrusted» wherever it travels.
- **Participants**: any callable taking a `Wake`; built-in `random`, `idle`, `policy:<name>`, and
  Anthropic / OpenAI tool-loop participants that take your own client.
- **Tooling**: `fg_env.check` (every issue with its path and a fix; optional smoke rounds),
  `env.preview`, snapshots (`env.snapshot()` / `Env.restore`), `fg_env.experiment` (arms × seeded runs,
  common random numbers), generated authoring guide (`fg_env.guide()`), JSON Schema (`fg_env.schema()`),
  CLI `fg-env check | run | preview | experiment | guide | schema`.
- **Turn and state controls**: `must_act` / `on_idle`, expression `terminal`, `$pending`, params whose
  choices depend on earlier params, element assignment (`$world.board[$i] = x`), policy rules with
  `each`, `env.entity` / `env.entities` / `env.props`, preview of the exact next turn.
- **Example contracts** in `examples/contracts/` — coffee market, forecasting council, order-book
  exchange, civil trial, town epidemic, werewolf, labor negotiation, Connect Four, Hold'em-lite,
  beer game, climate club, ride-hailing, checkers, Diplomacy-style strategy, misinformation network, lemonade stand — each
  written by an LLM agent from the guide alone, with golden-run tests.

- **Reliability**: runs advance through safe points (before each round, stage, pass and sequential
  turn), so `run(stop=...)` can stop anywhere and the next `run` continues exactly; snapshots capture
  every subsystem (turn numbering, pending wakes, schedule order, participant-text provenance) and
  restore to an identical continuation — every example contract is tested split by a JSON snapshot
  and stopped part-way through a round; `preview` plays earlier seats on a copy and never changes the
  run; pure `defs` are cached per world state.
- **LLM participants that survive providers**: retries with backoff for rate limits, timeouts and
  server errors (honouring `retry-after`), `on_error="fail" | "end_turn"`, one nudge for replies
  without tool calls, and real provider token usage in `RunResult.stats` (`Wake.record_usage`).
- **Experiments that keep every run**: up-front validation, failed runs kept with their error,
  process-pool fallback, and `ExperimentResult.deltas(control)` — paired arm − control differences with
  small-sample 95% intervals.

#### Changed
- **BREAKING:** distribution `fg-env-kernel` → `fg-env`, import `fg_env_kernel` → `fg_env`, console
  script `fg-env-kernel` → `fg-env`. The template-based API (`Kernel`, `simulate`, `load_world`) is
  still available under the new name.
- **BREAKING (template API):** the kernel no longer imports host-application modules.
  `DomainModuleRegistry` no longer auto-imports a top-level `assets` package, and the template
  loader no longer constructs `agents.crowd_agent.CrowdAgentManager`. A host that relied on either
  now registers its domain modules explicitly
  (`DomainModuleRegistry.get_instance().register(name, cls)`) and attaches its own crowd manager to
  `state.crowd_agents` after the world is built; `crowd_config` is still recorded on
  `state._crowd_config`.

#### Added (packaging)
- `fg_env.__version__` and `fg-env --version`.
- The contract JSON Schema is committed at `schema/contract.schema.json`; CI fails when the SDK's
  schema differs from it (`make schema` regenerates it).

#### Migrating from `fg-env-kernel`
There is no compatibility shim: `import fg_env_kernel` does not work with `fg-env` installed.
A re-export shim could not cover submodule imports (`fg_env_kernel.state`, ...), and it would
collide with an installed `fg-env-kernel` that owns the same import directory.

1. Replace the dependency: `pip uninstall fg-env-kernel && pip install fg-env`
   (in requirements, `fg-env-kernel` → `fg-env>=0.3`).
2. Rename imports: `fg_env_kernel` → `fg_env` everywhere, including submodule imports
   (`from fg_env_kernel.state import WorldState` → `from fg_env.state import WorldState`).
3. Rename CLI calls: `fg-env-kernel <command>` → `fg-env <command>`. Template commands
   (`compile`, `lint`, `contract`, ...) are unchanged.

The `fg-env-kernel` distribution stays on PyPI at its last 0.2.x release and receives no further
updates. Projects that cannot migrate yet should pin `fg-env-kernel<0.3`.

### Fixed
- `$entity(id).property` now resolves in action guards and termination predicates
  through the existing typed effect-value resolver. Missing entities/properties,
  invalid arguments and private attribute paths still fail closed under negation.

## [0.2.0] — 2026-08-10

### Changed
- **BREAKING: primitives auto-discovery no longer runs at import time.**
  `import fg_env` no longer scans `kernel_primitives/` directories or
  imports arbitrary `.py` files. Downstream code must either call
  `fg_env.discover()` explicitly or set `KERNEL_PRIMITIVES_DIR`
  (an explicitly configured directory is still honored at import).
- **`Kernel.load()` / `simulate()` now validate templates before building.**
  ERROR-severity lint issues (no agent-role entity type, unknown effect
  operations, unregistered check_types, ...) raise the new `TemplateError`
  with the full issue list instead of silently running an empty world.
  Warnings are logged and never block; pass `strict=True` to raise on
  warnings too. `lint_template()` accepts a `registry=` argument so
  kernel-scoped custom primitives don't false-positive.
- `load_world()` now honors the template's `temporal.max_rounds` and passes it
  to the engine (previously silently ignored; the engine default of 100 applied).
- `SimulationEngine.run()` is resume-aware: after prior `step()` calls it runs
  only the remaining round budget and emits `simulation_start` /
  `simulation_end` exactly once per engine lifetime.

### Added
- **SDK facade.** `Kernel(seed=...).load(template) -> World` — a thin typed
  wrapper over the canonical `(WorldState, SimulationEngine)` pair with
  `step()` / `run()` / `state` / `events` / `finished` / `terminated_by`.
  New public `SimulationEngine.step()` advances exactly one discrete round.
- **Typed agent contract.** `DecisionFn` / `OnEventFn` aliases document the
  real callback signature `(entity_id, perception, valid_actions) ->
  ActionInstance | None`; `ActionInstance`, `WorldState`, `SimulationEngine`,
  and `SimEvent` are now exported from the package root.
- **Per-kernel registry isolation.** `registry.fork()` returns a child
  registry that sees all built-in primitives (fallback to parent, nothing
  copied) while keeping its own registrations private. Every namespace has a
  registry-bound decorator (`my_registry.effect(...)`, `.termination(...)`,
  ...), and `Kernel(registry=my_registry)` / `load_world(registry=...)` /
  `build_world_state(registry=...)` / `termination.evaluate(registry=...)`
  resolve custom effect ops and termination checks against that registry only.
  Validation/reporting surfaces (`lint_template`, `export_kernel_contract`)
  still read the global registry.
- `py.typed` marker — the package now ships its type annotations.
- `examples/` — a complete runnable tic-tac-toe template plus
  `examples/quickstart.py`.
- **Continuous coupled-dynamics ("physics").** New `physics` module: a dt-aware
  system of coupled ODEs (`PhysicsModel`) integrated with 4th-order Runge–Kutta
  and sub-stepping. Variables may be free global scalars, read entity-property
  aggregates (`EntitySource`), and/or write values back onto entities
  (`EntityWriteback`). Rate expressions use a safe whitelisted-AST evaluator (no
  `eval` of arbitrary code). Declare via a `physics` block in the world schema;
  the world evolves between agent turns (predator/prey, SIR, price discovery)
  while agents remain turn-based. Deterministic and serializable.
- **Continuous-time mode wired end-to-end.** `temporal.mode: "continuous"` now
  builds a `ContinuousTemporalModel` (`build_continuous_model`) and drives an
  event-driven clock: agent turns reschedule by per-action `duration`, and a
  recurring environment tick (`environment_interval`) advances physics / property
  dynamics / world events by the real elapsed `dt`. Previously the model existed
  but was never instantiated or run.

### Fixed
- `ContinuousTemporalModel.is_empty()` (the engine's continuous loop called it
  but it was undefined — continuous mode could not start).

## [0.1.0] — 2026-05-27

### Added
- Initial release. Game-agnostic simulation kernel extracted from the Fareground simulation platform.
- Single runtime dependency (`pydantic>=2`).
- Core data types: entities, actions, resources, relations, state.
- Effect / predicate expression language.
- Extension points: verbs, archetypes, phases, primitives.
- Pipeline: compile, lint, smoke, contract.
- Auto-discovery of `kernel_primitives/*.py` extensions.
