# Score Normalization Tests

> 12 nodes · cohesion 0.21

## Key Concepts

- **_make_tuner()** (6 connections) — `tests/unit/core/test_tuner_shutdown.py`
- **test_tuner_shutdown.py** (6 connections) — `tests/unit/core/test_tuner_shutdown.py`
- **test_run_stops_instances_on_keyboard_interrupt_during_setup()** (3 connections) — `tests/unit/core/test_tuner_shutdown.py`
- **test_run_stops_instances_on_normal_exit()** (3 connections) — `tests/unit/core/test_tuner_shutdown.py`
- **test_run_stops_instances_when_setup_fails()** (3 connections) — `tests/unit/core/test_tuner_shutdown.py`
- **test_evaluate_worker_handles_recovery_exception_after_connection_failure()** (2 connections) — `tests/unit/core/test_tuner_shutdown.py`
- **Tests for PBTTuner shutdown behavior across normal and forced exits.** (1 connections) — `tests/unit/core/test_tuner_shutdown.py`
- **run() should stop instances when Ctrl+C interrupts setup.** (1 connections) — `tests/unit/core/test_tuner_shutdown.py`
- **Recovery failures after connection errors should not escape evaluate_worker.** (1 connections) — `tests/unit/core/test_tuner_shutdown.py`
- **Create a PBTTuner object with minimal state needed for run() tests.** (1 connections) — `tests/unit/core/test_tuner_shutdown.py`
- **run() should stop instances after normal completion.** (1 connections) — `tests/unit/core/test_tuner_shutdown.py`
- **run() should still stop instances when setup raises runtime errors.** (1 connections) — `tests/unit/core/test_tuner_shutdown.py`

## Relationships

- [[Instance Lifecycle]] (28 shared connections)
- [[Database Config & Connection]] (1 shared connections)

## Source Files

- `tests/unit/core/test_tuner_shutdown.py`

## Audit Trail

- EXTRACTED: 28 (97%)
- INFERRED: 1 (3%)
- AMBIGUOUS: 0 (0%)

---

*Part of the graphify knowledge wiki. See [[index]] to navigate.*