# Evaluation Tuning Tests

> 34 nodes · cohesion 0.11

## Key Concepts

- **DatabaseConfig** (63 connections) — `src/config/database.py`
- **TestReliabilityGate** (20 connections) — `tests/unit/core/test_evaluator_fault_injection.py`
- **_HealthyBenchmarkExecutor** (17 connections) — `tests/unit/core/test_dead_rescue_convergence_and_restart.py`
- **_ClosedConnection** (16 connections) — `tests/unit/core/test_evaluator_fault_injection.py`
- **_FailingBenchmarkExecutor** (14 connections) — `tests/unit/core/test_evaluator_fault_injection.py`
- **_InvalidBenchmarkExecutor** (14 connections) — `tests/unit/core/test_evaluator_fault_injection.py`
- **test_dead_rescue_convergence_and_restart.py** (12 connections) — `tests/unit/core/test_dead_rescue_convergence_and_restart.py`
- **_make_worker()** (11 connections) — `tests/unit/core/test_evaluator_fault_injection.py`
- **test_evaluator_fault_injection.py** (11 connections) — `tests/unit/core/test_evaluator_fault_injection.py`
- **_make_evaluator()** (9 connections) — `tests/unit/core/test_evaluator_fault_injection.py`
- **ApplicationResult** (7 connections) — `src/utils/applicator.py`
- **test_evaluate_worker_consumes_force_restart_marker()** (6 connections) — `tests/unit/core/test_dead_rescue_convergence_and_restart.py`
- **test_evaluate_worker_raises_on_benchmark_execution_failure()** (6 connections) — `tests/unit/core/test_evaluator_fault_injection.py`
- **test_apply_configuration_force_restart_overrides_interval_deferral()** (5 connections) — `tests/unit/core/test_dead_rescue_convergence_and_restart.py`
- **test_ensure_benchmark_ready_raises_if_schema_still_invalid()** (5 connections) — `tests/unit/core/test_evaluator_fault_injection.py`
- **evaluator()** (3 connections) — `tests/unit/core/test_evaluator_fault_injection.py`
- **.get_connection_string()** (2 connections) — `src/config/database.py`
- **.get_sqlalchemy_url()** (2 connections) — `src/config/database.py`
- **Get SQLAlchemy database URL.          Returns         -------         str** (1 connections) — `src/config/database.py`
- **Database configuration loaded from environment variables.      This class provid** (1 connections) — `src/config/database.py`
- **Get PostgreSQL connection string.          Parameters         ----------** (1 connections) — `src/config/database.py`
- **Regression tests for dead-worker rescue convergence and restart guards.** (1 connections) — `tests/unit/core/test_dead_rescue_convergence_and_restart.py`
- **Forced restart must execute even when restart interval would defer it.** (1 connections) — `tests/unit/core/test_dead_rescue_convergence_and_restart.py`
- **WorkloadOrchestrator should forward and clear force-restart marker after success** (1 connections) — `tests/unit/core/test_dead_rescue_convergence_and_restart.py`
- **Benchmark executor stub that always returns valid metrics.** (1 connections) — `tests/unit/core/test_dead_rescue_convergence_and_restart.py`
- *... and 9 more nodes in this community*

## Relationships

- [[Database Config & Connection]] (113 shared connections)
- [[Evaluator Fault Injection]] (17 shared connections)
- [[Benchmark Executor Base]] (14 shared connections)
- [[Metric Config Recalibration]] (13 shared connections)
- [[Population Initialization]] (12 shared connections)
- [[Benchmark Orchestrator]] (8 shared connections)
- [[Bare Metal Tests]] (6 shared connections)
- [[Sysbench Executor Tests]] (5 shared connections)
- [[Scoring & Weight Policies]] (5 shared connections)
- [[Metric Instrumentation]] (5 shared connections)
- [[DB Connection Reuse]] (5 shared connections)
- [[Visualization Plotting]] (4 shared connections)

## Source Files

- `src/config/database.py`
- `src/utils/applicator.py`
- `tests/unit/core/test_dead_rescue_convergence_and_restart.py`
- `tests/unit/core/test_evaluator_fault_injection.py`

## Audit Trail

- EXTRACTED: 130 (54%)
- INFERRED: 109 (46%)
- AMBIGUOUS: 0 (0%)

---

*Part of the graphify knowledge wiki. See [[index]] to navigate.*