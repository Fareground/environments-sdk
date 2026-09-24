# Python API reference

Generated from public exports in `fg_env`. Start with `check`, `load`, `run` and `experiment`; everything else is in a subpackage below. A source may be a contract dictionary, JSON text, file path or parsed `Contract`.

## Entry points

## `load`

```pyi
load(source: 'ContractLike', *, inputs: 'Mapping[str, Any] | None' = None, seed: 'int | None' = None, arm: 'str | None' = None, strict: 'bool' = False, parallel: 'int' = 8, data_dir: 'DataDir' = None, hosts: 'Any' = None, exposures: 'bool' = False, chance: 'Any' = None, calibrate: 'bool' = True, events: 'bool' = True) -> 'Env'
```

Check a contract and build a runnable :class:`Env`.

Errors raise :class:`ContractError` listing every problem with a fix; ``strict=True`` also rejects warnings.
``seed`` defaults to a fresh one (readable as ``env.seed``). Inputs with a ``source`` and the contract's ``assets``
read their files from ``data_dir`` (default: the contract file's folder). ``hosts`` (a :class:`~fg_env.host.Hosts`
or a mapping of host name to adapter) answers the judgment the contract asks of a host; build-time host work
(personas) is done before round 1. ``exposures=True`` records what every agent was shown on every wake
(``result.exposures``); a contract that calls ``$seen`` records it anyway. ``chance`` decides `chance` effects:
``"sampled"`` (the default: drawn from the seeded stream) or a callable given each
:class:`~fg_env.effects.chance.ChanceNode` that returns the index of the outcome to take (a fixed deal, duplicate
formats); :func:`fg_env.rl.game` enumerates chance for search. A contract with a ``calibration`` section fits its
inputs with pilot sessions first (``env.calibration`` is the report); ``calibrate=False`` skips that, as
``fg_env.check``'s smoke play does. ``events=False`` keeps no event log, for a big crowd played for many rounds:
``result.events`` is empty (``on_event`` still streams every event) and the run forgets each event once no agent's
news can reach it, so its memory stays flat however long it plays; everything the run does is the same (a contract
that reads `$events` or `$seen` keeps its log).

## `run`

```pyi
run(source: 'ContractLike', participants: 'Any' = None, *, inputs: 'Mapping[str, Any] | None' = None, seed: 'int | None' = None, arm: 'str | None' = None, rounds: 'int | None' = None, on_event: 'Any' = None, strict: 'bool' = False, data_dir: 'DataDir' = None, hosts: 'Any' = None, time_limit: 'float | None' = None, exposures: 'bool' = False, budget: 'Mapping[str, Any] | None' = None, events: 'bool' = True) -> 'RunResult'
```

Load and run in one call: ``fg_env.run("shop.json", {"buyer": "policy:thrifty"}, seed=1)``.

A run that fails — a rule that cannot be evaluated, a participant that raises, a model provider that refuses the
request — raises :class:`RunError` saying what failed and how to fix it; its ``result`` is the failed run.
(``env.run`` returns a failed run instead, and experiments keep failed runs and carry on.)

## `check`

```pyi
check(source: 'ContractLike', rounds: 'int | None' = None, seed: 'int' = 0, *, data_dir: 'DataDir' = None, hosts: 'Any' = None, inputs: 'Mapping[str, Any] | None' = None) -> 'list[Issue]'
```

Every problem in a contract, errors first then warnings. Never raises for contract problems: a missing file
or text that is not JSON is an issue too.

