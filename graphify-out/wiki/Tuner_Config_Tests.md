# Tuner Config Tests

> 21 nodes · cohesion 0.12

## Key Concepts

- **WorkloadFeatureExtractor** (41 connections) — `src/utils/scoring/workload_features.py`
- **Test TPC-H feature extraction.** (8 connections) — `tests/unit/scoring/test_workload_features.py`
- **.test_same_input_produces_same_output()** (3 connections) — `tests/unit/scoring/test_workload_features.py`
- **.test_extract_tpch_all_22_queries()** (3 connections) — `tests/unit/scoring/test_workload_features.py`
- **.test_extract_tpch_large_scale_factor()** (3 connections) — `tests/unit/scoring/test_workload_features.py`
- **.test_extract_tpch_medium_scale_factor()** (3 connections) — `tests/unit/scoring/test_workload_features.py`
- **.test_extract_tpch_small_scale_factor()** (3 connections) — `tests/unit/scoring/test_workload_features.py`
- **.test_extract_tpch_warmup_effect()** (3 connections) — `tests/unit/scoring/test_workload_features.py`
- **.extract_sysbench_features()** (2 connections) — `src/utils/scoring/workload_features.py`
- **.extract_template_features()** (2 connections) — `src/utils/scoring/workload_features.py`
- **.extract_tpch_features()** (2 connections) — `src/utils/scoring/workload_features.py`
- **Test TPC-H feature extraction for small scale factor.** (1 connections) — `tests/unit/scoring/test_workload_features.py`
- **Test TPC-H with medium scale factor.** (1 connections) — `tests/unit/scoring/test_workload_features.py`
- **Test TPC-H with large scale factor.** (1 connections) — `tests/unit/scoring/test_workload_features.py`
- **Test default TPC-H with all 22 queries.** (1 connections) — `tests/unit/scoring/test_workload_features.py`
- **Test warmup passes impact.** (1 connections) — `tests/unit/scoring/test_workload_features.py`
- **Test deterministic feature extraction.** (1 connections) — `tests/unit/scoring/test_workload_features.py`
- **Extract feature vector from weighted SQL templates and schema metadata.** (1 connections) — `src/utils/scoring/workload_features.py`
- **Extract static feature vectors for benchmark and template workloads.** (1 connections) — `src/utils/scoring/workload_features.py`
- **Extract static workload priors for sysbench modes.** (1 connections) — `src/utils/scoring/workload_features.py`
- **Extract static workload priors for TPC-H workloads.** (1 connections) — `src/utils/scoring/workload_features.py`

## Relationships

- [[Scoring Scorer Core]] (50 shared connections)
- [[Evaluator Core]] (13 shared connections)
- [[Evolution Strategies]] (7 shared connections)
- [[Import Analysis]] (5 shared connections)
- [[Benchmark Executor Tests]] (5 shared connections)
- [[BO Baseline & Workload]] (1 shared connections)
- [[Cross-Module Rationale]] (1 shared connections)
- [[Snapshot & Persistence]] (1 shared connections)

## Source Files

- `src/utils/scoring/workload_features.py`
- `tests/unit/scoring/test_workload_features.py`

## Audit Trail

- EXTRACTED: 39 (47%)
- INFERRED: 44 (53%)
- AMBIGUOUS: 0 (0%)

---

*Part of the graphify knowledge wiki. See [[index]] to navigate.*