# Python API reference

Generated from public exports in `fg_env`. Start with `check`, `load`, `run` and `experiment`. A source may be a contract dictionary, JSON text, file path or parsed `Contract`.

## Entry points

## `load`

```python
load(source: 'ContractLike', *, inputs: 'Optional[Mapping[str, Any]]' = None, seed: 'Optional[int]' = None, arm: 'Optional[str]' = None, strict: 'bool' = False, parallel: 'int' = 8, data_dir: 'DataDir' = None, hosts: 'Any' = None, exposures: 'bool' = False, chance: 'Any' = None, calibrate: 'bool' = True) -> 'Env'
```

Check a contract and build a runnable :class:`Env`.

Errors raise :class:`ContractError` listing every problem with a fix; ``strict=True``
also rejects warnings. ``seed`` defaults to a fresh one (readable as ``env.seed``).
Inputs with a ``source`` and the contract's ``assets`` read their files from ``data_dir`` (default: the contract
file's folder).
``hosts`` (a :class:`~fg_env.sdk.host.Hosts` or a mapping of host name to adapter) answers the
judgment the contract asks of a host; build-time host work (personas) is done before round 1.
``exposures=True`` records what every agent was shown on every wake (``result.exposures``); a
contract that calls ``$seen`` records it anyway. ``chance`` decides `chance` effects: ``"sampled"`` (the
default: drawn from the seeded stream) or a callable given each :class:`~fg_env.sdk.chance.ChanceNode`
that returns the index of the outcome to take (a fixed deal, duplicate formats); :func:`fg_env.game`
enumerates chance for search. A contract with a ``calibration`` section fits its inputs with pilot sessions first
(``env.calibration`` is the report); ``calibrate=False`` skips that, as ``fg_env.check``'s smoke round does.

## `run`

```python
run(source: 'ContractLike', participants: 'Any' = None, *, inputs: 'Optional[Mapping[str, Any]]' = None, seed: 'Optional[int]' = None, arm: 'Optional[str]' = None, rounds: 'Optional[int]' = None, on_event: 'Any' = None, strict: 'bool' = False, data_dir: 'DataDir' = None, hosts: 'Any' = None, time_limit: 'Optional[float]' = None, exposures: 'bool' = False, budget: 'Optional[Mapping[str, Any]]' = None) -> 'RunResult'
```

Load and run in one call: ``fg_env.run("shop.json", {"buyer": "policy:thrifty"}, seed=1)``.

## `check`

```python
check(source: 'ContractLike', rounds: 'int' = 1, seed: 'int' = 0, *, data_dir: 'DataDir' = None, hosts: 'Any' = None, inputs: 'Optional[Mapping[str, Any]]' = None) -> 'List[Issue]'
```

Every problem in a contract, errors first then warnings. Never raises for contract problems.