A contract without errors is also built and played, so problems that only appear with real values (sampling, later
rounds, views, outputs, a policy's own rules) are reported the same way: once with random agents that read
everything they are shown, once with agents that choose boundary values (a parameter's least value, zero, its
greatest), once with every agent idle (a turn that passes without an action, as when a model times out or refuses,
must not break the rules), then once per declared policy, played by the agent types whose default it is (or else
those that can take every action it takes). An action called in these plays that never once succeeded is reported
too. By default each play lasts up to 12 rounds (fewer when the run ends sooner) and all of them share a few
seconds; ``rounds`` plays exactly that many rounds instead (0 checks statically only). Inputs with a ``source`` are
read from ``data_dir`` (default: the contract file's folder); ``hosts`` answers what the contract asks of a host
during those plays. ``inputs`` checks a configured scenario without editing its defaults; supplied inputs are
validated even with ``rounds=0``, and the plays exercise them.

## `parse`

```pyi
parse(source: 'ContractLike', data_dir: 'DataDir' = None) -> 'Contract'
```

Read and structurally validate a contract (dict, path, JSON text or :class:`Contract`).

The contract remembers where its input data files are read from: ``data_dir`` when given, else the
contract file's folder, so every run, check and analysis of it finds them.

## `expand`

```pyi
expand(source: 'ContractLike', *, mechanisms: 'bool' = False) -> 'dict[str, Any]'
```

The contract data the engine reads: imports merged and macros expanded (and, with
``mechanisms=True``, every mechanism expanded into ordinary sections too; the ``mechanisms`` block stays,
since the generated effects read their config there, and loading the result again changes nothing).

Raises :class:`ContractError` for problems found while expanding; ``check`` reports the rest.

## `experiment`

```pyi
experiment(source: 'ContractLike', *, runs: 'int' = 10, arms: 'list[str] | None' = None, seed: 'int' = 0, inputs: 'Mapping[str, Any] | None' = None, participants: 'Any' = None, participants_for: 'Callable[[int, str | None], Any] | None' = None, rounds: 'int | None' = None, workers: 'int' = 1, data_dir: 'Any' = None, branch_at: 'int | None' = None, budget: 'Mapping[str, Any] | None' = None, exposures: 'bool' = False, hosts: 'Any' = None, uncertainty: 'Any' = None) -> 'ExperimentResult'
```

Run each arm ``runs`` times. Run *i* uses the same seed in every arm, so differences
between arms come from the arm, not from luck. ``arms`` defaults to every declared arm
(or a single baseline run set when none are declared). ``participants_for(i, arm)``
builds fresh participants per run when they hold state.

``branch_at=N`` shares history: run *i* plays its first N rounds once, without an arm, and every
arm continues from that same state (a fork: the arm's patch and inputs apply from round N + 1, and
an arm whose patch the state cannot follow raises before the experiment goes on).

``budget`` caps each run on its own (:mod:`fg_env.runtime.budget`); with ``branch_at`` the shared rounds are part of
every arm's run, so they count toward each arm's budget. ``exposures=True`` records what agents saw in every run
(``result.arms[label].runs[i].exposures``, events kept): each run is a trace to read or replay.
``data_dir`` is where inputs with a ``source`` are read (default: the contract file's folder); ``hosts``
answers the contract's host requests in every run. ``uncertainty`` (a calibration, a list of points or priors;
:mod:`fg_env.analysis.draws`) draws parameters per run, the same for run *i* in every arm, so the spread of
outcomes includes not knowing them.

Problems shared by every run (an unknown arm, bad inputs, an unknown participant) raise
before anything runs. A run that fails on its own is kept with ``status="failed"`` and its
error, and the rest of the experiment carries on.

## `fork`

```pyi
fork(contract: 'ContractLike', snapshot: 'Mapping[str, Any]', *, arm: 'Any' = KEEP_ARM, inputs: 'Mapping[str, Any] | None' = None, patch: 'Mapping[str, Any] | None' = None, to: 'ContractLike | None' = None, seed: 'int | None' = None, effects: 'list[Any] | None' = None, parallel: 'int' = 8, hosts: 'Any' = None, data_dir: 'str | os.PathLike[str] | None' = None) -> 'Env'
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

## `Branch`

```pyi
Branch(pilot: 'Pilot')
```

A private copy of a run that you drive: decide for the agents it pauses for, let it play on, read it.

Everything runs on the copy's own thread; its methods wait for it. Close it (or use ``with``, or let
it be garbage collected) to discard it.

## `guide`

```pyi
guide(part: 'str | None' = None) -> 'str'
```

The map of every part, or one part by name: a section (``"actions"``), a topic (``"expressions"``, ``"effects"``,
``"functions"``, ``"mechanisms"``, ``"patterns"``, ``"recipes"``, ``"running"`` …), a function group
(``"functions.stats"``), a mechanism family (``"market"``) or mode (``"market.auction"``) — or ``"all"`` for
everything. With no part, the map of every part; start with ``guide("authoring")``.

## `schema`

```pyi
schema() -> 'dict[str, Any]'
```

JSON Schema of the contract (structure only; ``fg_env.check`` verifies meaning).

## `new`

```pyi
new(template: 'str' = 'blank', path: 'str | os.PathLike[str] | None' = None, *, name: 'str | None' = None, overwrite: 'bool' = False) -> 'dict[str, Any]'
```

A ready-to-run contract from a template (blank, duel, shop, simulation, meeting).

With ``path`` it is also written there as JSON (an existing file is kept unless ``overwrite``); ``name``
replaces the contract's name (default: the template's, or the file name when a path is given).

## `author`

```pyi
author(brief: 'str', model: 'str', *, client: 'Any' = None, out: 'str | None' = None, budget: 'Mapping[str, float] | None' = None, progress: 'Callable[[str], None] | None' = None) -> 'AuthorResult'
```

Have ``model`` (``"anthropic:<model>"`` or ``"openai:<model>"``) write an environment for ``brief``; returns an
:class:`AuthorResult` (``result.contract``, ``result.ok``, ``result.summary()``).

``out`` is where the contract is written (nothing is written when None): each time a revision is kept, and at the
end; when none works, the latest is written beside it as ``<name>.not-working.json``. ``budget`` caps ``tokens``
(input, output and cache writes in full, a cache read counting :data:`CACHED_WEIGHT` of one, as a run's token
budget counts them), model ``calls`` and wall-clock ``seconds``, by default :data:`DEFAULT_BUDGET`; the model is
told these limits, the revision limit and the test time of a save up front. ``client`` replaces the official client
made from the environment; ``progress`` is called with one line per model call. Rate limits, overload, server
errors and empty replies are retried with backoff, never waiting past the ``seconds`` budget; a provider error that
persists or that retrying cannot fix does not raise: the loop stops (``result.stop`` says why) and keeps what
already works.

## `Env`

```pyi
Env(contract: 'Contract', inputs: 'dict[str, Any]', seed: 'int', arm: 'str | None' = None, parallel: 'int' = 8, exposures: 'bool' = False, assets: 'AssetStore | None' = None, events: 'bool' = True)
```

A loaded environment. Create with :func:`fg_env.load`; run with :meth:`run`; copy with :meth:`clone`
and :meth:`fork`.

## `Contract`

```pyi
Contract(*, fg_env: str = '1', name: str, description: str = '', imports: list[str] = <factory>, brief: fg_env.contract.world.Brief = <factory>, assets: dict[str, fg_env.assets.spec.AssetSpec] = <factory>, inputs: dict[str, fg_env.contract.world.InputSpec] = <factory>, clock: fg_env.contract.world.Clock = <factory>, space: fg_env.contract.world.Space | None = None, world: dict[str, fg_env.contract.world.PropSpec] = <factory>, types: dict[str, fg_env.contract.world.TypeSpec], entities: dict[str, fg_env.contract.world.EntitySpec] = <factory>, population: list[fg_env.contract.world.PopulationSpec] = <factory>, relations: dict[str, fg_env.contract.world.RelationSpec] = <factory>, links: list[fg_env.contract.world.LinkSpec] = <factory>, physics: fg_env.contract.world.PhysicsSpec | None = None, feeds: dict[str, fg_env.contract.world.FeedSpec] = <factory>, patterns: dict[str, dict[str, typing.Any]] = <factory>, records: dict[str, fg_env.contract.rules.RecordSpec] = <factory>, actions: dict[str, fg_env.contract.rules.ActionSpec] = <factory>, stages: list[fg_env.contract.rules.StageSpec] = <factory>, views: dict[str, fg_env.contract.rules.ViewSpec] = <factory>, events: list[fg_env.contract.rules.EventSpec] = <factory>, triggers: list[fg_env.contract.rules.TriggerSpec] = <factory>, policies: dict[str, fg_env.contract.rules.PolicySpec] = <factory>, metrics: dict[str, fg_env.contract.measure.MetricSpec] = <factory>, outputs: dict[str, fg_env.contract.measure.OutputSpec] = <factory>, end: list[fg_env.contract.measure.EndSpec] = <factory>, arms: dict[str, fg_env.contract.measure.ArmSpec] = <factory>, calibration: fg_env.contract.measure.CalibrationSpec | None = None, game: fg_env.contract.game.GameSpec | None = None, invariants: list[fg_env.contract.measure.InvariantSpec] = <factory>, defs: dict[str, fg_env.contract.measure.DefSpec] = <factory>, blocks: dict[str, fg_env.contract.measure.BlockSpec] = <factory>, mechanisms: dict[str, dict[str, typing.Any]] = <factory>) -> None
```

An environment: world, people, rules, what agents see, what is measured.

## `RunResult`

```pyi
RunResult(status: 'str', ended_by: 'str | None', rounds: 'int', seed: 'int', arm: 'str | None', inputs: 'dict[str, Any]', outputs: 'dict[str, Any]', metrics: 'dict[str, Any]', series: 'dict[str, list[Any]]', winner: 'Any' = None, error: 'str | None' = None, time: 'float | None' = None, returns: 'dict[str, float]' = <factory>, output_issues: 'list[dict[str, Any]]' = <factory>, stats: 'dict[str, Any]' = <factory>, agent_stats: 'dict[str, dict[str, Any]]' = <factory>, events: 'list[dict[str, Any]]' = <factory>, exposures: 'dict[str, Any]' = <factory>, frames: 'list[dict[str, Any]]' = <factory>, host_tape: 'dict[str, Any]' = <factory>, budget: 'dict[str, Any]' = <factory>, formats: 'dict[str, str]' = <factory>, diagnostics: 'list[dict[str, str]]' = <factory>, clock: 'dict[str, Any]' = <factory>, assets: 'dict[str, Any]' = <factory>, state: 'dict[str, Any]' = <factory>) -> None
```

Everything a run produced. ``outputs`` follows the contract's output contract.

## `ExperimentResult`

```pyi
ExperimentResult(arms: 'dict[str, ArmResult]', seeds: 'list[int]', rounds: 'int | None' = None) -> None
```

ExperimentResult(arms: 'dict[str, ArmResult]', seeds: 'list[int]', rounds: 'int | None' = None)

## `Wake`

```pyi
Wake(turn: 'Turn')
```

One agent's turn. Obtained from the runtime; never constructed directly.

## `ToolResult`

```pyi
ToolResult(ok: 'bool', text: 'str', ended: 'bool' = False, data: 'dict[str, Any]' = <factory>, attachments: 'list[Attachment]' = <factory>) -> None
```

What a tool call did. ``text`` is written for the agent; ``ended`` means the turn is over.

## `Issue`

```pyi
Issue(path: 'str', message: 'str', fix: 'str | None' = None, severity: 'str' = 'error') -> None
```

One problem found in a contract or its inputs.

## `ContractError`

```pyi
ContractError(issues: 'list[Issue]', title: 'str' = 'contract is invalid')
```

The contract is invalid. ``issues`` lists every error found (not just the first).

## `InputError`

```pyi
InputError(issues: 'list[Issue]')
```

Run inputs do not match the contract's declared inputs.

## `RunError`

```pyi
RunError(message: 'str', path: 'str | None' = None)
```

A run could not continue. ``path`` names the contract element that failed.

## `InvariantViolation`

```pyi
InvariantViolation(message: 'str', path: 'str | None' = None, why: 'str' = '')
```

A declared invariant stopped holding. Broken by an agent's action, the action is refused and undone; broken by
anything else, the run fails closed. ``why`` is the invariant's own reason (empty when it gives none).

## `FatalRunError`

```pyi
FatalRunError(message: 'str', path: 'str | None' = None)
```

A failure outside the contract's rules — a host failed or cannot be asked, a replay stopped matching its
recording, a mechanism's code crashed, the world reached its ceiling of living entities. Unlike a rule failing
inside an agent's action, it fails the run wherever it happens.

## `SnapshotError`

A snapshot cannot be restored into this contract.

## `list_engines`

```pyi
list_engines(*, available: 'bool | None' = None) -> 'list[EngineSpec]'
```

List behavioral engines, optionally filtered by implementation availability.

## `clone_engine`

```pyi
clone_engine(engine_id: 'str', destination: 'str | Path', *, name: 'str | None' = None, overwrite: 'bool' = False) -> 'Path'
```

Clone a reusable engine contract into a project-owned JSON file.

## `fg_env.participants`

Participants: whoever takes the turns. Anything callable with a :class:`~fg_env.Wake` works.

``"random"``, ``"idle"`` and ``"policy:<name>"`` name built-in participants; :func:`anthropic` and :func:`openai`
drive a turn with your own LLM client; :func:`replay` plays a recorded run back.

### `participants.RandomAgent`

```pyi
RandomAgent(seed: 'int' = 0, actions: 'int' = 1, pass_rate: 'float' = 0.0)
```

Takes up to ``actions`` random legal actions per turn with valid random arguments.

### `participants.Idle`

```pyi
Idle()
```

Never acts.

### `participants.PolicyAgent`

```pyi
PolicyAgent(contract: 'Contract', name: 'str', seed: 'int' = 0)
```

Runs a coded policy from the contract's ``policies`` section: the first rule whose condition
holds, whose action is legal and whose arguments are valid is taken.

### `participants.anthropic`

```pyi
anthropic(client: 'Any', model: 'str', *, max_tokens: 'int' = 16000, max_steps: 'int' = 8, system: 'str' = '', retries: 'int' = 4, media: 'Collection[str] | None' = None, retry_truncated: 'bool' = True, extra: 'Mapping[str, Any] | None' = None) -> 'Participant'
```

An LLM participant using an ``anthropic.Anthropic()`` client.

The model is shown one tool list for the whole turn: the tools legal when the turn starts. A call to one that is no
longer legal is refused with the reason, each tool result names the offered tools not available any more, and a
tool that becomes legal during the turn is added. So every call of a turn sends the same prefix, and two
prompt-cache breakpoints — the system prompt (``system`` and the brief, cached after the tools) and the latest
message — let each call read the one before it from the cache. A breakpoint is placed only once its prefix is long
enough for any model to cache (about 512 tokens). When the agent has no action it could take, the model is not
called and the turn ends.

Files the agent receives are sent as image and document blocks after the text (``media``: the attachment types
sent as content, default image, pdf and text; ``media=()`` for a text-only model, which reads each file's
reference — its caption and alt text — in the text only). See :mod:`fg_env.assets.multimodal`.

``extra`` holds more request fields sent with every call, such as ``{"temperature": 0}``. Pass the sync
client: an async client fails the run saying so.

Rate limits, timeouts, overload and server errors are retried ``retries`` times with backoff (honouring
``retry-after``, never past the turn's time limit: once the turn is over no call is made); if a call still fails,
the turn is forfeited, counted in ``stats["forfeits"]`` and reported in
the run's diagnostics. Any other error — a rejected API key, an unknown model, a bad request, a client that does
not fit — fails the run at once, naming the agent, the provider's error and the fix. A reply the provider refused
ends the turn and counts in ``stats["refusals"]``. Real token usage lands in the run's statistics and in
``participant.usage``.

A reply cut off at ``max_tokens`` (default 16000: room for a model that thinks before it answers) counts in
``stats["truncated"]``; when it called no tool, the model is asked once for a short tool call
(``retry_truncated=False`` ends the turn instead); its tool calls, whose arguments may be cut off, are not made.
Any other reply that calls no tool is reminded once of the tools offered; a reply that still calls none ends the
turn, and in a turn that took no action counts in ``stats["no_tool_replies"]`` (with an action open, a failed
turn). A turn that makes all ``max_steps`` model calls ends there and counts in ``stats["out_of_steps"]``. Calls
left in a reply after one of them ended the turn are not made. In a stage where the agent must act, the
participant never ends the turn itself: the engine closes it and reports that the agent did not act.

### `participants.openai`

```pyi
openai(client: 'Any', model: 'str', *, max_tokens: 'int | None' = None, reasoning_effort: 'str | None' = None, max_steps: 'int' = 8, system: 'str' = '', retries: 'int' = 4, media: 'Collection[str] | None' = None, retry_truncated: 'bool' = True, extra: 'Mapping[str, Any] | None' = None) -> 'Participant'
```

An LLM participant using an ``openai.OpenAI()``-compatible client (chat completions + tools).

``max_tokens`` caps each reply (sent as ``max_completion_tokens``) and ``reasoning_effort`` (``"low"``,
``"medium"``, ``"high"``) is passed on to reasoning models; each is sent only when given. A server that knows only
the older ``max_tokens`` field takes ``extra={"max_tokens": 1024}`` instead. A response with no choices in it, or
with ``finish_reason`` ``error`` (OpenRouter sends both now and then), is retried like an overload. Retries,
failures, refusals (a ``refusal`` message or ``finish_reason`` ``content_filter``), ``extra``, usage accounting,
truncated replies (``finish_reason`` ``length``), ``retry_truncated`` and the one tool list per turn work as for
:func:`anthropic`; arguments that are not a JSON object (or not valid JSON, which the refusal says) are refused and
counted as invalid calls. Files are sent as ``image_url`` data URLs, ``file`` and ``input_audio`` parts (``media``:
default image, pdf, audio and text; ``()`` for text only); files from tool results follow the tool messages in one
user message.

### `participants.replay`

```pyi
replay(recording: 'Any', fallback: 'Any' = None) -> 'Participant'
```

A participant that plays a recorded run's steps again, turn by turn, checking every wake against the recording
(``recording``: a result with exposures, its dict, a saved file, or a trace); the run it plays in must record
exposures. On the first difference the run fails with the divergence, or — given ``fallback`` — that participant
plays on. Usually you want ``fg_env.analysis.trace(recording).replay(contract)``, which also replays the host
answers and compares the outcome.

### `participants.resolve_participant`

```pyi
resolve_participant(value: 'Any', contract: 'Contract', seed: 'int', path: 'str' = 'participants') -> 'Participant'
```

The participant ``value`` names; an unknown name raises :class:`~fg_env.ContractError` at ``path``.

## `fg_env.analysis`

Analysis: turn a contract and its runs into findings, reports and readable records.

    from fg_env import analysis

    grid = analysis.sweep("shop.json", {"price": [8, 10, 12]}, runs=20)
    print(analysis.report(grid, audience="owner"))

* ``sweep``, ``sensitivity``, ``calibrate``, ``optimise`` — outputs across inputs, what drives them, inputs fitted to
  data, the best decision under constraints.
* ``validate``, ``score``, ``backtest``, ``precision`` — forecasts and runs checked against actual values.
* ``behavior_checks``, ``highlights``, ``narrative``, ``drivers``, ``compare``, ``chain`` — broken parts found by
  playing, the notable moments of a run, what separates outcomes, side-by-side results, contracts in sequence.
* ``fit_patterns``, ``decompose`` — a contract's world patterns fitted to history; one pattern split into its parts.
* ``report`` — any of these results as short sentences and tables a manager can act on.
* ``describe`` — an ODD document and game metadata derived from the contract.
* ``trace`` — a recorded run read turn by turn, and replayed offline.

### `analysis.fit_patterns`

```pyi
fit_patterns(contract: 'ContractLike', *, data_dir: 'str | Path | None' = None, inputs: 'Mapping[str, Any] | None' = None) -> 'FitResult'
```

Estimate every pattern that declares ``fit`` and return the contract with the estimates written back.

Data files are read from ``data_dir`` (default: the contract file's folder). ``inputs`` are used while fitting
(e.g. which history table to read) and are not written into the result.

### `analysis.FitResult`

```pyi
FitResult(contract: 'dict[str, Any]', fits: 'list[PatternFit]', priors: 'dict[str, dict[str, Any]]' = <factory>) -> None
```

The fitted contract (data, like the one given), a report per pattern, and the estimates as priors.

### `analysis.decompose`

```pyi
decompose(source: 'ContractLike | Any', pattern: 'str', *, key: 'Any' = None, rounds: 'int | Sequence[int] | None' = None, inputs: 'Mapping[str, Any] | None' = None, seed: 'int' = 0, data_dir: 'Any' = None, estimates: 'bool' = False) -> 'Decomposition'
```

Decompose a ``product`` or ``sum`` pattern into its factors (see the module).

``source`` is a contract (read over ``rounds``: a count from round 1, or a list of rounds; default every round of
the clock) or a run (read now). ``key`` is required when the pattern has keys. ``estimates`` reads a contract's
fitted parameters at their estimates, without the draws their standard errors (``uncertainty``) would make.

### `analysis.Decomposition`

```pyi
Decomposition(pattern: 'str', key: 'str | None', kind: 'str', rows: 'list[dict[str, Any]]' = <factory>) -> None
```

Each round: the total, every factor's value, and what each factor adds.

### `analysis.report`

```pyi
report(source: 'Any', audience: 'str' = 'owner', *, contract: 'ContractLike | None' = None, validation: 'ValidationResult | None' = None, optimisation: 'OptimisationResult | None' = None, objective: 'str | None' = None, require: 'Mapping[str, Any] | None' = None, control: 'str | None' = None, data_dir: 'Any' = None) -> 'Report'
```

A plain-language report of ``source``: a :class:`~fg_env.RunResult` (or a list of them), an experiment, a sweep,
a validation or an optimisation (see the module). ``contract`` adds names, arm descriptions, assumptions and
pattern decompositions; ``validation`` adds how well the model matched the data; ``optimisation`` adds how the
decision an experiment plays was found; ``objective`` and ``require`` choose the recommended option; ``control``
is the arm differences are measured against (default: the first).

### `analysis.Report`

```pyi
Report(title: 'str', audience: 'str', kind: 'str', sections: 'list[Section]', recommendation: 'dict[str, Any] | None' = None, notes: 'list[str]' = <factory>) -> None
```

Report(title: 'str', audience: 'str', kind: 'str', sections: 'list[Section]', recommendation: 'dict[str, Any] | None' = None, notes: 'list[str]' = <factory>)

### `analysis.describe`

```pyi
describe(contract: 'ContractLike', *, inputs: 'Mapping[str, Any] | None' = None, arm: 'str | None' = None, data_dir: 'Any' = None) -> 'Description'
```

Describe ``contract`` (with ``inputs`` and ``arm`` applied). Counts that need the built world (players,
entity choices, rounds given by an expression) come from building it once with seed 0; when it cannot be
built — a required input is missing, say — they are reported as unknown with the reason.

### `analysis.Description`

```pyi
Description(name: 'str', markdown: 'str', metadata: 'dict[str, Any]') -> None
```

Description(name: 'str', markdown: 'str', metadata: 'dict[str, Any]')

### `analysis.trace`

```pyi
trace(source: 'TraceSource') -> 'Trace'
```

A recorded run to read: a :class:`RunResult`, its ``to_dict()``, or a file written by ``result.save()``.

The run must have recorded exposures: ``fg_env.run(..., exposures=True)``.

### `analysis.Trace`

```pyi
Trace(source: 'TraceSource')
```

A recorded run. ``wakes`` are the exposure records in engine order; ``texts`` holds every text by hash.

### `analysis.sweep`

```pyi
sweep(contract: 'ContractLike', params: 'Mapping[str, ParamSpec]', *, runs: 'int' = 5, outputs: 'Sequence[str] | None' = None, arms: 'Sequence[str | None] | None' = None, inputs: 'Mapping[str, Any] | None' = None, design: 'str' = 'factorial', samples: 'int | None' = None, participants: 'Any' = None, rounds: 'int | None' = None, seed: 'int' = 0, workers: 'int' = 1, data_dir: 'Any' = None, hosts: 'Any' = None, uncertainty: 'Any' = None) -> 'SweepResult'
```

Run the contract across combinations of inputs.

``params``: ``{input: [values]}`` or ``{input: {"low", "high", "steps", "log"}}`` (range
bounds default to the input's declared ``min``/``max``). ``design="factorial"`` runs every
combination; ``design="lhs"`` draws ``samples`` Latin-hypercube points over the ranges
(list params are sampled by stratified index). ``arms`` adds the arm as another factor
(default: no arm). ``inputs`` fixes other inputs for every cell. ``outputs`` limits the
measured outputs (default: every numeric or yes/no output). ``data_dir`` is where inputs with a
``source`` are read (default: the contract file's folder); ``hosts`` answers host requests in every run.

### `analysis.SweepResult`

```pyi
SweepResult(contract: 'str', design: 'str', params: 'dict[str, list[Any]]', arms: 'list[str | None]', measures: 'list[str]', seeds: 'list[int]', cells: 'list[SweepCell]', rounds: 'int | None' = None, _effects: 'dict[str, dict[str, dict[str, Any]]]' = <factory>) -> None
```

SweepResult(contract: 'str', design: 'str', params: 'dict[str, list[Any]]', arms: 'list[str | None]', measures: 'list[str]', seeds: 'list[int]', cells: 'list[SweepCell]', rounds: 'int | None' = None, _effects: 'dict[str, dict[str, dict[str, Any]]]' = <factory>)

### `analysis.sensitivity`

```pyi
sensitivity(contract: 'ContractLike', inputs: 'InputRanges', output: 'str', *, method: 'str' = 'oat', runs: 'int' = 5, baseline: 'Mapping[str, Any] | None' = None, delta: 'float' = 0.1, trajectories: 'int' = 6, levels: 'int' = 4, samples: 'int' = 20, arm: 'str | None' = None, participants: 'Any' = None, rounds: 'int | None' = None, seed: 'int' = 0, workers: 'int' = 1, level: 'float' = 0.95, data_dir: 'Any' = None, hosts: 'Any' = None) -> 'SensitivityResult'
```

Rank ``inputs`` by their influence on ``output`` (an output or a metric's final value).

``inputs``: names, or ``{name: {"low", "high"}}`` (ranges default to declared min/max; OAT
only needs them to keep perturbations inside the allowed range). ``baseline`` overrides the
contract defaults as the OAT centre and the fixed values for the other inputs. ``data_dir`` is where
inputs with a ``source`` are read (default: the contract file's folder); ``hosts`` answers host requests.

### `analysis.SensitivityResult`

```pyi
SensitivityResult(contract: 'str', output: 'str', method: 'str', runs: 'int', ranking: 'list[dict[str, Any]]', details: 'dict[str, Any]' = <factory>) -> None
```

SensitivityResult(contract: 'str', output: 'str', method: 'str', runs: 'int', ranking: 'list[dict[str, Any]]', details: 'dict[str, Any]' = <factory>)

### `analysis.calibrate`

```pyi
calibrate(contract: 'ContractLike', targets: 'Any', params: 'Mapping[str, Mapping[str, Any]]', *, runs: 'int' = 5, budget: 'int' = 30, holdout: 'int | None' = None, method: 'str' = 'auto', inputs: 'Mapping[str, Any] | None' = None, arm: 'str | None' = None, participants: 'Any' = None, rounds: 'int | None' = None, seed: 'int' = 0, workers: 'int' = 1, test: 'Any' = None, folds: 'int | None' = None, data_dir: 'Any' = None, hosts: 'Any' = None) -> 'CalibrationResult'
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

### `analysis.CalibrationResult`

```pyi
CalibrationResult(contract: 'str', params: 'dict[str, Any]', method: 'str', fit: 'float', targets: 'list[dict[str, Any]]', validation: 'dict[str, Any]', uncertainty: 'dict[str, dict[str, Any]]', evaluations: 'int', history: 'list[dict[str, Any]]' = <factory>, notes: 'list[str]' = <factory>, cases: 'list[str]' = <factory>, holdout: 'dict[str, Any] | None' = None, plausible: 'list[dict[str, Any]]' = <factory>, pooled: 'list[dict[str, Any]]' = <factory>) -> None
```

CalibrationResult(contract: 'str', params: 'dict[str, Any]', method: 'str', fit: 'float', targets: 'list[dict[str, Any]]', validation: 'dict[str, Any]', uncertainty: 'dict[str, dict[str, Any]]', evaluations: 'int', history: 'list[dict[str, Any]]' = <factory>, notes: 'list[str]' = <factory>, cases: 'list[str]' = <factory>, holdout: 'dict[str, Any] | None' = None, plausible: 'list[dict[str, Any]]' = <factory>, pooled: 'list[dict[str, Any]]' = <factory>)

### `analysis.score`

```pyi
score(forecasts: 'Sequence[Any]', outcomes: 'Sequence[Any]', *, kind: 'str' = 'auto', climatology: 'Any' = None, bins: 'int' = 10, nominal: 'float | None' = None, epsilon: 'float' = 1e-15) -> 'dict[str, Any]'
```

Every applicable score for a set of forecasts against what happened.

``kind``: ``binary`` (probabilities of a yes/no event), ``categorical`` (mappings of
category → probability), ``ensemble`` (lists of simulated numbers), ``interval``
([low, high] pairs), or ``auto`` (``interval`` must be named: a two-member ensemble looks
the same). ``climatology`` is the reference forecast for the skill score: a base rate
(binary), a category distribution (categorical) or a sample of numbers (ensemble). Without
it the reference is the outcomes' own frequency — in-sample, and labelled as such.

### `analysis.brier`

```pyi
brier(probabilities: 'Sequence[float]', outcomes: 'Sequence[Any]') -> 'float'
```

Mean squared error of probability forecasts for a yes/no event (0 perfect, 1 worst).

### `analysis.brier_multiclass`

```pyi
brier_multiclass(forecasts: 'Sequence[Mapping[Any, float]]', outcomes: 'Sequence[Any]') -> 'float'
```

Σ over categories of (p − 1[outcome])², averaged over cases (0 perfect, 2 worst).

An outcome that no forecast lists counts as a category given probability 0.

### `analysis.log_loss`

```pyi
log_loss(probabilities: 'Sequence[float]', outcomes: 'Sequence[Any]', epsilon: 'float' = 1e-15) -> 'float'
```

Mean negative log likelihood of yes/no outcomes (0 perfect; punishes confident misses hard).

### `analysis.log_loss_multiclass`

```pyi
log_loss_multiclass(forecasts: 'Sequence[Mapping[Any, float]]', outcomes: 'Sequence[Any]', epsilon: 'float' = 1e-15) -> 'float'
```

### `analysis.crps`

```pyi
crps(ensembles: 'Sequence[Sequence[float]]', observations: 'Sequence[float]') -> 'float'
```

### `analysis.crps_ensemble`

```pyi
crps_ensemble(members: 'Sequence[float]', observation: 'float') -> 'float'
```

Continuous ranked probability score of one ensemble: E|X − y| − ½·E|X − X′|.

It is the mean absolute error generalised to a whole distribution, in the outcome's units.

### `analysis.interval_coverage`

```pyi
interval_coverage(intervals: 'Sequence[tuple[float, float]]', outcomes: 'Sequence[float]', nominal: 'float | None' = None) -> 'dict[str, Any]'
```

How often outcomes fall inside their intervals (ends included), with a Wilson interval.

### `analysis.reliability`

```pyi
reliability(probabilities: 'Sequence[float]', outcomes: 'Sequence[Any]', bins: 'int' = 10) -> 'list[ReliabilityBin]'
```

Non-empty bins of equal width over [0, 1]; a forecast of exactly 1 falls in the last bin.

### `analysis.ece`

```pyi
ece(probabilities: 'Sequence[float]', outcomes: 'Sequence[Any]', bins: 'int' = 10) -> 'float'
```

Expected calibration error: bin-size-weighted |mean forecast − observed frequency|.

### `analysis.murphy`

```pyi
murphy(probabilities: 'Sequence[float]', outcomes: 'Sequence[Any]', bins: 'int' = 10) -> 'dict[str, float]'
```

Brier = reliability − resolution + uncertainty (+ a within-bin residual).

reliability: calibration error (lower is better); resolution: how much forecasts separate
cases from the base rate (higher is better); uncertainty: base rate × (1 − base rate). The
identity is exact when forecasts inside a bin are equal; ``residual`` holds the difference.

### `analysis.skill_score`

```pyi
skill_score(value: 'float', reference: 'float', perfect: 'float' = 0.0) -> 'float | None'
```

1 − (score − perfect)/(reference − perfect): 1 perfect, 0 no better than the reference, < 0 worse.

### `analysis.backtest`

```pyi
backtest(contract: 'ContractLike', cases: 'Sequence[Mapping[str, Any]]', output: 'str', *, runs: 'int' = 10, threshold: 'float | None' = None, climatology: 'Any' = None, arm: 'str | None' = None, participants: 'Any' = None, rounds: 'int | None' = None, seed: 'int' = 0, workers: 'int' = 1, bins: 'int' = 10, test: 'Any' = None, folds: 'int | None' = None, data_dir: 'Any' = None, hosts: 'Any' = None, uncertainty: 'Any' = None) -> 'BacktestResult'
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

### `analysis.BacktestResult`

```pyi
BacktestResult(contract: 'str', output: 'str', kind: 'str', runs: 'int', cases: 'list[dict[str, Any]]', scores: 'dict[str, Any]', notes: 'list[str]' = <factory>, holdout: 'dict[str, Any] | None' = None) -> None
```

BacktestResult(contract: 'str', output: 'str', kind: 'str', runs: 'int', cases: 'list[dict[str, Any]]', scores: 'dict[str, Any]', notes: 'list[str]' = <factory>, holdout: 'dict[str, Any] | None' = None)

### `analysis.precision`

```pyi
precision(contract: 'ContractLike', output: 'str', *, target_se: 'float | None' = None, relative_se: 'float | None' = None, max_runs: 'int' = 100, batch: 'int' = 5, min_runs: 'int | None' = None, inputs: 'Mapping[str, Any] | None' = None, arm: 'str | None' = None, participants: 'Any' = None, rounds: 'int | None' = None, seed: 'int' = 0, workers: 'int' = 1, level: 'float' = 0.95, data_dir: 'Any' = None, hosts: 'Any' = None) -> 'PrecisionResult'
```

Add runs ``batch`` at a time until the standard error of ``output``'s mean is small enough.

Give ``target_se`` (in output units) or ``relative_se`` (a share of |mean|). A yes/no output
is a proportion: its standard error is the Wilson interval's half-width over z, which never
reaches 0 by luck at 0% or 100%. At least ``min_runs`` (default two batches) run before the
target can be declared met, so a few identical early runs cannot end the search.

### `analysis.PrecisionResult`

```pyi
PrecisionResult(contract: 'str', output: 'str', converged: 'bool', runs: 'int', estimate: 'Estimate', target_se: 'float', trace: 'list[dict[str, Any]]', runs_needed: 'int | None') -> None
```

PrecisionResult(contract: 'str', output: 'str', converged: 'bool', runs: 'int', estimate: 'Estimate', target_se: 'float', trace: 'list[dict[str, Any]]', runs_needed: 'int | None')

### `analysis.behavior_checks`

```pyi
behavior_checks(contract: 'ContractLike', *, runs: 'int' = 4, rounds: 'int | None' = None, seed: 'int' = 0, participants: 'Any' = 'random', inputs: 'Mapping[str, Any] | None' = None, test_inputs: 'Sequence[str] | None' = None, perturb: 'float' = 0.5, workers: 'int' = 1, data_dir: 'Any' = None, hosts: 'Any' = None, boundaries: 'bool' = False, max_boundary_cases: 'int' = 24) -> 'CheckReport'
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

### `analysis.CheckReport`

```pyi
CheckReport(contract: 'str', runs: 'int', rounds: 'int | None', findings: 'list[Finding]', tested_inputs: 'list[str]', untested_inputs: 'list[str]') -> None
```

CheckReport(contract: 'str', runs: 'int', rounds: 'int | None', findings: 'list[Finding]', tested_inputs: 'list[str]', untested_inputs: 'list[str]')

### `analysis.Finding`

```pyi
Finding(code: 'str', severity: 'str', subject: 'str', message: 'str', evidence: 'dict[str, Any]' = <factory>) -> None
```

Finding(code: 'str', severity: 'str', subject: 'str', message: 'str', evidence: 'dict[str, Any]' = <factory>)

### `analysis.highlights`

```pyi
highlights(result: 'RunResult', *, top: 'int' = 5, metrics: 'Sequence[str] | None' = None) -> 'list[Highlight]'
```

The ``top`` most notable moments of a run, most surprising first (ties: earliest first).

``metrics`` limits which metric series are scanned (default: all). Event-based moments need
the run's event log (``RunResult.events``).

### `analysis.narrative`

```pyi
narrative(result: 'RunResult', *, limit: 'int' = 10) -> 'str'
```

A compact factual account: how the run went, its notable moments in order — each with what happened around it
when the run shows it — and its results. Rounds are named in the clock's terms (``Week 7 (2026-10-12): …``,
``09:30–10:00: …``); moments no more unusual than the run's usual ups and downs are left out.

### `analysis.Highlight`

```pyi
Highlight(kind: 'str', round: 'int', subject: 'str', score: 'float', text: 'str', data: 'dict[str, Any]' = <factory>) -> None
```

Highlight(kind: 'str', round: 'int', subject: 'str', score: 'float', text: 'str', data: 'dict[str, Any]' = <factory>)

### `analysis.drivers`

```pyi
drivers(runs: 'Any', output: 'str', *, focus: 'Any' = None, threshold: 'float | None' = None, include: 'Sequence[str]' = ('inputs', 'metrics', 'outputs', 'actions', 'end'), permutations: 'int' = 500, alpha: 'float' = 0.05, top: 'int' = 10, seed: 'int' = 0) -> 'DriversResult'
```

Features that separate runs where ``output`` hits the focus from runs where it does not.

Focus: yes/no outputs → true; numbers → above the median (or ``threshold``); text → the most
common value (or ``focus``). ``include`` picks feature groups: inputs (and arm), metrics
(final value and peak), other outputs, actions (successful count, from event logs), end
(how the run ended, winner). Deterministic for a given ``seed``.

### `analysis.DriversResult`

```pyi
DriversResult(output: 'str', focus: 'str', n: 'int', base_rate: 'float', drivers: 'list[Driver]', tested: 'int', permutations: 'int', alpha: 'float', notes: 'list[str]' = <factory>) -> None
```

DriversResult(output: 'str', focus: 'str', n: 'int', base_rate: 'float', drivers: 'list[Driver]', tested: 'int', permutations: 'int', alpha: 'float', notes: 'list[str]' = <factory>)

### `analysis.Driver`

```pyi
Driver(feature: 'str', kind: 'str', lift: 'float', p_value: 'float', high_rate: 'float', low_rate: 'float', n_high: 'int', n_low: 'int', split: 'float | None' = None) -> None
```

Driver(feature: 'str', kind: 'str', lift: 'float', p_value: 'float', high_rate: 'float', low_rate: 'float', n_high: 'int', n_low: 'int', split: 'float | None' = None)

### `analysis.compare`

```pyi
compare(a: 'Any', b: 'Any', *, labels: 'tuple[str, str]' = ('a', 'b'), level: 'float' = 0.95) -> 'Comparison'
```

Compare results, matching shared unique seeds and excluding invalid outputs per pair.

Unmatched seeds are omitted when shared seeds exist; disjoint samples use an
independent comparison. Notes identify exclusions and numeric rows give sample sizes.

### `analysis.Comparison`

```pyi
Comparison(labels: 'tuple[str, str]', paired: 'bool', outputs: 'dict[str, dict[str, Any]]', series: 'dict[str, dict[str, Any]]', notes: 'list[str]' = <factory>, level: 'float' = 0.95) -> None
```

Comparison(labels: 'tuple[str, str]', paired: 'bool', outputs: 'dict[str, dict[str, Any]]', series: 'dict[str, dict[str, Any]]', notes: 'list[str]' = <factory>, level: 'float' = 0.95)

### `analysis.chain`

```pyi
chain(first: 'ContractLike', second: 'ContractLike', bind: 'Mapping[str, str]', *, runs: 'int' = 10, level: 'float' = 0.9, uncertainty: 'bool' = True, first_inputs: 'Mapping[str, Any] | None' = None, second_inputs: 'Mapping[str, Any] | None' = None, participants: 'Any' = None, second_participants: 'Any' = None, rounds: 'int | None' = None, second_rounds: 'int | None' = None, seed: 'int' = 0, workers: 'int' = 1, data_dir: 'Any' = None, second_data_dir: 'Any' = None, hosts: 'Any' = None) -> 'ChainResult'
```

Run ``first``, bind its outputs into ``second``'s inputs (``{second_input: first_output}``), run ``second``.

Each bound output is summarised by its mean (point) and the central ``level`` range of its
run values (lower, upper); yes/no outputs become the share of runs. ``second`` runs at the
point estimates and, with ``uncertainty``, at all-lower and all-upper bindings too. Values
outside a bound input's declared range are clamped, with a note. ``data_dir`` / ``second_data_dir`` are where
each contract's inputs with a ``source`` are read (default: each contract file's folder); ``hosts`` answers host
requests in the runs of both.

