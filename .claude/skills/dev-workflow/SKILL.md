---
name: dev-workflow
description: >
  Development workflow, CI gates, testing patterns, and contribution conventions for the
  PBT PostgreSQL tuning project. Covers make targets, pytest structure, ruff linting, mypy
  type checking, git branching, and commit conventions. Use this skill when running tests,
  fixing lint errors, adding new test files, setting up development environment, or
  preparing changes for commit.
---

# Development Workflow

## CI Gates (Makefile)

```bash
make lint           # ruff check src tests
make typecheck      # mypy src/evaluation src/utils src/scripts
make test           # pytest -q tests/unit
make check-all      # lint + typecheck + test (run before committing)
make lint-fix       # Auto-fix + format
```

The virtual environment is auto-detected: `.venv/bin/python` if present, else `python3`.

## Ruff Configuration (pyproject.toml)

- Line length: 88
- Target: Python 3.11
- Selected rules: E9, F63, F7, F82, F401, F841, I, UP, B
- Legacy per-file ignores: `src/**` and `tests/**` skip I001 and UP rules

## Mypy Scope

Type checking covers: `src/evaluation`, `src/utils`, `src/scripts`
- `ignore_missing_imports = true` (third-party stubs not required)
- `follow_imports = "skip"` (only checks listed files)

## Test Structure

```
tests/unit/
├── analysis/     # data_loader, importance
├── benchmarks/   # sysbench, tpch executor tests
├── config/       # hardware normalization
├── core/         # population, evaluator, CLI, warm start, saturation
├── evaluation/   # comparative evaluation, public API
├── knobs/        # metadata loader, policy loader
├── scoring/      # normalizer, weight model, workload features
├── scripts/      # cleanup instances
└── utils/        # environments, hardware info, instrumentation
```

All tests use `pytest`. Common fixtures are in `tests/conftest.py` and
`tests/unit/evaluation/conftest.py`.

## Git Conventions

- Branch naming: `{type}/{description}` (e.g., `feat/scoring-v2`, `fix/normalizer-drift`)
- Commit format: Conventional Commits (`feat(scope): description`)
- Use the `/commit` workflow for smart commits with pre-flight checks

## Adding a New Test

1. Create test file in the matching `tests/unit/{area}/` directory
2. Name: `test_{module_name}.py`
3. Import the module under test directly
4. Use `pytest.fixture` for shared setup
5. Run `make test` to verify

## Dependencies

- Runtime: `requirements.txt` (psycopg2, numpy, pandas, scipy, docker, fanova, shap)
- Dev: `requirements-dev.txt` (pytest, ruff, mypy)
- Install: `pip install -r requirements.txt -r requirements-dev.txt`