A contract without errors is also built and played for ``rounds`` rounds (default 1; 0 checks statically only)
with random agents that read everything they are shown, so problems that only appear with real values (sampling,
first turns, views, outputs) are reported the same way. Inputs with a ``source`` are read from ``data_dir``
(default: the contract file's folder); ``hosts`` answers what the contract asks of a host during that play.
``inputs`` checks a configured scenario without editing its defaults. Supplied inputs are validated even
with ``rounds=0``; positive rounds also exercise them in the smoke run.

## `parse`

```python
parse(source: 'ContractLike', data_dir: 'DataDir' = None) -> 'Contract'
```

Read and structurally validate a contract (dict, path, JSON text or :class:`Contract`).

The contract remembers where its input data files are read from: ``data_dir`` when given, else the
contract file's folder, so every run, check and analysis of it finds them.

## `expand`

```python
expand(source: 'ContractLike', *, mechanisms: 'bool' = False) -> 'Dict[str, Any]'
```

The contract data the engine reads: imports merged and macros expanded (and, with
``mechanisms=True``, every mechanism expanded into ordinary sections too).

Raises :class:`ContractError` for problems found while expanding; ``check`` reports the rest.

## `experiment`

```python
experiment(source: 'ContractLike', *, runs: 'int' = 10, arms: 'Optional[List[str]]' = None, seed: 'int' = 0, inputs: 'Optional[Mapping[str, Any]]' = None, participants: 'Any' = None, participants_for: 'Optional[Callable[[int, Optional[str]], Any]]' = None, rounds: 'Optional[int]' = None, workers: 'int' = 1, data_dir: 'Any' = None, branch_at: 'Optional[int]' = None, budget: 'Optional[Mapping[str, Any]]' = None, exposures: 'bool' = False, hosts: 'Any' = None, uncertainty: 'Any' = None) -> 'ExperimentResult'
```

Run each arm ``runs`` times. Run *i* uses the same seed in every arm, so differences
between arms come from the arm, not from luck. ``arms`` defaults to every declared arm
(or a single baseline run set when none are declared). ``participants_for(i, arm)``
builds fresh participants per run when they hold state.

``branch_at=N`` shares history: run *i* plays its first N rounds once, without an arm, and every
arm continues from that same state (a fork: the arm's patch and inputs apply from round N + 1, and
an arm whose patch the state cannot follow raises before the experiment goes on).

``budget`` caps each run on its own (:mod:`fg_env.sdk.budget`); with ``branch_at`` the shared rounds are part of
every arm's run, so they count toward each arm's budget. ``exposures=True`` records what agents saw in every run
(``result.arms[label].runs[i].exposures``, events kept): each run is a trace to read or replay.
``data_dir`` is where inputs with a ``source`` are read (default: the contract file's folder); ``hosts``
answers the contract's host requests in every run. ``uncertainty`` (a calibration, a list of points or priors;
:mod:`fg_env.sdk.analysis.draws`) draws parameters per run, the same for run *i* in every arm, so the spread of
outcomes includes not knowing them.

Problems shared by every run (an unknown arm, bad inputs, an unknown participant) raise
before anything runs. A run that fails on its own is kept with ``status="failed"`` and its
error, and the rest of the experiment carries on.

## `fork`

```python
fork(contract: 'ContractLike', snapshot: 'Mapping[str, Any]', *, arm: 'Any' = KEEP_ARM, inputs: 'Optional[Mapping[str, Any]]' = None, patch: 'Optional[Mapping[str, Any]]' = None, to: 'Optional[ContractLike]' = None, seed: 'Optional[int]' = None, effects: 'Optional[List[Any]]' = None, parallel: 'int' = 8, hosts: 'Any' = None, data_dir: "Union[str, 'os.PathLike[str]', None]" = None) -> "'Env'"
```

A run continuing ``snapshot`` (taken with ``contract``) under changes.

* ``arm`` — another declared arm (``None`` for none); its patch applies and its inputs are set. Inputs
  the old arm set, and that still hold its values, go back to the contract's defaults first.
* ``inputs`` — input values from here on (rules reading ``$inputs`` see them; the built world stays).
* ``patch`` — deep-merged into the contract (objects merge, lists replace), after the arm.
* ``to`` — a whole replacement contract (its arms and patch apply the same way).
* ``seed`` — the luck from here on comes from this seed instead of continuing the run's streams.
* ``effects`` — interventions applied at the fork, atomically, logged as a `fork` event, invariants checked.

Raises :class:`ContractError` listing everything the state cannot follow, each with a fix.

## `game`

```python
game(source: 'ContractLike', *, inputs: 'Optional[Mapping[str, Any]]' = None, seed: 'int' = 0, arm: 'Optional[str]' = None, players: 'Optional[Sequence[str]]' = None, others: 'Any' = None, chance: 'str' = 'explicit', simultaneous: 'str' = 'joint', dry_run: 'bool' = True, max_combinations: 'int' = 10000, hosts: 'Any' = None, data_dir: "Union[str, 'os.PathLike[str]', None]" = None) -> 'Game'
```

A contract as a game for search, solving and learning code.

* ``players`` — the seats (entity ids); default: the contract's ``game.players`` (else every agent), in seat order.
* ``others`` — participants for agents that are not seats (as in ``Env.run``).
* ``chance`` — ``"explicit"``: every `chance` effect is a chance node whose outcomes search code chooses;
  ``"sampled"``: outcomes are drawn from the seed. Other randomness is always fixed by ``seed``.
* ``simultaneous`` — ``"joint"``: a simultaneous stage is one node (``apply_actions``); ``"turn_based"``:
  its sealed turns are decided one seat at a time.
* ``dry_run`` — legal calls are also tried without effect, so a call whose effects would refuse it is not
  listed (the engine's own judgement at submit); ``False`` lists every call that validates.
* ``max_combinations`` — most argument combinations listed per action before it counts as parametric.

## `gym`

```python
gym(source: 'ContractLike', agent: 'str', *, others: 'Any' = None, inputs: 'Optional[Mapping[str, Any]]' = None, arm: 'Optional[str]' = None, seed: 'Optional[int]' = None, max_steps: 'Optional[int]' = None, action_ids: 'bool' = False, hosts: 'Any' = None, render_mode: 'Optional[str]' = None, data_dir: "Union[str, 'os.PathLike[str]', None]" = None) -> 'GymEnv'
```

One agent (an entity id) of a contract as a Gymnasium-style environment; ``others`` play the rest.

``seed`` seeds the episodes (``reset(seed=...)`` reseeds them); ``max_steps`` truncates an episode after
that many calls; ``action_ids=True`` accepts integer action ids and adds ``legal_actions`` and
``action_mask`` to ``info`` (see :func:`fg_env.game` for how ids are numbered).

## `Branch`

```python
Branch(pilot: 'Pilot')
```

A private copy of a run that you drive: decide for the agents it pauses for, let it play on, read it.

Everything runs on the copy's own thread; its methods wait for it. Close it (or use ``with``, or let
it be garbage collected) to discard it.

## `Game`

```python
Game(root: 'Env', *, players: 'Optional[Sequence[str]]', others: 'Any', chance: 'str', turn_based: 'bool', dry_run: 'bool', limit: 'int')
```

A contract as a game: seats, a numbered action space, and states to search from.

Create with :func:`fg_env.game`. States pause at every decision of a seat (and at every chance
node when chance is explicit); agents that are not seats are played by ``others``.

## `GameState`

```python
GameState(game: "'Game'", run: 'Union[Branch, Run]', history: 'List[Dict[str, Any]]', previous: 'Optional[List[float]]' = None, legal: 'Optional[Dict[int, Legal]]' = None, path: 'bytes' = b'')
```

One state of a :class:`~fg_env.sdk.game.Game`. ``apply_action`` changes it; ``child`` and ``clone``
give new independent states. Players are seat indices (``game.players[i]`` is the entity id).

## `GymEnv`

```python
GymEnv(root: 'Env', agent: 'str', *, others: 'Any', max_steps: 'Optional[int]', action_ids: 'bool', hosts: 'Any', render_mode: 'Optional[str]')
```

One agent of a contract as a Gymnasium-style environment. Create with :func:`fg_env.gym`.

## `guide`

```python
guide(part: 'Optional[str]' = None) -> 'str'
```

The core guide, or one part by name: a section (``"actions"``), a topic (``"expressions"``, ``"effects"``,
``"functions"``, ``"mechanisms"``, ``"patterns"``, ``"recipes"``, ``"running"`` …), a function group (``"functions.stats"``),
a mechanism family (``"market"``) or mode (``"market.auction"``) — or ``"all"`` for everything.
The core guide ends with a map of the parts.

## `schema`

```python
schema() -> 'Dict[str, Any]'
```

JSON Schema of the contract (structure only; ``fg_env.check`` verifies meaning).

## `new`

```python
new(template: 'str' = 'blank', path: "Union[str, 'os.PathLike[str]', None]" = None, *, name: 'Optional[str]' = None, overwrite: 'bool' = False) -> 'Dict[str, Any]'
```

A ready-to-run contract from a template (blank, game, market, simulation, social).

With ``path`` it is also written there as JSON (an existing file is kept unless ``overwrite``); ``name``
replaces the contract's name (default: the template's, or the file name when a path is given).

