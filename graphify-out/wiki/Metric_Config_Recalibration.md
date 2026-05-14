# Metric Config Recalibration

> 18 nodes · cohesion 0.14

## Key Concepts

- **BenchmarkConfig** (11 connections) — `src/utils/types.py`
- **.test_result_format_compatibility()** (9 connections) — `tests/test_bo_baseline.py`
- **TestResultFormat** (8 connections) — `tests/test_bo_baseline.py`
- **.test_result_generation_history()** (8 connections) — `tests/test_bo_baseline.py`
- **write_bo_results()** (7 connections) — `src/scripts/bo_baseline/result_writer.py`
- **resolve_bo_output_root()** (5 connections) — `src/scripts/bo_baseline/result_writer.py`
- **._build_log_output_file()** (4 connections) — `src/scripts/bo_baseline/runner.py`
- **test_bo_baseline.py** (4 connections) — `tests/test_bo_baseline.py`
- **result_writer.py** (3 connections) — `src/scripts/bo_baseline/result_writer.py`
- **Result serialization for Bayesian Optimization baseline runner.** (1 connections) — `src/scripts/bo_baseline/result_writer.py`
- **Resolve the base BO output directory under results.** (1 connections) — `src/scripts/bo_baseline/result_writer.py`
- **Serialize Bayesian Optimization results in PBT-compatible JSON format.      Para** (1 connections) — `src/scripts/bo_baseline/result_writer.py`
- **Create the HTML log output file under results.** (1 connections) — `src/scripts/bo_baseline/runner.py`
- **Unit tests for Bayesian Optimization baseline components.** (1 connections) — `tests/test_bo_baseline.py`
- **Test result serialization format.** (1 connections) — `tests/test_bo_baseline.py`
- **Test that generated results are compatible with loader.** (1 connections) — `tests/test_bo_baseline.py`
- **Test that generation history is properly formatted.** (1 connections) — `tests/test_bo_baseline.py`
- **Benchmark and workload configuration settings.      Args:         benchmark: Ben** (1 connections) — `src/utils/types.py`

## Relationships

- [[Population Tests]] (42 shared connections)
- [[BO Config & Worker]] (8 shared connections)
- [[Session Management]] (6 shared connections)
- [[BO Baseline & Workload]] (3 shared connections)
- [[Hardware Normalization Tests]] (3 shared connections)
- [[Cross-Module Rationale]] (1 shared connections)
- [[Docker Environment Management]] (1 shared connections)
- [[PBT Literature & Papers]] (1 shared connections)
- [[Snapshot & Persistence]] (1 shared connections)
- [[Evolution Algorithms]] (1 shared connections)
- [[Evaluation Types]] (1 shared connections)

## Source Files

- `src/scripts/bo_baseline/result_writer.py`
- `src/scripts/bo_baseline/runner.py`
- `src/utils/types.py`
- `tests/test_bo_baseline.py`

## Audit Trail

- EXTRACTED: 36 (53%)
- INFERRED: 32 (47%)
- AMBIGUOUS: 0 (0%)

---

*Part of the graphify knowledge wiki. See [[index]] to navigate.*