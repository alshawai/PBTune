# TPC-H Loader & Data

> 22 nodes · cohesion 0.13

## Key Concepts

- **load_pbt_results()** (21 connections) — `src/analysis/data_loader.py`
- **test_data_loader.py** (13 connections) — `tests/unit/analysis/test_data_loader.py`
- **test_load_pbt_results_coerces_worker_resources_dict()** (3 connections) — `tests/unit/analysis/test_data_loader.py`
- **test_load_pbt_results_mixed_scoring_policy()** (3 connections) — `tests/unit/analysis/test_data_loader.py`
- **test_load_pbt_results_mixed_version_metadata()** (3 connections) — `tests/unit/analysis/test_data_loader.py`
- **test_mixed_version_metric_reference_version()** (3 connections) — `tests/unit/analysis/test_data_loader.py`
- **test_mixed_version_scoring_policy_propagation()** (3 connections) — `tests/unit/analysis/test_data_loader.py`
- **mock_mismatched_pbt_directory()** (2 connections) — `tests/unit/analysis/test_data_loader.py`
- **mock_pbt_directory()** (2 connections) — `tests/unit/analysis/test_data_loader.py`
- **test_knob_bounds_hardware_relative()** (2 connections) — `tests/unit/analysis/test_data_loader.py`
- **test_load_pbt_results_empty_history()** (2 connections) — `tests/unit/analysis/test_data_loader.py`
- **test_load_pbt_results_global_rescoring()** (2 connections) — `tests/unit/analysis/test_data_loader.py`
- **test_load_pbt_results_mismatched_knobs()** (2 connections) — `tests/unit/analysis/test_data_loader.py`
- **test_metadata_and_rescoring_checks()** (2 connections) — `tests/unit/analysis/test_data_loader.py`
- **Load, validate, and globally re-score PBT training results across multiple files** (1 connections) — `src/analysis/data_loader.py`
- **Creates JSON files with different configurations being tuned.** (1 connections) — `tests/unit/analysis/test_data_loader.py`
- **Creates a temporary directory with mock PBT result JSON files.** (1 connections) — `tests/unit/analysis/test_data_loader.py`
- **Test that mixed metric_reference_version across files is handled correctly.** (1 connections) — `tests/unit/analysis/test_data_loader.py`
- **Test that mixed scoring_policy across files is handled correctly.** (1 connections) — `tests/unit/analysis/test_data_loader.py`
- **Test that mixed scoring_policy versions are handled correctly.** (1 connections) — `tests/unit/analysis/test_data_loader.py`
- **Test that mixed metric_reference_version values are handled correctly.** (1 connections) — `tests/unit/analysis/test_data_loader.py`
- **worker_resources JSON dicts are converted to WorkerResources before resolution.** (1 connections) — `tests/unit/analysis/test_data_loader.py`

## Relationships

- [[PostgreSQL Knob Tests]] (5 shared connections)
- [[Feature Scoring Docs]] (1 shared connections)
- [[Analysis Data Pipeline]] (1 shared connections)
- [[Evaluator Fault Injection]] (1 shared connections)
- [[DB Connection Reuse]] (1 shared connections)
- [[BO Config & Worker]] (1 shared connections)
- [[Visualization & Theming]] (1 shared connections)

## Source Files

- `src/analysis/data_loader.py`
- `tests/unit/analysis/test_data_loader.py`

## Audit Trail

- EXTRACTED: 46 (65%)
- INFERRED: 25 (35%)
- AMBIGUOUS: 0 (0%)

---

*Part of the graphify knowledge wiki. See [[index]] to navigate.*