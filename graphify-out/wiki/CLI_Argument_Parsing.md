# CLI Argument Parsing

> 19 nodes · cohesion 0.13

## Key Concepts

- **execute()** (28 connections) — `src/benchmarks/executor.py`
- **WorkloadExecutor** (14 connections) — `src/tuner/benchmark/workload.py`
- **._execute_sequential()** (5 connections) — `src/tuner/benchmark/workload.py`
- **workload.py** (5 connections) — `src/tuner/benchmark/workload.py`
- **extract_workload_template_metadata()** (4 connections) — `src/tuner/benchmark/workload.py`
- **._execute_concurrent()** (4 connections) — `src/tuner/benchmark/workload.py`
- **._instantiate_query()** (4 connections) — `src/tuner/benchmark/workload.py`
- **load_from_file()** (3 connections) — `src/tuner/benchmark/workload.py`
- **_log_pg_error()** (3 connections) — `src/benchmarks/tpch/executor.py`
- **Template-Based Workload Executor =================================  Provides a S** (1 connections) — `src/tuner/benchmark/workload.py`
- **Execute template queries with optional concurrent execution.          Parameters** (1 connections) — `src/tuner/benchmark/workload.py`
- **Execute queries with multiple concurrent threads.** (1 connections) — `src/tuner/benchmark/workload.py`
- **Template-based SQL query executor.      Executes user-provided SQL queries for w** (1 connections) — `src/tuner/benchmark/workload.py`
- **Execute queries sequentially.** (1 connections) — `src/tuner/benchmark/workload.py`
- **Build normalized feature-extraction metadata from a template executor.** (1 connections) — `src/tuner/benchmark/workload.py`
- **Instantiate query template with random parameters.** (1 connections) — `src/tuner/benchmark/workload.py`
- **No-op execute method.** (1 connections) — `src/scripts/cleanup_instances.py`
- **Extract and log PostgreSQL diagnostic info from a psycopg2 exception.** (1 connections) — `src/benchmarks/tpch/executor.py`
- **Execute TPC-H benchmark and return performance metrics.** (1 connections) — `src/benchmarks/tpch/executor.py`

## Relationships

- [[Visualization Plotting]] (46 shared connections)
- [[Benchmark Executor Base]] (10 shared connections)
- [[Database Config & Connection]] (4 shared connections)
- [[Evaluator Fault Injection]] (4 shared connections)
- [[Sysbench Executor Tests]] (3 shared connections)
- [[Cross-Module Rationale]] (2 shared connections)
- [[Evaluator Core]] (2 shared connections)
- [[BO Config & Worker]] (2 shared connections)
- [[Benchmark Orchestrator]] (2 shared connections)
- [[Metric Config & Composite]] (1 shared connections)
- [[Sysbench Command Builder]] (1 shared connections)
- [[TPC-H Schema Tests]] (1 shared connections)

## Source Files

- `src/benchmarks/executor.py`
- `src/benchmarks/tpch/executor.py`
- `src/scripts/cleanup_instances.py`
- `src/tuner/benchmark/workload.py`

## Audit Trail

- EXTRACTED: 67 (84%)
- INFERRED: 13 (16%)
- AMBIGUOUS: 0 (0%)

---

*Part of the graphify knowledge wiki. See [[index]] to navigate.*