### `analysis.ChainResult`

```pyi
ChainResult(first: 'str', second: 'str', runs: 'int', level: 'float', bindings: 'dict[str, dict[str, Any]]', scenarios: 'dict[str, dict[str, Any]]', envelope: 'dict[str, dict[str, float]]', notes: 'list[str]' = <factory>) -> None
```

ChainResult(first: 'str', second: 'str', runs: 'int', level: 'float', bindings: 'dict[str, dict[str, Any]]', scenarios: 'dict[str, dict[str, Any]]', envelope: 'dict[str, dict[str, float]]', notes: 'list[str]' = <factory>)

### `analysis.statistic`

```pyi
statistic(name: 'str', series: 'Sequence[float]') -> 'float'
```

Evaluate a named statistic (``"volatility"``, ``"autocorrelation:2"``) on a series.

### `analysis.AnalysisError`

An analysis cannot produce a result: every run failed, or a request is impossible.

### `analysis.validate`

```pyi
validate(contract: 'ContractLike', cases: 'Sequence[Mapping[str, Any]]', *, runs: 'int' = 10, levels: 'Sequence[float]' = (0.8, 0.95), season: 'int | None' = None, baselines: 'Sequence[str]' = ('last', 'mean', 'seasonal'), test: 'Any' = None, arm: 'str | None' = None, participants: 'Any' = None, rounds: 'int | None' = None, seed: 'int' = 0, workers: 'int' = 1, data_dir: 'Any' = None, hosts: 'Any' = None, uncertainty: 'Any' = None) -> 'ValidationResult'
```

