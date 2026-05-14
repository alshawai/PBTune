# Hardware Normalization Tests

> 28 nodes · cohesion 0.10

## Key Concepts

- **WorkloadOrchestrator** (37 connections) — `src/tuner/benchmark/orchestrator.py`
- **get_logger()** (18 connections) — `src/utils/logger/setup.py`
- **.evaluate_worker()** (16 connections) — `src/tuner/main.py`
- **.apply_configuration()** (6 connections) — `src/tuner/benchmark/orchestrator.py`
- **.refine_workload_features_from_generation()** (5 connections) — `src/tuner/benchmark/orchestrator.py`
- **._vacuum_after_dml()** (5 connections) — `src/tuner/benchmark/orchestrator.py`
- **._perform_restart()** (4 connections) — `src/tuner/benchmark/orchestrator.py`
- **._refine_workload_features()** (4 connections) — `src/tuner/benchmark/orchestrator.py`
- **._build_failure_result()** (4 connections) — `src/tuner/main.py`
- **._apply_reliability_gate()** (3 connections) — `src/tuner/benchmark/orchestrator.py`
- **.collect_system_metrics()** (3 connections) — `src/tuner/benchmark/orchestrator.py`
- **._ensure_benchmark_ready()** (3 connections) — `src/tuner/benchmark/orchestrator.py`
- **orchestrator.py** (2 connections) — `src/tuner/benchmark/orchestrator.py`
- **Workload Orchestrator for Database Tuning ======================================** (1 connections) — `src/tuner/benchmark/orchestrator.py`
- **Refine workload features using aggregated metrics from all workers in a generati** (1 connections) — `src/tuner/benchmark/orchestrator.py`
- **Main WorkloadOrchestrator class for workload execution and performance measureme** (1 connections) — `src/tuner/benchmark/orchestrator.py`
- **Apply knob configuration and optionally restart via policy.          This method** (1 connections) — `src/tuner/benchmark/orchestrator.py`
- **Restart PostgreSQL via the injected environment.          Parameters         ---** (1 connections) — `src/tuner/benchmark/orchestrator.py`
- **Collect system-level metrics by delegating to the environment.          Memory u** (1 connections) — `src/tuner/benchmark/orchestrator.py`
- **Run bounded post-workload maintenance after DML-heavy workloads.          Full-d** (1 connections) — `src/tuner/benchmark/orchestrator.py`
- **Validate benchmark state before execution and repair it if needed.** (1 connections) — `src/tuner/benchmark/orchestrator.py`
- **Evaluate a Worker's configuration.          This is the main evaluation method c** (1 connections) — `src/tuner/benchmark/orchestrator.py`
- **Configuration for WorkloadOrchestrator behavior.      Parameters     ----------** (1 connections) — `src/tuner/benchmark/orchestrator.py`
- **Classify the evaluation result and set ``failure_type`` if degraded.          Th** (1 connections) — `src/tuner/benchmark/orchestrator.py`
- **Refine static workload features with runtime observations using EMA blending.** (1 connections) — `src/tuner/benchmark/orchestrator.py`
- *... and 3 more nodes in this community*

## Relationships

- [[Benchmark Orchestrator]] (79 shared connections)
- [[Scoring & Weight Policies]] (7 shared connections)
- [[Database Config & Connection]] (7 shared connections)
- [[BO Baseline & Workload]] (5 shared connections)
- [[Evaluator Fault Injection]] (4 shared connections)
- [[Cross-Module Rationale]] (2 shared connections)
- [[Population Initialization]] (2 shared connections)
- [[Metric Config & Composite]] (2 shared connections)
- [[Visualization Plotting]] (2 shared connections)
- [[Performance Metrics]] (2 shared connections)
- [[PBT Worker Core]] (2 shared connections)
- [[Benchmark Executor Base]] (1 shared connections)

## Source Files

- `src/tuner/benchmark/orchestrator.py`
- `src/tuner/main.py`
- `src/utils/logger/setup.py`

## Audit Trail

- EXTRACTED: 74 (59%)
- INFERRED: 51 (41%)
- AMBIGUOUS: 0 (0%)

---

*Part of the graphify knowledge wiki. See [[index]] to navigate.*