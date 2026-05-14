# BO Baseline & Workload

> 43 nodes · cohesion 0.07

## Key Concepts

- **TestComputeComparisonStatistics** (28 connections) — `tests/unit/evaluation/test_evaluate_tuning.py`
- **compute_comparison_statistics()** (26 connections) — `src/evaluation/statistics.py`
- **statistics.py** (11 connections) — `src/evaluation/statistics.py`
- **_apply_significance()** (3 connections) — `src/evaluation/statistics.py`
- **_build_extractor()** (3 connections) — `src/evaluation/statistics.py`
- **_build_power_warning()** (3 connections) — `src/evaluation/statistics.py`
- **_holm_adjusted_pvalues()** (3 connections) — `src/evaluation/statistics.py`
- **.test_ci_is_ordered()** (3 connections) — `tests/unit/evaluation/test_evaluate_tuning.py`
- **.test_clear_improvement_detected()** (3 connections) — `tests/unit/evaluation/test_evaluate_tuning.py`
- **.test_empty_runs_raises()** (3 connections) — `tests/unit/evaluation/test_evaluate_tuning.py`
- **.test_endpoint_directionality_buffer_miss_rate()** (3 connections) — `tests/unit/evaluation/test_evaluate_tuning.py`
- **.test_endpoint_directionality_latency()** (3 connections) — `tests/unit/evaluation/test_evaluate_tuning.py`
- **.test_endpoint_directionality_memory_utilization()** (3 connections) — `tests/unit/evaluation/test_evaluate_tuning.py`
- **.test_endpoint_directionality_score()** (3 connections) — `tests/unit/evaluation/test_evaluate_tuning.py`
- **.test_endpoint_directionality_throughput()** (3 connections) — `tests/unit/evaluation/test_evaluate_tuning.py`
- **.test_latency_higher_is_better_flag()** (3 connections) — `tests/unit/evaluation/test_evaluate_tuning.py`
- **.test_memory_utilization_in_secondary_endpoints()** (3 connections) — `tests/unit/evaluation/test_evaluate_tuning.py`
- **.test_mismatched_pair_keys_raise()** (3 connections) — `tests/unit/evaluation/test_evaluate_tuning.py`
- **.test_primary_alpha_correct()** (3 connections) — `tests/unit/evaluation/test_evaluate_tuning.py`
- **.test_returns_primary_plus_two_secondary_metrics()** (3 connections) — `tests/unit/evaluation/test_evaluate_tuning.py`
- **.test_tpch_uses_latency_p99_endpoint()** (3 connections) — `tests/unit/evaluation/test_evaluate_tuning.py`
- **.test_statistics_metadata_includes_power_warning_for_n5()** (2 connections) — `tests/unit/evaluation/test_evaluate_tuning.py`
- **Statistical Analysis for Comparative Evaluation ================================** (1 connections) — `src/evaluation/statistics.py`
- **Apply corrected p-values and significance flags to metric comparisons.** (1 connections) — `src/evaluation/statistics.py`
- **Return a function that extracts the named metric from a RunResult.** (1 connections) — `src/evaluation/statistics.py`
- *... and 18 more nodes in this community*

## Relationships

- [[Evaluation Tuning Tests]] (97 shared connections)
- [[Evaluation Statistics]] (29 shared connections)
- [[Docker Environment Tests]] (4 shared connections)
- [[Comparison Runner]] (4 shared connections)
- [[Evaluation Types]] (2 shared connections)
- [[Bare Metal Memory Tests]] (2 shared connections)
- [[Hardware Normalization Tests]] (1 shared connections)
- [[Performance Metrics]] (1 shared connections)
- [[Docker Environment Management]] (1 shared connections)

## Source Files

- `src/evaluation/statistics.py`
- `tests/unit/evaluation/test_evaluate_tuning.py`

## Audit Trail

- EXTRACTED: 99 (70%)
- INFERRED: 43 (30%)
- AMBIGUOUS: 0 (0%)

---

*Part of the graphify knowledge wiki. See [[index]] to navigate.*