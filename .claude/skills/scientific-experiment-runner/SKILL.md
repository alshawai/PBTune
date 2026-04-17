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
python -m src.tuner.main \
    --workload oltp --tier core \
    --population-size 8 --generations 20 \
    --seed 42 \
    --output results/sysbench/pbt_runs/core/seed_42/
```

Report: mean ± std of best score across seeds, plus convergence curves.

**Critical constraint:** Validation MUST use the same workload/benchmark with the same
scale factor and parameters (same-distribution requirement). Evaluating an OLTP-tuned
config on TPC-H is *transfer learning*, not validation.

## Baseline Evaluation

Run benchmark against **default PostgreSQL config** with **5 repetitions**:

```bash
python -m src.tuner.main \
    --workload oltp --tier minimal \
    --baseline-only --repetitions 5 \
    --output results/sysbench/baselines/
```

Compute mean ± std for all metrics. This is the reference point for improvement%.

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
├── sysbench/
│   ├── baselines/                    # Default PG config benchmarks
│   │   └── baseline_{timestamp}.json
│   ├── pbt_runs/
│   │   ├── minimal/                  # By knob tier
│   │   │   └── seed_{seed}/
│   │   │       └── results_{timestamp}.json
│   │   ├── core/
│   │   ├── standard/
│   │   └── extensive/
│   └── bo_comparison/
│       └── smac3_{timestamp}.json
├── tpch/
│   └── (same structure)
└── best_configs/
    └── best_config_{timestamp}.json  # For warm-start
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

Hardware provenance is captured via `src/tuner/utils/hardware_info.py`.

## Citing Published Baselines

When comparing against existing work, always cite and note hardware differences:
- **OtterTune** (SIGMOD 2017) — Gaussian Process BO
- **CDBTune** (SIGMOD 2019) — Deep Reinforcement Learning
- **LlamaTune** (VLDB 2022) — BO with subspace projection
- **GPTuner** (VLDB 2024) — LLM-guided optimization
