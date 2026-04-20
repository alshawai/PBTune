VENV_PYTHON := .venv/bin/python
PYTHON ?= $(if $(wildcard $(VENV_PYTHON)),$(VENV_PYTHON),python3)

.PHONY: install-dev test lint lint-fix typecheck check-all fix-and-check

install-dev:
	$(PYTHON) -m pip install -r requirements-dev.txt

test:
	$(PYTHON) -m pytest -q tests/unit

lint:
	$(PYTHON) -m ruff check src tests

lint-fix:
	$(PYTHON) -m ruff check --fix src tests
	$(PYTHON) -m ruff format src tests

typecheck:
	$(PYTHON) -m mypy src/evaluation src/utils src/scripts

check-all: lint typecheck test

fix-and-check: lint-fix lint typecheck test