Check the contract's forecasts against each case's ``actuals`` (see the module notes).

``cases``: ``[{"name"?, "inputs"?, "arm"?, "actuals": {measure: number | {key: number} | [numbers]}}]`` in time
order; a measure is an output or a metric (its final value). ``levels`` are the nominal interval coverages checked.
Baselines forecast each key from earlier cases: ``last``, ``mean`` and ``seasonal`` (the value ``season`` cases
back; needs ``season``). ``test`` (a share or case names) also scores the held-out cases on their own.
``uncertainty`` (a calibration, points or priors: :mod:`.draws`) draws parameters per run, so intervals include
not knowing them.

### `analysis.ValidationResult`

```pyi
ValidationResult(contract: 'str', runs: 'int', levels: 'list[float]', cases: 'list[str]', measures: 'dict[str, dict[str, Any]]', rows: 'list[dict[str, Any]]', warnings: 'list[str]' = <factory>, notes: 'list[str]' = <factory>) -> None
```

ValidationResult(contract: 'str', runs: 'int', levels: 'list[float]', cases: 'list[str]', measures: 'dict[str, dict[str, Any]]', rows: 'list[dict[str, Any]]', warnings: 'list[str]' = <factory>, notes: 'list[str]' = <factory>)

### `analysis.optimise`

