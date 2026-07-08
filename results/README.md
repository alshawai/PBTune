# PBTune Experiments

Experiment artifacts for [PBTune](https://github.com/eima40x4c/PBTune) — a Population-Based Training framework for PostgreSQL knob tuning.

## Structure

```text
results/
├── olap/                           # TPC-H workloads
│   ├── pbt_runs/<tier>/
│   │   ├── tuning_sessions/        # Full PBT session JSONs
│   │   └── best_configs/           # Extracted best configurations
│   ├── bo_runs/<tier>/             # BO baseline sessions
│   └── comparisons/<tier>/         # Post-tuning evaluation results
│
├── oltp/                           # Sysbench workloads
│   ├── oltp_read_write/
│   │   ├── pbt_runs/<tier>/
│   │   ├── bo_runs/<tier>/
│   │   └── comparisons/<tier>/
│   ├── oltp_read_only/
│   └── oltp_write_only/
│
└── analysis/                       # Cross-experiment analysis artifacts
```

## What's Tracked

| Artifact | Tracked | Why |
|----------|---------|-----|
| Tuning session JSONs (`pbt_results_*.json`) | ✅ | Primary experimental output |
| BO baseline JSONs (`bo_results_*.json`) | ✅ | Baseline comparison data |
| Best configs (`best_config*.json`) | ✅ | Deployable configurations |
| Evaluation JSONs (`comparison_*.json`) | ✅ | Statistical significance results |
| HTML logs | ✅ | Human-readable summaries |
| Intermediate generations | ❌ | Bulky, reconstructable from session JSON |
| SMAC internal state | ❌ | BO surrogate internals, not needed |

## Usage

This repo lives inside PBTune's `results/` directory (which is gitignored by the parent repo).

```bash
# On experiment server (after running experiments)
cd /path/to/PBTune/results
git add -A && git commit -m "exp: OLTP RW seed=42 pop=8 gen=50"
git push

# On local machine (to monitor results)
cd /path/to/PBTune
git clone <this-repo-url> results/
```
