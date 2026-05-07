---
name: codebase-architecture
description: >
  Complete codebase map for the PBT PostgreSQL tuning research project. Covers all source
  packages, file inventory with responsibilities, dependency relationships, data flow through
  the tuning pipeline, and navigation guide. Use this skill whenever you need to understand
  where code lives, how packages relate to each other, which file to modify for a given task,
  or when onboarding to the project. This is the first skill to consult when starting any
  new task in this repository.
---

# Codebase Architecture

## Project Identity

- **Name**: Population-Based Training for PostgreSQL Configuration Tuning
- **Language**: Python 3.11+
- **Target DB**: PostgreSQL 14+
- **Source**: ~21,000 lines across `src/`
- **License**: Academic Research (Non-Commercial)

## High-Level Data Flow

```
CLI args (main.py)
    → TunerConfig
    → KnobSpace (tiered CSV → KnobDefinitions)
    → Population (N Workers, LHS-initialized)
    → FOR each generation:
        → Parallel evaluation via ThreadPoolExecutor
            → KnobApplicator (ALTER SYSTEM + restart/reload)
            → WorkloadEvaluator (sysbench / TPC-H / template SQL)
            → PerformanceMetrics collected
            → CompositeScorer (normalize → weight → gate → score)
        → Evolution (truncation selection → exploit → explore → perturb)
        → Convergence check
    → Best config saved to results/
    → Optional: Post-hoc evaluation (src/evaluation/)
```

## Package Map

### `src/tuner/` — PBT Tuning Engine (entry point: `python -m src.tuner.main`)
| File | Responsibility |
|------|---------------|
| `main.py` | CLI arg parsing, orchestration, session output |
| `core/population.py` | PBT loop: init → evaluate → evolve → converge |
| `core/worker.py` | Worker state (config, score, history, readiness) |
| `core/evolution.py` | Truncation selection, perturbation, convergence detection |
| `evaluator/evaluator.py` | Benchmark dispatch, metric collection, scoring integration |
| `evaluator/workload.py` | JSON/YAML workload template execution |
| `evaluator/restart_policy.py` | CDBTune-inspired batched restart logic |
| `config/knob_space.py` | Search space definition, LHS sampling, perturbation |
| `config/knob_loader.py` | Tiered knob CSV loading (minimal/core/standard/extensive) |
| `config/tuner_config.py` | CLI-derived configuration dataclass |

### `src/utils/scoring/` — Feature-Driven Scoring (v2)
| File | Responsibility |
|------|---------------|
| `scorer.py` | `CompositeScorer`: G × Σ(w_i × u_i) |
| `normalization.py` | `QuantileUtilityNormalizer`: quantile anchoring, drift, saturation |
| `weights.py` | `FeatureDrivenWeightModel`: floor-constrained softmax |
| `policies.py` | `ScoringPolicySpec`, `fixed_v1`, `feature_driven_v2` definitions |
| `workload_features.py` | Feature extraction (sysbench, TPC-H, template SQL) |
| `contracts.py` | `WorkloadFeatures`, `MetricSnapshot`, `ScoreBreakdown`, `NormalizationState` |
| `constants.py` | Metric IDs, directionality, default policy constants |

### `src/utils/` — Shared Utilities
| File | Responsibility |
|------|---------------|
| `metrics.py` | `PerformanceMetrics` dataclass — canonical metric record |
| `rescoring.py` | Post-hoc global score recalibration utilities |
| `applicator.py` | `KnobApplicator` — applies configs via ALTER SYSTEM |
| `hardware_info.py` | System resource detection (CPU, RAM, disk) |
| `metric_instrumentation.py` | Extended metric collection (buffer stats, scan efficiency) |
| `environments/base.py` | `DatabaseEnvironment` ABC |
| `environments/bare_metal.py` | Bare-metal PostgreSQL backend |
| `environments/docker.py` | Docker container-based backend |
| `environments/factory.py` | Environment factory (auto-selects backend) |
| `logger/` | Colored logging, banners, formatters, adapters (7 files) |

### `src/evaluation/` — Post-Hoc Comparison Suite (entry: `python -m src.evaluation`)
| File | Responsibility |
|------|---------------|
| `__main__.py` | CLI entry point |
| `runner.py` | `ComparisonRunner` — orchestrates default-vs-tuned evaluations |
| `statistics.py` | Wilcoxon, bootstrap CI, Holm-corrected α, Cohen's d |
| `loader.py` | Session JSON parser with scoring metadata compatibility |
| `types.py` | `ComparisonConfig`, `RunResult`, `ComparisonReport` |
| `exceptions.py` | Domain-specific exception hierarchy |

### `src/analysis/` — Post-Run Analysis
| File | Responsibility |
|------|---------------|
| `data_loader.py` | Load/normalize PBT session results for analysis |
| `importance.py` | fANOVA + TreeSHAP knob importance analysis |

### Other Packages
| Package | Responsibility |
|---------|---------------|
| `src/database/` | PostgreSQL connection management |
| `src/knobs/` | Knob metadata retrieval from PG catalogs |
| `src/benchmarks/` | Benchmark executor interfaces (sysbench, TPC-H) |
| `src/scripts/` | Setup, cleanup, knob analysis utilities |
| `src/config/` | Global database configuration |

## Key Data Types

| Type | Location | Purpose |
|------|----------|---------|
| `PerformanceMetrics` | `src/utils/metrics.py` | Raw metric record from evaluation |
| `Worker` | `src/tuner/core/worker.py` | Config + score + history |
| `KnobDefinition` | `src/tuner/config/knob_space.py` | Knob metadata (type, bounds, context) |
| `TunerConfig` | `src/tuner/config/tuner_config.py` | Session configuration |
| `ScoringPolicySpec` | `src/utils/scoring/policies.py` | Policy definition |
| `ComparisonConfig` | `src/evaluation/types.py` | Evaluation session config |

## Build & Validation

```bash
make lint           # ruff check src tests
make typecheck      # mypy src/evaluation src/utils src/scripts
make test           # pytest -q tests/unit
make check-all      # lint + typecheck + test
```

## Result Directories

```
results/
├── oltp/{sysbench_workload}/
│   ├── pbt_runs/{tier}/tuning_sessions/
│   ├── comparisons/{tier}/
│   └── baselines/
├── olap/
│   └── (same structure for TPC-H)
└── best_configs/
```

## Documentation Index

| Document | Focus |
|----------|-------|
| `docs/FEATURE_DRIVEN_SCORING.md` | Scoring-v2 architecture |
| `docs/PBT_CORE_COMPONENTS.md` | Worker, Evolution, Population |
| `docs/PERFORMANCE_EVALUATION.md` | Evaluator, metrics, scoring |
| `docs/BENCHMARKING.md` | Dual-evaluation strategy |
| `docs/EVALUATION_RUNBOOK.md` | Reproducibility commands |
| `docs/CONFIGURATION_MANAGEMENT.md` | KnobSpace, sampling, perturbation |
| `docs/AUTOTUNING_KNOB_POLICY.md` | Comprehensive knob policy reference |
