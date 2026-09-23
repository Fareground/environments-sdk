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

The package lives in `src/fg_env/`: the core modules at its top level, the engine catalog in
`src/fg_env/engines/`, persona sampling in `src/fg_env/personas.py`, and each larger part in its own subpackage.

| Module | Responsibility |
| --- | --- |
| `contract/` | The contract model: one pydantic document describing an environment |
| `api.py` | Public entry points: `parse`, `check`, `load`, `run` |
| `checks/` | Static checking: every issue with its path and a fix |
| `expr/`, `functions.py` | The expression language and its built-in `$functions` |
| `build.py`, `world.py` | Building the starting world; the live world of one run |
| `runtime.py` | The engine: rounds, stages, turns, events, ending, invariants, snapshots |
| `actions.py`, `effects.py` | Actions as typed tools; how actions, events and stages change the world |
| `session.py`, `perception.py`, `template.py` | The agent turn: brief, update, tools, rendered text |
| `participants.py` | Built-in participants (random, idle, policy, Anthropic, OpenAI) |
| `inputs.py`, `seeds.py`, `measure.py` | Caller inputs, the per-run seed tree, metrics and outputs |
| `experiments.py` | Arms × seeded runs |
| `guides/` | The generated authoring guide and JSON Schema |
| `cli/` | `fg-env check / run / preview / experiment / guide / schema` |
| `errors.py` | Typed errors |

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

1. Write `examples/contracts/<name>.json`, working from `fg-env guide`.
2. Make it check clean: `fg-env check examples/contracts/<name>.json`.
3. Write its golden with `FG_ENV_UPDATE_GOLDEN=1 PYTHONPATH=src python -m pytest tests/test_examples.py -q`,
   run the test again without it to confirm it reproduces, then commit `tests/golden/<name>.json` with the
   contract. A contract with no golden fails the test.
4. List it in the README examples section and the CHANGELOG.

### Contract JSON Schema

`schema/contract.schema.json` is the committed output of `fg-env schema`. CI fails if
the SDK's schema differs from it, so every change to the contract surface is deliberate.
After an intended contract change:

```bash
make schema          # regenerate: PYTHONPATH=src python -m fg_env schema
make check-schema    # what CI runs
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
ruff check src tests   # lint (what CI runs)
mypy                   # type-check
```

The kernel keeps a single runtime dependency (`pydantic`) — please keep it that way.
Optional features may add dev-only dependencies under `[project.optional-dependencies]`.

## Commits and pull requests

- Follow [Conventional Commits](https://www.conventionalcommits.org/):
  `feat:`, `fix:`, `refactor:`, `docs:`, `test:`, `chore:`, `perf:`, `ci:`.
- One logical change per PR; include tests for new behavior.
- Update `CHANGELOG.md` under `## [Unreleased]`.
- Run `pytest -n auto`, `ruff check src tests`, `mypy` and `make check-schema` locally before pushing.
- **Commits must not include AI or assistant co-author attribution** — no
  `Co-authored-by` trailers or generated-by notices for any AI tool.

## Documentation

The current Environments SDK documentation lives in `docs/sdk/`. Edit tutorials there. Regenerate the schema-derived reference with `PYTHONPATH=src python scripts/build_docs_reference.py`. The website imports these Markdown sources with `site/sync_sdk_docs.py`; do not independently edit its SDK copies. Run `pytest tests/test_documented_inventory.py` when changing the quickstart.
