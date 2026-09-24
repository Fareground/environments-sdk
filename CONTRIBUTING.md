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
pytest -n auto                                     # with an editable install
PYTHONPATH=src python -m pytest tests -q -n auto   # straight from a checkout (same as `make test`)
```

`-n auto` spreads the tests over every CPU core with pytest-xdist (part of the `dev` extra); the suite is
several times faster that way. Leave it off (or use `-n 0`) to debug one test with `pdb` or `print` output.
`testpaths` is set to `tests` in `pyproject.toml`, so a bare `pytest` from the
repo root discovers everything. Narrow a run with `pytest tests/<file>.py -k <expr>`.

## Developing the Environment SDK

The package lives in `src/fg_env/`. `fg_env/__init__.py` is the public surface: every name in its `__all__`
and the subpackages it exports (`analysis`, `rl`, `engines`, `personas`, `participants`). Below it:

- **The core engine** is the modules at the top level of `fg_env/`: building the world, running rounds, stages and
  turns, actions and effects, what each agent perceives, measurements. Related modules share a name prefix
  (`run_*`, `action_*`, `*physics*`).
- **Each larger part is a subpackage** (`contract/`, `checks/`, `expr/`, `stdlib/`, `mechanisms/`, `guides/`,
  `cli/`, `analysis/`, `game/`, `host/` …) whose `__init__.py` docstring says what it holds.

Every module opens with a docstring saying what it is for: read those rather than a map here, which would go stale.
To find the code behind a behaviour, search for the error message or guide text it produces.

No module is named after a top-level function: `fg_env.check`, `fg_env.guide`, `fg_env.experiment` and `fg_env.fork`
are the functions; the code behind them is in `checks/`, `guides/`, `experiments.py` and `forks.py`.

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
(target `py311`, line length 100):

```bash
ruff check src tests scripts   # lint
mypy                           # type-check
```

The kernel keeps a single runtime dependency (`pydantic`) — please keep it that way.
Optional features may add dev-only dependencies under `[project.optional-dependencies]`.

## Commits and pull requests

- Follow [Conventional Commits](https://www.conventionalcommits.org/):
  `feat:`, `fix:`, `refactor:`, `docs:`, `test:`, `chore:`, `perf:`, `ci:`.
- One logical change per PR; include tests for new behavior.
- Update `CHANGELOG.md` under `## [Unreleased]`.
- There is no CI test run: run `pytest -n auto`, `ruff check src tests scripts`, `mypy`, `make check-schema` and
  `make check-docs` locally before pushing.
- **Commits must not include AI or assistant co-author attribution** — no
  `Co-authored-by` trailers or generated-by notices for any AI tool.

## Documentation

The Environments SDK documentation lives in `docs/sdk/`. Edit the tutorials there. The reference pages
(`docs/sdk/reference*.md`, `docs/sdk/api.md`) and the contracts table in `examples/README.md` are generated from the
SDK's guides, public API and example contracts: never edit them by hand.

```bash
make docs          # regenerate: PYTHONPATH=src python scripts/build_docs_reference.py
make check-docs    # fails if a generated file differs from the committed one
```

Every python and bash sample in `README.md` and the hand-written `docs/sdk/` pages runs under
`tests/test_doc_samples.py`. The website imports these Markdown sources with `site/sync_sdk_docs.py`; do not
independently edit its SDK copies. Run `pytest tests/test_documented_inventory.py` when changing the quickstart.
