# Database Config & Connection

> 34 nodes · cohesion 0.11

## Key Concepts

- **WorkloadType** (25 connections) — `src/utils/metrics.py`
- **MetricInstrumentationEngine** (17 connections) — `src/utils/metric_instrumentation.py`
- **DerivedMetrics** (14 connections) — `src/utils/metric_instrumentation.py`
- **TestScanEfficiency** (12 connections) — `tests/unit/utils/test_metric_instrumentation.py`
- **TestTailLatencyAmplification** (11 connections) — `tests/unit/utils/test_metric_instrumentation.py`
- **test_metric_instrumentation.py** (9 connections) — `tests/unit/utils/test_metric_instrumentation.py`
- **TestEdgeCasesAndBoundaries** (9 connections) — `tests/unit/utils/test_metric_instrumentation.py`
- **TestMetricsFormatting** (8 connections) — `tests/unit/utils/test_metric_instrumentation.py`
- **TestOLAPMetrics** (8 connections) — `tests/unit/utils/test_metric_instrumentation.py`
- **TestOLTPMetrics** (8 connections) — `tests/unit/utils/test_metric_instrumentation.py`
- **Test derived metrics computation.** (7 connections) — `tests/unit/utils/test_metric_instrumentation.py`
- **TestMixedWorkloadMetrics** (7 connections) — `tests/unit/utils/test_metric_instrumentation.py`
- **.test_compute_all_derived_metrics()** (4 connections) — `tests/unit/utils/test_metric_instrumentation.py`
- **.test_derived_metrics_enrich_dict()** (4 connections) — `tests/unit/utils/test_metric_instrumentation.py`
- **.test_metrics_consistency()** (4 connections) — `tests/unit/utils/test_metric_instrumentation.py`
- **.test_format_derived_metrics()** (4 connections) — `tests/unit/utils/test_metric_instrumentation.py`
- **.test_log_metrics_summary()** (4 connections) — `tests/unit/utils/test_metric_instrumentation.py`
- **Computed metrics derived from raw PerformanceMetrics.      These metrics provide** (1 connections) — `src/utils/metric_instrumentation.py`
- **Engine for computing and enriching derived metrics.** (1 connections) — `src/utils/metric_instrumentation.py`
- **Type of database workload** (1 connections) — `src/utils/metrics.py`
- **Unit tests for metric instrumentation and derived metrics.  Tests cover: - Tail** (1 connections) — `tests/unit/utils/test_metric_instrumentation.py`
- **Test scan efficiency metric computation.** (1 connections) — `tests/unit/utils/test_metric_instrumentation.py`
- **Test computation of all derived metrics together.** (1 connections) — `tests/unit/utils/test_metric_instrumentation.py`
- **Test tail latency amplification computation.** (1 connections) — `tests/unit/utils/test_metric_instrumentation.py`
- **Test enriching a metrics dictionary with derived metrics.** (1 connections) — `tests/unit/utils/test_metric_instrumentation.py`
- *... and 9 more nodes in this community*

## Relationships

- [[Metric Instrumentation]] (99 shared connections)
- [[Evaluator Fault Injection]] (23 shared connections)
- [[Metric Edge Cases]] (13 shared connections)
- [[Dead Worker Rescue]] (12 shared connections)
- [[Paper Design & Structure]] (11 shared connections)
- [[BO Baseline & Workload]] (3 shared connections)
- [[Logger Colors]] (1 shared connections)
- [[Cross-Module Rationale]] (1 shared connections)
- [[Benchmark Orchestrator]] (1 shared connections)
- [[DB Connection Reuse]] (1 shared connections)
- [[Quantile Utility Normalizer]] (1 shared connections)
- [[TPC-H Star Schema Queries]] (1 shared connections)

## Source Files

- `src/utils/metric_instrumentation.py`
- `src/utils/metrics.py`
- `tests/unit/utils/test_metric_instrumentation.py`

## Audit Trail

- EXTRACTED: 82 (48%)
- INFERRED: 90 (52%)
- AMBIGUOUS: 0 (0%)

---

*Part of the graphify knowledge wiki. See [[index]] to navigate.*