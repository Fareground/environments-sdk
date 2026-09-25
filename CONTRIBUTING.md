# Contributing

## Development setup

The distribution name is `fg-env` and the import package is `fg_env`.
Do not rename either — downstream projects depend on them.

```bash
git clone https://github.com/Fareground/environments-sdk
cd environments-sdk
python -m venv .venv
source .venv/bin/activate
pip install -e ".[dev]"
```

## Running the tests

The suite lives in `tests/`. Run all of it with:

```bash
pytest -n 2                                     # with an editable install
PYTHONPATH=src python -m pytest tests -q -n 2   # straight from a checkout (same as `make test`)
```

`-n 2` spreads the tests over two worker processes with pytest-xdist (part of the `dev` extra), which fits a
machine with 8 GB of memory; the make targets take `WORKERS` (`make test WORKERS=auto` uses every core where memory
allows). Leave it off (or use `-n 0`) to debug one test with `pdb` or `print` output.

While iterating, `make test-fast` runs everything except the tests marked `slow` (statistical and engine-behaviour
checks that take many seconds each) in a minute or two. Run the whole suite, `make test` (part of `make gate`), before
every push. Mark a new test `@pytest.mark.slow` when it takes more than a few seconds on its own.

A third tier runs only with `FG_ENV_SLOW=1`: thousands of fuzzed contracts, more seeds and playouts, every adversary
on every example, full engine and exchange sessions. `make test-slow` runs the whole suite with it on; run it before
a release and after changing the run's kernel, the game algorithms or the fuzzer. A test that needs minutes goes in
this tier (`pytest.mark.skipif(not os.environ.get("FG_ENV_SLOW"), ...)`, or more seeds when it is set).
`make test-oracle` runs the whole suite with every expression also evaluated by a second, reference evaluator
(`tests/expr_oracle.py`, with `FG_ENV_EXPR_ORACLE=1`) and the two results compared: run it after changing the expression
compiler.
`testpaths` is set to `tests` in `pyproject.toml`, so a bare `pytest` from the
repo root discovers everything. Narrow a run with `pytest tests/<file>.py -k <expr>`.

## Developing the Environment SDK

The package lives in `src/fg_env/`. `fg_env/__init__.py` is the public surface: every name in its `__all__`
and the subpackages it exports (`analysis`, `rl`, `engines`, `personas`, `participants`). Below it:

