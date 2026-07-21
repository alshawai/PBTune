# Workload Orchestrator


See also: [Documentation Index](../README.md), [Performance Evaluation](performance-evaluation.md), [Generation Barriers](generation-barriers.md), [Environment Backends](environment-backends.md), [Configuration Management](configuration-management.md), [Benchmarking](../reference/benchmarking.md)

## Overview

The `WorkloadOrchestrator` is the per-worker evaluation engine. Given a `Worker`, it applies the worker's configuration to PostgreSQL, executes the configured benchmark, captures metrics, and returns a `(PerformanceMetrics, ScoreBreakdown)` pair. It replaces the previous `Evaluator` class — every reference to the old evaluator module in older docs/code is obsolete; the component now lives at `src/tuners/engine/orchestrator.py`.

The orchestrator package lives at [src/tuners/engine/](../../src/tuners/engine/) and consists of three focused modules:

- **[orchestrator.py](../../src/tuners/engine/orchestrator.py)** — `WorkloadOrchestrator`, `WorkloadOrchestratorConfig`. Drives the B1–B17 lockstep flow.
- **[restart_policy.py](../../src/tuners/engine/restart_policy.py)** — Pure decision function `should_restart()`. The CDBTune-inspired adaptive batching lives here.
- **[workload.py](../../src/benchmarks/workload.py)** — `WorkloadExecutor` for SQL-template workloads + `WorkloadFileLoader` for JSON/YAML workload files.

This split — *policy* vs. *mechanism* vs. *workload-specific execution* — is what lets the orchestrator stay benchmark-agnostic while the same plumbing handles Sysbench, TPC-H, and arbitrary user workloads.

---

## Table of Contents

