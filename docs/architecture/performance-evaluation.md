# Performance Evaluation System

See also: [Documentation Index](../README.md), [Feature-Driven Scoring](feature-driven-scoring.md), [Workload Orchestrator](workload-orchestrator.md), [Generation Barriers](generation-barriers.md)

## Overview

The performance evaluation system is PBT's **fitness function**: it converts a candidate PostgreSQL configuration into a single scalar score that drives evolution. It is composed of three layers:

1. **[`PerformanceMetrics`](../../src/utils/metrics.py)** — typed record of raw measurements collected from a single evaluation window (latency, throughput, variance, memory, scan efficiency, error rate, …).
2. **Scoring-v2 pipeline** — `WorkloadFeatures` → `QuantileUtilityNormalizer` → `FeatureDrivenWeightModel` → `CompositeScorer` → `ScoreBreakdown`. The math and policies are documented in [FEATURE_DRIVEN_SCORING.md](feature-driven-scoring.md).
3. **[`WorkloadOrchestrator`](../../src/tuner/benchmark/orchestrator.py)** — the runtime that applies a configuration, drives a benchmark executor, captures metrics, and emits a `ScoreBreakdown`. It is the component PBT's [`Population`](../../src/tuner/core/population.py) calls during each generation.

The previous evaluator at `src/tuner/evaluator/evaluator.py` has been retired; all evaluation logic now lives under [src/tuner/benchmark/](../../src/tuner/benchmark/) and the scoring layer under [src/utils/scoring/](../../src/utils/scoring/).

---

## Table of Contents

