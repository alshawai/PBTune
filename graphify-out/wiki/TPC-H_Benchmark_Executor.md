# TPC-H Benchmark Executor

> 58 nodes · cohesion 0.11

## Key Concepts

- **ComparisonRunner** (58 connections) — `src/evaluation/runner.py`
- **ComparisonConfig** (32 connections) — `src/evaluation/types.py`
- **TestStatisticalPrimitives** (20 connections) — `tests/unit/evaluation/test_evaluate_tuning.py`
- **TestCLI** (19 connections) — `tests/unit/evaluation/test_evaluate_tuning.py`
- **TestOutputPathResolution** (19 connections) — `tests/unit/evaluation/test_evaluate_tuning.py`
- **TuningSessionData** (19 connections) — `src/evaluation/types.py`
- **EvaluationError** (17 connections) — `src/evaluation/exceptions.py`
- **TuningSessionLoadError** (17 connections) — `src/evaluation/exceptions.py`
- **ComparisonResult** (17 connections) — `src/evaluation/types.py`
- **TestBenchmarkParameterResolution** (16 connections) — `tests/unit/evaluation/test_evaluate_tuning.py`
- **ComparisonStatistics** (16 connections) — `src/evaluation/types.py`
- **RunResult** (16 connections) — `src/evaluation/types.py`
- **DockerEnvironmentError** (15 connections) — `src/evaluation/exceptions.py`
- **test_evaluate_tuning.py** (15 connections) — `tests/unit/evaluation/test_evaluate_tuning.py`
- **TestDockerPrerequisites** (14 connections) — `tests/unit/evaluation/test_evaluate_tuning.py`
- **TestRunnerHelpers** (14 connections) — `tests/unit/evaluation/test_evaluate_tuning.py`
- **TestRescoring** (13 connections) — `tests/unit/evaluation/test_evaluate_tuning.py`
- **_build_result()** (12 connections) — `tests/unit/evaluation/test_evaluate_tuning.py`
- **TestTunedKnobResolution** (12 connections) — `tests/unit/evaluation/test_evaluate_tuning.py`
- **._make_session()** (7 connections) — `tests/unit/evaluation/test_evaluate_tuning.py`
- **.test_hardware_relative_knobs_are_resolved_from_fractions()** (5 connections) — `tests/unit/evaluation/test_evaluate_tuning.py`
- **_fake_docker_module()** (4 connections) — `tests/unit/evaluation/test_evaluate_tuning.py`
- **.test_cli_sysbench_workload_overrides_session()** (4 connections) — `tests/unit/evaluation/test_evaluate_tuning.py`
- **.test_cli_values_override_session()** (4 connections) — `tests/unit/evaluation/test_evaluate_tuning.py`
- **.test_session_sysbench_workload_used_when_cli_omits()** (4 connections) — `tests/unit/evaluation/test_evaluate_tuning.py`
- *... and 33 more nodes in this community*

## Relationships

- [[Comparison Runner]] (217 shared connections)
- [[Bare Metal Memory Tests]] (59 shared connections)
- [[Hardware Normalization Tests]] (40 shared connections)
- [[Performance Metrics]] (31 shared connections)
- [[Docker Environment Management]] (27 shared connections)
- [[Evaluation Statistics]] (21 shared connections)
- [[Evaluation Types]] (16 shared connections)
- [[Evaluation Tuning Tests]] (11 shared connections)
- [[Workload Orchestrator]] (8 shared connections)
- [[BO Config & Worker]] (7 shared connections)
- [[Evaluator Fault Injection]] (6 shared connections)
- [[Benchmark Executor Base]] (4 shared connections)

## Source Files

- `src/evaluation/exceptions.py`
- `src/evaluation/runner.py`
- `src/evaluation/types.py`
- `tests/unit/evaluation/test_evaluate_tuning.py`

## Audit Trail

- EXTRACTED: 178 (39%)
- INFERRED: 281 (61%)
- AMBIGUOUS: 0 (0%)

---

*Part of the graphify knowledge wiki. See [[index]] to navigate.*