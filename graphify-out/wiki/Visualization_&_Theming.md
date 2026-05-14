# Visualization & Theming

> 47 nodes · cohesion 0.06

## Key Concepts

- **rescore_metrics_globally()** (14 connections) — `src/utils/rescoring.py`
- **DataLoadError** (12 connections) — `src/visualization/exceptions.py`
- **load_session()** (10 connections) — `src/visualization/loaders/session.py`
- **SessionTrace** (9 connections) — `src/visualization/loaders/session.py`
- **InvalidSchemaError** (9 connections) — `src/visualization/exceptions.py`
- **AblationGroup** (7 connections) — `src/visualization/loaders/ablation.py`
- **load_ablation_study()** (7 connections) — `src/visualization/loaders/ablation.py`
- **BOTrace** (7 connections) — `src/visualization/loaders/baseline.py`
- **load_bo_trace()** (7 connections) — `src/visualization/loaders/baseline.py`
- **ComparisonData** (6 connections) — `src/visualization/loaders/comparison.py`
- **load_comparison()** (6 connections) — `src/visualization/loaders/comparison.py`
- **_dict_to_statsummary()** (4 connections) — `src/visualization/loaders/comparison.py`
- **MultiSeedAggregate** (4 connections) — `src/visualization/loaders/multi_seed.py`
- **rescoring.py** (4 connections) — `src/utils/rescoring.py`
- **comparison.py** (4 connections) — `src/visualization/loaders/comparison.py`
- **aggregate_seeds()** (3 connections) — `src/visualization/loaders/multi_seed.py`
- **ablation.py** (3 connections) — `src/visualization/loaders/ablation.py`
- **baseline.py** (3 connections) — `src/visualization/loaders/baseline.py`
- **multi_seed.py** (3 connections) — `src/visualization/loaders/multi_seed.py`
- **session.py** (3 connections) — `src/visualization/loaders/session.py`
- **_count_valid_observations()** (3 connections) — `src/utils/rescoring.py`
- **workload_for_benchmark()** (3 connections) — `src/utils/rescoring.py`
- **.test_rescoring_is_deterministic_for_same_inputs()** (2 connections) — `tests/unit/evaluation/test_evaluate_tuning.py`
- **.test_rescoring_uses_workload_latency_endpoint()** (2 connections) — `tests/unit/evaluation/test_evaluate_tuning.py`
- **Loader for ablation studies.** (1 connections) — `src/visualization/loaders/ablation.py`
- *... and 22 more nodes in this community*

## Relationships

- [[Evaluator Fault Injection]] (6 shared connections)
- [[DB Connection Reuse]] (5 shared connections)
- [[Bare Metal Memory Tests]] (4 shared connections)
- [[Evaluation Statistics]] (2 shared connections)
- [[BO Baseline & Workload]] (1 shared connections)
- [[TPC-H Loader & Data]] (1 shared connections)
- [[Cleanup Scripts]] (1 shared connections)
- [[Benchmark Orchestrator]] (1 shared connections)
- [[Data Loader & Analysis]] (1 shared connections)

## Source Files

- `src/utils/rescoring.py`
- `src/visualization/exceptions.py`
- `src/visualization/loaders/ablation.py`
- `src/visualization/loaders/baseline.py`
- `src/visualization/loaders/comparison.py`
- `src/visualization/loaders/multi_seed.py`
- `src/visualization/loaders/session.py`
- `tests/unit/evaluation/test_evaluate_tuning.py`

## Audit Trail

- EXTRACTED: 97 (61%)
- INFERRED: 61 (39%)
- AMBIGUOUS: 0 (0%)

---

*Part of the graphify knowledge wiki. See [[index]] to navigate.*