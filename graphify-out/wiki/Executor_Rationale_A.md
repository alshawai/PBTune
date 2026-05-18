# Executor Rationale A

> 2 nodes · cohesion 1.00

## Key Concepts

- **.test_low_cache_efficiency()** (3 connections) — `tests/unit/utils/test_metric_instrumentation.py`
- **Test efficiency with low cache hit ratio.** (1 connections) — `tests/unit/utils/test_metric_instrumentation.py`

## Relationships

- [[Dead Worker Rescue]] (3 shared connections)
- [[Evaluator Fault Injection]] (1 shared connections)

## Source Files

- `tests/unit/utils/test_metric_instrumentation.py`

## Audit Trail

- EXTRACTED: 3 (75%)
- INFERRED: 1 (25%)
- AMBIGUOUS: 0 (0%)

---

*Part of the graphify knowledge wiki. See [[index]] to navigate.*