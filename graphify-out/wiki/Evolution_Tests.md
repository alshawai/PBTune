# Evolution Tests

> 14 nodes · cohesion 0.14

## Key Concepts

- **Test runtime feature vector refinement in evaluator.** (9 connections) — `tests/unit/scoring/test_workload_features.py`
- **.test_runtime_feature_vector_refinement_with_template_queries()** (4 connections) — `tests/unit/scoring/test_workload_features.py`
- **.test_runtime_feature_vector_stability()** (4 connections) — `tests/unit/scoring/test_workload_features.py`
- **.test_runtime_feature_vector_bounds()** (3 connections) — `tests/unit/scoring/test_workload_features.py`
- **.test_runtime_feature_vector_concurrency_refinement()** (3 connections) — `tests/unit/scoring/test_workload_features.py`
- **.test_runtime_feature_vector_sensitivity_to_concurrency()** (3 connections) — `tests/unit/scoring/test_workload_features.py`
- **.test_runtime_feature_vector_sensitivity_to_working_set()** (3 connections) — `tests/unit/scoring/test_workload_features.py`
- **Runtime feature vectors should maintain normalized bounds.** (1 connections) — `tests/unit/scoring/test_workload_features.py`
- **Runtime feature vectors should be stable across repeated extractions.** (1 connections) — `tests/unit/scoring/test_workload_features.py`
- **Runtime feature vectors should reflect concurrency changes.** (1 connections) — `tests/unit/scoring/test_workload_features.py`
- **Runtime feature vectors should reflect working set size changes.** (1 connections) — `tests/unit/scoring/test_workload_features.py`
- **Feature vectors should be stable when scaled proportionally.** (1 connections) — `tests/unit/scoring/test_workload_features.py`
- **Template feature vectors should refine based on query complexity.** (1 connections) — `tests/unit/scoring/test_workload_features.py`
- **Feature vectors should refine concurrency pressure based on thread count.** (1 connections) — `tests/unit/scoring/test_workload_features.py`

## Relationships

- [[Evolution Strategies]] (26 shared connections)
- [[Scoring Scorer Core]] (7 shared connections)
- [[Evaluator Core]] (2 shared connections)
- [[Import Analysis]] (1 shared connections)

## Source Files

- `tests/unit/scoring/test_workload_features.py`

## Audit Trail

- EXTRACTED: 27 (75%)
- INFERRED: 9 (25%)
- AMBIGUOUS: 0 (0%)

---

*Part of the graphify knowledge wiki. See [[index]] to navigate.*