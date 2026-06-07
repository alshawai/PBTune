# Architecture Overview

> Last reviewed: 2026-06-07

See also: [Documentation index](../README.md)

This document is the **first thing to read** when onboarding to the codebase. It ties together every per-component architecture doc with a single high-level system map, then points you at the right deep dive for whatever you're touching.

If you only have ten minutes, read just this page.

---

## What this project does

PBTune applies **Population-Based Training** — DeepMind's evolutionary hyperparameter optimisation algorithm — to **PostgreSQL configuration tuning**. Given a workload (Sysbench OLTP, TPC-H OLAP, or a custom JSON template), the tuner discovers high-performing combinations of PostgreSQL knobs without DBA intervention. The codebase also includes a Bayesian-Optimisation baseline (SMAC3) for direct comparison and a post-hoc statistical evaluation suite for publication-grade default-vs-tuned reporting.

The design priorities, in order:

1. **Methodological defensibility for research.** Measurement fairness, paired statistical tests, deterministic seeds, reproducibility metadata in every artefact.
2. **Single-host parallel evaluation.** N workers run on one machine using Docker CPU subset isolation; the lockstep barrier system makes their measurement windows overlap.
3. **Workload-agnostic scoring.** A feature-driven composite score adapts metric weights to workload shape rather than hardcoding per-benchmark formulas.
4. **Hardware-portable configurations.** Knobs serialise as fractions of detected resources, so tuning campaigns transfer across heterogeneous machines.

---

## High-level data flow

```text
                 ┌──────────────────────────────────────────────┐
                 │   CLI: python -m src.tuner.main              │
                 │   --tier core --config standard --workload   │
                 └──────────────────┬───────────────────────────┘
                                    │
                                    ▼
                 ┌──────────────────────────────────────────────┐
                 │   TunerConfig + KnobSpace + WorkerResources  │
                 │   (CSV-tiered knobs, hardware-aware bounds)  │
                 └──────────────────┬───────────────────────────┘
                                    │
                                    ▼
                 ┌──────────────────────────────────────────────┐
                 │   Population (N workers, LHS-initialised)    │
                 │   on N PostgreSQL instances (Docker / bare)  │
                 └──────────────────┬───────────────────────────┘
                                    │
                                    ▼
                  ┌─────────────────────────────────┐
                  │   For each generation:          │
                  │     parallel evaluate (B1-B17)  │
                  │     truncation + exploit        │
                  │     instance clone + perturb    │
                  │     dead-worker rescue          │
                  │     convergence check           │
                  └─────────────────┬───────────────┘
                                    │
                       converged or  │
                       max gens      │
                                    ▼
                 ┌──────────────────────────────────────────────┐
                 │   results/.../tuning_sessions/               │
                 │     pbt_results_*.json                       │
                 │     (config, score breakdown, history,       │
                 │      reproducibility metadata)               │
                 └──────────────────┬───────────────────────────┘
                                    │
                  ┌─────────────────┼──────────────────────┐
                  │                 │                      │
                  ▼                 ▼                      ▼
       ┌──────────────────┐ ┌──────────────────┐ ┌────────────────────┐
       │ src.evaluation   │ │ src.analysis     │ │ src.visualization  │
       │ default-vs-tuned │ │ fANOVA + TreeSHAP│ │ publication PDFs   │
       │ paired stats     │ │ data-driven tiers│ │ from session trees │
       └──────────────────┘ └──────────────────┘ └────────────────────┘
```

The arrow from tuning to the three downstream consumers is one-directional: the tuner emits session JSON, never imports from the consumers. This makes the consumers independently testable against checked-in fixtures.

---

## Package map

