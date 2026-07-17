---
name: benchmark-orchestration
description: >
  Sysbench OLTP and TPC-H OLAP benchmark execution patterns, multi-instance PostgreSQL
  management, snapshot management, the full WorkloadOrchestrator pipeline, and performance
  measurement workflows. Use this skill when working on benchmark executors, evaluation
  pipeline, instance management, snapshot restoration, configuration application, restart
  policy, system metrics collection, or any code in src/benchmarks/, src/tuners/engine/,
  src/utils/applicator.py, or src/utils/environments/.
---

# Benchmark Orchestration Patterns

## Evaluation Pipeline

The full evaluation of a single worker follows this pipeline:

```
evaluate_worker(worker)
    ├── apply_configuration(worker.knob_config)
    │   ├── Separate knobs by context (postmaster vs sighup)
    │   ├── Write ALL knobs to postgresql.conf
    │   ├── If postmaster knobs changed → restart via environment backend
    │   ├── Else if sighup knobs changed → pg_ctl reload
    │   └── _verify_configuration() via SELECT current_setting()
    ├── _ensure_benchmark_ready()
    │   └── Check tables exist; restore snapshot if needed
    ├── _vacuum_after_dml()
    │   └── VACUUM ANALYZE ensures clean statistics after DML warmup
    ├── executor.run_benchmark()
    │   └── SysbenchExecutor.run() or TPCHExecutor.run()
    ├── collect_system_metrics()
    │   └── psutil: CPU%, memory%, I/O read/write MB
    └── Return (PerformanceMetrics, score)
```

All of this is orchestrated by `WorkloadOrchestrator` in `src/tuners/engine/orchestrator.py`.
Per-worker timing is recorded via a `TimingRecorder` local to `evaluate_worker` and attached
as `worker.last_eval_timing`.

## Sysbench OLTP

- **CLI pattern:** `prepare → run (--warmup=N) → cleanup`
- **Implementation:** Calls native `sysbench` binary via `subprocess.run()`
- **Output parsing:** Regex extraction for TPS, p95 latency, error rate
- **Output partitioning:** Results split by workload mode under
  `results/oltp/{sysbench_workload}/...`
- **Location:** `src/benchmarks/sysbench/`

## TPC-H OLAP

- **Power Test only:** Single-stream, all 22 queries sequentially
- **Metric:** `Power@Size = geometric_mean(query_times)`
- **Design choice:** No Throughput Test (consistent with OtterTune, CDBTune papers)
- **Data generation:** `dbgen` → COPY into PostgreSQL tables
- **Statement timeout:** Scales dynamically with `scale_factor` to prevent hangs
- **Location:** `src/benchmarks/tpch/`

## Multi-Instance PostgreSQL Management

Each PBT worker gets a dedicated PostgreSQL instance to enable true parallel evaluation:

| Component | Detail |
|-----------|--------|
| **Port scheme** | `base_port + worker_id` (default base: 5440) |
| **Data dirs** | `{pg_data_base}/worker_{worker_id}/` |
| **Backends** | `DockerEnvironment` (with CPU subset isolation, ADR-004) or `BareMetalEnvironment` |
| **Creation** | `initdb → configure postgresql.conf → pg_ctl start` |
| **Auto-detect** | Finds `pg_ctl`/`initdb` via PATH or common install dirs |
| **Reuse** | Reuses existing data dirs if already initialized |
| **Resource slicing** | Manual override via `--worker-ram` / `--worker-cpus` |
| **Location** | `src/utils/environments/` |

## Snapshot Management

Prevents data drift between generations (sysbench DML modifies tables):

| Strategy | Method | When Used |
|----------|--------|-----------|
| **rsync** (preferred) | `rsync -a --delete` | When rsync is available |
| **shutil** (fallback) | `shutil.copytree()` | When rsync unavailable |

- **Interval-based restore:** Configurable via `snapshot_restore_interval`
- **Backend-managed:** Snapshots live alongside the worker data directory managed by the
  `DatabaseEnvironment` backend.

## Configuration Application

The `KnobApplicator` (`src/utils/applicator.py`) handles:

1. **Context-aware application:** Separates postmaster vs sighup knobs
2. **Restart minimization:** Only restarts when postmaster values actually differ
3. **Verification:** Confirms each knob via `SELECT current_setting()`
4. **Fraction resolution:** Converts hardware fractions to absolute values

## Restart Policy

`src/tuners/engine/restart_policy.py` exposes the restart policy with three tuning modes:

| `TuningMode` | Behavior |
|--------------|----------|
| `ONLINE` | Minimize restarts; favor reload-only changes |
| `OFFLINE` | Restart freely between evaluations (default) |
| `ADAPTIVE` | Decide per-cycle based on which knobs actually changed |

Selected via `--tuning-mode {online,offline,adaptive}` on the tuner CLI. The legacy
`RestartCostModel` was archived to `prototypes/restart_cost_model/`.

## System Metrics Collection

Collected during benchmark execution via `psutil`:
- CPU utilization (average during run)
- Memory utilization (fraction)
- Disk I/O (read/write MB)
- Stored in `PerformanceMetrics` dataclass

## Error Handling

| Scenario | Response | Score Impact |
|----------|----------|-------------|
| PostgreSQL crash during eval | `failure_type = "pg_crash"` | Score = 0.0 |
| Benchmark timeout | `failure_type = "benchmark_timeout"` | Score = 0.0 |
| Output parse failure | `failure_type = "output_parse_error"` | Score = 0.0 |
| Statement timeout (TPC-H) | Query marked as timed out | Partial scoring |
| Dead worker (score = 0.0) | `rescue_dead_workers()` resamples | Next gen gets new config |

Dead worker penalty: Any worker with `failure_type is not None` gets score 0.0,
preventing failed configs from contaminating normalization ranges.

## Reference Files
- Read `references/execution-patterns.md` for sysbench CLI patterns, TPC-H query flow, and WorkloadOrchestrator step-by-step
