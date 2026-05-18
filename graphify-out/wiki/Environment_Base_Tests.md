# Environment Base Tests

> 17 nodes · cohesion 0.18

## Key Concepts

- **MetricConfig** (28 connections) — `src/utils/metrics.py`
- **create_metric_config()** (11 connections) — `src/utils/metrics.py`
- **metrics.py** (9 connections) — `src/utils/metrics.py`
- **for_mixed()** (3 connections) — `src/utils/metrics.py`
- **for_olap()** (3 connections) — `src/utils/metrics.py`
- **for_oltp()** (3 connections) — `src/utils/metrics.py`
- **.expand_ranges_for_metrics()** (3 connections) — `src/utils/metrics.py`
- **.get_normalization_metadata()** (3 connections) — `src/utils/metrics.py`
- **.get_scoring_metadata()** (3 connections) — `src/utils/metrics.py`
- **.detect_saturation()** (2 connections) — `src/utils/metrics.py`
- **Performance Metrics Module ==========================  This module defines the c** (1 connections) — `src/utils/metrics.py`
- **Configuration for workload-specific metric computation.      This configuration** (1 connections) — `src/utils/metrics.py`
- **Build normalization metadata for persistence and compatibility checks.** (1 connections) — `src/utils/metrics.py`
- **Build scoring metadata for tuning/evaluation serialization.** (1 connections) — `src/utils/metrics.py`
- **Detect if metrics are saturating (hitting normalization ceiling).          Satur** (1 connections) — `src/utils/metrics.py`
- **Expand normalization ranges to accommodate metrics that exceed current bounds.** (1 connections) — `src/utils/metrics.py`
- **Factory function to create metric configuration.      Parameters     ----------** (1 connections) — `src/utils/metrics.py`

## Relationships

- [[DB Connection Reuse]] (48 shared connections)
- [[Visualization & Theming]] (5 shared connections)
- [[Database Config & Connection]] (5 shared connections)
- [[Analysis Data Pipeline]] (3 shared connections)
- [[TPC-H Star Schema Queries]] (3 shared connections)
- [[Logger Colors]] (1 shared connections)
- [[Metric Instrumentation]] (1 shared connections)
- [[Evaluator Fault Injection]] (1 shared connections)
- [[Performance Metrics]] (1 shared connections)
- [[Cross-Module Rationale]] (1 shared connections)
- [[TPC-H Loader & Data]] (1 shared connections)
- [[Benchmark Orchestrator]] (1 shared connections)

## Source Files

- `src/utils/metrics.py`

## Audit Trail

- EXTRACTED: 56 (75%)
- INFERRED: 19 (25%)
- AMBIGUOUS: 0 (0%)

---

*Part of the graphify knowledge wiki. See [[index]] to navigate.*