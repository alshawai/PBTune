# Connection Reuse

> 12 nodes · cohesion 0.17

## Key Concepts

- **.__post_init__()** (9 connections) — `src/tuner/core/worker.py`
- **validate_sysbench_workload()** (8 connections) — `src/benchmarks/sysbench/executor.py`
- **_normalize_tuning_config()** (5 connections) — `src/evaluation/loader.py`
- **._resolve_effective_benchmark_params()** (5 connections) — `src/evaluation/runner.py`
- **._resolve_sysbench_workload()** (5 connections) — `src/evaluation/runner.py`
- **Validate configuration after initialization** (1 connections) — `src/tuner/config/tuner_config.py`
- **Initialize worker with random configuration if none provided.** (1 connections) — `src/tuner/core/worker.py`
- **Normalize runtime tuning metadata into canonical evaluation keys.      This keep** (1 connections) — `src/evaluation/loader.py`
- **Resolve effective benchmark runtime parameters using strict precedence.** (1 connections) — `src/evaluation/runner.py`
- **Resolve sysbench workload mode using CLI -> session -> default precedence.** (1 connections) — `src/evaluation/runner.py`
- **Validate and normalize a sysbench workload mode.** (1 connections) — `src/benchmarks/sysbench/executor.py`
- **Validate configuration** (1 connections) — `src/utils/metrics.py`

## Relationships

- [[PBT Literature & Papers]] (22 shared connections)
- [[BO Config & Worker]] (5 shared connections)
- [[Docker Environment Management]] (3 shared connections)
- [[Comparison Runner]] (2 shared connections)
- [[Population Initialization]] (1 shared connections)
- [[Evolution Algorithms]] (1 shared connections)
- [[Population Tests]] (1 shared connections)
- [[DB Connection Reuse]] (1 shared connections)
- [[BO Baseline & Workload]] (1 shared connections)
- [[Benchmark Executor Base]] (1 shared connections)
- [[Cross-Module Rationale]] (1 shared connections)

## Source Files

- `src/benchmarks/sysbench/executor.py`
- `src/evaluation/loader.py`
- `src/evaluation/runner.py`
- `src/tuner/config/tuner_config.py`
- `src/tuner/core/worker.py`
- `src/utils/metrics.py`

## Audit Trail

- EXTRACTED: 27 (69%)
- INFERRED: 12 (31%)
- AMBIGUOUS: 0 (0%)

---

*Part of the graphify knowledge wiki. See [[index]] to navigate.*