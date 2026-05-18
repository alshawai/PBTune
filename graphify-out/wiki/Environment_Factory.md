# Environment Factory

> 8 nodes · cohesion 0.32

## Key Concepts

- **_CursorStub** (8 connections) — `tests/unit/benchmarks/test_tpch_executor_schema_cleanup.py`
- **test_drop_existing_public_tables_removes_foreign_workload_tables()** (5 connections) — `tests/unit/benchmarks/test_tpch_executor_schema_cleanup.py`
- **test_drop_existing_public_tables_noop_when_schema_is_empty()** (4 connections) — `tests/unit/benchmarks/test_tpch_executor_schema_cleanup.py`
- **test_tpch_executor_schema_cleanup.py** (4 connections) — `tests/unit/benchmarks/test_tpch_executor_schema_cleanup.py`
- **Unit tests for TPC-H schema cleanup safeguards.** (1 connections) — `tests/unit/benchmarks/test_tpch_executor_schema_cleanup.py`
- **Cursor stub for validating DROP behavior in schema cleanup.** (1 connections) — `tests/unit/benchmarks/test_tpch_executor_schema_cleanup.py`
- **TPC-H cleanup should remove leftover Sysbench/public tables before load.** (1 connections) — `tests/unit/benchmarks/test_tpch_executor_schema_cleanup.py`
- **Cleanup should be a no-op when public schema has no tables.** (1 connections) — `tests/unit/benchmarks/test_tpch_executor_schema_cleanup.py`

## Relationships

- [[TPC-H Schema Tests]] (18 shared connections)
- [[Benchmark Executor Base]] (3 shared connections)
- [[Visualization Plotting]] (1 shared connections)
- [[Cross-Module Rationale]] (1 shared connections)
- [[Sysbench Executor Tests]] (1 shared connections)
- [[BO Config & Worker]] (1 shared connections)

## Source Files

- `tests/unit/benchmarks/test_tpch_executor_schema_cleanup.py`

## Audit Trail

- EXTRACTED: 21 (84%)
- INFERRED: 4 (16%)
- AMBIGUOUS: 0 (0%)

---

*Part of the graphify knowledge wiki. See [[index]] to navigate.*