1. [Architecture](#architecture)
2. [PerformanceMetrics](#performancemetrics)
3. [Scoring Integration](#scoring-integration)
4. [WorkloadOrchestrator](#workloadorchestrator)
5. [System Monitoring](#system-monitoring)
6. [Workload Types and Optimization Goals](#workload-types-and-optimization-goals)
7. [Design Decisions](#design-decisions)
8. [Related Documentation](#related-documentation)

---

## Architecture

```text
┌─────────────────────────────────────────────────────────────┐
│                      PBT Training Loop                      │
│                (see PBT_CORE_COMPONENTS.md)                 │
└───────────────────────────┬─────────────────────────────────┘
                            │
                            │ Population.evaluate_generation()
                            │ → orchestrator.evaluate_worker(worker)
                            ▼
                ┌──────────────────────────────┐
                │     WorkloadOrchestrator     │
                │ (per-worker, lockstep B1–B17)│
                └──────────────┬───────────────┘
                               │
        ┌──────────────────────┼──────────────────────┐
        │                      │                      │
        ▼                      ▼                      ▼
┌────────────────┐    ┌────────────────┐    ┌────────────────┐
│ KnobApplicator │    │  BenchmarkExec │    │   Metric       │
│  apply+verify  │    │  (Sysbench /   │    │ Instrumentation│
│                │    │   TPC-H /      │    │ (pg_stat_*,    │
│                │    │   Workload)    │    │  psutil)       │
└────────┬───────┘    └────────┬───────┘    └────────┬───────┘
         │                     │                     │
         └─────────────────────┼─────────────────────┘
                               │
                               ▼
                  ┌──────────────────────────┐
                  │   PerformanceMetrics     │
                  │   (raw measurement)      │
                  └────────────┬─────────────┘
                               │
                               ▼
                  ┌──────────────────────────┐
                  │   CompositeScorer        │
                  │  G × Σ(w_i · u_i)        │
                  │  (see FEATURE_DRIVEN_*)  │
                  └────────────┬─────────────┘
                               │
                               ▼
                  ┌──────────────────────────┐
                  │     ScoreBreakdown       │
                  │  score ∈ [0, 1]          │
                  │  + per-metric components │
                  └────────────┬─────────────┘
                               │
                               ▼
                       back to Population
                     (exploit / explore step)
```

The orchestrator is invoked from `Population.evaluate_generation()`, runs **per-worker on its own thread** (one per `WorkerResources` slice), and synchronises with the rest of the generation through the lockstep [`GenerationBarrier`](generation-barriers.md) so every worker's measurement window experiences the same level of contention.

---

## PerformanceMetrics

**Location**: [src/utils/metrics.py](../../src/utils/metrics.py)

`PerformanceMetrics` is the canonical raw-measurement record. It is the **input** to scoring and the **output** of every benchmark executor. The dataclass is intentionally a superset of what any single scoring policy needs — the active policy selects which fields it consumes.

### Fields

```python
@dataclass
class PerformanceMetrics:
    # Latency
    latency_p50: float = 0.0
    latency_p95: float = 0.0
    latency_p99: float = 0.0
    latency_unit: str = "ms"
    latency_variance: float = 0.0
    tail_amplification: float = 0.0      # p99 / p50 ratio

    # Throughput
    throughput: float = 0.0
    throughput_unit: str = "TPS"          # or "QphH" for TPC-H
    throughput_variance: float = 0.0

    # Volume
    total_queries: int = 0
    total_time: float = 0.0

    # Memory
    memory_utilization: float = 0.0       # PostgreSQL RSS / worker budget
    memory_pressure: float = 0.0          # derived pressure signal

    # I/O
    io_read_mb: float = 0.0
    io_write_mb: float = 0.0

    # Cache / scan
    cache_hit_ratio: float = 0.0
    buffer_miss_rate: float = 0.0
    scan_efficiency: float = 0.0
    rows_examined: int = 0
    rows_returned: int = 0

    # Reliability
    error_rate: float = 0.0
    failure_type: Optional[str] = None
```

### Why so many fields?

The scoring layer's [`FeatureDrivenWeightModel`](../../src/utils/scoring/weights.py) computes weights from **workload features** (read/write mix, OLAP complexity, tail sensitivity, etc.). Different workloads emphasise different fields:

- A Sysbench `oltp_read_write` run weighs `throughput`, `latency_p95`, and `error_rate` heavily.
- A TPC-H run shifts weight toward `latency_p99`, `tail_amplification`, `scan_efficiency`, and `buffer_miss_rate`.
- The `memory_pressure` and `memory_utilization` fields act as resource regularisers across both.

Keeping all fields populated by every executor lets the scorer reuse one schema across benchmarks without per-workload metric tables.

### Serialisation

```python
metrics.to_dict()                         # dict[str, float | str | int | None]
```

Used by session writers, the BO baseline, the post-hoc evaluation suite, and the analysis pipeline. No `from_dict` is provided — deserialisation goes through the session loaders in [src/evaluation/loader.py](../../src/evaluation/loader.py) and [src/visualization/loaders/](../../src/visualization/loaders/), which handle policy/version migration.

---

## Scoring Integration

The orchestrator never imports the scoring math directly; it delegates to the `CompositeScorer` configured on the [`MetricConfig`](../../src/utils/metrics.py) attached to its `WorkloadOrchestratorConfig`. The contract is:

```python
score_breakdown: ScoreBreakdown = scorer.score(metrics, workload_features)
final_score: float = score_breakdown.score      # ∈ [0, 1]
```

`ScoreBreakdown` (see [`src/utils/scoring/contracts.py`](../../src/utils/scoring/contracts.py)) carries the resolved weights, per-metric utilities, reliability gate value, and the policy / metric reference version. Sessions persist this breakdown so post-hoc tools can rescore consistently.

The score is bounded by `[0, 1]`. The legacy `× 100.0` scaling factor used in the old policy is no longer part of the pipeline — comparisons, dashboards, and the BO baseline all read fractional scores from `ScoreBreakdown.score`.

For the math, the floor-constrained softmax over feature-conditioned logits, and the reliability gate, see [FEATURE_DRIVEN_SCORING.md](feature-driven-scoring.md).

---

## WorkloadOrchestrator

**Location**: [src/tuner/benchmark/orchestrator.py](../../src/tuner/benchmark/orchestrator.py)

`WorkloadOrchestrator` replaces the older `Evaluator`. Its responsibility is to take a single worker's knob configuration and return a `(PerformanceMetrics, ScoreBreakdown)` pair.

### `WorkloadOrchestratorConfig`

```python
@dataclass
class WorkloadOrchestratorConfig:
    workload_type: WorkloadType
    metric_config: MetricConfig
    db_config: DatabaseConfig
    warmup_duration: float = 30.0
    measurement_duration: float = 60.0
    cooldown_duration: float = 5.0
    tuning_mode: TuningMode = TuningMode.ONLINE
    adaptive_restart_interval: int = 10
    random_seed: Optional[int] = None
    warmup_passes: int = 0
    vacuum_analyze_timeout_seconds: float = 45.0
    worker_memory_budget_bytes: Optional[int] = None
```

Notable fields beyond the obvious timing knobs:

- **`tuning_mode`** — `ONLINE` (default) applies via `pg_reload_conf()` and restarts only when needed; `OFFLINE` restarts unconditionally to mirror an academic batch tuner; `ADAPTIVE` applies the CDBTune-inspired batched-restart policy from [`src/tuner/benchmark/restart_policy.py`](../../src/tuner/benchmark/restart_policy.py).
- **`adaptive_restart_interval`** — generation interval at which `ADAPTIVE` mode forces a restart even if no `postmaster`-context knob changed.
- **`worker_memory_budget_bytes`** — the per-worker RAM slice used to normalise PostgreSQL's RSS into `memory_utilization`. Set from `WorkerResources` when running multiple workers on one host; falls back to total host RAM when unset.
- **`vacuum_analyze_timeout_seconds`** — bounds the post-measurement `VACUUM ANALYZE` so a stuck maintenance pass cannot stall a whole generation.

### `evaluate_worker(worker)` flow

Each call passes through 17 barrier-synchronised sub-steps (see [GENERATION_BARRIERS.md](generation-barriers.md)):

```text
B1  connect                  TCP connection established
B2  apply config             ALTER SYSTEM + pg_reload_conf
B3  restart (or skip)        based on tuning mode and restart_policy
B4  reconnect                post-restart re-connection
B5  verify config            applicator.verify() — returns the actually
                             quantised knob values from current_setting()
B6  capture pre-stats        pg_stat_database snapshot
B7  benchmark ready          schema validate / prepare
B8  warmup                   executor warmup phase
B9  measurement              timed window — the only window scored
B10 capture post-stats       pg_stat_database snapshot
B11 compute I/O delta        from the two pg_stat snapshots
B12 system metrics           memory_utilization, cache_hit_ratio via
                             metric_instrumentation
B13 memory pressure          derived signal
B14 reliability gate         classify failure / degradation
B15 vacuum analyze           post-DML safety pass, bounded by timeout
B16 score                    composite scorer → ScoreBreakdown
B17 disconnect               clean teardown
```

The return value is `(PerformanceMetrics, ScoreBreakdown)`. The breakdown is what `Worker.update_metrics()` stores and what gets serialised into session JSON.

### Read-back: why `verify()` matters

PostgreSQL silently rounds values to internal block boundaries (e.g. `shared_buffers` rounds to the nearest 8 kB page). The orchestrator calls `KnobApplicator.verify()` at B5 and merges the **actually-applied** quantised values back into the worker's config. This:

- gives the BO baseline correct surrogate-model gradients (no spurious flat regions),
- gives PBT honest lineage tracking for warm-start serialisation,
- ensures session JSON reflects what PostgreSQL is really running with.

See [CONFIGURATION_MANAGEMENT.md](configuration-management.md#verifying-applied-config) for the verify contract.

---

## System Monitoring

**Location**: [src/utils/metric_instrumentation.py](../../src/utils/metric_instrumentation.py)

System-level metrics (`memory_utilization`, `memory_pressure`, `cache_hit_ratio`, `buffer_miss_rate`, `scan_efficiency`, `io_read_mb`, `io_write_mb`) are collected from two sources:

1. **PostgreSQL itself** — `pg_stat_database`, `pg_stat_bgwriter`, `pg_stat_user_tables`. Captured at B6 (pre) and B10 (post) and deltaed.
2. **Process telemetry** — the PostgreSQL process RSS via `psutil`, normalised against `worker_memory_budget_bytes`. CPU stats are not part of the score (they vary too much with co-located workers); the budget-relative RSS is the stable memory signal.

The CPU subset isolation and worker memory budgets are configured by [`EnvironmentFactory`](../../src/utils/environments/factory.py) — see [ENVIRONMENT_BACKENDS.md](environment-backends.md).

---

## Workload Types and Optimization Goals

| Workload | Latency emphasis | Throughput emphasis | Memory regularisation | Notes |
| --- | --- | --- | --- | --- |
| **OLTP** (Sysbench) | `latency_p95` | TPS, high weight | medium | `feature_driven_v2` raises weights on `latency_variance` for `oltp_write_only` |
| **OLAP** (TPC-H) | `latency_p99`, `tail_amplification` | QphH, lower weight | high (large sorts) | scan efficiency + buffer miss rate matter |
| **MIXED** / template workloads | derived from workload features | derived | derived | features extracted from the workload JSON / SQL text |

There are **no** workload-specific scoring functions any more. A single `CompositeScorer` instance handles all three; what differs is the workload feature vector fed to `FeatureDrivenWeightModel`. Legacy sessions tagged with `fixed_v1` still resolve through the compatibility branch in [`policies.py`](../../src/utils/scoring/policies.py).

---

## Design Decisions

### 1. Single orchestrator, no per-workload subclasses

The orchestrator stays workload-agnostic. Workload-specific logic lives in [`BenchmarkExecutor`](../../src/benchmarks/executor.py) implementations ([`SysbenchExecutor`](../../src/benchmarks/sysbench/executor.py), [`TPCHExecutor`](../../src/benchmarks/tpch/executor.py), [`WorkloadExecutor`](../../src/tuner/benchmark/workload.py)). This keeps the scoring contract and the barrier sequence shared across benchmarks.

### 2. Score in `[0, 1]`, not `[0, 100]`

The previous `× 100.0` scaling factor existed only to make logs more readable. It now leaks into post-hoc analysis and BO cost transforms in confusing ways, so the score is kept in `[0, 1]` and any human-facing rounding happens at the display layer.

### 3. Read-back at B5

The verify-and-merge pattern means the configuration **stored in the session** is the configuration PostgreSQL is **actually running**, not the configuration the optimiser **suggested**. This is the only sound foundation for cross-session comparison.

### 4. Lockstep barriers around the measurement window

Without barriers, workers that finished restarting early would run their measurement window with less contention than workers still restarting. The barriers (see [GENERATION_BARRIERS.md](generation-barriers.md)) force every measurement window to overlap, eliminating that bias.

### 5. Reliability gate is multiplicative, not additive

A configuration that fails should not be ranked by accident on its non-failing dimensions. The gate `G ∈ [0, 1]` multiplies the weighted utility sum, so any unbounded `error_rate` or fatal `failure_type` collapses the score regardless of how good the latency/throughput numbers looked.

### 6. Single source of metric semantics

[`src/utils/scoring/constants.py`](../../src/utils/scoring/constants.py) holds the canonical metric IDs, directionality (higher-is-better vs lower-is-better), and version constants. Adding or renaming a metric is a one-place change.

---

## Related Documentation

### Scoring layer

- **[FEATURE_DRIVEN_SCORING.md](feature-driven-scoring.md)** — policies, weight model, normaliser, reliability gate.
- **[METRICS_VALIDATION.md](../reference/metrics-validation.md)** — academic validation of the multi-objective formulation.

### Surrounding components

- **[CONFIGURATION_MANAGEMENT.md](configuration-management.md)** — `KnobSpace`, `KnobApplicator`, `verify()`.
- **[PBT_CORE_COMPONENTS.md](pbt-core.md)** — how `Population` drives the orchestrator each generation.
- **[GENERATION_BARRIERS.md](generation-barriers.md)** — the B1–B17 lockstep barriers.
- **[WORKLOAD_ORCHESTRATOR.md](workload-orchestrator.md)** — orchestrator internals, restart policy, executor selection.
- **[ENVIRONMENT_BACKENDS.md](environment-backends.md)** — Docker vs bare-metal, CPU subsets, per-worker memory budgets.
- **[BENCHMARKING.md](../reference/benchmarking.md)** — dual-evaluation strategy (external C-binaries vs JSON templates).

### File locations

- `PerformanceMetrics`, `MetricConfig`, `WorkloadType`: [src/utils/metrics.py](../../src/utils/metrics.py)
- `WorkloadOrchestrator`, `WorkloadOrchestratorConfig`: [src/tuner/benchmark/orchestrator.py](../../src/tuner/benchmark/orchestrator.py)
- `WorkloadExecutor`, `WorkloadFileLoader`: [src/tuner/benchmark/workload.py](../../src/tuner/benchmark/workload.py)
- `should_restart`: [src/tuner/benchmark/restart_policy.py](../../src/tuner/benchmark/restart_policy.py)
- `CompositeScorer`, `ScoreBreakdown`: [src/utils/scoring/scorer.py](../../src/utils/scoring/scorer.py), [src/utils/scoring/contracts.py](../../src/utils/scoring/contracts.py)
- Metric instrumentation: [src/utils/metric_instrumentation.py](../../src/utils/metric_instrumentation.py)
- Tests: [tests/unit/core/](../../tests/unit/core/), [tests/unit/scoring/](../../tests/unit/scoring/), [tests/unit/utils/test_metric_instrumentation.py](../../tests/unit/utils/test_metric_instrumentation.py)
