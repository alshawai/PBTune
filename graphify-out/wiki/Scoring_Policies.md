# Scoring Policies

> 25 nodes · cohesion 0.09

## Key Concepts

- **conftest.py** (11 connections) — `tests/conftest.py`
- **make_run_result()** (6 connections) — `tests/unit/evaluation/conftest.py`
- **.test_mismatched_lengths_raise()** (4 connections) — `tests/unit/evaluation/test_evaluate_tuning.py`
- **.test_no_improvement_not_significant()** (4 connections) — `tests/unit/evaluation/test_evaluate_tuning.py`
- **default_runs()** (3 connections) — `tests/unit/evaluation/conftest.py`
- **sample_worker_resources()** (3 connections) — `tests/unit/evaluation/conftest.py`
- **tuned_runs()** (3 connections) — `tests/unit/evaluation/conftest.py`
- **sample_session_file()** (2 connections) — `tests/unit/evaluation/conftest.py`
- **data_dir()** (2 connections) — `tests/conftest.py`
- **project_root()** (2 connections) — `tests/conftest.py`
- **pytest_configure()** (2 connections) — `tests/conftest.py`
- **test_data_dir()** (2 connections) — `tests/conftest.py`
- **Shared fixtures for evaluate_tuning unit tests.  Uses in-memory fakes so no Dock** (1 connections) — `tests/unit/evaluation/conftest.py`
- **Five default-config runs with realistic variance.** (1 connections) — `tests/unit/evaluation/conftest.py`
- **Five tuned-config runs showing clear improvement.** (1 connections) — `tests/unit/evaluation/conftest.py`
- **Write a valid PBT results JSON to a temp directory and return the path.** (1 connections) — `tests/unit/evaluation/conftest.py`
- **Return the WorkerResources matching the sample session.** (1 connections) — `tests/unit/evaluation/conftest.py`
- **Factory fixture: create a RunResult with deterministic values.      Usage::** (1 connections) — `tests/unit/evaluation/conftest.py`
- **Mismatched lengths should fail to protect pair integrity.** (1 connections) — `tests/unit/evaluation/test_evaluate_tuning.py`
- **When both configs produce identical scores → not significant.** (1 connections) — `tests/unit/evaluation/test_evaluate_tuning.py`
- **Shared pytest fixtures and configuration for all tests.  This file is automatica** (1 connections) — `tests/conftest.py`
- **Provide the project root directory.** (1 connections) — `tests/conftest.py`
- **Provide the data directory.** (1 connections) — `tests/conftest.py`
- **Provide a temporary directory for test data.** (1 connections) — `tests/conftest.py`
- **Configure custom pytest markers.** (1 connections) — `tests/conftest.py`

## Relationships

- [[Docker Environment Tests]] (52 shared connections)
- [[Evaluation Tuning Tests]] (4 shared connections)
- [[Hardware Normalization Tests]] (1 shared connections)

## Source Files

- `tests/conftest.py`
- `tests/unit/evaluation/conftest.py`
- `tests/unit/evaluation/test_evaluate_tuning.py`

## Audit Trail

- EXTRACTED: 50 (88%)
- INFERRED: 7 (12%)
- AMBIGUOUS: 0 (0%)

---

*Part of the graphify knowledge wiki. See [[index]] to navigate.*