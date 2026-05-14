# Population Tests

> 16 nodes · cohesion 0.12

## Key Concepts

- **._run_single()** (8 connections) — `src/evaluation/runner.py`
- **ApplicatorConfig** (8 connections) — `src/utils/applicator.py`
- **_metrics_to_score()** (7 connections) — `src/evaluation/runner.py`
- **._run_paired_comparisons()** (4 connections) — `src/evaluation/runner.py`
- **.test_metrics_to_score_sysbench_high_tps()** (4 connections) — `tests/unit/evaluation/test_evaluate_tuning.py`
- **.test_metrics_to_score_sysbench_zero_tps()** (4 connections) — `tests/unit/evaluation/test_evaluate_tuning.py`
- **.test_metrics_to_score_tpch()** (4 connections) — `tests/unit/evaluation/test_evaluate_tuning.py`
- **._build_environment()** (3 connections) — `src/evaluation/runner.py`
- **Create an evaluation environment via EnvironmentFactory.          Each call crea** (1 connections) — `src/evaluation/runner.py`
- **Execute strict paired runs where each pair shares one deterministic seed.** (1 connections) — `src/evaluation/runner.py`
- **Execute one benchmark repetition in a fresh environment.          Lifecycle per** (1 connections) — `src/evaluation/runner.py`
- **Compute a composite score using the same workload-specific metric model     used** (1 connections) — `src/evaluation/runner.py`
- **High TPS, low latency → score approaches 100.** (1 connections) — `tests/unit/evaluation/test_evaluate_tuning.py`
- **Zero TPS → score = 0.** (1 connections) — `tests/unit/evaluation/test_evaluate_tuning.py`
- **TPC-H score capped at 100, non-negative.** (1 connections) — `tests/unit/evaluation/test_evaluate_tuning.py`
- **Configuration for KnobApplicator behavior.      Attributes     ----------     pe** (1 connections) — `src/utils/applicator.py`

## Relationships

- [[Performance Metrics]] (34 shared connections)
- [[Comparison Runner]] (4 shared connections)
- [[Evaluator Fault Injection]] (3 shared connections)
- [[Scoring & Weight Policies]] (2 shared connections)
- [[Benchmark Orchestrator]] (2 shared connections)
- [[BO Baseline & Workload]] (1 shared connections)
- [[Workload Orchestrator]] (1 shared connections)
- [[DB Connection Reuse]] (1 shared connections)
- [[Cross-Module Rationale]] (1 shared connections)
- [[Database Config & Connection]] (1 shared connections)

## Source Files

- `src/evaluation/runner.py`
- `src/utils/applicator.py`
- `tests/unit/evaluation/test_evaluate_tuning.py`

## Audit Trail

- EXTRACTED: 32 (64%)
- INFERRED: 18 (36%)
- AMBIGUOUS: 0 (0%)

---

*Part of the graphify knowledge wiki. See [[index]] to navigate.*