# Changelog

All notable changes to this project will be documented in this file.

The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.1.0/),
and this project adheres to [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

## [Unreleased]

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
  beer game, climate club, ride-hailing, checkers, Diplomacy-style strategy, lemonade stand — each
  written by an LLM agent from the guide alone, with golden-run tests.

#### Changed
- **BREAKING:** distribution `fg-env-kernel` → `fg-env`, import `fg_env_kernel` → `fg_env`, console
  script `fg-env-kernel` → `fg-env`. The template-based API (`Kernel`, `simulate`, `load_world`) is
  still available under the new name.


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
