# Evaluator Fault Injection

> 29 nodes · cohesion 0.09

## Key Concepts

- **BenchmarkExecutor** (24 connections)
- **prepare()** (21 connections) — `src/benchmarks/executor.py`
- **TPCHExecutor** (19 connections) — `src/benchmarks/tpch/executor.py`
- **validate()** (16 connections) — `src/benchmarks/executor.py`
- **executor.py** (14 connections) — `src/benchmarks/executor.py`
- **_NoopBenchmarkExecutor** (9 connections) — `src/scripts/cleanup_instances.py`
- **._drop_existing_public_tables()** (7 connections) — `src/benchmarks/tpch/executor.py`
- **._create_executor()** (6 connections) — `src/evaluation/runner.py`
- **ABC** (4 connections)
- **_parse_output()** (4 connections) — `src/benchmarks/sysbench/executor.py`
- **_parse_histogram()** (2 connections) — `src/benchmarks/sysbench/executor.py`
- **_strip_trailing_delimiter()** (2 connections) — `src/benchmarks/tpch/executor.py`
- **Create required sbtest tables using native sysbench C-binary.** (1 connections) — `src/tuner/benchmark/workload.py`
- **Check if all required sbtest tables exist.** (1 connections) — `src/tuner/benchmark/workload.py`
- **External Benchmark Executors =============================  Provides interfaces** (1 connections) — `src/benchmarks/executor.py`
- **Abstract interface for external benchmarking tools.      Subclasses wrap standar** (1 connections) — `src/benchmarks/executor.py`
- **Drop all existing tables in PostgreSQL public schema.          Args:** (1 connections) — `src/benchmarks/executor.py`
- **Raised when a benchmark run fails inside an evaluation container.      Covers sy** (1 connections) — `src/evaluation/exceptions.py`
- **Create the appropriate BenchmarkExecutor for the configured benchmark.** (1 connections) — `src/evaluation/runner.py`
- **Minimal schema provider used for environment lifecycle-only cleanup.** (1 connections) — `src/scripts/cleanup_instances.py`
- **No-op prepare method.** (1 connections) — `src/scripts/cleanup_instances.py`
- **No-op validate method.** (1 connections) — `src/scripts/cleanup_instances.py`
- **Return True only when schema shape matches the configured Sysbench profile.** (1 connections) — `src/benchmarks/sysbench/executor.py`
- **Run native `sysbench prepare` to create all sbtest tables.** (1 connections) — `src/benchmarks/sysbench/executor.py`
- **queries()** (1 connections) — `src/benchmarks/tpch/executor.py`
- *... and 4 more nodes in this community*

## Relationships

- [[Benchmark Executor Base]] (76 shared connections)
- [[Database Config & Connection]] (14 shared connections)
- [[Visualization Plotting]] (10 shared connections)
- [[Sysbench Executor Tests]] (5 shared connections)
- [[Metric Config & Composite]] (4 shared connections)
- [[BO Config & Worker]] (4 shared connections)
- [[BO Baseline & Workload]] (4 shared connections)
- [[Bare Metal Environment]] (3 shared connections)
- [[Comparison Runner]] (3 shared connections)
- [[TPC-H Schema Tests]] (3 shared connections)
- [[Bare Metal Memory Tests]] (2 shared connections)
- [[TPC-H Query Executor]] (2 shared connections)

## Source Files

- `src/benchmarks/executor.py`
- `src/benchmarks/sysbench/executor.py`
- `src/benchmarks/tpch/executor.py`
- `src/evaluation/exceptions.py`
- `src/evaluation/runner.py`
- `src/scripts/cleanup_instances.py`
- `src/tuner/benchmark/workload.py`

## Audit Trail

- EXTRACTED: 101 (70%)
- INFERRED: 44 (30%)
- AMBIGUOUS: 0 (0%)

---

*Part of the graphify knowledge wiki. See [[index]] to navigate.*