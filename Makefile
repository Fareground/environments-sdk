# The project's virtualenv when there is one, so `make` never runs some other Python on the PATH.
PYTHON ?= $(if $(wildcard .venv/bin/python),.venv/bin/python,python)
SCHEMA := schema/contract.schema.json
RUN := PYTHONPATH=src $(PYTHON)

.PHONY: gate test test-fast lint typecheck schema check-schema docs check-docs

# Everything that must pass before a push: the whole suite, lint, types, the schema and the generated docs.
gate: test lint typecheck check-schema check-docs

# The whole suite: run it before every push.
test:
	$(RUN) -m pytest tests -q -n auto

# Everything but the tests marked slow (statistical and engine-behaviour checks): the loop while iterating.
test-fast:
	$(RUN) -m pytest tests -q -n auto -m "not slow"

lint:
	$(PYTHON) -m ruff check src tests scripts

typecheck:
	$(PYTHON) -m mypy

# Regenerate the committed contract JSON Schema after a deliberate contract change.
schema:
	$(RUN) -m fg_env schema > $(SCHEMA).tmp
	mv $(SCHEMA).tmp $(SCHEMA)

# Fail if the SDK's contract schema differs from the committed file.
check-schema:
	$(RUN) -m fg_env schema > $(SCHEMA).tmp
	@if diff -u $(SCHEMA) $(SCHEMA).tmp; then rm -f $(SCHEMA).tmp; else \
		rm -f $(SCHEMA).tmp; \
		echo "The contract schema changed. If intended, run 'make schema', commit $(SCHEMA) and add a CHANGELOG entry." >&2; \
		exit 1; \
	fi

# Regenerate the docs (every docs/sdk page but index.md and migration.md) and the examples table after a change to
# the guides, a recipe, the public API, an engine or an example contract.
docs:
	$(RUN) scripts/build_docs_reference.py

# Fail if the generated docs differ from the committed files.
check-docs:
	$(RUN) scripts/build_docs_reference.py --check