```pyi
optimise(contract: 'ContractLike', decisions: 'Mapping[str, Any]', objective: 'Any', constraints: 'Any' = (), *, runs: 'int' = 10, seed: 'int' = 0, method: 'str' = 'auto', budget: 'int' = 50, workers: 'int' = 1, confidence: 'float' = 0.9, uncertainty: 'Any' = None, holdout_seeds: 'int | None' = None, inputs: 'Mapping[str, Any] | None' = None, arm: 'str | None' = None, participants: 'Any' = None, rounds: 'int | None' = None, data_dir: 'Any' = None, hosts: 'Any' = None) -> 'OptimisationResult'
```

Search ``decisions`` for the best ``objective`` subject to ``constraints`` (see the module notes).

``decisions``: ``{input: {low, high, step?} | [values] | {length|keys, low, high, step?, monotone?, sum?}}``.
``objective``: ``"maximise margin"``, ``"minimise p90 of cost"``, or a list of two or three for a Pareto frontier.
``constraints``: ``["fill_rate >= 0.95", "sl >= 0.8 in 90% of runs", "each sl_by_interval >= 0.8"]``, each held
with ``confidence`` unless it says "with 95% confidence". ``runs`` seeds judge each decision; ``budget`` caps the
distinct decisions searched; ``method``: auto, grid, random, lhs, local, race, nelder_mead, cross_entropy or (for a
frontier) frontier. ``holdout_seeds`` (default ``runs``; 0 skips it) fresh seeds check the choice. ``inputs`` and
``arm`` fix everything else; ``uncertainty`` draws parameters per run.