1. [Where it sits](#where-it-sits)
2. [`WorkloadOrchestratorConfig`](#workloadorchestratorconfig)
3. [The `evaluate_worker` flow](#the-evaluate_worker-flow)
4. [Restart policy and tuning modes](#restart-policy-and-tuning-modes)
5. [Workload executor selection](#workload-executor-selection)
6. [Template workloads](#template-workloads)
7. [Failure handling](#failure-handling)
8. [Design decisions](#design-decisions)
9. [Related documentation](#related-documentation)

---

## Where it sits

```text
                Population.evaluate_generation()
                              │
                              │ ThreadPoolExecutor(max_workers=N)
                              ▼
                   per-worker thread:
                      ┌────────────────────────────────┐
                      │     WorkloadOrchestrator       │
                      │   .evaluate_worker(worker)     │
                      └────────────────┬───────────────┘
                                       │
            ┌──────────────────────────┼──────────────────────────┐
            │                          │                          │
            ▼                          ▼                          ▼
    ┌──────────────┐         ┌──────────────────┐        ┌────────────────┐
    │KnobApplicator│         │ DatabaseEnvironment│      │ BenchmarkExec  │
    │   apply +    │         │  start / stop /    │      │  (Sysbench /   │
    │   verify     │         │  restart / restore │      │   TPC-H /      │
    │              │         │                    │      │   Workload-    │
    └──────┬───────┘         └─────────┬──────────┘      │   Executor)    │
           │                           │                 └────────┬───────┘
           │                           │                          │
           └───────────────────────────┼──────────────────────────┘
                                       ▼
                            ┌────────────────────────┐
                            │  PerformanceMetrics    │
                            └──────────┬─────────────┘
                                       ▼
                            ┌────────────────────────┐
                            │   create_scoring_      │
                            │   engine() (lazy,      │
                            │   thread-safe)         │
                            │   → CompositeScorer    │
                            └──────────┬─────────────┘
                                       ▼
                            ┌────────────────────────┐
                            │     ScoreBreakdown     │
                            └────────────────────────┘
```

The orchestrator is constructed once per session by [`src/tuners/pbt/cli.py`](../../src/tuners/pbt/cli.py) and passed into the population. Every worker thread shares the same orchestrator instance — its scoring engine is built lazily under a lock so multiple threads don't race during the first call.

---

## `WorkloadOrchestratorConfig`

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

Field-by-field:

| Field | Purpose |
| --- | --- |
| `workload_type` | `OLTP` / `OLAP` / `MIXED`. Drives default metric weights and the workload-feature extractor. |
| `metric_config` | The active scoring policy and floors. See [FEATURE_DRIVEN_SCORING.md](feature-driven-scoring.md). |
| `db_config` | Base PostgreSQL credentials. The environment substitutes `host` / `port` per worker. |
| `warmup_duration` | Seconds spent in B8 warmup before measurement. Default 30. |
| `measurement_duration` | Seconds in the timed measurement window (B9). The only window that contributes to the score. Default 60. |
| `cooldown_duration` | Quiescence period after `apply_config` and before warmup. Default 5. |
| `tuning_mode` | `ONLINE` / `OFFLINE` / `ADAPTIVE` — see [Restart policy](#restart-policy-and-tuning-modes). |
| `adaptive_restart_interval` | When `tuning_mode=ADAPTIVE`, restart every N generations even if no postmaster knob changed. |
| `random_seed` | Propagated to the workload executor's RNG for deterministic query selection. |
| `warmup_passes` | TPC-H specific — number of warm passes through the 22-query power test before B9. |
| `vacuum_analyze_timeout_seconds` | Bound on the post-measurement `VACUUM ANALYZE` (B15) so a stuck maintenance pass cannot stall a generation. |
| `worker_memory_budget_bytes` | Per-worker RAM slice used to normalise PostgreSQL RSS into `memory_utilization ∈ [0, 1]`. Falls back to total host RAM when `None`. |

The orchestrator owns the runtime parameters; *what* to run (Sysbench vs TPC-H vs custom SQL) is decided by the `workload_executor` argument to `__init__`.

---

## The `evaluate_worker` flow

Public entry point:

```python
metrics, score_breakdown = orchestrator.evaluate_worker(worker)
```

The body is a sequence of 17 sub-steps, each ending with `barriers.wait(name, worker_id)` (see [GENERATION_BARRIERS.md](generation-barriers.md) for the full table). Annotated:

```text
B1  connect()                            # psycopg2 connection, retry on "starting up" / "recovering"
B2  applicator.apply(worker.knob_config) # ALTER SYSTEM SET … + pg_reload_conf if sighup
B3  if should_restart(...) → environment.restart_instance(worker_id)
B4  reconnect()                          # post-restart re-connection
B5  applicator.verify(...)               # read-back; merge db_config into worker.knob_config
B6  capture pg_stat_* baseline           # pg_stat_database, pg_stat_bgwriter, pg_stat_user_tables
B7  workload_executor.validate(...)      # ensure schema is correct; prepare() if not
B8  workload_executor.execute(warmup=True, duration=warmup_duration)
B9  workload_executor.execute(warmup=False, duration=measurement_duration)
B10 capture pg_stat_* final
B11 io = (post - pre)                    # delta-derived I/O + buffer stats
B12 system metrics                        # memory_utilization, cache_hit_ratio, scan_efficiency, …
B13 memory_pressure derivation
B14 reliability gate G                    # failure classification → G ∈ [0, 1]
B15 VACUUM ANALYZE                        # bounded by vacuum_analyze_timeout_seconds
B16 scorer.score(metrics, features)       # → ScoreBreakdown
B17 disconnect()
```

Two non-obvious details:

### Connection retries

`connect()` retries on errors that match `"starting up"`, `"not yet accepting connections"`, `"consistent recovery state"`, or `"connection refused … is the server running"`. These can legitimately appear right after `restart_instance()` while the postmaster initialises. Hard errors (auth, not-found, schema mismatches) propagate immediately.

### Read-back at B5

`applicator.verify()` runs `current_setting(name)` for each knob in `worker.knob_config` and returns the **actually quantised** values PostgreSQL is using. The orchestrator merges these back into `worker.knob_config` so the session JSON reflects what the database is really running with — see [CONFIGURATION_MANAGEMENT.md §Verifying applied config](configuration-management.md#verifying-applied-config). This step is cheap and is what makes BO surrogate-model gradients honest in the BO baseline that uses the same orchestrator.

---

## Restart policy and tuning modes

**Location**: [src/tuners/engine/restart_policy.py](../../src/tuners/engine/restart_policy.py)

```python
def should_restart(
    mode: TuningMode,
    restart_required: bool,
    generation: int | None,
    adaptive_restart_interval: int = 10,
    force: bool = False,
) -> bool:
    if force:
        return True
    if not restart_required:
        return False
    if mode == TuningMode.ONLINE:
        return False
    if mode == TuningMode.OFFLINE:
        return True
    if mode == TuningMode.ADAPTIVE:
        return generation is not None and generation % adaptive_restart_interval == 0
    return False
```

The function is a pure decision — easy to unit-test in isolation and reused by the BO baseline objective. The three modes:

| Mode | Behaviour | When to use |
| --- | --- | --- |
| **`ONLINE`** | Never restart. Postmaster-context knobs are persisted via `ALTER SYSTEM SET` but their effect waits until a manual restart. | Production-style tuning where downtime is forbidden. The score reflects only the runtime-modifiable knobs' effect. |
| **`OFFLINE`** | Restart whenever any postmaster knob changed. | Academic offline tuning — equivalent to a batch tuner that takes its time. The score reflects the full configuration. |
| **`ADAPTIVE`** | Restart only when `generation % adaptive_restart_interval == 0`. CDBTune-inspired batching. | Default for PBT runs — amortises restart cost while still reflecting postmaster knobs every N generations. |

`force=True` is used by post-recovery paths (after `recover_instance` or `rebuild_worker_instance`) to ensure the recovered process is in a known-clean state.

`restart_required` is signalled by the previous `apply()` call's `ApplicationResult.restart_required` set — non-empty means the apply touched at least one postmaster-context knob.

---

## Workload executor selection

The orchestrator accepts any object that satisfies a small contract — `prepare(db_config)`, `validate(db_config)`, `execute(db_config, duration, warmup) -> PerformanceMetrics`. Three implementations live in the codebase:

| Executor | Where | Workloads | Notes |
| --- | --- | --- | --- |
| **`SysbenchExecutor`** | [src/benchmarks/sysbench/executor.py](../../src/benchmarks/sysbench/executor.py) | `oltp_read_only`, `oltp_read_write`, `oltp_write_only` | Wraps the `sysbench` C-binary (1.1.0+). The score's TPS/latency metrics come from sysbench's own output. |
| **`TPCHExecutor`** | [src/benchmarks/tpch/executor.py](../../src/benchmarks/tpch/executor.py) | TPC-H 22-query power test | Uses `dbgen` for data generation, `psycopg2.copy_expert()` for bulk load, raw psycopg2 for query execution. Scale factor configurable. |
| **`WorkloadExecutor`** | [src/benchmarks/workload.py](../../src/benchmarks/workload.py) | Custom JSON/YAML templates | Pure Python multi-threaded SQL execution. Used for OLTP/OLAP/MIXED templates and arbitrary user workloads. |

Selection is decided in [`src/tuners/pbt/cli.py`](../../src/tuners/pbt/cli.py) based on CLI flags:

```text
--benchmark sysbench    → SysbenchExecutor      (CLI: --sysbench-workload, --sysbench-tables, etc.)
--benchmark tpch        → TPCHExecutor          (CLI: --scale-factor, --tpch-warmup-passes)
--workload-file PATH    → WorkloadExecutor      (loaded via WorkloadFileLoader)
(default)               → built-in OLTP / OLAP / MIXED template via WorkloadExecutor
```

The full strategy comparison — when to use external C-binaries vs internal templates and why — is in [BENCHMARKING.md](../reference/benchmarking.md).

---

## Template workloads

**Location**: [src/benchmarks/workload.py](../../src/benchmarks/workload.py)

`WorkloadExecutor` is the engine behind both the built-in OLTP/OLAP/MIXED templates and any user-supplied JSON/YAML workload file.

### Workload file format

```json
{
  "name": "Production Trace",
  "schema": {
    "tables": 10,
    "table_size": 100000
  },
  "queries": [
    { "sql": "SELECT * FROM {table} WHERE id = {id}", "weight": 0.4 },
    { "sql": "SELECT COUNT(*) FROM {table} WHERE k = {k_val}", "weight": 0.6 }
  ]
}
```

The `schema` block is optional; without it the executor logs a warning and defaults to 10 tables × 100K rows. The `weights` are normalised to sum to 1; the `queries` list can use any of the supported placeholders:

| Placeholder | Resolved to |
| --- | --- |
| `{id}` | Random integer in `[1, table_size]` |
| `{k_val}` | Random integer in `[1, table_size]` |
| `{threshold}` | Random integer in `[1, 10000]` |
| `{table}` | Random table name from `sbtest1…sbtest{num_tables}` |
| `{table2}` | Different random table for cross-table joins |

`WorkloadFileLoader.load_from_file(path)` returns a fully constructed `WorkloadExecutor`; `extract_workload_template_metadata(path)` returns `TemplateWorkloadMetadata` used by the workload-feature extractor (see [FEATURE_DRIVEN_SCORING.md](feature-driven-scoring.md)).

### Concurrent execution

`execute()` dispatches to either `_execute_concurrent` (default) or `_execute_sequential` (debugging only). The concurrent path uses a fixed-size `ThreadPoolExecutor` of `num_threads` workers, each pulling random-instantiated queries from the queue until the duration elapses. Latency percentiles are computed from the per-query timings collected across all threads; throughput is total completed queries divided by elapsed wall time.

### Real-database workloads

For tuning against a real production replica (see [BENCHMARKING.md §Tuning Against a Real Database Snapshot](../reference/benchmarking.md#tuning-against-a-real-database-snapshot)), the workload file omits both the `schema` block and the placeholders — `WorkloadExecutor` natively supports raw unparameterised SQL. The orchestrator's apply / measure / score pipeline is unchanged; the schema is whatever `pg_basebackup` cloned from the source database.

---

## Failure handling

Three classes of failure can interrupt an evaluation. The orchestrator handles each differently.

### 1. PostgreSQL apply / restart errors

Caught at B2–B4. The orchestrator marks the worker's metrics with a `failure_type` (`"apply_failed"`, `"restart_failed"`, `"reconnect_failed"`), drains the remaining barriers via [`barriers.drain_remaining`](generation-barriers.md#drain_remainingstart_from-worker_id), and propagates the exception. The reliability gate `G` collapses to 0, the population's score-finalisation logic records the failure in session JSON, and `rescue_dead_workers()` may pick up the worker on the next generation boundary.

### 2. Workload execution errors

Caught at B7–B9. Same pattern: tag `failure_type`, drain remaining barriers, propagate. The metrics record whatever was captured (e.g. partial throughput before a crash) but `G = 0` makes them score-irrelevant.

### 3. Dead-instance detection

The population's separate health-check thread polls `environment.is_alive(worker_id)`. If a worker's PostgreSQL is unresponsive, the population calls [`barriers.abort()`](generation-barriers.md#abort) — the running orchestrator's next `wait()` raises `BrokenBarrierError`, every per-worker thread exits cleanly, and the recovery ladder runs (`recover_instance` → `rebuild_worker_instance` → dead-worker rescue).

The orchestrator also surfaces the read-back values from `applicator.verify()` to the population layer regardless of whether the evaluation completed. This lets the BO baseline correctly attribute the actually-quantised configuration even to runs that crashed mid-measurement.

---

## Design decisions

### 1. Policy/mechanism split via `restart_policy.py`

`should_restart` is a pure function with no I/O. Two payoffs: the BO baseline reuses it without instantiating an orchestrator, and unit tests cover all four code paths in `should_restart` without standing up PostgreSQL.

### 2. Workload-agnostic orchestrator

Workload-specific logic lives in `BenchmarkExecutor` implementations and `WorkloadExecutor`. The orchestrator's body never branches on workload type — it calls `prepare`, `validate`, `execute`. Adding a new benchmark means writing a new executor; the orchestrator is unchanged.

### 3. Lazy thread-safe scoring engine

`create_scoring_engine` is built once on first `evaluate_worker` call, under a lock. Every per-worker thread then shares the engine. Eager construction in `__init__` would force the metric-config to be valid before any environment setup — sometimes inconvenient.

### 4. Verify-and-merge at B5, not at session end

Merging quantised values back into `worker.knob_config` immediately means lineage tracking, exploit cloning, and BO surrogate gradients all see the same canonical configuration. A session-end merge would lose precision for any failure path that exits before the merge runs.

### 5. Bounded `VACUUM ANALYZE`

`vacuum_analyze_timeout_seconds` exists because a poorly-tuned configuration can make autovacuum throughput drop into the single digits, producing a multi-minute B15 stall. The bound is generous (45 s default) but firm — the alternative is generations that finish in 60 s but contain 30 minutes of post-measurement maintenance.

### 6. Connection retry logic at the orchestrator level

The retry loop knows about PostgreSQL-specific recovery messages (`"starting up"`, `"consistent recovery state"`). Pushing this into `KnobApplicator` would couple it to recovery semantics that aren't its concern; pushing it into `DatabaseEnvironment` would mix Postgres-specific text matching into a backend-agnostic interface. The orchestrator is the right place.

---

## Related documentation

- **[Performance Evaluation](performance-evaluation.md)** — `PerformanceMetrics`, `MetricConfig`, scoring integration.
- **[Generation Barriers](generation-barriers.md)** — the B1–B17 lockstep mechanism.
- **[Environment Backends](environment-backends.md)** — Docker / bare-metal lifecycle.
- **[Configuration Management](configuration-management.md)** — `KnobApplicator.apply` / `verify`.
- **[Feature-Driven Scoring](feature-driven-scoring.md)** — the scoring engine constructed at B16.
- **[Benchmarking](../reference/benchmarking.md)** — dual-evaluation strategy and SchemaProvider protocol.
- **[BO Baseline](../guides/bo-baseline.md)** — uses the same orchestrator with a different driver.

### File locations

- `WorkloadOrchestrator`, `WorkloadOrchestratorConfig`: [src/tuners/engine/orchestrator.py](../../src/tuners/engine/orchestrator.py)
- `should_restart`: [src/tuners/engine/restart_policy.py](../../src/tuners/engine/restart_policy.py)
- `WorkloadExecutor`, `WorkloadFileLoader`: [src/benchmarks/workload.py](../../src/benchmarks/workload.py)
- `TuningMode`: [src/utils/types.py](../../src/utils/types.py)
- Tests: [tests/unit/tuners/engine/test_restart_policy.py](../../tests/unit/tuners/engine/test_restart_policy.py), [tests/unit/tuners/engine/test_evaluator_fault_injection.py](../../tests/unit/tuners/engine/test_evaluator_fault_injection.py), [tests/unit/tuners/engine/test_evaluator_memory_normalization.py](../../tests/unit/tuners/engine/test_evaluator_memory_normalization.py)
