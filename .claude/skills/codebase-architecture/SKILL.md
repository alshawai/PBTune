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

- **Name**: Population-Based Training for PostgreSQL Configuration Tuning (PBTune)
- **Language**: Python 3.11+
- **Target DB**: PostgreSQL 14+
- **License**: Academic Research (Non-Commercial)

## High-Level Data Flow

```
CLI args (src.tuners pbt → pbt/cli.py)
    → PBTConfig + TunerLifecycleConfig
    → KnobSpace (tiered CSV → KnobDefinitions)
    → Population (N Workers, LHS-initialized)
    → FOR each generation:
        → Parallel evaluation via ThreadPoolExecutor (lockstep barriers B1..B17)
            → KnobApplicator (ALTER SYSTEM + restart/reload)
            → WorkloadOrchestrator (sysbench / TPC-H / template SQL)
            → PerformanceMetrics collected
            → CompositeScorer (normalize → weight → gate → score)
        → Evolution (truncation selection → exploit → explore → perturb)
        → Convergence check
    → Best config saved to results/
    → Optional: Post-hoc evaluation (src/evaluation/)
```

## Package Map

### `src/tuners/` — Unified Tuning Framework (entry point: `python -m src.tuners pbt`)
| File | Responsibility |
|------|---------------|
| `base.py` | `BaseTuner` — invariant lifecycle (Template Method): setup, generation loop, teardown, result assembly |
| `cli.py` | Strategy-agnostic CLI flag groups + shared HTML-log attach |
| `__main__.py` | Positional-token router (`python -m src.tuners <strategy> ...`) |
| `pbt/tuner.py` | `PBTTuner(BaseTuner)` — PBT strategy hooks (propose/step/collect_best/payload) |
| `pbt/cli.py` | PBT CLI + config build (entry: `python -m src.tuners.pbt`) |
| `pbt/population.py` | PBT loop: init → evaluate → evolve → converge |
| `pbt/evolution.py` | Truncation selection, perturbation, convergence detection |
| `pbt/worker.py` | `PBTWorker(BaseWorker)` — evolution mechanics + score/lineage state |
| `pbt/config.py` | `PBTConfig` + the 4 profile constants (`*_CONFIG`) |
| `engine/orchestrator.py` | `WorkloadOrchestrator` — benchmark dispatch, metric collection, scoring integration |
| `engine/worker.py` | `BaseWorker` — generic eval vehicle (config, score_breakdown, identity) |
| `engine/barriers.py` | B1..B17 lockstep generation barriers |
| `engine/restart_policy.py` | Restart policy with `TuningMode` {ONLINE, OFFLINE, ADAPTIVE} |
| `lhs_design/tuner.py` | `LHSDesignTuner(BaseTuner)` — Latin-hypercube importance-design strategy |

### `src/utils/scoring/` — Feature-Driven Scoring (v2)
| File | Responsibility |
|------|---------------|
| `scorer.py` | `CompositeScorer`: 100 × G × Σ(w_i × u_i) / (1 − w_error) |
| `normalization.py` | `QuantileUtilityNormalizer`: quantile anchoring, drift, saturation |
| `weights.py` | `FeatureDrivenWeightModel`: floor-constrained softmax |
| `policies.py` | `ScoringPolicySpec`, `fixed_v1`, `feature_driven_v2` definitions |
| `workload_features.py` | Feature extraction (sysbench, TPC-H, template SQL) |
| `contracts.py` | `WorkloadFeatures`, `MetricSnapshot`, `ScoreBreakdown`, `NormalizationState` |
| `constants.py` | Metric IDs, directionality, default policy constants |
| `outlier_filtering.py` | Outlier-resistant pre-filtering for normalization |

### `src/utils/` — Shared Utilities
| File | Responsibility |
|------|---------------|
| `metrics.py` | `PerformanceMetrics` dataclass — canonical metric record |
| `calibration.py` | Post-hoc global score recalibration utilities |
| `applicator.py` | `KnobApplicator` — applies configs via ALTER SYSTEM |
| `hardware_info.py` | System resource detection (CPU, RAM, disk) |
| `metric_instrumentation.py` | Extended metric collection (buffer stats, scan efficiency) |
| `timing.py` | `TimingRecorder`, `TimingRecord` (frozen dataclass) — v1.1 timing primitives |
| `session_clock.py` | `session_timestamp()` — wall-clock for filenames/log lines |
| `types.py` | Shared dataclasses and type aliases |
| `environments/base.py` | `DatabaseEnvironment` ABC |
| `environments/bare_metal.py` | Bare-metal PostgreSQL backend |
| `environments/docker.py` | Docker container-based backend (supports CPU subset isolation, ADR-004) |
| `environments/factory.py` | Environment factory (auto-selects backend) |
| `logger/` | Colored logging, banners, formatters, adapters |

> Durations come from `time.monotonic()` (never `time.time()`). Three recorder layers:
> (1) `tuner.bootstrap_timing` on `PBTTuner`, (2) `population.generation_timing` on
> `Population`, and (3) a per-worker recorder local in `WorkloadOrchestrator.evaluate_worker`
> (attached as `worker.last_eval_timing`).

