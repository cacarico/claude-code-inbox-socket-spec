# Checks that need no Claude Code and cost nothing: `make check`.
# `make conformance` runs the live check against your Claude Code; it starts
# throwaway `claude -p` sessions and spends a few cents of model use.

PYTHON ?= python3
PY_FILES = examples/send.py examples/waiter.py conformance/check.py tests/lint_docs.py tests/spec_table.py tests/test_clients.py

.PHONY: check lint table compile shellcheck test conformance

check: lint table compile shellcheck test

# Tags sit in the pages' Evidence tables; relative links and anchors resolve.
lint:
	$(PYTHON) tests/lint_docs.py

# The counts in SPEC.md's summary table match the tags on each page.
table:
	$(PYTHON) tests/spec_table.py

# Syntax only, without writing __pycache__.
compile:
	$(PYTHON) -c 'import sys; [compile(open(f).read(), f, "exec") for f in sys.argv[1:]]' $(PY_FILES)

shellcheck:
	@if command -v shellcheck >/dev/null 2>&1; then shellcheck examples/send.sh && echo "shellcheck: ok"; \
	else echo "SKIP shellcheck: not installed"; fi

# The example clients against a fake receiver; no model.
test:
	$(PYTHON) -m unittest discover -s tests -v

conformance:
	$(PYTHON) conformance/check.py
