# Knob Space Configuration

> 31 nodes · cohesion 0.08

## Key Concepts

- **str** (79 connections)
- **TuningMode** (12 connections) — `src/utils/types.py`
- **BOConfig** (11 connections) — `src/scripts/bo_baseline/config.py`
- **TestPBTSessionParity** (9 connections) — `tests/test_bo_baseline.py`
- **.apply_pbt_session()** (6 connections) — `src/scripts/bo_baseline/config.py`
- **config.py** (4 connections) — `src/scripts/bo_baseline/config.py`
- **clone_benchmark_config()** (4 connections) — `src/utils/types.py`
- **from_args()** (3 connections) — `src/scripts/bo_baseline/config.py`
- **.test_cli_defaults_are_owned_by_python_entrypoint()** (3 connections) — `tests/unit/evaluation/test_evaluate_tuning.py`
- **.test_legacy_warmup_flag_rejected()** (3 connections) — `tests/unit/evaluation/test_evaluate_tuning.py`
- **.test_no_docker_flag_sets_use_docker_false()** (3 connections) — `tests/unit/evaluation/test_evaluate_tuning.py`
- **.test_repetitions_less_than_2_exits_nonzero()** (3 connections) — `tests/unit/evaluation/test_evaluate_tuning.py`
- **.test_seed_and_sysbench_flags_propagate_to_config()** (3 connections) — `tests/unit/evaluation/test_evaluate_tuning.py`
- **.test_successful_run_returns_zero()** (3 connections) — `tests/unit/evaluation/test_evaluate_tuning.py`
- **_load_pbt_session()** (2 connections) — `src/scripts/bo_baseline/config.py`
- **.test_bo_config_explicit_iterations_override_pbt_budget()** (2 connections) — `tests/test_bo_baseline.py`
- **.test_bo_config_extracts_pbt_session_parameters()** (2 connections) — `tests/test_bo_baseline.py`
- **Configuration dataclass for Bayesian Optimization baseline runner.** (1 connections) — `src/scripts/bo_baseline/config.py`
- **Configuration for Bayesian Optimization baseline tuning.** (1 connections) — `src/scripts/bo_baseline/config.py`
- **Apply comparable benchmark/search settings from a PBT tuning session.** (1 connections) — `src/scripts/bo_baseline/config.py`
- **Simple string representation.** (1 connections) — `src/tuner/core/worker.py`
- **--repetitions 1 should fail validation.** (1 connections) — `tests/unit/evaluation/test_evaluate_tuning.py`
- **With a mocked ComparisonRunner, CLI should return 0.** (1 connections) — `tests/unit/evaluation/test_evaluate_tuning.py`
- **--no-docker flag sets use_docker=False in ComparisonConfig.** (1 connections) — `tests/unit/evaluation/test_evaluate_tuning.py`
- **Generic --warmup flag is removed; benchmark-specific flags must be used.** (1 connections) — `tests/unit/evaluation/test_evaluate_tuning.py`
- *... and 6 more nodes in this community*

## Relationships

- [[BO Config & Worker]] (74 shared connections)
- [[Population Tests]] (8 shared connections)
- [[Bare Metal Memory Tests]] (7 shared connections)
- [[Evolution Algorithms]] (7 shared connections)
- [[BO Baseline & Workload]] (5 shared connections)
- [[PBT Literature & Papers]] (5 shared connections)
- [[Benchmark Executor Base]] (4 shared connections)
- [[Benchmark Validation Tests]] (4 shared connections)
- [[Bare Metal Environment]] (4 shared connections)
- [[Docker Environment Management]] (3 shared connections)
- [[Workload Orchestrator]] (3 shared connections)
- [[Docker Volume Management]] (3 shared connections)

## Source Files

- `src/scripts/bo_baseline/config.py`
- `src/tuner/core/worker.py`
- `src/utils/types.py`
- `tests/test_bo_baseline.py`
- `tests/unit/evaluation/test_evaluate_tuning.py`

## Audit Trail

- EXTRACTED: 58 (35%)
- INFERRED: 108 (65%)
- AMBIGUOUS: 0 (0%)

---

*Part of the graphify knowledge wiki. See [[index]] to navigate.*