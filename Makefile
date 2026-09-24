PYTHON ?= python
SCHEMA := schema/contract.schema.json
RUN := PYTHONPATH=src $(PYTHON)

.PHONY: test test-fast lint schema check-schema docs check-docs

# The whole suite: run it before every push.
test:
	$(RUN) -m pytest tests -q -n auto

# Everything but the tests marked slow (statistical and engine-behaviour checks): the loop while iterating.
test-fast:
	$(RUN) -m pytest tests -q -n auto -m "not slow"

lint:
	ruff check src tests scripts

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

# Regenerate the reference docs (docs/sdk/reference*.md, docs/sdk/api.md) and the examples table after a change to
# the guides, the public API or an example contract.
docs:
	$(RUN) scripts/build_docs_reference.py

# Fail if the generated docs differ from the committed files.
check-docs:
	$(RUN) scripts/build_docs_reference.py --check
