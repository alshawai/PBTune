# DBGEN Setup & Build

> 9 nodes · cohesion 0.31

## Key Concepts

- **test_metric_config_composite_scorer_integration.py** (5 connections) — `tests/unit/utils/test_metric_config_composite_scorer_integration.py`
- **_metric()** (5 connections) — `tests/unit/utils/test_metric_config_composite_scorer_integration.py`
- **test_compute_detailed_scores_keeps_legacy_component_keys()** (3 connections) — `tests/unit/utils/test_metric_config_composite_scorer_integration.py`
- **test_compute_score_respects_failure_gate()** (3 connections) — `tests/unit/utils/test_metric_config_composite_scorer_integration.py`
- **test_feature_driven_policy_responds_to_workload_features()** (3 connections) — `tests/unit/utils/test_metric_config_composite_scorer_integration.py`
- **Integration-style tests for MetricConfig and CompositeScorer wiring.** (1 connections) — `tests/unit/utils/test_metric_config_composite_scorer_integration.py`
- **Failure-tagged metrics should always produce a zero score.** (1 connections) — `tests/unit/utils/test_metric_config_composite_scorer_integration.py`
- **Different feature vectors should yield different scores for same metrics.** (1 connections) — `tests/unit/utils/test_metric_config_composite_scorer_integration.py`
- **Detailed score output should preserve legacy key names for consumers.** (1 connections) — `tests/unit/utils/test_metric_config_composite_scorer_integration.py`

## Relationships

- [[Feature Weight Tuning]] (22 shared connections)
- [[Evaluator Fault Injection]] (1 shared connections)

## Source Files

- `tests/unit/utils/test_metric_config_composite_scorer_integration.py`

## Audit Trail

- EXTRACTED: 22 (96%)
- INFERRED: 1 (4%)
- AMBIGUOUS: 0 (0%)

---

*Part of the graphify knowledge wiki. See [[index]] to navigate.*