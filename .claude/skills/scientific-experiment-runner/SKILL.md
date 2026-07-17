---
name: scientific-experiment-runner
description: >
  Patterns for designing, executing, and analyzing reproducible scientific experiments
  in the database tuning domain. Covers multi-seed runs, baseline comparisons, BO
  comparisons, statistical reporting, and results directory structure. Use this skill
  when creating experiment scripts, setting up baselines, running multi-seed campaigns,
  comparing against Bayesian Optimization, computing improvement percentages, designing
  experiment protocols, or working on any experiment orchestration code.
---

# Scientific Experiment Runner

## Multi-Seed Protocol

Run full PBT pipeline with **5 random seeds**: `[42, 123, 456, 789, 1024]`

```bash
# Each seed gets a separate run
python -m src.tuners pbt \
    --workload oltp --tier core \
    --population 8 --generations 20 \
    --random-seed 42 \
    --output-dir results/oltp/oltp_read_write/pbt_runs/core/seed_42/
```

Report: mean ± std of best score across seeds, plus convergence curves.

**Critical constraint:** Validation MUST use the same workload/benchmark with the same
scale factor and parameters (same-distribution requirement). Evaluating an OLTP-tuned
config on TPC-H is *transfer learning*, not validation.

## Baseline Evaluation

Run benchmark against **default PostgreSQL config** with **5 repetitions**
via the post-hoc evaluation suite (`src/evaluation`):

```bash
python -m src.evaluation \
    --session results/oltp/oltp_read_write/pbt_runs/core/tuning_sessions/pbt_results_YYYYMMDD_HHMM.json \
    --repetitions 5
```

The comparison runner evaluates both the default config and the tuned config
under identical Docker isolation. Compute mean ± std for all metrics. This is
the reference point for improvement%.

## Improvement Calculation
```
improvement = (best_pbt_score - baseline_score) / baseline_score × 100%
```
Report with confidence intervals from multi-seed runs:
```
CI = mean ± t_{0.025, n-1} × (std / √n)    where n = 5 seeds
```

## BO Comparison (SMAC3/OpenBox)

Wrap existing evaluation pipeline with BO library. Key requirement: **identical conditions**:
- Same knob space (`KnobSpace` instance)
- Same scoring formula (`MetricConfig`)
- Same benchmark config (workload, scale factor, duration)
- Same hardware

Compare on three axes:
| Metric | What it Shows |
|--------|---------------|
| Sample efficiency | Total evaluations to reach 95% of best score (BO may win) |
| Wall-clock time | Calendar time to same quality (PBT should win via parallelism) |
| Final quality | Absolute best score after equal budget |

## Results Directory Structure
```
results/
├── oltp/{oltp_read_only,oltp_read_write,oltp_write_only}/
│   ├── baselines/                         # Default PG config benchmarks
│   ├── pbt_runs/
│   │   ├── {minimal,core,standard,extensive}/   # By knob tier
│   │   │   ├── tuning_sessions/
│   │   │   │   └── pbt_results_{timestamp}.json
│   │   │   └── best_configs/
│   │   │       └── best_config_{timestamp}.json # For warm-start
│   ├── bo_runs/{tier}/
│   └── comparisons/{tier}/
├── olap/
│   └── (same structure for TPC-H)
└── analysis/{workload}/
```

## Results JSON Schema

Every results file MUST include:

```json
{
  "system_info": {
    "cpu_model": "...", "cpu_cores": 8,
    "ram_total_gb": 16.0, "disk_type": "SSD",
    "pg_version": "16.2", "os": "Ubuntu 22.04"
  },
  "experiment_config": {
    "workload": "oltp", "tier": "core",
    "population_size": 8, "generations": 20,
    "seed": 42
  },
  "generation_history": [...],
  "best_config": {...},
  "best_score": 85.3,
  "total_time_seconds": 3600.0
}
```

Hardware provenance is captured via `src/utils/hardware_info.py`.

## BO Baseline & Cross-Method Comparison

This project ships an SMAC3-based BO baseline and a cross-method comparison script:

```bash
python -m src.scripts.bo_baseline        # Runs the SMAC3 baseline
python -m src.scripts.pbt_vs_bo_comarison  # Cross-method comparison (filename typo intentional)
```

## Citing Published Baselines

When comparing against existing work, always cite and note hardware differences:
- **OtterTune** (SIGMOD 2017) — Gaussian Process BO
- **CDBTune** (SIGMOD 2019) — Deep Reinforcement Learning
- **LlamaTune** (VLDB 2022) — BO with subspace projection
- **GPTuner** (VLDB 2024) — LLM-guided optimization
