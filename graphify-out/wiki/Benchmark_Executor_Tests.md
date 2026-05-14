# Benchmark Executor Tests

> 10 nodes · cohesion 0.20

## Key Concepts

- **TestFeatureNormalization** (7 connections) — `tests/unit/scoring/test_workload_features.py`
- **test_workload_features.py** (7 connections) — `tests/unit/scoring/test_workload_features.py`
- **.test_template_features_normalized()** (4 connections) — `tests/unit/scoring/test_workload_features.py`
- **.test_sysbench_features_normalized()** (3 connections) — `tests/unit/scoring/test_workload_features.py`
- **.test_tpch_features_normalized()** (3 connections) — `tests/unit/scoring/test_workload_features.py`
- **Unit tests for workload feature extraction.  Tests cover: - Sysbench feature ext** (1 connections) — `tests/unit/scoring/test_workload_features.py`
- **Test that all extracted features are properly normalized.** (1 connections) — `tests/unit/scoring/test_workload_features.py`
- **Verify Sysbench features are within bounds.** (1 connections) — `tests/unit/scoring/test_workload_features.py`
- **Verify TPC-H features are within bounds.** (1 connections) — `tests/unit/scoring/test_workload_features.py`
- **Verify template features are within bounds.** (1 connections) — `tests/unit/scoring/test_workload_features.py`

## Relationships

- [[Import Analysis]] (18 shared connections)
- [[Scoring Scorer Core]] (5 shared connections)
- [[Evaluator Core]] (4 shared connections)
- [[Evolution Strategies]] (1 shared connections)

## Source Files

- `tests/unit/scoring/test_workload_features.py`

## Audit Trail

- EXTRACTED: 23 (79%)
- INFERRED: 6 (21%)
- AMBIGUOUS: 0 (0%)

---

*Part of the graphify knowledge wiki. See [[index]] to navigate.*