### `src/evaluation/` — Post-Hoc Comparison Suite (entry: `python -m src.evaluation`)
| File | Responsibility |
|------|---------------|
| `__main__.py` | CLI entry point |
| `runner.py` | `ComparisonRunner` — orchestrates default-vs-tuned (and optional BO) evaluations |
| `statistics.py` | Wilcoxon, bootstrap CI, Holm-corrected α, Cohen's d |
| `loader.py` | Session JSON parser with scoring + timing-schema compatibility |
| `types.py` | `ComparisonConfig`, `RunResult`, `ComparisonReport` |
| `exceptions.py` | Domain-specific exception hierarchy |

### `src/analysis/` — Post-Run Analysis
| File | Responsibility |
|------|---------------|
| `data_loader.py` | Load/normalize PBT session results for analysis |
| `importance.py` | fANOVA + TreeSHAP knob importance analysis |
| `hardware_validator.py` | Cross-hardware importance stability checks |
| `tier_generator.py` | Data-driven tier generation (Jenks Natural Breaks) |
| `timing_breakdown.py` | Aggregates v1.1 timing records to LaTeX/CSV |

### `src/visualization/` — Publication Figures (entry: `python -m src.visualization`)
| File | Responsibility |
|------|---------------|
| `__main__.py` | CLI entry point |
| `plots/` | Convergence, trajectory, breakdown, BO-vs-PBT figures |
| `loaders/` | Session/comparison JSON loaders |
| `registry.py` | Figure registry |
| `theme.py`, `colors.py` | Publication theme + colorblind palette |
| `export.py` | PDF/PNG export helpers |
| `types.py`, `utils.py`, `exceptions.py` | Shared dataclasses, helpers, errors |

### Other Packages
| Package | Responsibility |
|---------|---------------|
| `src/database/` | PostgreSQL connection + management (`connection.py`, `data_loader.py`, `management.py`) |
| `src/knobs/` | Knob metadata, retrieval, preprocessing, policy (`knob_metadata.py`, `retrieval.py`, `preprocess_knobs.py`, `policy.py`) |
| `src/benchmarks/` | Benchmark executor interfaces (`executor.py`, `sysbench/`, `tpch/`) |
| `src/scripts/` | Setup, cleanup, knob analysis, `pbt_vs_bo_comarison.py` (filename typo is intentional) |
| `src/config/` | Global database configuration |

## Key Data Types

| Type | Location | Purpose |
|------|----------|---------|
| `PerformanceMetrics` | `src/utils/metrics.py` | Raw metric record from evaluation |
| `Worker` | `src/tuners/engine/worker.py` | Config + score + history |
| `KnobDefinition` | `src/knobs/knob_space.py` | Knob metadata (type, bounds, context) |
| `PBTConfig` | `src/tuners/pbt/config.py` | Session configuration |
| `ScoringPolicySpec` | `src/utils/scoring/policies.py` | Policy definition |
| `ComparisonConfig` | `src/evaluation/types.py` | Evaluation session config |
| `TimingRecorder`, `TimingRecord` | `src/utils/timing.py` | Timing instrumentation primitives |

## Build & Validation

```bash
make install-dev
make lint           # ruff check src tests
make typecheck      # mypy src/evaluation src/utils src/scripts (includes src/utils/logger)
make test           # pytest -q tests/unit
make check-all      # lint + typecheck + test
make fix-and-check  # auto-fix then re-run check-all
```

## CLI Entry Points (the five user-facing commands)

```bash
python -m src.tuners pbt                # PBT tuning
python -m src.evaluation                # Post-hoc default-vs-tuned comparison
python -m src.tuners bo                 # SMAC3 BO baseline
python -m src.scripts.pbt_vs_bo_comarison  # Cross-method comparison (filename typo is intentional)
python -m src.visualization             # Publication figure generation
```

Setup/utility scripts:
```bash
bash scripts/bootstrap.sh
python -m src.scripts.setup_database
python -m src.scripts.cleanup_instances
python -m src.scripts.analyze_knobs
python -m src.scripts.analyze_knob_importance
```

## Result Directories

```
results/
├── sessions/{workload}/{pbt,bo,lhs}/{tier}/   # {workload} = one granular key (olap, oltp_read_write, ...)
│   ├── traces/trace_{timestamp}.json          # per-run tuning trace (was tuning_sessions/*_results_*.json)
│   ├── best_configs/best_{timestamp}.json     # best config for warm-start
│   └── logs/session_{timestamp}.html
├── comparisons/{workload}/{tier}/             # evaluation reports (comparisons BEFORE workload)
│   └── comparison_{timestamp}.json
└── analysis/importance/                        # fANOVA/SHAP/SCALPEL importance + tier outputs
```

## Documentation Index

- `docs/architecture/` — explanation (Diataxis): PBT core, feature-driven scoring, ADRs
- `docs/reference/` — lookup tables, session JSON schema (`session-json-schema.md`)
- `docs/guides/` — how-tos (evaluation runbook, knob policy, etc.)
- `docs/getting-started/` — tutorials
- `docs/research/` — positioning, baselines, related work