## `Env`

```python
Env(contract: 'Contract', inputs: 'Dict[str, Any]', seed: 'int', arm: 'Optional[str]' = None, parallel: 'int' = 8, exposures: 'bool' = False, assets: 'Optional[AssetStore]' = None)
```

A loaded environment. Create with :func:`fg_env.load`; run with :meth:`run`; copy with :meth:`clone`
and :meth:`fork`.

## `Contract`

```python
Contract(*, fg_env: str = '1', name: str, description: str = '', imports: List[str] = <factory>, brief: fg_env.sdk.contract_world.Brief = <factory>, assets: Dict[str, fg_env.sdk.assets.spec.AssetSpec] = <factory>, inputs: Dict[str, fg_env.sdk.contract_world.InputSpec] = <factory>, clock: fg_env.sdk.contract_world.Clock = <factory>, space: Optional[fg_env.sdk.contract_world.Space] = None, world: Dict[str, fg_env.sdk.contract_world.PropSpec] = <factory>, types: Dict[str, fg_env.sdk.contract_world.TypeSpec], entities: Dict[str, fg_env.sdk.contract_world.EntitySpec] = <factory>, population: List[fg_env.sdk.contract_world.PopulationSpec] = <factory>, relations: Dict[str, fg_env.sdk.contract_world.RelationSpec] = <factory>, links: List[fg_env.sdk.contract_world.LinkSpec] = <factory>, physics: Optional[fg_env.sdk.contract_world.PhysicsSpec] = None, feeds: Dict[str, fg_env.sdk.contract_world.FeedSpec] = <factory>, patterns: Dict[str, Dict[str, Any]] = <factory>, records: Dict[str, fg_env.sdk.contract_rules.RecordSpec] = <factory>, actions: Dict[str, fg_env.sdk.contract_rules.ActionSpec] = <factory>, stages: List[fg_env.sdk.contract_rules.StageSpec] = <factory>, views: Dict[str, fg_env.sdk.contract_rules.ViewSpec] = <factory>, events: List[fg_env.sdk.contract_rules.EventSpec] = <factory>, triggers: List[fg_env.sdk.contract_rules.TriggerSpec] = <factory>, policies: Dict[str, fg_env.sdk.contract_rules.PolicySpec] = <factory>, metrics: Dict[str, fg_env.sdk.contract_measure.MetricSpec] = <factory>, outputs: Dict[str, fg_env.sdk.contract_measure.OutputSpec] = <factory>, end: List[fg_env.sdk.contract_measure.EndSpec] = <factory>, arms: Dict[str, fg_env.sdk.contract_measure.ArmSpec] = <factory>, calibration: Optional[fg_env.sdk.contract_measure.CalibrationSpec] = None, game: Optional[fg_env.sdk.game_spec.GameSpec] = None, invariants: List[fg_env.sdk.contract_measure.InvariantSpec] = <factory>, defs: Dict[str, fg_env.sdk.contract_measure.DefSpec] = <factory>, blocks: Dict[str, fg_env.sdk.contract_measure.BlockSpec] = <factory>, mechanisms: Dict[str, Dict[str, Any]] = <factory>) -> None
```

An environment: world, people, rules, what agents see, what is measured.

## `RunResult`

```python
RunResult(status: 'str', ended_by: 'Optional[str]', rounds: 'int', seed: 'int', arm: 'Optional[str]', inputs: 'Dict[str, Any]', outputs: 'Dict[str, Any]', metrics: 'Dict[str, Any]', series: 'Dict[str, List[Any]]', winner: 'Any' = None, error: 'Optional[str]' = None, time: 'Optional[float]' = None, returns: 'Dict[str, float]' = <factory>, output_issues: 'List[Dict[str, Any]]' = <factory>, stats: 'Dict[str, Any]' = <factory>, agent_stats: 'Dict[str, Dict[str, Any]]' = <factory>, events: 'List[Dict[str, Any]]' = <factory>, exposures: 'Dict[str, Any]' = <factory>, frames: 'List[Dict[str, Any]]' = <factory>, host_tape: 'Dict[str, Any]' = <factory>, budget: 'Dict[str, Any]' = <factory>, formats: 'Dict[str, str]' = <factory>, diagnostics: 'List[Dict[str, str]]' = <factory>, clock: 'Dict[str, Any]' = <factory>, assets: 'Dict[str, Any]' = <factory>) -> None
```

Everything a run produced. ``outputs`` follows the contract's output contract.

## `ExperimentResult`

```python
ExperimentResult(arms: 'Dict[str, ArmResult]', seeds: 'List[int]', rounds: 'Optional[int]' = None) -> None
```

ExperimentResult(arms: 'Dict[str, ArmResult]', seeds: 'List[int]', rounds: 'Optional[int]' = None)

## `Wake`

```python
Wake(turn: "'Turn'")
```

One agent's turn. Obtained from the runtime; never constructed directly.

## `ToolResult`

```python
ToolResult(ok: 'bool', text: 'str', ended: 'bool' = False, data: 'Dict[str, Any]' = <factory>, attachments: 'List[Attachment]' = <factory>) -> None
```

What a tool call did. ``text`` is written for the agent; ``ended`` means the turn is over.

## `Issue`

```python
Issue(path: 'str', message: 'str', fix: 'Optional[str]' = None, severity: 'str' = 'error') -> None
```

One problem found in a contract or its inputs.

## `ContractError`

```python
ContractError(issues: 'List[Issue]', title: 'str' = 'contract is invalid')
```

The contract is invalid. ``issues`` lists every error found (not just the first).

## `InputError`

```python
InputError(issues: 'List[Issue]')
```

Run inputs do not match the contract's declared inputs.

## `RunError`

```python
RunError(message: 'str', path: 'Optional[str]' = None)
```

A run could not continue. ``path`` names the contract element that failed.

## `InvariantViolation`

```python
InvariantViolation(message: 'str', path: 'Optional[str]' = None)
```

A declared invariant stopped holding; the run fails closed.

## `SnapshotError`

A snapshot cannot be restored into this contract.

