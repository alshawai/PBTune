# Comparison Runner

> 42 nodes · cohesion 0.08

## Key Concepts

- **SysbenchExecutor** (33 connections) — `src/benchmarks/sysbench/executor.py`
- **test_sysbench_executor_validation.py** (21 connections) — `tests/unit/benchmarks/test_sysbench_executor_validation.py`
- **_FakeCursor** (12 connections) — `tests/unit/benchmarks/test_sysbench_executor_validation.py`
- **_FakeConnection** (10 connections) — `tests/unit/benchmarks/test_sysbench_executor_validation.py`
- **_PrepareCursorStub** (9 connections) — `tests/unit/benchmarks/test_sysbench_executor_validation.py`
- **_make_db_config()** (6 connections) — `tests/unit/benchmarks/test_sysbench_executor_validation.py`
- **test_prepare_drops_tpch_leftovers_before_sysbench_prepare()** (6 connections) — `tests/unit/benchmarks/test_sysbench_executor_validation.py`
- **test_validate_accepts_exact_table_set_and_row_count()** (6 connections) — `tests/unit/benchmarks/test_sysbench_executor_validation.py`
- **test_validate_rejects_extra_tables_from_previous_profile()** (6 connections) — `tests/unit/benchmarks/test_sysbench_executor_validation.py`
- **test_validate_rejects_row_cardinality_mismatch()** (6 connections) — `tests/unit/benchmarks/test_sysbench_executor_validation.py`
- **test_default_sysbench_workload_is_read_write()** (3 connections) — `tests/unit/benchmarks/test_sysbench_executor_validation.py`
- **test_invalid_sysbench_workload_mode_raises()** (3 connections) — `tests/unit/benchmarks/test_sysbench_executor_validation.py`
- **test_sysbench_executor_interval_variance_consistency()** (3 connections) — `tests/unit/benchmarks/test_sysbench_executor_validation.py`
- **test_sysbench_executor_interval_variance_with_scale_factors()** (3 connections) — `tests/unit/benchmarks/test_sysbench_executor_validation.py`
- **test_sysbench_executor_p99_latency_tracking()** (3 connections) — `tests/unit/benchmarks/test_sysbench_executor_validation.py`
- **test_sysbench_executor_p99_with_different_thread_counts()** (3 connections) — `tests/unit/benchmarks/test_sysbench_executor_validation.py`
- **test_sysbench_interval_variance_tracking()** (3 connections) — `tests/unit/benchmarks/test_sysbench_executor_validation.py`
- **test_sysbench_p99_latency_metric_available()** (3 connections) — `tests/unit/benchmarks/test_sysbench_executor_validation.py`
- **test_sysbench_workload_mode_is_accepted()** (3 connections) — `tests/unit/benchmarks/test_sysbench_executor_validation.py`
- **.fetchall()** (3 connections) — `tests/unit/benchmarks/test_tpch_executor_schema_cleanup.py`
- **test_sysbench_parse_output_extracts_latency_p95()** (2 connections) — `tests/unit/benchmarks/test_sysbench_executor_validation.py`
- **test_sysbench_parse_output_handles_missing_latency_p95()** (2 connections) — `tests/unit/benchmarks/test_sysbench_executor_validation.py`
- **Unit tests for strict Sysbench schema-profile validation.** (1 connections) — `tests/unit/benchmarks/test_sysbench_executor_validation.py`
- **Validation should pass only when schema matches configured profile shape.** (1 connections) — `tests/unit/benchmarks/test_sysbench_executor_validation.py`
- **Rapid profile should reject leftover standard schema (10 tables instead of 2).** (1 connections) — `tests/unit/benchmarks/test_sysbench_executor_validation.py`
- *... and 17 more nodes in this community*

## Relationships

- [[Sysbench Executor Tests]] (136 shared connections)
- [[Bare Metal Tests]] (8 shared connections)
- [[Database Config & Connection]] (5 shared connections)
- [[Benchmark Executor Base]] (5 shared connections)
- [[Cross-Module Rationale]] (4 shared connections)
- [[Visualization Plotting]] (3 shared connections)
- [[BO Baseline & Workload]] (3 shared connections)
- [[Sysbench Command Builder]] (2 shared connections)
- [[TPC-H Schema Tests]] (1 shared connections)
- [[Evaluator Fault Injection]] (1 shared connections)

## Source Files

- `src/benchmarks/sysbench/executor.py`
- `tests/unit/benchmarks/test_sysbench_executor_validation.py`
- `tests/unit/benchmarks/test_tpch_executor_schema_cleanup.py`

## Audit Trail

- EXTRACTED: 123 (73%)
- INFERRED: 46 (27%)
- AMBIGUOUS: 0 (0%)

---

*Part of the graphify knowledge wiki. See [[index]] to navigate.*