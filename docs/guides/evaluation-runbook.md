# Evaluation Reproducibility Runbook

> Last reviewed: 2026-06-15

See also: [Documentation Index](../README.md)

This runbook defines the canonical workflow for reproducing comparative
`default vs tuned` evaluation results from a saved tuning session.

## Scope

Use this runbook for:

- Re-running evaluation after a tuning session
- Generating reviewer-ready comparison JSON artifacts
- Verifying reproducibility assumptions (seed handling, environment capture)

## Prerequisites

- Python environment with dev dependencies installed:

```bash
pip install -r requirements-dev.txt
```

- A completed tuning session JSON file (for example):

`results/olap/pbt_runs/extensive/tuning_sessions/pbt_results_YYYYMMDD_HHMM.json`

- Docker daemon running for isolated evaluation (recommended)

## Canonical Commands

### Option A: Docker-Isolated Evaluation (Recommended)

```bash
python -m src.evaluation \
  --session results/olap/pbt_runs/extensive/tuning_sessions/pbt_results_YYYYMMDD_HHMM.json \
  --repetitions 10 \
  --seed 50000
```

### Option B: Bare-Metal Fallback (Reduced Isolation)

```bash
python -m src.evaluation \
  --session results/olap/pbt_runs/extensive/tuning_sessions/pbt_results_YYYYMMDD_HHMM.json \
  --repetitions 10 \
  --seed 50000 \
  --no-docker
```

### Option C: Explicit Sysbench Runtime Overrides

```bash
python -m src.evaluation \
  --session results/oltp/oltp_read_write/pbt_runs/core/tuning_sessions/pbt_results_YYYYMMDD_HHMM.json \
  --repetitions 8 \
  --sysbench-workload oltp_read_write \
  --sysbench-tables 16 \
  --sysbench-table-size 200000 \
  --sysbench-duration 90 \
  --sysbench-warmup-seconds 15
```

### Option C1: Evaluate Read-Only Sysbench Session

```bash
python -m src.evaluation \
  --session results/oltp/oltp_read_only/pbt_runs/core/tuning_sessions/pbt_results_YYYYMMDD_HHMM.json \
  --repetitions 8 \
  --sysbench-workload oltp_read_only
```

### Option C2: Evaluate Write-Only Sysbench Session

```bash
python -m src.evaluation \
  --session results/oltp/oltp_write_only/pbt_runs/core/tuning_sessions/pbt_results_YYYYMMDD_HHMM.json \
  --repetitions 8 \
  --sysbench-workload oltp_write_only
```

### Option D: Explicit TPC-H Runtime Overrides

```bash
python -m src.evaluation \
  --session results/olap/pbt_runs/extensive/tuning_sessions/pbt_results_YYYYMMDD_HHMM.json \
  --repetitions 8 \
  --tpch-scale-factor 1.0 \
  --tpch-warmup-passes 2
```

### Option E: Three-Way Comparison (Default vs BO vs PBT)

When a paired BO baseline run is available, run a multi-arm comparison so that
default, BO-tuned, and PBT-tuned configurations are measured against the same
workload under the same paired seeds:

```bash
python -m src.evaluation \
  --session results/oltp/oltp_read_write/pbt_runs/core/tuning_sessions/pbt_results_YYYYMMDD_HHMM.json \
  --bo-session results/oltp/oltp_read_write/bo_runs/core/tuning_sessions/bo_results_YYYYMMDD_HHMM.json \
  --repetitions 10
```

When omitted, `--seed` defaults to `50000`.

### Scoring Policy Override

Override the scoring policy used during comparison (useful for re-evaluating
historical sessions under the newer feature-driven model):

```bash
python -m src.evaluation \
    --session results/olap/pbt_runs/extensive/tuning_sessions/pbt_results_YYYYMMDD_HHMM.json \
    --repetitions 5 \
    --scoring-policy feature_driven_v2
```

Available policies:
- `fixed_v1` — legacy static weights (default for historical sessions)
- `feature_driven_v2` — dynamic workload-feature-conditioned weights

**Note:** Using `feature_driven_v2` may shift which metrics dominate the composite score
compared to the original tuning session, potentially changing the ranking of configurations.
This is expected behavior and reflects the improved metric weighting strategy.

## Output Location

By default, evaluation outputs are written to:

- `results/oltp/{sysbench_workload}/comparisons/{tier}/` for Sysbench OLTP workloads
- `results/olap/comparisons/{tier}/` for OLAP workloads
- `results/mixed/comparisons/{tier}/` for mixed or unknown workloads

Within the selected output directory, artifacts are split as:

- Comparison JSON: `comparison_{timestamp}.json`
- Session HTML log: `logs/evaluation_{timestamp}.html`

The `{tier}` segment is inferred from `tuning_session.knob_tier` in the
session JSON, with a fallback to the tier segment in the session path
(`.../pbt_runs/{tier}/...`). If neither source is available, the fallback
tier is `unknown`.

Override this with `--output-dir <path>` when needed.

## Reproducibility Checklist

For each run, verify the generated comparison JSON includes:

- `comparison_metadata.repetitions`
- `comparison_metadata.evaluation_environment`
- `comparison_metadata.resource_constraints`
- `comparison_metadata.scoring_policy`
- `comparison_metadata.scoring_policy_version`
- `comparison_metadata.metric_reference_version`
- `comparison_metadata.workload_features`
- `comparison_metadata.normalization_metadata`
- `comparison_metadata.score_breakdown`
- `comparison_metadata.reproducibility.python_version`
- `comparison_metadata.reproducibility.postgres_version`
- `comparison_metadata.reproducibility.docker_image` (when Docker mode is used)
- `comparison_metadata.reproducibility.python_package_versions`
- `comparison_metadata.reproducibility.benchmark_binary_paths`

## Random Seed Handling and Deterministic Limits

### Tuning stage (`src/tuner`)

- Tuning CLI exposes `--random-seed` (default: `42`) in `src/tuner/main.py`.
- Population initialization and perturbation sampling use deterministic seed
  propagation from the global seed.

### Evaluation stage (`src/evaluation`)

- Each default/tuned run pair uses an identical deterministic workload seed.
  Repetition `i` uses `base_seed + i - 1` for both configurations.
- Statistical bootstrap uses a fixed RNG seed (`42`) in
  `src/evaluation/statistics.py` for deterministic confidence interval
  computation.

## Statistical Endpoint Policy

- Primary endpoint: `score` tested at $\alpha = 0.05$ (no family correction).
- Secondary endpoint family: benchmark latency endpoint + throughput +
  memory utilization.
  - Sysbench secondary latency endpoint: `latency_p95`.
  - TPC-H secondary latency endpoint: `latency_p99`.
- Secondary p-values use Holm correction.
- Score comparisons should be interpreted with the recorded scoring policy and
  policy version from the comparison JSON when comparing results across runs.

### Practical limits

Even with fixed seeds, full determinism is not guaranteed due to:

- OS scheduling and process timing noise
- PostgreSQL background activity
- Storage and cache effects
- Bare-metal resource contention

Docker isolation materially reduces this variance and is recommended for
publication-facing comparisons.

## Failure Triage

- Docker unavailable: rerun with `--no-docker` and mark output as reduced-isolation.
- Missing benchmark binaries (`sysbench`/`psql`): install system packages and rerun.
- Session file missing fields: validate the tuning session JSON first.
- Runtime mismatch: compare reproducibility metadata between baseline and rerun.