### `analysis.OptimisationResult`

```pyi
OptimisationResult(contract: 'str', method: 'str', decisions: 'list[str]', objectives: 'list[str]', constraints: 'list[str]', runs: 'int', seed: 'int', evaluations: 'int', total_runs: 'int', best: 'dict[str, Any] | None' = None, feasible: 'bool' = False, verdict: 'str' = 'infeasible', confidence: 'float' = 0.9, estimates: 'dict[str, Any] | None' = None, runner_up: 'dict[str, Any] | None' = None, holdout: 'dict[str, Any] | None' = None, sensitivity: 'list[dict[str, Any]]' = <factory>, frontier: 'list[dict[str, Any]]' = <factory>, history: 'list[dict[str, Any]]' = <factory>, notes: 'list[str]' = <factory>) -> None
```

OptimisationResult(contract: 'str', method: 'str', decisions: 'list[str]', objectives: 'list[str]', constraints: 'list[str]', runs: 'int', seed: 'int', evaluations: 'int', total_runs: 'int', best: 'dict[str, Any] | None' = None, feasible: 'bool' = False, verdict: 'str' = 'infeasible', confidence: 'float' = 0.9, estimates: 'dict[str, Any] | None' = None, runner_up: 'dict[str, Any] | None' = None, holdout: 'dict[str, Any] | None' = None, sensitivity: 'list[dict[str, Any]]' = <factory>, frontier: 'list[dict[str, Any]]' = <factory>, history: 'list[dict[str, Any]]' = <factory>, notes: 'list[str]' = <factory>)

## `fg_env.rl`

Agents in the loop: a contract as a game, a Gymnasium or PettingZoo environment, a tournament or an evaluation.

    from fg_env import rl

    g = rl.game("nim.json")                              # OpenSpiel-style: seats, numbered actions, chance nodes
    env = rl.gym("nim.json", "ann", others="random")     # one agent as a Gymnasium-style environment
    table = rl.tournament("poker.json", {"a": bot_a, "b": bot_b}, games=50)

* ``game`` / ``Game`` / ``GameState`` — any contract as a game for search, solving and learning code;
  ``conformance`` checks it, ``playthrough`` prints one game move by move. Transforms, benchmarks and verified
  algorithms live in :mod:`fg_env.game`.
* ``gym`` / ``GymEnv``, ``pettingzoo_aec`` / ``pettingzoo_parallel`` — reinforcement-learning adapters.
* ``tournament`` — pit participants against each other in the contract's seats, then rate and rank them.
* ``evaluate`` — how well a focal participant does among background agents, against a baseline on the same seeds.

### `rl.game`

