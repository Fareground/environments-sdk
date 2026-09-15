# Changelog

All notable changes to this project will be documented in this file.

The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.1.0/),
and this project adheres to [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

## [Unreleased]

### Environment SDK (`fg-env`)

#### Added
- **Crowd scale**: `invariants[].check` (`action` default, `round`, `end`); an invariant already found to
  hold in the same state is not evaluated again. Type members are indexed (no world scan per
  `$choice(type)`/`$count(type)`/`each`), a condition opening with `$it.field == value` skips entities whose
  field differs without evaluating them, and a coded policy's entity argument is validated without listing
  every candidate. Boltzmann 20 000 agents: 11.8 s → 0.48 s per round.
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
