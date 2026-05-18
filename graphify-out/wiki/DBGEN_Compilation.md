# DBGEN Compilation

> 7 nodes · cohesion 0.38

## Key Concepts

- **test_metric_config_recalibration.py** (4 connections) — `tests/unit/utils/test_metric_config_recalibration.py`
- **_build_metric()** (4 connections) — `tests/unit/utils/test_metric_config_recalibration.py`
- **test_expand_ranges_for_metrics_recalibrates_from_out_of_support_drift()** (3 connections) — `tests/unit/utils/test_metric_config_recalibration.py`
- **test_expand_ranges_for_metrics_recalibrates_from_saturation()** (3 connections) — `tests/unit/utils/test_metric_config_recalibration.py`
- **Regression tests for MetricConfig normalizer recalibration behavior.** (1 connections) — `tests/unit/utils/test_metric_config_recalibration.py`
- **Out-of-support drift should trigger recalibration and range expansion.** (1 connections) — `tests/unit/utils/test_metric_config_recalibration.py`
- **Multiple saturated workers should trigger immediate per-metric anchor expansion.** (1 connections) — `tests/unit/utils/test_metric_config_recalibration.py`

## Relationships

- [[Metric Recalibration Tests]] (16 shared connections)
- [[Evaluator Fault Injection]] (1 shared connections)

## Source Files

- `tests/unit/utils/test_metric_config_recalibration.py`

## Audit Trail

- EXTRACTED: 16 (94%)
- INFERRED: 1 (6%)
- AMBIGUOUS: 0 (0%)

---

*Part of the graphify knowledge wiki. See [[index]] to navigate.*