```pyi
game(source: 'ContractLike', *, inputs: 'Mapping[str, Any] | None' = None, seed: 'int' = 0, arm: 'str | None' = None, players: 'Sequence[str] | None' = None, others: 'Any' = None, chance: 'str' = 'explicit', simultaneous: 'str' = 'joint', dry_run: 'bool' = True, max_combinations: 'int' = 10000, hosts: 'Any' = None, data_dir: 'str | os.PathLike[str] | None' = None) -> 'Game'
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

### `rl.Game`

```pyi
Game(root: 'Env', *, players: 'Sequence[str] | None', others: 'Any', chance: 'str', turn_based: 'bool', dry_run: 'bool', limit: 'int')
```

A contract as a game: seats, a numbered action space, and states to search from.

Create with :func:`fg_env.rl.game`. States pause at every decision of a seat (and at every chance
node when chance is explicit); agents that are not seats are played by ``others``.

### `rl.GameState`

```pyi
GameState(game: 'Game', run: 'Branch | Run', history: 'list[dict[str, Any]]', previous: 'list[float] | None' = None, legal: 'dict[int, Legal] | None' = None, path: 'bytes' = b'')
```

One state of a :class:`~fg_env.game.Game`. ``apply_action`` changes it; ``child`` and ``clone``
give new independent states. Players are seat indices (``game.players[i]`` is the entity id).

### `rl.conformance`

```pyi
conformance(source: 'ContractLike | Game', *, sims: 'int' = 20, seed: 'int' = 0, inputs: 'Mapping[str, Any] | None' = None, simultaneous: 'str' = 'joint', leak_branches: 'int' = 2, max_steps: 'int' = 1000, resume: 'bool' = True) -> 'ConformanceReport'
```

Check a game (a contract, or a :class:`Game` from :func:`fg_env.rl.game`) over ``sims`` seeded random playouts.

``simultaneous="turn_based"`` checks the one-seat-at-a-time view of simultaneous stages, where the leak test
also covers sealed choices. ``leak_branches`` is how many steps of each playout are changed for the leak test;
``max_steps`` is the longest a playout may run; ``resume=False`` skips the whole-run resume check.

### `rl.ConformanceReport`

```pyi
ConformanceReport(game: 'str', sims: 'int', decisions: 'int' = 0, chance_nodes: 'int' = 0, checks: 'dict[str, int]' = <factory>, issues: 'list[ConformanceIssue]' = <factory>) -> None
```

What :func:`conformance` checked and found.

### `rl.playthrough`

```pyi
playthrough(source: 'ContractLike | Game', *, seed: 'int' = 0, steps: 'Sequence[Mapping[str, Any]] | None' = None, inputs: 'Mapping[str, Any] | None' = None, simultaneous: 'str' = 'joint', max_steps: 'int' = 500) -> 'str'
```

The playthrough text of one game: ``steps`` when given (see :mod:`.steps`), else random ones from ``seed``.

### `rl.gym`

```pyi
gym(source: 'ContractLike', agent: 'str', *, others: 'Any' = None, inputs: 'Mapping[str, Any] | None' = None, arm: 'str | None' = None, seed: 'int | None' = None, max_steps: 'int | None' = None, action_ids: 'bool' = False, hosts: 'Any' = None, render_mode: 'str | None' = None, data_dir: 'str | os.PathLike[str] | None' = None) -> 'GymEnv'
```

One agent (an entity id) of a contract as a Gymnasium-style environment; ``others`` play the rest.

``seed`` seeds the episodes (``reset(seed=...)`` reseeds them); ``max_steps`` truncates an episode after
that many calls; ``action_ids=True`` accepts integer action ids and adds ``legal_actions`` and
``action_mask`` to ``info`` (see :func:`fg_env.rl.game` for how ids are numbered).

### `rl.GymEnv`

```pyi
GymEnv(root: 'Env', agent: 'str', *, others: 'Any', max_steps: 'int | None', action_ids: 'bool', hosts: 'Any', render_mode: 'str | None')
```

One agent of a contract as a Gymnasium-style environment. Create with :func:`fg_env.rl.gym`.

### `rl.pettingzoo_aec`

```pyi
pettingzoo_aec(source: 'ContractLike', *, inputs: 'Mapping[str, Any] | None' = None, seed: 'int | None' = None, max_steps: 'int | None' = None, render_mode: 'str | None' = None) -> 'AECGame'
```

A contract as a PettingZoo AEC environment (simultaneous stages one seat at a time; needs game.returns).

### `rl.pettingzoo_parallel`

```pyi
pettingzoo_parallel(source: 'ContractLike', *, inputs: 'Mapping[str, Any] | None' = None, seed: 'int | None' = None, max_steps: 'int | None' = None, render_mode: 'str | None' = None) -> 'ParallelGame'
```

A contract as a PettingZoo parallel environment (needs game.returns).

### `rl.tournament`

```pyi
tournament(contract: 'ContractLike', entrants: 'Mapping[str, Any]', *, seats: 'Sequence[str] | None' = None, pairing: 'str' = 'round_robin', games: 'int' = 1, score: 'ScoreSpec' = None, rating: 'str' = 'elo', swiss_rounds: 'int | None' = None, others: 'Any' = None, inputs: 'Mapping[str, Any] | None' = None, arm: 'str | None' = None, rounds: 'int | None' = None, seed: 'int' = 0, workers: 'int' = 1, data_dir: 'Any' = None, budget: 'Mapping[str, Any] | None' = None, exposures: 'bool' = False) -> 'TournamentResult'
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

Standings are ranked by ``rating``: ``elo`` (maximum-likelihood, with 95% intervals) or ``glicko2``; both are
reported, with win/draw/loss, points and score means. ``evaluation`` adds the Nash average, α-Rank and a Schulze
vote, which stay meaningful when skill is not transitive; ``returns`` gives every entrant's score in every seat, and
each standing's ``cost`` its turns, calls, invalid calls, timeouts, undone turns and model tokens. A callable
entrant is shared by all its games: with ``workers > 1`` those run in threads at once. ``budget`` caps each game on
its own (:mod:`fg_env.runtime.budget`); ``exposures=True`` records what agents saw in every game
(``result.runs[i].exposures``, events kept), each a trace to read or replay.

### `rl.TournamentResult`

```pyi
TournamentResult(contract: 'str', pairing: 'str', rating: 'str', score: 'str', seats: 'list[str]', entrants: 'list[str]', games_per_seating: 'int', standings: 'list[dict[str, Any]]', head_to_head: 'dict[str, dict[str, dict[str, int]]]', returns: 'dict[str, dict[str, dict[str, Any]]]', seat_points: 'dict[str, dict[str, Any]]', evaluation: 'dict[str, Any]', games: 'list[dict[str, Any]]', runs: 'list[RunResult]' = <factory>, notes: 'list[str]' = <factory>) -> None
```

``standings`` is best first by ``rating``. ``evaluation`` holds the margin matrix, the Nash average, α-Rank
and the Schulze vote; ``returns[entrant][seat]`` the score in each seat; ``games`` one record per game or bye.

### `rl.evaluate`

```pyi
evaluate(suite: 'Any', *, focal: 'Any', background: 'Any' = None, baseline: 'Any' = None, seats: 'Any' = None, score: 'ScoreSpec' = None, modes: 'Mapping[str, float] | None' = None, inputs: 'Mapping[str, Any] | None' = None, arm: 'str | None' = None, runs: 'int' = 10, rounds: 'int | None' = None, budget: 'Mapping[str, Any] | None' = None, seed: 'int' = 0, workers: 'int' = 1, exposures: 'bool' = False) -> 'EvaluationResult'
```

How ``focal`` does among ``background`` agents, compared with ``baseline`` in the same seats on the same seeds.

