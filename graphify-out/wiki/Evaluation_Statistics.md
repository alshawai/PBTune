# Evaluation Statistics

> 46 nodes · cohesion 0.06

## Key Concepts

- **load_tuning_session()** (29 connections) — `src/evaluation/loader.py`
- **TestLoadTuningSession** (24 connections) — `tests/unit/evaluation/test_evaluate_tuning.py`
- **loader.py** (8 connections) — `src/evaluation/loader.py`
- **_expect_object_or_empty()** (5 connections) — `src/evaluation/loader.py`
- **_extract_scoring_metadata()** (5 connections) — `src/evaluation/loader.py`
- **_infer_benchmark_and_workload()** (5 connections) — `src/evaluation/loader.py`
- **type** (5 connections)
- **_assert_fields()** (4 connections) — `src/evaluation/loader.py`
- **_check_version_compatibility()** (3 connections) — `src/evaluation/loader.py`
- **.test_empty_knobs_raises()** (3 connections) — `tests/unit/evaluation/test_evaluate_tuning.py`
- **.test_happy_path()** (3 connections) — `tests/unit/evaluation/test_evaluate_tuning.py`
- **.test_invalid_json_raises()** (3 connections) — `tests/unit/evaluation/test_evaluate_tuning.py`
- **.test_invalid_scoring_metadata_type_raises()** (3 connections) — `tests/unit/evaluation/test_evaluate_tuning.py`
- **.test_missing_best_configuration_raises()** (3 connections) — `tests/unit/evaluation/test_evaluate_tuning.py`
- **.test_missing_file_raises()** (3 connections) — `tests/unit/evaluation/test_evaluate_tuning.py`
- **.test_missing_worker_resources_raises()** (3 connections) — `tests/unit/evaluation/test_evaluate_tuning.py`
- **.test_negative_cpu_cores_raises()** (3 connections) — `tests/unit/evaluation/test_evaluate_tuning.py`
- **.test_runtime_metadata_is_normalized_for_evaluation()** (3 connections) — `tests/unit/evaluation/test_evaluate_tuning.py`
- **.test_scoring_metadata_defaults_for_legacy_session()** (3 connections) — `tests/unit/evaluation/test_evaluate_tuning.py`
- **.test_scoring_metadata_loaded_when_present()** (3 connections) — `tests/unit/evaluation/test_evaluate_tuning.py`
- **.test_sysbench_workload_defaults_for_legacy_session()** (3 connections) — `tests/unit/evaluation/test_evaluate_tuning.py`
- **.test_sysbench_workload_parsed_from_session()** (3 connections) — `tests/unit/evaluation/test_evaluate_tuning.py`
- **ColorCodeMeta** (3 connections) — `src/utils/logger/colors.py`
- **test_benchmark_inferred_from_path()** (2 connections) — `tests/unit/evaluation/test_evaluate_tuning.py`
- **.__getattribute__()** (2 connections) — `src/utils/logger/colors.py`
- *... and 21 more nodes in this community*

## Relationships

- [[Docker Environment Management]] (130 shared connections)
- [[Comparison Runner]] (7 shared connections)
- [[BO Config & Worker]] (3 shared connections)
- [[Hardware Normalization Tests]] (3 shared connections)
- [[PBT Literature & Papers]] (3 shared connections)
- [[Bare Metal Memory Tests]] (2 shared connections)
- [[Docker Snapshot]] (2 shared connections)
- [[BO Baseline & Workload]] (1 shared connections)
- [[Population Tests]] (1 shared connections)
- [[Performance Metrics]] (1 shared connections)
- [[Evaluation Types]] (1 shared connections)
- [[Session Management]] (1 shared connections)

## Source Files

- `src/evaluation/loader.py`
- `src/utils/logger/colors.py`
- `tests/unit/evaluation/test_evaluate_tuning.py`

## Audit Trail

- EXTRACTED: 100 (65%)
- INFERRED: 55 (35%)
- AMBIGUOUS: 0 (0%)

---

*Part of the graphify knowledge wiki. See [[index]] to navigate.*