# Bare Metal Tests

> 23 nodes · cohesion 0.12

## Key Concepts

- **.close()** (10 connections) — `src/utils/logger/formatters.py`
- **_StubPostmasterProcess** (9 connections) — `tests/unit/utils/test_bare_metal_memory_utilization.py`
- **_PrepareConnectionStub** (8 connections) — `tests/unit/benchmarks/test_sysbench_executor_validation.py`
- **test_bare_metal_memory_utilization.py** (8 connections) — `tests/unit/utils/test_bare_metal_memory_utilization.py`
- **_StubBackendProcess** (8 connections) — `tests/unit/utils/test_bare_metal_memory_utilization.py`
- **_StubConnection** (8 connections) — `tests/unit/utils/test_bare_metal_memory_utilization.py`
- **_StubCursor** (8 connections) — `tests/unit/utils/test_bare_metal_memory_utilization.py`
- **test_collect_memory_utilization_falls_back_to_host_total_without_budget()** (6 connections) — `tests/unit/utils/test_bare_metal_memory_utilization.py`
- **test_collect_memory_utilization_uses_worker_budget_when_available()** (6 connections) — `tests/unit/utils/test_bare_metal_memory_utilization.py`
- **.cursor()** (4 connections) — `tests/unit/utils/test_bare_metal_memory_utilization.py`
- **.fetchone()** (2 connections) — `tests/unit/utils/test_bare_metal_memory_utilization.py`
- **Connection stub for deterministic prepare() flow.** (1 connections) — `tests/unit/benchmarks/test_sysbench_executor_validation.py`
- **Close handler and write HTML footer.** (1 connections) — `src/utils/logger/formatters.py`
- **Unit tests for bare-metal worker memory utilization normalization.** (1 connections) — `tests/unit/utils/test_bare_metal_memory_utilization.py`
- **When worker budget is unavailable, host-total fallback should still work.** (1 connections) — `tests/unit/utils/test_bare_metal_memory_utilization.py`
- **Cursor stub returning a fixed backend PID.** (1 connections) — `tests/unit/utils/test_bare_metal_memory_utilization.py`
- **Connection stub for backend PID lookup.** (1 connections) — `tests/unit/utils/test_bare_metal_memory_utilization.py`
- **Process stub with parent+children RSS accounting support.** (1 connections) — `tests/unit/utils/test_bare_metal_memory_utilization.py`
- **Backend process stub exposing parent() to postmaster.** (1 connections) — `tests/unit/utils/test_bare_metal_memory_utilization.py`
- **RSS should be normalized by worker RAM budget to avoid host-scale dilution.** (1 connections) — `tests/unit/utils/test_bare_metal_memory_utilization.py`
- **.parent()** (1 connections) — `tests/unit/utils/test_bare_metal_memory_utilization.py`
- **.children()** (1 connections) — `tests/unit/utils/test_bare_metal_memory_utilization.py`
- **.memory_info()** (1 connections) — `tests/unit/utils/test_bare_metal_memory_utilization.py`

## Relationships

- [[Sysbench Executor Tests]] (8 shared connections)
- [[Database Config & Connection]] (6 shared connections)
- [[Bare Metal Environment]] (4 shared connections)
- [[Cross-Module Rationale]] (3 shared connections)
- [[Sysbench Core]] (3 shared connections)
- [[Metric Config Schema]] (1 shared connections)
- [[Tuner Config Tests]] (1 shared connections)
- [[Visualization Plotting]] (1 shared connections)

## Source Files

- `src/utils/logger/formatters.py`
- `tests/unit/benchmarks/test_sysbench_executor_validation.py`
- `tests/unit/utils/test_bare_metal_memory_utilization.py`

## Audit Trail

- EXTRACTED: 79 (89%)
- INFERRED: 10 (11%)
- AMBIGUOUS: 0 (0%)

---

*Part of the graphify knowledge wiki. See [[index]] to navigate.*