``suite`` is a contract, a list of scenarios, or a suite file (see :mod:`fg_env.evaluate.suite`); the other
arguments are defaults for scenarios that leave them out. ``seats`` are the agents the focal participant may
take (ids, or a type; default every starting agent). ``modes`` maps a mode name to the share of those seats the
focal participant takes (``{"resident": 0.75, "visitor": 0.25}``; default ``{"all": 1.0}``); which seats is
drawn from the seed, so every candidate evaluated with the same seed meets the same draw. ``background`` plays
every other agent (default: its type's policy, else random); ``baseline`` plays the focal seats in the paired
runs (default: the background). ``score`` scores each seat as in :func:`fg_env.rl.tournament`: by default the
returns the contract's ``game`` section declares, else the winner; or an output, an expression over ``$seat``,
or ``fn(result, seat)``.

Run *i* of every scenario uses the seeds of :func:`fg_env.experiment`. A focal run's score is the mean over its
focal seats (per focal agent), and its difference is that minus the baseline run's score over the same seats.
``budget`` caps each run on its own; ``exposures=True`` records what agents saw in every run. ``results`` keeps
every run: pair *i* is ``results[2i]`` (focal) and ``results[2i + 1]`` (baseline). Callable participants are
shared by all their runs; with ``workers > 1`` runs go to threads, or to processes when every participant is
given by name.

### `rl.EvaluationResult`

```pyi
EvaluationResult(focal: 'str', runs: 'int', seed: 'int', scenarios: 'list[dict[str, Any]]', modes: 'dict[str, dict[str, Any]]', tags: 'dict[str, dict[str, Any]]', splits: 'dict[str, dict[str, Any]]', overall: 'dict[str, Any]', pairs: 'list[dict[str, Any]]', notes: 'list[str]' = <factory>, results: 'list[RunResult]' = <factory>) -> None
```

``scenarios``: one row per scenario and mode. ``modes``, ``tags``, ``splits`` (``in_sample`` / ``held_out``,
when the suite holds scenarios out) and ``overall`` pool the run pairs they cover. Every pool has ``n`` scored
pairs, ``focal``, ``baseline`` and ``difference`` (focal − baseline) estimates with 95% intervals, ``clear``
(the interval excludes zero), ``unscored`` pairs and ``cost`` per side. ``pairs`` holds every run pair, and
``results`` every run: pair *i* is ``results[2i]`` (focal) and ``results[2i + 1]`` (baseline).

## `fg_env.engines`

Versioned, reusable behavioral engines bundled with :mod:`fg_env`.

This package deliberately contains engines, not finished environments, scenario
presets, or Arena games.  A builder clones an available engine and supplies the
roles, population, subject matter, and rules for its custom scenario.

### `engines.EngineCatalog`

```pyi
EngineCatalog(raw: 'Mapping[str, Any]')
```

Immutable view of the behavioral engines shipped in this SDK version.

### `engines.EngineNotFound`

An engine id is absent from the installed SDK catalog.

### `engines.EngineUnavailable`

An engine is defined but its reusable implementation is not shipped yet.

### `engines.EngineSpec`

```pyi
EngineSpec(id: 'str', title: 'str', summary: 'str', description: 'str', status: 'str', path: 'str | None' = None, resources: 'tuple[str, ...]' = ()) -> None
```

One reusable human-interaction engine.

### `engines.catalog`

```pyi
catalog() -> 'EngineCatalog'
```

Return the engine catalog shipped with the installed SDK version.

### `engines.list_engines`

```pyi
list_engines(*, available: 'bool | None' = None) -> 'list[EngineSpec]'
```

List behavioral engines, optionally filtered by implementation availability.

### `engines.get`

```pyi
get(engine_id: 'str') -> 'EngineSpec'
```

Resolve one behavioral engine by its stable id.

### `engines.clone`

```pyi
clone(engine_id: 'str', destination: 'str | Path', *, name: 'str | None' = None, overwrite: 'bool' = False) -> 'Path'
```

Clone a reusable engine contract into a project-owned JSON file.

### `engines.load`

```pyi
load(engine_id: 'str', *, inputs: 'Mapping[str, Any] | None' = None, seed: 'int' = 0) -> 'Any'
```

Load a reusable engine as :class:`fg_env.Env`.

## `fg_env.personas`

Shared persona/cohort sampling for every environment engine.

Sampling creates participants; it is not itself a behavioural engine.  The
functions here deliberately operate on ordinary mappings so an application can
feed census records, research panels, authored personas or fixed participants
without coupling the SDK to a particular database.

### `personas.PersonaSample`

```pyi
PersonaSample(people: 'tuple[dict[str, Any], ...]', provenance: 'SamplingProvenance') -> None
```

PersonaSample(people: 'tuple[dict[str, Any], ...]', provenance: 'SamplingProvenance')

### `personas.SamplingProvenance`

```pyi
SamplingProvenance(source: 'str', source_version: 'str | None', seed: 'int', run: 'int', resampled: 'bool', requested: 'int', selected: 'int', pool_size: 'int', constraints: 'dict[str, Any]', group_by: 'str | None', weight_field: 'str | None', fixed_ids: 'tuple[str, ...]', sampled_ids: 'tuple[str, ...]') -> None
```

SamplingProvenance(source: 'str', source_version: 'str | None', seed: 'int', run: 'int', resampled: 'bool', requested: 'int', selected: 'int', pool_size: 'int', constraints: 'dict[str, Any]', group_by: 'str | None', weight_field: 'str | None', fixed_ids: 'tuple[str, ...]', sampled_ids: 'tuple[str, ...]')

### `personas.sample_records`

```pyi
sample_records(records: 'Iterable[Mapping[str, Any]]', *, size: 'int', seed: 'int' = 0, run: 'int' = 0, resample: 'bool' = True, constraints: 'Mapping[str, Constraint] | None' = None, fixed: 'Iterable[Mapping[str, Any]] | None' = None, id_field: 'str' = 'id', group_by: 'str | None' = None, weight_field: 'str | None' = None, source: 'str' = 'records', source_version: 'str | None' = None) -> 'PersonaSample'
```

Sample role-neutral personas with replacement disabled and full provenance.

``fixed`` participants are always first and count toward ``size``.  ``group_by``
keeps related records together in draw order (Market uses household ids).  A
group may be truncated at the requested cohort size; no record is duplicated.
``run`` only changes the draw when ``resample`` is true, allowing experiments
to choose fixed-cohort repetition or a fresh cohort per run explicitly.

### `personas.assign_labels`

```pyi
assign_labels(records: 'Iterable[Mapping[str, Any]]', labels: 'Sequence[tuple[str, float]]', *, field: 'str' = 'role', seed: 'int' = 0) -> 'list[dict[str, Any]]'
```

Assign labels by proportional shares using largest remainder, then shuffle.

## Environment methods

### `Env.preview`

```pyi
preview(self, entity_id: 'str', stage: 'str | None' = None, participants: 'Any' = None) -> 'dict[str, Any]'
```

What the agent would receive on its next turn: brief, update, tools and time limit. Changes nothing.

Between rounds this plays the next round on a copy up to the agent's turn — scheduled
effects, start events, physics and the turns of agents before it — so the preview shows the
turn as the agent will get it. ``participants`` (as for :meth:`run`) plays those earlier turns; by default
the run's own participants do. Only free ones play: random, idle, a policy, or a callable you pass here. A
named LLM or search algorithm is never called — its agent plays its default (its type's policy, else random)
— and a run's own callables are not called either. A turn an `auto` stage plays without waking the agent is
skipped, as the run skips it.

### `Env.run`

```pyi
run(self, participants: 'Any' = None, *, rounds: 'int | None' = None, stop: 'Callable[[Env], bool] | None' = None, on_event: 'Callable[[dict[str, Any]], None] | None' = None, raise_errors: 'bool' = False, hosts: 'Any' = None, time_limit: 'float | None' = None, budget: 'Mapping[str, Any] | None' = None) -> 'RunResult'
```

Run to the end, or for ``rounds`` more rounds, or until ``stop(env)`` is true.

``participants`` is a callable for every agent, or a mapping from entity id, type or ``"*"`` to a participant (a
callable — plain or ``async def`` — ``"random"``, ``"idle"``, ``"policy:<name>"``). Agents without one use their
type's ``policy`` or ``"random"``. Every participant is offered the contract's in-turn host tools; ``hosts``
binds the run to host adapters first. ``time_limit`` sets :attr:`time_limit`, the wall-clock seconds per turn
for stages that set none; ``budget`` caps the run (:mod:`fg_env.runtime.budget`). In an event loop, use
:meth:`arun`.

``stop`` is checked before every round, stage, pass and sequential turn. A stopped run
continues exactly where it stopped on the next call; finishing a round that was
stopped part-way counts as one of ``rounds``.

### `Env.arun`

```pyi
arun(self, participants: 'Any' = None, *, rounds: 'int | None' = None, stop: 'Callable[[Env], bool] | None' = None, on_event: 'Callable[[dict[str, Any]], None] | None' = None, raise_errors: 'bool' = False, hosts: 'Any' = None, time_limit: 'float | None' = None, budget: 'Mapping[str, Any] | None' = None) -> 'RunResult'
```

:meth:`run` as a coroutine, for use inside a running event loop.

Async participants run on this loop — so clients bound to it work — and a simultaneous
stage's async participants run concurrently. The engine itself runs in a worker thread, so
the loop stays free while it plays; ``stop`` and ``on_event`` are called from that thread.
Cancelling the call stops the run at its next safe point.

### `Env.step`

```pyi
step(self, participants: 'Any' = None) -> 'RunResult'
```

Run exactly one round (or finish the round a stopped run is in).

### `Env.snapshot`

```pyi
snapshot(self) -> 'dict[str, Any]'
```

Everything needed to continue this run later, as JSON-safe data: between rounds, or stopped part-way
through a round (``run(stop=...)``).

### `Env.restore`

```pyi
restore(contract: 'Any', snapshot: 'Mapping[str, Any]', parallel: 'int' = 8, hosts: 'Any' = None, data_dir: 'Any' = None) -> 'Env'
```

Continue a run from :meth:`snapshot`. ``contract`` is the contract it was taken with
(a :class:`Contract`, dict, path or JSON text; the snapshot's arm is applied if needed).
``hosts`` answers host judgment; answers already recorded in the snapshot are never asked again.
The contract's files are found again in its folder (or ``data_dir``) and checked against the recorded hashes.

### `Env.clone`

```pyi
clone(self) -> 'Env'
```

An independent copy of this run now, continuing exactly as it would.

Between rounds it is a restored snapshot; a run stopped part-way through a round (``run(stop=...)``) is
copied by replaying it, so the copy stops at the same point. Inside a turn, use ``wake.clone()``.

### `Env.fork`

```pyi
fork(self, **changes: 'Any') -> 'Env'
```

A new run continuing this one from now under changes, leaving this run untouched: another ``arm``
(``None`` for none), ``inputs``, a contract ``patch`` or a whole replacement ``contract``, a ``seed`` for
the luck from here on, and intervention ``effects`` applied at the fork (logged as a `fork` event,
invariants checked). Without changes it is :meth:`clone`; like a clone, it keeps this run's participants.

Changes apply between rounds. Whatever the changed contract cannot hold of the current state is refused
with a :class:`~fg_env.ContractError` listing each problem and its fix. See :func:`fg_env.fork`.

### `Env.entity`

```pyi
entity(self, entity_id: 'str') -> 'dict[str, Any] | None'
```

A copy of one entity: ``{id, name, type, alive, at, props}``, or None.

### `Env.entities`

```pyi
entities(self, type_name: 'str | None' = None, alive: 'bool' = True) -> 'list[dict[str, Any]]'
```

Copies of entities, optionally of one type (subtypes included) and only alive ones.

### `Env.result`

```pyi
result(self) -> 'RunResult'
```



### `Env.spectate`

```pyi
spectate(self) -> 'dict[str, str]'
```

Every spectator view (``"for": "spectator"``) rendered against the world now, by name. Changes
nothing: views that draw randomness use a stream of their own.

## Detailed runtime behavior

See [running](reference-running.md) for participants, budgets, traces, snapshots and experiments.
