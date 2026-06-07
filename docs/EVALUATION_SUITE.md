# Evaluation Suite

> Last reviewed: 2026-06-07

See also: [Documentation Index](./README.md), [Evaluation Reproducibility Runbook](./EVALUATION_RUNBOOK.md), [Feature-Driven Scoring](./FEATURE_DRIVEN_SCORING.md), [Workload Orchestrator](./WORKLOAD_ORCHESTRATOR.md), [Statistical Analysis](#statistical-analysis)

## Overview

The post-hoc **evaluation suite** at [src/evaluation/](../src/evaluation/) compares a tuned configuration produced by a tuning run (PBT or BO) against the PostgreSQL default configuration under controlled conditions, and emits a JSON report containing significance tests, confidence intervals, and effect sizes.

The suite is invoked via `python -m src.evaluation` and is the canonical way to answer the question *"is the tuned configuration statistically better than the default, and by how much?"* It is independent of the tuning loop — it ingests a saved tuning-session JSON, sets up a fresh database environment, and runs paired evaluations of `default` and `tuned` configurations.

The runbook commands and reproducibility checklist live in [EVALUATION_RUNBOOK.md](./EVALUATION_RUNBOOK.md). **This document covers the architecture and statistical methodology.**

---

## Table of Contents

1. [Where it sits](#where-it-sits)
2. [Module layout](#module-layout)
3. [`ComparisonConfig`](#comparisonconfig)
4. [`ComparisonRunner` flow](#comparisonrunner-flow)
5. [Session loader and version compatibility](#session-loader-and-version-compatibility)
6. [Statistical analysis](#statistical-analysis)
7. [Output JSON schema](#output-json-schema)
8. [Multi-arm comparisons](#multi-arm-comparisons)
9. [Design decisions](#design-decisions)
10. [Related documentation](#related-documentation)

---

## Where it sits

```text
                       ┌─────────────────────────────────┐
                       │  Tuning session JSON            │
                       │  results/.../pbt_results_*.json │
                       │  results/.../bo_results_*.json  │
                       └────────────────┬────────────────┘
                                        │
                                        ▼
                       ┌─────────────────────────────────┐
                       │       loader.py                 │
                       │  load_tuning_session(path)      │
                       │   → TuningSessionData           │
                       └────────────────┬────────────────┘
                                        │
                                        ▼
                       ┌─────────────────────────────────┐
                       │      ComparisonRunner            │
                       │                                  │
                       │  1. Build benchmark executor     │
                       │  2. Build environment            │
                       │  3. For repetition i:            │
                       │      run default config          │
                       │      run tuned config            │
                       │  4. Compute statistics           │
                       │  5. Save JSON + HTML log         │
                       └────────────────┬────────────────┘
                                        │
                                        ▼
                       ┌─────────────────────────────────┐
                       │  Comparison JSON + HTML log     │
                       │  results/.../comparisons/.../   │
                       │  comparison_*.json              │
                       └─────────────────────────────────┘
```

The runner reuses three components from the tuning side:

- **[`WorkloadOrchestrator`](./WORKLOAD_ORCHESTRATOR.md)** to drive each evaluation.
- **[`DatabaseEnvironment`](./ENVIRONMENT_BACKENDS.md)** (preferentially Docker) to isolate runs.
- **The scoring engine** to produce comparable scalar scores under a chosen scoring policy.

---

## Module layout

| File | Responsibility |
| --- | --- |
| [`__main__.py`](../src/evaluation/__main__.py) | CLI entry point. Argparse, runbook-friendly defaults, dispatch. |
| [`runner.py`](../src/evaluation/runner.py) | `ComparisonRunner` — orchestrates default-vs-tuned evaluations, multi-arm comparisons, JSON serialisation. |
| [`statistics.py`](../src/evaluation/statistics.py) | Wilcoxon signed-rank, paired bootstrap CIs, paired Cohen's d, Holm correction for the secondary endpoint family. |
| [`loader.py`](../src/evaluation/loader.py) | `load_tuning_session(path)` — parses session JSON across schema versions, normalises tuning config, extracts scoring metadata. |
| [`types.py`](../src/evaluation/types.py) | Frozen dataclasses: `ComparisonConfig`, `TuningSessionData`, `RunResult`, `MetricComparison`, `ComparisonResult`, `PairwiseResult`, `MultiArmComparisonResult`. |
| [`exceptions.py`](../src/evaluation/exceptions.py) | Domain-specific exception hierarchy (`SessionLoadError`, `EvaluationSetupError`, `RunFailureError`). |

The package surfaces a small public API in `__init__.py` — `ComparisonRunner`, `ComparisonConfig`, `load_tuning_session`, plus the result types used by downstream scripts and the visualization comparison loader.

---

## `ComparisonConfig`

```python
@dataclass(frozen=True)
class ComparisonConfig:
    session_path: Path
    repetitions: int = 5
    seed: int = 50000
    use_docker: bool = True
    docker_image: Optional[str] = None
    output_dir: Optional[Path] = None
    scoring_policy: Optional[str] = None
    scoring_policy_version: Optional[str] = None
    sysbench_overrides: SysbenchOverrides = ...
    tpch_overrides: TPCHOverrides = ...
    multi_arm_sessions: list[Path] = ()
    # ... see source
```

The CLI in `__main__.py` builds this dataclass; the runner consumes it. The frozen dataclass means a runner instance cannot mutate its config mid-run, which keeps the reproducibility metadata in the output JSON honest.

Runtime overrides (Sysbench tables / table size / duration / warmup, TPC-H scale factor / warmup passes) are grouped into nested dataclasses so the override field set is auditable; the runner falls back to session-recorded values when an override is unset.

---

## `ComparisonRunner` flow

```text
ComparisonRunner.run() — paired comparison
─────────────────────────────────────────────
1. session = load_tuning_session(config.session_path)
2. tuned_knobs = _resolve_tuned_knobs(session)
3. _validate_session_compatibility(session)            # benchmark family, workload type
4. _validate_docker_prerequisites() if use_docker
5. executor = _create_executor()                       # SysbenchExecutor / TPCHExecutor / WorkloadExecutor
6. env = _build_environment(executor)                  # Docker (preferred) or bare-metal
7. orchestrator = WorkloadOrchestrator(...)
8. _run_paired_comparisons(orchestrator, env):
       for i in range(repetitions):
           seed_i = base_seed + i - 1                  # identical for default and tuned
           run_default_i = _run_single(default_knobs, seed_i)
           run_tuned_i   = _run_single(tuned_knobs,   seed_i)
9. statistics = compute_comparison_statistics(default_runs, tuned_runs)
10. result = ComparisonResult(... runs, statistics, metadata, reproducibility)
11. _save_result(result) → comparison_*.json + logs/evaluation_*.html
```

Notable choices:

- **Identical paired seeds.** Repetition `i` uses `base_seed + i - 1` for *both* the default and the tuned run. The two configurations face the same workload sequence, so paired statistical tests (Wilcoxon, paired bootstrap CI, paired Cohen's d) are valid.
- **Default knobs come from `KnobSpace.get_default_config()`.** They are not the cluster's current knobs; they are the PostgreSQL defaults captured by the same KnobSpace that produced the tuned config. This is the only way the paired test is fair on knobs the tuner explored.
- **Tuned knobs come from `session.best_configuration.knobs`.** When the session JSON stores fractional values (hardware-relative knobs), `_resolve_tuned_knobs` calls `KnobSpace.fractions_to_config(...)` against the *evaluation* host's resources, not the original tuning host's. This is what makes "tune on a 16-GB host, evaluate on a 32-GB host" sound — the fractional encoding is the transfer medium.
- **Output partitioning by tier and workload.** `_resolve_output_dir` writes to `results/{workload_kind}/comparisons/{tier}/` derived from `session.tuning_session.knob_tier`, with a fallback to the session path's `pbt_runs/{tier}/` segment. This keeps the results tree navigable even when the session metadata is partial.

---

## Session loader and version compatibility

**Location**: [src/evaluation/loader.py](../src/evaluation/loader.py)

`load_tuning_session(path)` parses both PBT and BO session JSONs into a single `TuningSessionData` shape. The loader is deliberately permissive about historical schema variations:

- **Scoring policy default.** Sessions without a `scoring_policy` field are treated as `fixed_v1` with policy version `1.0` and metric reference version `v1` (the constants in [src/utils/scoring/constants.py](../src/utils/scoring/constants.py)). Runs from before scoring-v2 still load and compare correctly.
- **Tuning-config normalisation.** `_normalize_tuning_config()` coerces numeric fields (`population_size`, `total_generations`, `sysbench_table_size`, `tpch_scale_factor`, etc.) from strings or floats into the right types, since older runs sometimes wrote durations as strings.
- **Benchmark / workload inference.** When a session JSON omits `benchmark_name` or `workload_type`, `_infer_benchmark_and_workload()` derives them from the session path (`results/oltp/oltp_read_write/...` → sysbench OLTP; `results/olap/...` → TPC-H OLAP).
- **Version compatibility check.** `_check_version_compatibility` warns (does not block) on metric-reference-version mismatches between sessions in a multi-arm comparison. The user can override the active scoring policy via `--scoring-policy` to force re-evaluation under newer weights — at which point the comparison JSON records both the original session policies and the active comparison policy.

The runbook [EVALUATION_RUNBOOK.md](./EVALUATION_RUNBOOK.md) lists the metadata fields the loader populates so a reviewer can audit them.

---

## Statistical analysis

**Location**: [src/evaluation/statistics.py](../src/evaluation/statistics.py)

The statistical layer is paired-design throughout. Given `default_runs` and `tuned_runs` of equal length, where pair `i` shares its seed:

```python
def compute_comparison_statistics(
    default_runs: list[RunResult],
    tuned_runs: list[RunResult],
    primary_endpoint: str = "score",
    secondary_endpoint_family: list[str] = (...),
    alpha: float = 0.05,
    bootstrap_resamples: int = 10000,
    rng_seed: int = 42,
) -> ComparisonStatistics: ...
```

### Endpoints

- **Primary endpoint: `score`** — the composite score from the active scoring policy. Tested at α = 0.05 with no family correction.
- **Secondary endpoint family** — benchmark latency endpoint + throughput + memory utilization.
  - Sysbench secondary latency endpoint: `latency_p95`.
  - TPC-H secondary latency endpoint: `latency_p99`.
  - Secondary p-values are corrected with **Holm's step-down procedure** (see `_holm_adjusted_pvalues`).

The asymmetry — primary endpoint uncorrected, secondary family corrected — is intentional: the primary endpoint is what the optimisation actually targets; the secondary family is for understanding the *direction* of the win, not for additional confirmatory testing.

### Tests applied per endpoint

For each endpoint, the statistics module produces a `MetricComparison` with:

| Field | Computation |
| --- | --- |
| `default` / `tuned` | `StatSummary` (mean, std, median, p25, p75, n) |
| `delta_mean`, `delta_median`, `pct_change` | Tuned − default summaries. |
| `wilcoxon_p` | Wilcoxon signed-rank test on per-pair differences. Falls back to `1.0` when all differences are exactly zero (degenerate case for short reps). |
| `bootstrap_ci_median` | Bias-corrected accelerated bootstrap CI on the median difference. RNG seed pinned to `42` for deterministic CI computation. |
| `cohens_d` | Paired Cohen's d on the difference vector. |
| `significant` | `p < α` (with Holm correction applied to the secondary family). |

### Power warning

`_build_power_warning(n_pairs)` returns a string like `"Statistical power is limited with n=3 paired observations; consider --repetitions 10"` when fewer than 5 repetitions were used. It is surfaced into the comparison JSON so reviewers can see it without re-reading the runbook.

### Determinism

The bootstrap RNG is seeded explicitly (`rng_seed=42` by default). The same input produces the same CI on every run. The benchmark-execution noise is bounded by Docker isolation but not eliminated; that's why we report mean ± std and a CI rather than a single point estimate.

---

## Output JSON schema

A comparison JSON is structured as follows (truncated):

```json
{
  "comparison_metadata": {
    "session_path": "...",
    "repetitions": 5,
    "seed": 50000,
    "evaluation_environment": "docker",
    "resource_constraints": { "ram_bytes": ..., "cpu_cores": ... },
    "scoring_policy": "feature_driven_v2",
    "scoring_policy_version": "2.0",
    "metric_reference_version": "v2",
    "workload_features": { ... },
    "normalization_metadata": { ... },
    "score_breakdown": { ... },
    "reproducibility": {
      "python_version": "3.11.x",
      "postgres_version": "16.x",
      "docker_image": "postgres:16",
      "python_package_versions": { ... },
      "benchmark_binary_paths": { ... }
    }
  },
  "default_runs":   [ RunResult, RunResult, ... ],
  "tuned_runs":     [ RunResult, RunResult, ... ],
  "statistics": {
    "score":              MetricComparison,
    "latency_p95":        MetricComparison,
    "throughput":         MetricComparison,
    "memory_utilization": MetricComparison
  },
  "power_warning": "..."  // optional
}
```

Every field listed in the [reproducibility checklist of the runbook](./EVALUATION_RUNBOOK.md#reproducibility-checklist) is populated by the runner. A reviewer should be able to recreate the comparison from the JSON alone.

---

## Multi-arm comparisons

`ComparisonRunner.run_multi_arm()` handles the case where multiple tuning sessions (e.g. several seeds, or PBT vs BO) need to be compared against the same default baseline.

```bash
python -m src.evaluation \
  --session results/.../pbt_results_seed42.json \
  --session results/.../pbt_results_seed123.json \
  --session results/.../bo_results_seed42.json \
  --repetitions 8
```

The runner:

1. Runs the default configuration once for the full repetition budget; this is the shared control arm.
2. Runs each tuned arm against its own paired seeds (still `base_seed + i - 1`).
3. Computes pairwise statistics between every (arm, default) pair and between every (arm_a, arm_b) pair via `compute_pairwise_statistics`.
4. Emits a `MultiArmComparisonResult` JSON with one `PairwiseResult` per pair plus the metadata block.

This is what feeds the multi-arm comparison plots produced by [`src/scripts/pbt_vs_bo_comarison.py`](../src/scripts/pbt_vs_bo_comarison.py) and the comparison loader in [src/visualization/loaders/comparison.py](../src/visualization/loaders/comparison.py).

---

## Design decisions

### 1. Paired design with shared seeds

Comparing against the default with independent seeds wastes statistical power; the workload variance dominates the configuration variance. Pairing repetitions on the same seed pulls the workload variance out of the comparison, leaving the configuration effect easier to detect with fewer reps.

### 2. Score is the primary endpoint, not throughput

A throughput-only primary endpoint biases the test toward configurations that trade tail latency for raw TPS. The composite score weighs latency, throughput, memory, and reliability together — testing on it answers the question the optimiser is actually asked to answer. Throughput remains in the secondary family for direction-of-win interpretation.

### 3. Holm correction on the secondary family only

Holm corrects for the multiple comparisons within the secondary family (latency, throughput, memory). The primary endpoint is a single confirmatory test and isn't part of any family. Bonferroni would over-correct given the secondary endpoints' known correlation structure.

### 4. Bootstrap CI seeded deterministically

Reviewers have to be able to reproduce the same CI from the same input — non-deterministic CIs would invite re-running until a "good" CI appears. Seeding the bootstrap RNG locks the result.

### 5. Docker preferred, bare-metal opt-in only

A reviewer reading a publication-facing comparison shouldn't have to wonder whether the result is contaminated by host noise. The runner defaults to Docker; bare-metal requires `--no-docker` and tags the output as reduced-isolation in `evaluation_environment`.

### 6. Output partitioning by `(workload, tier)`

Running comparisons across many sessions generates many JSONs. The path layout `results/{workload_kind}/comparisons/{tier}/` keeps related artefacts adjacent, and downstream scripts can glob over a tier directory to assemble multi-arm plots without parsing every file.

### 7. Loader tolerates schema drift

A research repo accumulates session formats over months. The loader's compatibility branches mean today's evaluator can compare against a session from before scoring-v2 — the runner re-evaluates under the active policy and the comparison JSON records both versions for auditability.

---

## Related documentation

- **[Evaluation Reproducibility Runbook](./EVALUATION_RUNBOOK.md)** — canonical commands, output paths, reproducibility checklist.
- **[Feature-Driven Scoring](./FEATURE_DRIVEN_SCORING.md)** — what the active scoring policy controls.
- **[Workload Orchestrator](./WORKLOAD_ORCHESTRATOR.md)** — the engine that runs each evaluation.
- **[Environment Backends](./ENVIRONMENT_BACKENDS.md)** — Docker vs bare-metal trade-offs the runner inherits.
- **[BO Baseline](./BO_BASELINE.md)** — produces session JSONs the suite consumes.
- **[PBT vs BO Comparison](./PBT_VS_BO_COMPARISON.md)** — multi-arm visualization on top of comparison JSONs.
- **[Metrics Validation](./METRICS_VALIDATION.md)** — academic justification for the multi-objective scoring formulation.

### File locations

- CLI entry: [src/evaluation/__main__.py](../src/evaluation/__main__.py)
- `ComparisonRunner`: [src/evaluation/runner.py](../src/evaluation/runner.py)
- Statistics: [src/evaluation/statistics.py](../src/evaluation/statistics.py)
- Session loader: [src/evaluation/loader.py](../src/evaluation/loader.py)
- Types: [src/evaluation/types.py](../src/evaluation/types.py)
- Exceptions: [src/evaluation/exceptions.py](../src/evaluation/exceptions.py)
- Tests: [tests/unit/evaluation/](../tests/unit/evaluation/)