| Package | Role | Read first |
| --- | --- | --- |
| [`src/tuner/`](../../src/tuner/) | PBT engine (entry: `python -m src.tuner.main`) | [pbt-core](pbt-core.md) |
| [`src/tuner/core/`](../../src/tuner/core/) | Population, Worker, Evolution, GenerationBarrier | [pbt-core](pbt-core.md), [generation-barriers](generation-barriers.md) |
| [`src/tuner/benchmark/`](../../src/tuner/benchmark/) | WorkloadOrchestrator, restart policy, workload templates | [workload-orchestrator](workload-orchestrator.md) |
| [`src/tuner/config/`](../../src/tuner/config/) | KnobSpace, tier loading, TunerConfig | [configuration-management](configuration-management.md) |
| [`src/utils/scoring/`](../../src/utils/scoring/) | Feature-driven scoring v2 | [feature-driven-scoring](feature-driven-scoring.md) |
| [`src/utils/environments/`](../../src/utils/environments/) | Docker / bare-metal PostgreSQL backends | [environment-backends](environment-backends.md) |
| [`src/utils/applicator.py`](../../src/utils/applicator.py) | KnobApplicator (apply + verify) | [configuration-management §KnobApplicator](configuration-management.md#knobapplicator) |
| [`src/utils/metrics.py`](../../src/utils/metrics.py) | PerformanceMetrics, MetricConfig | [performance-evaluation](performance-evaluation.md) |
| [`src/utils/hardware_info.py`](../../src/utils/hardware_info.py) | WorkerResources detection | [hardware-aware-normalization](hardware-aware-normalization.md) |
| [`src/benchmarks/`](../../src/benchmarks/) | Sysbench, TPC-H executors | [benchmarking](../reference/benchmarking.md) |
| [`src/database/`](../../src/database/) | psycopg2 / SQLAlchemy connections, lifecycle | [postgresql-connection-and-knobs](postgresql-connection-and-knobs.md) |
| [`src/knobs/`](../../src/knobs/) | pg_settings retrieval, tuning metadata, policy filter | [postgresql-connection-and-knobs](postgresql-connection-and-knobs.md) |
| [`src/analysis/`](../../src/analysis/) | fANOVA + TreeSHAP + tier generation | [knob-importance-analysis](knob-importance-analysis.md) |
| [`src/evaluation/`](../../src/evaluation/) | Post-hoc default-vs-tuned comparison suite | [evaluation-suite](evaluation-suite.md) |
| [`src/visualization/`](../../src/visualization/) | Publication-figure generation | [visualization guide](../guides/visualization.md) |
| [`src/scripts/`](../../src/scripts/) | CLI entry points (setup, cleanup, BO baseline, comparison) | [cli](../reference/cli.md) |

---

## What makes this design different

Three architectural choices separate this codebase from a "vanilla PBT for databases" baseline:

### 1. Lockstep generation barriers

When N workers run on one host, their critical paths diverge — one finishes a config-apply quickly, another spends 30 seconds in a postmaster restart. Without synchronisation, the workers' measurement windows don't overlap, and the score difference within a generation reflects *scheduling artefacts* rather than configuration quality. The [`GenerationBarrier`](generation-barriers.md) inserts 17 explicit synchronisation points (B1–B17) around `evaluate_worker()`, guaranteeing every worker's measurement window overlaps every other's. Without this, parallel evaluation would not be methodologically defensible for publication.

### 2. Physical instance cloning during exploit

Standard PBT's exploit step copies knob values from elite to inheritor. That leaves the inheritor with a "cold" database state — empty buffer cache, no warmed-up indexes — so its first generation under the inherited config measures warmup, not configuration quality. This codebase additionally clones the elite's PostgreSQL data directory at exploit time (see [environment-backends §Instance cloning](environment-backends.md#instance-cloning-during-exploit)), so the inheritor begins with the elite's warmed-up state.

### 3. Feature-driven scoring with reliability gate

Per-benchmark hardcoded weights conflate workload shape with benchmark name — they cannot tell `sysbench oltp_read_only` from `sysbench oltp_write_only` even though those workloads have very different optimal trade-offs. The [scoring-v2 pipeline](feature-driven-scoring.md) extracts a workload feature vector (read/write ratio, OLAP complexity, tail sensitivity, …), feeds it into a floor-constrained softmax, and produces metric weights that adapt to workload shape. A multiplicative reliability gate `G ∈ [0, 1]` ensures crashed or degraded evaluations cannot win the score by accident.

---

## Key data types

| Type | Where defined | Carries |
| --- | --- | --- |
| [`PerformanceMetrics`](../../src/utils/metrics.py) | `src/utils/metrics.py` | Raw measurements: latency p50/p95/p99, throughput, variance, memory, scan efficiency, error rate, failure type. |
| [`ScoreBreakdown`](../../src/utils/scoring/contracts.py) | `src/utils/scoring/contracts.py` | Final score + per-metric utilities + resolved weights + reliability gate + policy version. |
| [`WorkloadFeatures`](../../src/utils/scoring/contracts.py) | `src/utils/scoring/contracts.py` | Workload shape vector consumed by `FeatureDrivenWeightModel`. |
| [`Worker`](../../src/tuner/core/worker.py) | `src/tuner/core/worker.py` | Configuration + score + lineage + step count + environment handle. |
| [`KnobDefinition`](../../src/tuner/config/knob_space.py) | `src/tuner/config/knob_space.py` | Bounds + scale + type + restart context + hardware-relative flag. |
| [`WorkerResources`](../../src/utils/hardware_info.py) | `src/utils/hardware_info.py` | Per-worker CPU / RAM / disk slice. |
| [`TunerConfig`](../../src/tuner/config/tuner_config.py) | `src/tuner/config/tuner_config.py` | Session-level configuration derived from CLI args. |
| [`ComparisonConfig`](../../src/evaluation/types.py) | `src/evaluation/types.py` | Post-hoc evaluation session config. |

Every persisted artefact (session JSON, BO baseline JSON, comparison JSON) is built from these types. The schema is documented in [reference/session-json-schema](../reference/session-json-schema.md).

---

## Result directory layout

```text
results/
├── oltp/{sysbench_workload}/         # one of oltp_read_only / oltp_read_write / oltp_write_only
│   ├── pbt_runs/{tier}/
│   │   └── tuning_sessions/
│   │       └── pbt_results_<timestamp>.json
│   ├── bo_runs/{tier}/
│   │   └── tuning_sessions/
│   │       └── bo_results_<timestamp>.json
│   ├── comparisons/{tier}/
│   │   ├── comparison_<timestamp>.json
│   │   └── logs/evaluation_<timestamp>.html
│   └── baselines/
│       └── default_<timestamp>.json
├── olap/                             # same structure for TPC-H
└── analysis/{workload_label}/
    ├── importance_results.json
    └── analysis_log.html
```

The path partitioning is deliberate: every consumer of the result tree (visualization loaders, evaluation suite, knob-importance analysis) globs over a `(workload, tier)` directory rather than parsing every file to find the right ones. See [reference/session-json-schema](../reference/session-json-schema.md) for the file contents.

---

## Where to go next

The doc set is organised by reader intent. From this overview:

- **You want to run something** → [getting-started/quickstart](../getting-started/quickstart.md), [guides/](../README.md#guides--how-to)
- **You want to modify the algorithm** → [pbt-core](pbt-core.md), then [generation-barriers](generation-barriers.md)
- **You want to add a knob or workload** → [guides/adding-knobs](../guides/adding-knobs.md), [guides/adding-workloads](../guides/adding-workloads.md)
- **You want to read the score formula** → [feature-driven-scoring](feature-driven-scoring.md), [reference/metrics-validation](../reference/metrics-validation.md)
- **You want to write tooling on top of session JSON** → [reference/session-json-schema](../reference/session-json-schema.md)
- **You want to understand a design choice** → [decisions/](decisions/)
- **You want the literature context** → [research/](../research/)
