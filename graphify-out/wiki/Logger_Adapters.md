# Logger Adapters

> 6 nodes · cohesion 0.33

## Key Concepts

- **test_public_api_exports.py** (3 connections) — `tests/unit/evaluation/test_public_api_exports.py`
- **test_all_declared_exports_are_resolvable()** (2 connections) — `tests/unit/evaluation/test_public_api_exports.py`
- **test_performance_snapshot_is_intentionally_not_exported()** (2 connections) — `tests/unit/evaluation/test_public_api_exports.py`
- **Public API export contract tests for src.evaluation.** (1 connections) — `tests/unit/evaluation/test_public_api_exports.py`
- **Every symbol listed in __all__ should be importable from src.evaluation.** (1 connections) — `tests/unit/evaluation/test_public_api_exports.py`
- **PerformanceSnapshot should remain absent from the public API contract.** (1 connections) — `tests/unit/evaluation/test_public_api_exports.py`

## Relationships

- [[Evaluation API Tests]] (10 shared connections)

## Source Files

- `tests/unit/evaluation/test_public_api_exports.py`

## Audit Trail

- EXTRACTED: 10 (100%)
- INFERRED: 0 (0%)
- AMBIGUOUS: 0 (0%)

---

*Part of the graphify knowledge wiki. See [[index]] to navigate.*