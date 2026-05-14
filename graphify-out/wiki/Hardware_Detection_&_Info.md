# Hardware Detection & Info

> 47 nodes · cohesion 0.04

## Key Concepts

- **PerformanceMetrics** (115 connections) — `src/utils/metrics.py`
- **.test_degraded_at_threshold()** (3 connections) — `tests/unit/core/test_evaluator_fault_injection.py`
- **.test_degraded_error_rate()** (3 connections) — `tests/unit/core/test_evaluator_fault_injection.py`
- **.test_does_not_overwrite_existing_failure_type()** (3 connections) — `tests/unit/core/test_evaluator_fault_injection.py`
- **.test_healthy_evaluation_no_failure_type()** (3 connections) — `tests/unit/core/test_evaluator_fault_injection.py`
- **.test_high_error_rate()** (3 connections) — `tests/unit/core/test_evaluator_fault_injection.py`
- **.test_high_error_rate_above_threshold()** (3 connections) — `tests/unit/core/test_evaluator_fault_injection.py`
- **.test_high_error_rate_takes_priority_over_near_zero_throughput()** (3 connections) — `tests/unit/core/test_evaluator_fault_injection.py`
- **.test_just_below_degraded_threshold()** (3 connections) — `tests/unit/core/test_evaluator_fault_injection.py`
- **.test_near_zero_throughput()** (3 connections) — `tests/unit/core/test_evaluator_fault_injection.py`
- **.test_near_zero_throughput_takes_priority_over_degraded()** (3 connections) — `tests/unit/core/test_evaluator_fault_injection.py`
- **.test_zero_throughput()** (3 connections) — `tests/unit/core/test_evaluator_fault_injection.py`
- **.test_all_zero_metrics()** (3 connections) — `tests/unit/utils/test_metric_instrumentation.py`
- **.test_extreme_tail_amplification()** (3 connections) — `tests/unit/utils/test_metric_instrumentation.py`
- **.test_typical_olap_metrics()** (3 connections) — `tests/unit/utils/test_metric_instrumentation.py`
- **.test_oltp_under_stress()** (3 connections) — `tests/unit/utils/test_metric_instrumentation.py`
- **.test_typical_oltp_metrics()** (3 connections) — `tests/unit/utils/test_metric_instrumentation.py`
- **.test_high_cache_efficiency()** (3 connections) — `tests/unit/utils/test_metric_instrumentation.py`
- **.test_moderate_cache_efficiency()** (3 connections) — `tests/unit/utils/test_metric_instrumentation.py`
- **.test_negative_cache_ratio_clamped()** (3 connections) — `tests/unit/utils/test_metric_instrumentation.py`
- **.test_perfect_cache_hit_ratio()** (3 connections) — `tests/unit/utils/test_metric_instrumentation.py`
- **.test_zero_cache_efficiency()** (3 connections) — `tests/unit/utils/test_metric_instrumentation.py`
- **.test_negative_p50_latency()** (3 connections) — `tests/unit/utils/test_metric_instrumentation.py`
- **.test_zero_p50_latency()** (3 connections) — `tests/unit/utils/test_metric_instrumentation.py`
- **Test typical OLTP performance profile.** (2 connections) — `tests/unit/utils/test_metric_instrumentation.py`
- *... and 22 more nodes in this community*

## Relationships

- [[Evaluator Fault Injection]] (70 shared connections)
- [[Dead Worker Rescue]] (22 shared connections)
- [[Database Config & Connection]] (16 shared connections)
- [[Paper Design & Structure]] (12 shared connections)
- [[Metric Instrumentation]] (11 shared connections)
- [[Metric Edge Cases]] (10 shared connections)
- [[Quantile Utility Normalizer]] (8 shared connections)
- [[Visualization & Theming]] (6 shared connections)
- [[Population Initialization]] (6 shared connections)
- [[Metric Config Recalibration]] (6 shared connections)
- [[Performance Metrics]] (4 shared connections)
- [[Visualization Plotting]] (4 shared connections)

## Source Files

- `src/utils/metrics.py`
- `tests/unit/core/test_evaluator_fault_injection.py`
- `tests/unit/utils/test_metric_instrumentation.py`

## Audit Trail

- EXTRACTED: 74 (36%)
- INFERRED: 134 (64%)
- AMBIGUOUS: 0 (0%)

---

*Part of the graphify knowledge wiki. See [[index]] to navigate.*