- **The core engine** is a few subpackages, one per part of a run: `world/` (the world's store — entities, links,
  records, the log, space — whose every change is journaled; how expressions read it; its luck; and building it from
  a contract), `runtime/` (the run's state, the rules, the schedule of rounds, stages and turns, driving participants
  under the run's one lock, what a run measures), `actions/` (legal actions and their arguments), `information/`
  (everything an agent is shown or offered — brief, update, views, inspect, tools — rendered through one gate for its
  reader),
  `effects/` (how rules change the world), `physics/` (continuous dynamics), `copying/` (branches, forks, replays,
  snapshots, stepped copies) and `sampling/` (seeded random streams and exact draws). `api.py` and `errors.py` sit
  beside them.
- **Each larger part is a subpackage** (`contract/`, `checks/`, `expr/`, `stdlib/`, `mechanisms/`, `guides/`,
  `authoring/`, `experiments/`, `cli/`, `analysis/`, `game/`, `host/` …) whose `__init__.py` docstring says what it
  holds.

The parts form layers, and a module imports at load time only from its own layer or the ones below:
the contract (`contract/`), then the expression language (`expr/`), then the world and what changes it (`world/`,
`effects/`, `actions/`, `information/`, `mechanisms/`, `host/` …), then runs (`runtime/`, `participants/`,
`copying/`, `checks/`, `api.py`), then the tools built on runs (`analysis/`, `report/`, `game/`, `authoring/`,
`guides/`, `cli/` …).
`tests/test_import_cycles.py` holds the exact list and fails on an import that points up; an import inside a function
that points up is allowed only where its `DEFERRED_UPWARD` list names it, and that list only shrinks.

Every module opens with a docstring saying what it is for: read those rather than a map here, which would go stale.
To find the code behind a behaviour, search for the error message or guide text it produces.

No module is named after a top-level function: `fg_env.check`, `fg_env.guide`, `fg_env.experiment` and `fg_env.fork`
are the functions; the code behind them is in `checks/`, `guides/`, `experiments/` and `copying/forks.py`.

### Golden-run fingerprints

`tests/test_examples.py` checks, runs and fingerprints every contract in
`examples/contracts/`. A fingerprint (status, rounds, metrics, event count, the first events
as readable lines, a hash of the whole event log, action and wake counts) is stored per example in
`tests/golden/`.
Floats are rounded to 10 significant digits so goldens hold on every supported Python.

A fingerprint mismatch means a run changed. If that is a bug, fix the code. If it is an
intended behaviour change, regenerate deliberately and review the golden diff:

```bash
FG_ENV_UPDATE_GOLDEN=1 PYTHONPATH=src python -m pytest tests/test_examples.py -q
```

Commit the regenerated goldens together with the change that caused them, and say why
in the commit message.

### Adding an example contract

1. Write `examples/contracts/<name>.json`, working from `fg-env guide authoring`, with a `description`.
2. Make it check clean: `fg-env check examples/contracts/<name>.json`.
3. Write its golden with `FG_ENV_UPDATE_GOLDEN=1 PYTHONPATH=src python -m pytest tests/test_examples.py -q`,
   run the test again without it to confirm it reproduces, then commit `tests/golden/<name>.json` with the
   contract. A contract with no golden fails the test.
4. Run `make docs`: it adds the contract to the table in `examples/README.md` from its `name` and `description`.

### Contract JSON Schema

`schema/contract.schema.json` is the committed output of `fg-env schema`. `make check-schema` fails if
the SDK's schema differs from it, so every change to the contract surface is deliberate.
After an intended contract change:

```bash
make schema          # regenerate: PYTHONPATH=src python -m fg_env schema
make check-schema    # fails if the committed schema is stale
```

### Public-surface changes need a CHANGELOG entry

Any change to the public surface — contract fields, expression functions, `fg_env`
exports, CLI commands and flags, participant behaviour, the JSON Schema, or output
shapes — needs an entry under `## [Unreleased]` in `CHANGELOG.md`. Mark breaking changes
**BREAKING** and say how to migrate.

## Lint and format

Tooling and rules are configured in `pyproject.toml` under `[tool.ruff]`
(target `py311`, line length 120):

```bash
make lint        # ruff check src tests scripts
make typecheck   # mypy
```

The kernel keeps a single runtime dependency (`pydantic`) — please keep it that way.
Optional features may add dev-only dependencies under `[project.optional-dependencies]`.

## Commits and pull requests

- Follow [Conventional Commits](https://www.conventionalcommits.org/):
  `feat:`, `fix:`, `refactor:`, `docs:`, `test:`, `chore:`, `perf:`, `ci:`.
- One logical change per PR; include tests for new behavior.
- Update `CHANGELOG.md` under `## [Unreleased]`.
- There is no CI: the tests and checks run locally. Use `make test-fast` while iterating, and before every push run
  `make gate`: the whole suite, ruff, mypy, `make check-schema` and `make check-docs`. `make` uses `.venv/bin/python`
  when it exists; pass `PYTHON=...` for another interpreter.
- A pushed `v*` tag publishes that commit to PyPI without running anything: run the same checks on it first.
- **Commits must not include AI or assistant co-author attribution** — no
  `Co-authored-by` trailers or generated-by notices for any AI tool.

## Documentation

The documentation is `fg_env.guide`: the start page (`src/fg_env/guides/authoring.py`), the cookbook (the recipe
contracts in `src/fg_env/authoring/recipes/`, with their known answers in `authoring/scaffold.py`), the prose parts
(`guides/text.py`) and the pages generated from the models, functions, effects, mechanisms and engines. Every page in
`docs/sdk/` is rendered from it, with the Python API page and the contracts table in `examples/README.md`, except
`index.md` and `migration.md`, which are written by hand. Edit the guide, never a generated page.

```bash
make docs          # regenerate: PYTHONPATH=src python scripts/build_docs_reference.py
make check-docs    # fails if a generated file differs from the committed one
```

Every python and bash sample in `README.md` and in `docs/sdk/` runs under `tests/test_doc_samples.py`, which also
holds every cookbook recipe to its known answer. The website imports these Markdown sources with
`site/sync_sdk_docs.py`; do not independently edit its SDK copies.