## `sweep`

```python
sweep(contract: 'ContractLike', params: 'Mapping[str, ParamSpec]', *, runs: 'int' = 5, outputs: 'Optional[Sequence[str]]' = None, arms: 'Optional[Sequence[Optional[str]]]' = None, inputs: 'Optional[Mapping[str, Any]]' = None, design: 'str' = 'factorial', samples: 'Optional[int]' = None, participants: 'Any' = None, rounds: 'Optional[int]' = None, seed: 'int' = 0, workers: 'int' = 1, data_dir: 'Any' = None, hosts: 'Any' = None, uncertainty: 'Any' = None) -> 'SweepResult'
```

Run the contract across combinations of inputs.

``params``: ``{input: [values]}`` or ``{input: {"low", "high", "steps", "log"}}`` (range
bounds default to the input's declared ``min``/``max``). ``design="factorial"`` runs every
combination; ``design="lhs"`` draws ``samples`` Latin-hypercube points over the ranges
(list params are sampled by stratified index). ``arms`` adds the arm as another factor
(default: no arm). ``inputs`` fixes other inputs for every cell. ``outputs`` limits the
measured outputs (default: every numeric or yes/no output). ``data_dir`` is where inputs with a
``source`` are read (default: the contract file's folder); ``hosts`` answers host requests in every run.

## `sensitivity`

```python
sensitivity(contract: 'ContractLike', inputs: 'InputRanges', output: 'str', *, method: 'str' = 'oat', runs: 'int' = 5, baseline: 'Optional[Mapping[str, Any]]' = None, delta: 'float' = 0.1, trajectories: 'int' = 6, levels: 'int' = 4, samples: 'int' = 20, arm: 'Optional[str]' = None, participants: 'Any' = None, rounds: 'Optional[int]' = None, seed: 'int' = 0, workers: 'int' = 1, level: 'float' = 0.95, data_dir: 'Any' = None, hosts: 'Any' = None) -> 'SensitivityResult'
```

Rank ``inputs`` by their influence on ``output`` (an output or a metric's final value).

``inputs``: names, or ``{name: {"low", "high"}}`` (ranges default to declared min/max; OAT
only needs them to keep perturbations inside the allowed range). ``baseline`` overrides the
contract defaults as the OAT centre and the fixed values for the other inputs. ``data_dir`` is where
inputs with a ``source`` are read (default: the contract file's folder); ``hosts`` answers host requests.

## `calibrate`

```python
calibrate(contract: 'ContractLike', targets: 'Any', params: 'Mapping[str, Mapping[str, Any]]', *, runs: 'int' = 5, budget: 'int' = 30, holdout: 'Optional[int]' = None, method: 'str' = 'auto', inputs: 'Optional[Mapping[str, Any]]' = None, arm: 'Optional[str]' = None, participants: 'Any' = None, rounds: 'Optional[int]' = None, seed: 'int' = 0, workers: 'int' = 1, test: 'Any' = None, folds: 'Optional[int]' = None, data_dir: 'Any' = None, hosts: 'Any' = None) -> 'CalibrationResult'
```

Search ``params`` (``{input: {"low", "high", "log"?}}``) so the contract matches ``targets``.

``targets``: a mapping of targets, or a list of cases ``{name?, inputs?, arm?, targets}`` (see the
module notes). ``method``: ``bisection`` (one parameter, one number target, monotone response),
``golden`` (one parameter), ``nelder_mead`` or ``cross_entropy`` (several), or ``auto``
(bisection when it applies and the response brackets the target, else golden for one
parameter, Nelder–Mead for several). ``budget`` caps distinct evaluated points, each costing
``runs`` runs per case; ``holdout`` (default ``runs``) fresh seeds validate the best point.
With cases, ``test`` returns the fit to the other cases with its error on the held-out ones, and
``folds`` adds a cross-validated error to the fit on every case (one extra search per fold).
``data_dir`` is where inputs with a ``source`` are read (default: the contract file's folder); ``hosts`` answers
host requests (feeds, judges) in every run.

## `optimise`

```python
optimise(contract: 'ContractLike', decisions: 'Mapping[str, Any]', objective: 'Any', constraints: 'Any' = (), *, runs: 'int' = 10, seed: 'int' = 0, method: 'str' = 'auto', budget: 'int' = 50, workers: 'int' = 1, confidence: 'float' = 0.9, uncertainty: 'Any' = None, holdout_seeds: 'Optional[int]' = None, inputs: 'Optional[Mapping[str, Any]]' = None, arm: 'Optional[str]' = None, participants: 'Any' = None, rounds: 'Optional[int]' = None, data_dir: 'Any' = None, hosts: 'Any' = None) -> 'OptimisationResult'
```

Search ``decisions`` for the best ``objective`` subject to ``constraints`` (see the module notes).

``decisions``: ``{input: {low, high, step?} | [values] | {length|keys, low, high, step?, monotone?, sum?}}``.
``objective``: ``"maximise margin"``, ``"minimise p90 of cost"``, or a list of two or three for a Pareto frontier.
``constraints``: ``["fill_rate >= 0.95", "sl >= 0.8 in 90% of runs", "each sl_by_interval >= 0.8"]``, each held
with ``confidence`` unless it says "with 95% confidence". ``runs`` seeds judge each decision; ``budget`` caps the
distinct decisions searched; ``method``: auto, grid, random, lhs, local, race, nelder_mead, cross_entropy or (for a
frontier) frontier. ``holdout_seeds`` (default ``runs``; 0 skips it) fresh seeds check the choice. ``inputs`` and
``arm`` fix everything else; ``uncertainty`` draws parameters per run.

## `fit_patterns`

```python
fit_patterns(contract: 'ContractLike', *, data_dir: 'Union[str, Path, None]' = None, inputs: 'Optional[Mapping[str, Any]]' = None) -> 'FitResult'
```

Estimate every pattern that declares ``fit`` and return the contract with the estimates written back.

Data files are read from ``data_dir`` (default: the contract file's folder). ``inputs`` are used while fitting
(e.g. which history table to read) and are not written into the result.

## `FitResult`

```python
FitResult(contract: 'Dict[str, Any]', fits: 'List[PatternFit]', priors: 'Dict[str, Dict[str, Any]]' = <factory>) -> None
```

The fitted contract (data, like the one given), a report per pattern, and the estimates as priors.

## `decompose`

```python
decompose(source: 'Union[ContractLike, Any]', pattern: 'str', *, key: 'Any' = None, rounds: 'Optional[Union[int, Sequence[int]]]' = None, inputs: 'Optional[Mapping[str, Any]]' = None, seed: 'int' = 0, data_dir: 'Any' = None, estimates: 'bool' = False) -> 'Decomposition'
```

Decompose a ``product`` or ``sum`` pattern into its factors (see the module).

``source`` is a contract (read over ``rounds``: a count from round 1, or a list of rounds; default every round of
the clock) or a run (read now). ``key`` is required when the pattern has keys. ``estimates`` reads a contract's
fitted parameters at their estimates, without the draws their standard errors (``uncertainty``) would make.

## `Decomposition`

```python
Decomposition(pattern: 'str', key: 'Optional[str]', kind: 'str', rows: 'List[Dict[str, Any]]' = <factory>) -> None
```

Each round: the total, every factor's value, and what each factor adds.

## `report`

```python
report(source: 'Any', audience: 'str' = 'owner', *, contract: 'Optional[ContractLike]' = None, validation: 'Optional[ValidationResult]' = None, optimisation: 'Optional[OptimisationResult]' = None, objective: 'Optional[str]' = None, require: 'Optional[Mapping[str, Any]]' = None, control: 'Optional[str]' = None, data_dir: 'Any' = None) -> 'Report'
```

A plain-language report of ``source``: a :class:`~fg_env.RunResult` (or a list of them), an experiment, a sweep,
a validation or an optimisation (see the module). ``contract`` adds names, arm descriptions, assumptions and
pattern decompositions; ``validation`` adds how well the model matched the data; ``optimisation`` adds how the
decision an experiment plays was found; ``objective`` and ``require`` choose the recommended option; ``control``
is the arm differences are measured against (default: the first).

## `Report`

```python
Report(title: 'str', audience: 'str', kind: 'str', sections: 'List[Section]', recommendation: 'Optional[Dict[str, Any]]' = None, notes: 'List[str]' = <factory>) -> None
```

Report(title: 'str', audience: 'str', kind: 'str', sections: 'List[Section]', recommendation: 'Optional[Dict[str, Any]]' = None, notes: 'List[str]' = <factory>)

## `score`

```python
score(forecasts: 'Sequence[Any]', outcomes: 'Sequence[Any]', *, kind: 'str' = 'auto', climatology: 'Any' = None, bins: 'int' = 10, nominal: 'Optional[float]' = None, epsilon: 'float' = 1e-15) -> 'Dict[str, Any]'
```

Every applicable score for a set of forecasts against what happened.

``kind``: ``binary`` (probabilities of a yes/no event), ``categorical`` (mappings of
category → probability), ``ensemble`` (lists of simulated numbers), ``interval``
([low, high] pairs), or ``auto`` (``interval`` must be named: a two-member ensemble looks
the same). ``climatology`` is the reference forecast for the skill score: a base rate
(binary), a category distribution (categorical) or a sample of numbers (ensemble). Without
it the reference is the outcomes' own frequency — in-sample, and labelled as such.

## `backtest`

```python
backtest(contract: 'ContractLike', cases: 'Sequence[Mapping[str, Any]]', output: 'str', *, runs: 'int' = 10, threshold: 'Optional[float]' = None, climatology: 'Any' = None, arm: 'Optional[str]' = None, participants: 'Any' = None, rounds: 'Optional[int]' = None, seed: 'int' = 0, workers: 'int' = 1, bins: 'int' = 10, test: 'Any' = None, folds: 'Optional[int]' = None, data_dir: 'Any' = None, hosts: 'Any' = None, uncertainty: 'Any' = None) -> 'BacktestResult'
```

Score the contract's forecasts of ``output`` against each case's known ``outcome``.

``cases``: ``[{"inputs": {...}, "outcome": value, "name"?: text, "arm"?: text}]``. Outcome
types pick the forecast: yes/no → the share of runs where the output is true (or above
``threshold`` for a numeric output); numbers → the ensemble of run values (CRPS, coverage;
with ``threshold`` the numbers become yes/no events); text → the frequency of each output
value. Every case uses the same seeds.

Skill compares the forecasts with a climatology, which by default comes from the same cases'
outcomes (in sample). ``test`` (a share, or a list of case names) or ``folds`` (k-fold) also score
the held-out cases against a climatology built only from the other cases: out-of-sample skill.
``data_dir`` is where inputs with a ``source`` are read (default: the contract file's folder); ``hosts``
answers host requests (feeds, judges) in every run.

## `validate`

```python
validate(contract: 'ContractLike', cases: 'Sequence[Mapping[str, Any]]', *, runs: 'int' = 10, levels: 'Sequence[float]' = (0.8, 0.95), season: 'Optional[int]' = None, baselines: 'Sequence[str]' = ('last', 'mean', 'seasonal'), test: 'Any' = None, arm: 'Optional[str]' = None, participants: 'Any' = None, rounds: 'Optional[int]' = None, seed: 'int' = 0, workers: 'int' = 1, data_dir: 'Any' = None, hosts: 'Any' = None, uncertainty: 'Any' = None) -> 'ValidationResult'
```

Check the contract's forecasts against each case's ``actuals`` (see the module notes).

``cases``: ``[{"name"?, "inputs"?, "arm"?, "actuals": {measure: number | {key: number} | [numbers]}}]`` in time
order; a measure is an output or a metric (its final value). ``levels`` are the nominal interval coverages checked.
Baselines forecast each key from earlier cases: ``last``, ``mean`` and ``seasonal`` (the value ``season`` cases
back; needs ``season``). ``test`` (a share or case names) also scores the held-out cases on their own.
``uncertainty`` (a calibration, points or priors: :mod:`.draws`) draws parameters per run, so intervals include
not knowing them.

## `precision`

```python
precision(contract: 'ContractLike', output: 'str', *, target_se: 'Optional[float]' = None, relative_se: 'Optional[float]' = None, max_runs: 'int' = 100, batch: 'int' = 5, min_runs: 'Optional[int]' = None, inputs: 'Optional[Mapping[str, Any]]' = None, arm: 'Optional[str]' = None, participants: 'Any' = None, rounds: 'Optional[int]' = None, seed: 'int' = 0, workers: 'int' = 1, level: 'float' = 0.95, data_dir: 'Any' = None, hosts: 'Any' = None) -> 'PrecisionResult'
```

Add runs ``batch`` at a time until the standard error of ``output``'s mean is small enough.

Give ``target_se`` (in output units) or ``relative_se`` (a share of |mean|). A yes/no output
is a proportion: its standard error is the Wilson interval's half-width over z, which never
reaches 0 by luck at 0% or 100%. At least ``min_runs`` (default two batches) run before the
target can be declared met, so a few identical early runs cannot end the search.

## `behavior_checks`

```python
behavior_checks(contract: 'ContractLike', *, runs: 'int' = 4, rounds: 'Optional[int]' = None, seed: 'int' = 0, participants: 'Any' = 'random', inputs: 'Optional[Mapping[str, Any]]' = None, test_inputs: 'Optional[Sequence[str]]' = None, perturb: 'float' = 0.5, workers: 'int' = 1, data_dir: 'Any' = None, hosts: 'Any' = None, boundaries: 'bool' = False, max_boundary_cases: 'int' = 24) -> 'CheckReport'
```

Run ``runs`` seeds with random agents (plus one set per varied input) and report findings.

``test_inputs`` limits which inputs are varied (default: every number, whole number, yes/no
and choice input); each is moved by ±``perturb`` of its value (flipped, or set to another
choice) on the same seeds, so any difference comes from the input. ``rounds`` caps each run:
shorter runs are faster but can miss behaviour that only appears later, which the findings say.
``data_dir`` is where inputs with a ``source`` are read (default: the contract file's folder); ``hosts`` answers
host requests in every run.
``boundaries=True`` also samples declared zero/min/max values, choices, and empty/short collections,
including fields in the first row/item. It varies configured inputs too, up to ``max_boundary_cases``
single-input cases, including reordered tables and an added duplicate row. Findings include
replayable paths/values and the sampling limit. This is not an
exhaustive combination search or evidence that the business model matches its brief.

## `highlights`

```python
highlights(result: 'RunResult', *, top: 'int' = 5, metrics: 'Optional[Sequence[str]]' = None) -> 'List[Highlight]'
```

The ``top`` most notable moments of a run, most surprising first (ties: earliest first).

``metrics`` limits which metric series are scanned (default: all). Event-based moments need
the run's event log (``RunResult.events``).

## `narrative`

```python
narrative(result: 'RunResult', *, limit: 'int' = 10) -> 'str'
```

A compact factual account: how the run went, its notable moments in order — each with what happened around it
when the run shows it — and its results. Rounds are named in the clock's terms (``Week 7 (2026-10-12): …``,
``09:30–10:00: …``); moments no more unusual than the run's usual ups and downs are left out.

## `drivers`

```python
drivers(runs: 'Any', output: 'str', *, focus: 'Any' = None, threshold: 'Optional[float]' = None, include: 'Sequence[str]' = ('inputs', 'metrics', 'outputs', 'actions', 'end'), permutations: 'int' = 500, alpha: 'float' = 0.05, top: 'int' = 10, seed: 'int' = 0) -> 'DriversResult'
```

Features that separate runs where ``output`` hits the focus from runs where it does not.

Focus: yes/no outputs → true; numbers → above the median (or ``threshold``); text → the most
common value (or ``focus``). ``include`` picks feature groups: inputs (and arm), metrics
(final value and peak), other outputs, actions (successful count, from event logs), end
(how the run ended, winner). Deterministic for a given ``seed``.

## `compare`

```python
compare(a: 'Any', b: 'Any', *, labels: 'Tuple[str, str]' = ('a', 'b'), level: 'float' = 0.95) -> 'Comparison'
```

Compare results, matching shared unique seeds and excluding invalid outputs per pair.

Unmatched seeds are omitted when shared seeds exist; disjoint samples use an
independent comparison. Notes identify exclusions and numeric rows give sample sizes.

## `chain`

```python
chain(first: 'ContractLike', second: 'ContractLike', bind: 'Mapping[str, str]', *, runs: 'int' = 10, level: 'float' = 0.9, uncertainty: 'bool' = True, first_inputs: 'Optional[Mapping[str, Any]]' = None, second_inputs: 'Optional[Mapping[str, Any]]' = None, participants: 'Any' = None, second_participants: 'Any' = None, rounds: 'Optional[int]' = None, second_rounds: 'Optional[int]' = None, seed: 'int' = 0, workers: 'int' = 1, data_dir: 'Any' = None, second_data_dir: 'Any' = None, hosts: 'Any' = None) -> 'ChainResult'
```

Run ``first``, bind its outputs into ``second``'s inputs (``{second_input: first_output}``), run ``second``.

Each bound output is summarised by its mean (point) and the central ``level`` range of its
run values (lower, upper); yes/no outputs become the share of runs. ``second`` runs at the
point estimates and, with ``uncertainty``, at all-lower and all-upper bindings too. Values
outside a bound input's declared range are clamped, with a note. ``data_dir`` / ``second_data_dir`` are where
each contract's inputs with a ``source`` are read (default: each contract file's folder); ``hosts`` answers host
requests in the runs of both.

## `tournament`

```python
tournament(contract: 'ContractLike', entrants: 'Mapping[str, Any]', *, seats: 'Optional[Sequence[str]]' = None, pairing: 'str' = 'round_robin', games: 'int' = 1, score: 'ScoreSpec' = None, rating: 'str' = 'elo', swiss_rounds: 'Optional[int]' = None, others: 'Any' = None, inputs: 'Optional[Mapping[str, Any]]' = None, arm: 'Optional[str]' = None, rounds: 'Optional[int]' = None, seed: 'int' = 0, workers: 'int' = 1, data_dir: 'Any' = None, budget: 'Optional[Mapping[str, Any]]' = None, exposures: 'bool' = False) -> 'TournamentResult'
```

Play ``entrants`` (``{name: participant}``) against each other in the contract's ``seats``.

``seats`` are agent entity ids (default: every agent the contract starts with). ``pairing``:
``round_robin`` (every group of entrants, rotated so each sits in every seat equally), ``all_play_all``
(every group in every seat order) or ``swiss`` (``swiss_rounds`` rounds, default ⌈log₂ entrants⌉, of
tables drawn by points while avoiding rematches; an entrant left over sits out with a bye worth a win).

Duplicate deals: every seating plays ``games`` games and game *g* uses the same seed at every table
and in every rotation (the seeds of :func:`fg_env.experiment`). The world's chance is drawn from the
seed, so the same cards, dice and events meet each entrant in each seat, and luck cancels out.

``score`` ranks a game's seats, higher is better: the winner (default), an output (a map of seat →
number, or a winner), an expression over ``$outputs``, ``$metrics``, ``$winner``, ``$seat`` and
``$seat_name``, or ``fn(result, seat_id)``. Agents without a seat play ``others`` (default: their
type's policy, else random).

Standings are ranked by ``rating``: ``elo`` (maximum-likelihood, with 95% intervals) or ``glicko2``;
both are reported, with win/draw/loss, points and score means. ``evaluation`` adds the Nash average,
α-Rank and a Schulze vote, which stay meaningful when skill is not transitive; ``returns`` gives every
entrant's score in every seat, and each standing's ``cost`` its turns, calls, invalid calls, timeouts,
undone turns and model tokens. A callable entrant is shared by all its games: with ``workers > 1`` those run in threads at once.
``budget`` caps each game on its own (:mod:`fg_env.sdk.budget`); ``exposures=True`` records what agents saw in
every game (``result.runs[i].exposures``, events kept), each a trace to read or replay.

## `describe`

```python
describe(contract: 'ContractLike', *, inputs: 'Optional[Mapping[str, Any]]' = None, arm: 'Optional[str]' = None, data_dir: 'Any' = None) -> 'Description'
```

Describe ``contract`` (with ``inputs`` and ``arm`` applied). Counts that need the built world (players,
entity choices, rounds given by an expression) come from building it once with seed 0; when it cannot be
built — a required input is missing, say — they are reported as unknown with the reason.

## `trace`

```python
trace(source: 'TraceSource') -> "'Trace'"
```

A recorded run to read: a :class:`RunResult`, its ``to_dict()``, or a file written by ``result.save()``.

The run must have recorded exposures: ``fg_env.run(..., exposures=True)``.

## `evaluate`

```python
evaluate(suite: 'Any', *, focal: 'Any', background: 'Any' = None, baseline: 'Any' = None, seats: 'Any' = None, score: 'ScoreSpec' = None, modes: 'Optional[Mapping[str, float]]' = None, inputs: 'Optional[Mapping[str, Any]]' = None, arm: 'Optional[str]' = None, runs: 'int' = 10, rounds: 'Optional[int]' = None, budget: 'Optional[Mapping[str, Any]]' = None, seed: 'int' = 0, workers: 'int' = 1, exposures: 'bool' = False) -> 'EvaluationResult'
```

How ``focal`` does among ``background`` agents, compared with ``baseline`` in the same seats on the same seeds.

``suite`` is a contract, a list of scenarios, or a suite file (see :mod:`fg_env.sdk.evaluate.suite`); the other
arguments are defaults for scenarios that leave them out. ``seats`` are the agents the focal participant may
take (ids, or a type; default every starting agent). ``modes`` maps a mode name to the share of those seats the
focal participant takes (``{"resident": 0.75, "visitor": 0.25}``; default ``{"all": 1.0}``); which seats is
drawn from the seed, so every candidate evaluated with the same seed meets the same draw. ``background`` plays
every other agent (default: its type's policy, else random); ``baseline`` plays the focal seats in the paired
runs (default: the background). ``score`` scores each seat as in :func:`fg_env.tournament`: by default the
returns the contract's ``game`` section declares, else the winner; or an output, an expression over ``$seat``,
or ``fn(result, seat)``.

Run *i* of every scenario uses the seeds of :func:`fg_env.experiment`. A focal run's score is the mean over its
focal seats (per focal agent), and its difference is that minus the baseline run's score over the same seats.
``budget`` caps each run on its own; ``exposures=True`` records what agents saw in every run. ``results`` keeps
every run: pair *i* is ``results[2i]`` (focal) and ``results[2i + 1]`` (baseline). Callable participants are
shared by all their runs; with ``workers > 1`` runs go to threads, or to processes when every participant is
given by name.

## `conformance`

```python
conformance(source: 'Union[ContractLike, Game]', *, sims: 'int' = 20, seed: 'int' = 0, inputs: 'Optional[Mapping[str, Any]]' = None, simultaneous: 'str' = 'joint', leak_branches: 'int' = 2, max_steps: 'int' = 1000, resume: 'bool' = True) -> 'ConformanceReport'
```

Check a game (a contract, or a :class:`Game` from :func:`fg_env.game`) over ``sims`` seeded random playouts.

``simultaneous="turn_based"`` checks the one-seat-at-a-time view of simultaneous stages, where the leak test
also covers sealed choices. ``leak_branches`` is how many steps of each playout are changed for the leak test;
``max_steps`` is the longest a playout may run; ``resume=False`` skips the whole-run resume check.

## `playthrough`

```python
playthrough(source: 'Union[ContractLike, Game]', *, seed: 'int' = 0, steps: 'Optional[Sequence[Mapping[str, Any]]]' = None, inputs: 'Optional[Mapping[str, Any]]' = None, simultaneous: 'str' = 'joint', max_steps: 'int' = 500) -> 'str'
```

The playthrough text of one game: ``steps`` when given (see :mod:`.steps`), else random ones from ``seed``.

## `pettingzoo_aec`

```python
pettingzoo_aec(source: 'ContractLike', *, inputs: 'Optional[Mapping[str, Any]]' = None, seed: 'Optional[int]' = None, max_steps: 'Optional[int]' = None, render_mode: 'Optional[str]' = None) -> 'AECGame'
```

A contract as a PettingZoo AEC environment (simultaneous stages one seat at a time; needs game.returns).

## `pettingzoo_parallel`

```python
pettingzoo_parallel(source: 'ContractLike', *, inputs: 'Optional[Mapping[str, Any]]' = None, seed: 'Optional[int]' = None, max_steps: 'Optional[int]' = None, render_mode: 'Optional[str]' = None) -> 'ParallelGame'
```

A contract as a PettingZoo parallel environment (needs game.returns).

## Environment methods

### `Env.preview`

```python
preview(self, entity_id: 'str', stage: 'Optional[str]' = None) -> 'Dict[str, Any]'
```

What the agent would receive on its next turn: brief, update, tools and time limit. Changes nothing.

Between rounds this plays the next round on a copy up to the agent's turn — scheduled
effects, start events, physics and the turns of agents before it (with their built-in
or named participants; your own callables are never called) — so the preview shows the
turn as the agent will get it.

### `Env.run`

```python
run(self, participants: 'Any' = None, *, rounds: 'Optional[int]' = None, stop: "Optional[Callable[['Env'], bool]]" = None, on_event: 'Optional[Callable[[Dict[str, Any]], None]]' = None, raise_errors: 'bool' = False, hosts: 'Any' = None, time_limit: 'Optional[float]' = None, budget: 'Optional[Mapping[str, Any]]' = None) -> 'RunResult'
```

Run to the end, or for ``rounds`` more rounds, or until ``stop(env)`` is true.

``participants`` is a callable for every agent, or a mapping from entity id, type or
``"*"`` to a participant (a callable — plain or ``async def`` — ``"random"``, ``"idle"``,
``"policy:<name>"``). Agents without one use their type's ``policy`` or ``"random"``. Every
participant is offered the contract's in-turn host tools; ``hosts`` binds the run to host
adapters first. ``time_limit`` sets :attr:`time_limit`, the wall-clock seconds per turn for
stages that set none; ``budget`` caps the run (:mod:`fg_env.sdk.budget`). In an event loop, use :meth:`arun`.

``stop`` is checked before every round, stage, pass and sequential turn. A stopped run
continues exactly where it stopped on the next call; finishing a round that was
stopped part-way counts as one of ``rounds``.

### `Env.arun`

```python
arun(self, participants: 'Any' = None, *, rounds: 'Optional[int]' = None, stop: "Optional[Callable[['Env'], bool]]" = None, on_event: 'Optional[Callable[[Dict[str, Any]], None]]' = None, raise_errors: 'bool' = False, hosts: 'Any' = None, time_limit: 'Optional[float]' = None, budget: 'Optional[Mapping[str, Any]]' = None) -> 'RunResult'
```

:meth:`run` as a coroutine, for use inside a running event loop.

Async participants run on this loop — so clients bound to it work — and a simultaneous
stage's async participants run concurrently. The engine itself runs in a worker thread, so
the loop stays free while it plays; ``stop`` and ``on_event`` are called from that thread.
Cancelling the call stops the run at its next safe point.

### `Env.step`

```python
step(self, participants: 'Any' = None) -> 'RunResult'
```

Run exactly one round (or finish the round a stopped run is in).

### `Env.snapshot`

```python
snapshot(self) -> 'Dict[str, Any]'
```

Everything needed to continue this run later, as JSON-safe data (between rounds).

### `Env.restore`

```python
restore(contract: 'Any', snapshot: 'Mapping[str, Any]', parallel: 'int' = 8, hosts: 'Any' = None, data_dir: 'Any' = None) -> "'Env'"
```

Continue a run from :meth:`snapshot`. ``contract`` is the contract it was taken with
(a :class:`Contract`, dict, path or JSON text; the snapshot's arm is applied if needed).
``hosts`` answers host judgment; answers already recorded in the snapshot are never asked again.
The contract's files are found again in its folder (or ``data_dir``) and checked against the recorded hashes.

### `Env.clone`

```python
clone(self: "'Env'") -> "'Env'"
```

An independent copy of this run now, continuing exactly as it would.

Between rounds it is a restored snapshot; a run stopped part-way through a round (``run(stop=...)``) is
copied by replaying it, so the copy stops at the same point. Inside a turn, use ``wake.clone()``.

### `Env.fork`

```python
fork(self: "'Env'", **changes: 'Any') -> "'Env'"
```

A new run continuing this one from now under changes, leaving this run untouched: another ``arm``
(``None`` for none), ``inputs``, a contract ``patch`` or a whole replacement ``contract``, a ``seed`` for
the luck from here on, and intervention ``effects`` applied at the fork (logged as a `fork` event,
invariants checked). Without changes it is :meth:`clone`.

Changes apply between rounds. Whatever the changed contract cannot hold of the current state is refused
with a :class:`~fg_env.ContractError` listing each problem and its fix. See :func:`fg_env.fork`.

### `Env.entity`

```python
entity(self, entity_id: 'str') -> 'Optional[Dict[str, Any]]'
```

A copy of one entity: ``{id, name, type, alive, at, props}``, or None.

### `Env.entities`

```python
entities(self, type_name: 'Optional[str]' = None, alive: 'bool' = True) -> 'List[Dict[str, Any]]'
```

Copies of entities, optionally of one type (subtypes included) and only alive ones.

### `Env.result`

```python
result(self) -> 'RunResult'
```



### `Env.spectate`

```python
spectate(self) -> 'Dict[str, str]'
```

Every spectator view (``"for": "spectator"``) rendered against the world now, by name. Changes
nothing: views that draw randomness use a stream of their own.

## Detailed runtime behavior

See [running](reference-running.md) for participants, budgets, traces, snapshots and experiments.
