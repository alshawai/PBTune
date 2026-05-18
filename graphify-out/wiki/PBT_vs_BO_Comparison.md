# PBT vs BO Comparison

> 7 nodes · cohesion 0.38

## Key Concepts

- **_make_workload_orchestrator()** (5 connections) — `tests/unit/core/test_evaluator_memory_normalization.py`
- **test_evaluator_memory_normalization.py** (4 connections) — `tests/unit/core/test_evaluator_memory_normalization.py`
- **test_collect_system_metrics_delegates_to_environment()** (3 connections) — `tests/unit/core/test_evaluator_memory_normalization.py`
- **test_collect_system_metrics_needs_environment_delegation()** (3 connections) — `tests/unit/core/test_evaluator_memory_normalization.py`
- **Tests for evaluator system metrics delegation to the environment.** (1 connections) — `tests/unit/core/test_evaluator_memory_normalization.py`
- **When env is set, evaluator should delegate memory + cache hit to it.** (1 connections) — `tests/unit/core/test_evaluator_memory_normalization.py`
- **Evaluator should not perform SQL fallback when env is present.** (1 connections) — `tests/unit/core/test_evaluator_memory_normalization.py`

## Relationships

- [[Memory Normalization Tests]] (16 shared connections)
- [[Benchmark Orchestrator]] (1 shared connections)
- [[Database Config & Connection]] (1 shared connections)

## Source Files

- `tests/unit/core/test_evaluator_memory_normalization.py`

## Audit Trail

- EXTRACTED: 16 (89%)
- INFERRED: 2 (11%)
- AMBIGUOUS: 0 (0%)

---

*Part of the graphify knowledge wiki. See [[index]] to navigate.*