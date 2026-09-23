PYTHON ?= python
SCHEMA := schema/contract.schema.json
RUN := PYTHONPATH=src $(PYTHON)

.PHONY: test lint schema check-schema

test:
	$(RUN) -m pytest tests -q -n auto

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
