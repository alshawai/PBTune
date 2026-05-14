# Restart Policy

> 33 nodes · cohesion 0.09

## Key Concepts

- **should_restart()** (16 connections) — `src/tuner/benchmark/restart_policy.py`
- **TestAdaptiveMode** (8 connections) — `tests/unit/core/test_restart_policy.py`
- **TestEdgeCases** (6 connections) — `tests/unit/core/test_restart_policy.py`
- **TestOfflineMode** (6 connections) — `tests/unit/core/test_restart_policy.py`
- **TestOnlineMode** (5 connections) — `tests/unit/core/test_restart_policy.py`
- **test_restart_policy.py** (5 connections) — `tests/unit/core/test_restart_policy.py`
- **.test_no_restart_when_not_required()** (4 connections) — `tests/unit/core/test_restart_policy.py`
- **.test_force_override()** (3 connections) — `tests/unit/core/test_restart_policy.py`
- **.test_restart_at_generation_zero()** (3 connections) — `tests/unit/core/test_restart_policy.py`
- **.test_force_always_wins()** (3 connections) — `tests/unit/core/test_restart_policy.py`
- **.test_none_generation_adaptive()** (3 connections) — `tests/unit/core/test_restart_policy.py`
- **.test_none_generation_offline()** (3 connections) — `tests/unit/core/test_restart_policy.py`
- **.test_custom_interval()** (2 connections) — `tests/unit/core/test_restart_policy.py`
- **.test_no_restart_off_boundary()** (2 connections) — `tests/unit/core/test_restart_policy.py`
- **.test_restart_at_interval_boundary()** (2 connections) — `tests/unit/core/test_restart_policy.py`
- **.test_tuning_mode_values()** (2 connections) — `tests/unit/core/test_restart_policy.py`
- **.test_force_restart_even_without_required()** (2 connections) — `tests/unit/core/test_restart_policy.py`
- **.test_restart_every_generation()** (2 connections) — `tests/unit/core/test_restart_policy.py`
- **.test_restart_when_required()** (2 connections) — `tests/unit/core/test_restart_policy.py`
- **.test_no_restart_even_at_interval_boundary()** (2 connections) — `tests/unit/core/test_restart_policy.py`
- **restart_policy.py** (2 connections) — `src/tuner/benchmark/restart_policy.py`
- **Restart Policy ===============  Pure-function restart decision logic based on tu** (1 connections) — `src/tuner/benchmark/restart_policy.py`
- **Decide whether to restart the database after configuration application.      Par** (1 connections) — `src/tuner/benchmark/restart_policy.py`
- **Unit tests for the RestartPolicy module (TuningMode + should_restart).** (1 connections) — `tests/unit/core/test_restart_policy.py`
- **Generation 0 is always a boundary (0 % N == 0).** (1 connections) — `tests/unit/core/test_restart_policy.py`
- *... and 8 more nodes in this community*

## Relationships

- [[Benchmark Orchestrator]] (1 shared connections)

## Source Files

- `src/tuner/benchmark/restart_policy.py`
- `tests/unit/core/test_restart_policy.py`

## Audit Trail

- EXTRACTED: 68 (72%)
- INFERRED: 27 (28%)
- AMBIGUOUS: 0 (0%)

---

*Part of the graphify knowledge wiki. See [[index]] to navigate.*