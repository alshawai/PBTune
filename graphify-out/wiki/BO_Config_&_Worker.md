# BO Config & Worker

> 29 nodes · cohesion 0.07

## Key Concepts

- **types.py** (20 connections) — `src/utils/types.py`
- **StatSummary** (11 connections) — `src/evaluation/types.py`
- **_compare_metric()** (8 connections) — `src/evaluation/statistics.py`
- **_bootstrap_ci_median()** (5 connections) — `src/evaluation/statistics.py`
- **_paired_cohens_d()** (5 connections) — `src/evaluation/statistics.py`
- **MetricComparison** (5 connections) — `src/evaluation/types.py`
- **.test_bootstrap_ci_contains_median()** (3 connections) — `tests/unit/evaluation/test_evaluate_tuning.py`
- **.test_bootstrap_ci_width_reasonable()** (3 connections) — `tests/unit/evaluation/test_evaluate_tuning.py`
- **.test_cohens_d_large_effect()** (3 connections) — `tests/unit/evaluation/test_evaluate_tuning.py`
- **.test_cohens_d_zero_effect()** (3 connections) — `tests/unit/evaluation/test_evaluate_tuning.py`
- **.test_stat_summary_basic()** (3 connections) — `tests/unit/evaluation/test_evaluate_tuning.py`
- **.test_stat_summary_single_value()** (3 connections) — `tests/unit/evaluation/test_evaluate_tuning.py`
- **Build a MetricComparison for one metric.** (1 connections) — `src/evaluation/statistics.py`
- **Bootstrap 95% confidence interval on the median of paired differences.      Resa** (1 connections) — `src/evaluation/statistics.py`
- **Paired Cohen's d = mean(differences) / std(differences).      Interpreted as: 0.** (1 connections) — `src/evaluation/statistics.py`
- **Compute mean, std, median, and IQR for a list of values.** (1 connections) — `src/evaluation/statistics.py`
- **_stat_summary computes correct mean, std, median, IQR.** (1 connections) — `tests/unit/evaluation/test_evaluate_tuning.py`
- **Single-element list produces std=0 without errors.** (1 connections) — `tests/unit/evaluation/test_evaluate_tuning.py`
- **Bootstrap 95% CI should contain the true sample median.** (1 connections) — `tests/unit/evaluation/test_evaluate_tuning.py`
- **CI should be non-zero width for non-constant differences.** (1 connections) — `tests/unit/evaluation/test_evaluate_tuning.py`
- **Large consistent improvement → |d| > 0.8.** (1 connections) — `tests/unit/evaluation/test_evaluate_tuning.py`
- **All-zero differences → d=0.0.** (1 connections) — `tests/unit/evaluation/test_evaluate_tuning.py`
- **Type definitions for the evaluate_tuning module. ===============================** (1 connections) — `src/evaluation/types.py`
- **Statistical summary for a collection of scalar measurements.      Attributes:** (1 connections) — `src/evaluation/types.py`
- **Statistical comparison for a single performance metric.      Attributes:** (1 connections) — `src/evaluation/types.py`
- *... and 4 more nodes in this community*

## Relationships

- [[Evaluation Statistics]] (57 shared connections)
- [[Evaluation Types]] (13 shared connections)
- [[Hardware Normalization Tests]] (3 shared connections)
- [[Evaluator Fault Injection]] (2 shared connections)
- [[Visualization & Theming]] (2 shared connections)
- [[Comparison Runner]] (2 shared connections)
- [[Logger Colors]] (2 shared connections)
- [[Evaluation Tuning Tests]] (1 shared connections)
- [[Performance Metrics]] (1 shared connections)
- [[Visualization Types]] (1 shared connections)
- [[Scoring & Weight Policies]] (1 shared connections)
- [[Data Loader & Analysis]] (1 shared connections)

## Source Files

- `src/evaluation/statistics.py`
- `src/evaluation/types.py`
- `src/utils/types.py`
- `src/visualization/types.py`
- `tests/unit/evaluation/test_evaluate_tuning.py`

## Audit Trail

- EXTRACTED: 69 (78%)
- INFERRED: 20 (22%)
- AMBIGUOUS: 0 (0%)

---

*Part of the graphify knowledge wiki. See [[index]] to navigate.*