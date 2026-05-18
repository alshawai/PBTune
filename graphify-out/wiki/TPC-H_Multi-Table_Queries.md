# TPC-H Multi-Table Queries

> 6 nodes · cohesion 0.33

## Key Concepts

- **WorkerLoggerAdapter** (4 connections) — `src/utils/logger/adapters.py`
- **.process()** (2 connections) — `src/utils/logger/adapters.py`
- **adapters.py** (2 connections) — `src/utils/logger/adapters.py`
- **Worker-Aware Logger Adapter ============================  Provides ``WorkerLogge** (1 connections) — `src/utils/logger/adapters.py`
- **Logger adapter that injects worker_id into all log records.      The ``worker_id** (1 connections) — `src/utils/logger/adapters.py`
- **Add worker_id to log record.** (1 connections) — `src/utils/logger/adapters.py`

## Relationships

- [[Logger Adapters]] (10 shared connections)
- [[Benchmark Orchestrator]] (1 shared connections)

## Source Files

- `src/utils/logger/adapters.py`

## Audit Trail

- EXTRACTED: 10 (91%)
- INFERRED: 1 (9%)
- AMBIGUOUS: 0 (0%)

---

*Part of the graphify knowledge wiki. See [[index]] to navigate.*