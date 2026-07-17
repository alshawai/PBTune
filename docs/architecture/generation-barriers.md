# Generation Barriers — Lockstep Worker Synchronization

See also: [Documentation Index](../README.md), [PBT Core Components](pbt-core.md), [Workload Orchestrator](workload-orchestrator.md), [Performance Evaluation](performance-evaluation.md), [ADR-003](decisions/ADR-003-lockstep-generation-barriers.md)

## Why barriers exist

When `N` workers evaluate in parallel on one host, their critical paths inevitably diverge: one finishes `pg_reload_conf()` quickly while another spins through a slow restart; one warms up its buffer cache from disk while another already has data resident. If the workers' **measurement windows** don't overlap, the late one measures under lighter contention than the early one — and the score difference reflects *scheduling artefacts*, not configuration quality.

The `GenerationBarrier` enforces **lockstep evaluation**: every worker waits at every sub-step until all workers have arrived. This guarantees that the measurement window (B9) of every worker overlaps the measurement window of every other worker in the same generation. The score difference between workers within a generation then reflects only their knob configurations.

This is the single piece of machinery that makes "8 workers in parallel on one host" methodologically defensible. Without it the parallel speedup would come at the cost of measurement bias.

---

## Table of Contents

1. [Barrier table (B1–B17)](#barrier-table-b1b17)
2. [API](#api)
3. [Graceful degradation](#graceful-degradation)
4. [Why no timeout](#why-no-timeout)
5. [Sequential mode](#sequential-mode)
6. [Integration with the orchestrator](#integration-with-the-orchestrator)
7. [Design decisions](#design-decisions)
8. [Related documentation](#related-documentation)

---

## Barrier table (B1–B17)

Every call into [`WorkloadOrchestrator.evaluate_worker()`](../../src/tuners/engine/orchestrator.py) passes through 17 ordered sub-steps. Each one ends with `barriers.wait(name, worker_id)` that blocks until all `N` workers in the generation have arrived.

| # | Name (`BARRIER_NAMES`) | Sub-step | What just completed |
| --- | --- | --- | --- |
| B1 | `connected` | Connect | TCP/psycopg2 connection established. |
| B2 | `config_applied` | Apply config | `ALTER SYSTEM SET …` + `pg_reload_conf()` for sighup knobs. |
| B3 | `restarted` | Restart (or skip) | Restart triggered by [`should_restart`](../../src/tuners/engine/restart_policy.py) finished. Workers that didn't need a restart pass through immediately and wait here for those that did. |
| B4 | `reconnected` | Reconnect | Post-restart re-connection succeeded. |
| B5 | `config_verified` | Verify | [`KnobApplicator.verify()`](../../src/utils/applicator.py) read-back; quantised values merged into `worker.knob_config`. |
| B6 | `pre_stats_captured` | Pre-stats snapshot | `pg_stat_database` / `pg_stat_bgwriter` / `pg_stat_user_tables` snapshot taken. |
| B7 | `benchmark_ready` | Benchmark prepare | Schema validated; data loaded if first run. |
| B8 | `warmup_done` | Warmup | Executor's warmup phase complete. |
| B9 | `measurement_done` | **Measurement** | Timed measurement window complete. **This is the only window whose metrics enter the score.** |
| B10 | `post_stats_captured` | Post-stats snapshot | Second `pg_stat_*` snapshot. |
| B11 | `io_computed` | I/O delta | I/O delta + buffer stats derived from pre/post snapshots. |
| B12 | `system_metrics_collected` | System metrics | Memory + cache + scan metrics via [`metric_instrumentation`](../../src/utils/metric_instrumentation.py). |
| B13 | `memory_pressure_computed` | Memory pressure | Derived memory-pressure signal computed. |
| B14 | `reliability_gated` | Reliability gate | Failure / degradation classification applied to the gate `G ∈ [0, 1]`. |
| B15 | `vacuum_done` | Vacuum analyze | Post-DML `VACUUM ANALYZE` (bounded by `vacuum_analyze_timeout_seconds`). |
| B16 | `score_computed` | Score | `CompositeScorer.score()` → `ScoreBreakdown`. |
| B17 | `disconnected` | Disconnect | Connection closed. |

The canonical list is the [`BARRIER_NAMES`](../../src/tuners/engine/barriers.py) constant. Any code that adds or removes a sub-step must update this list and the orchestrator's call sites together.

---

## API

**Location**: [src/tuners/engine/barriers.py](../../src/tuners/engine/barriers.py)

```python
class GenerationBarrier:
    def __init__(self, num_workers: int, enabled: bool = True) -> None: ...

    @property
    def enabled(self) -> bool: ...
    @property
    def broken(self) -> bool: ...

    def wait(self, name: str, worker_id: int) -> None: ...
    def drain_remaining(self, start_from: str, worker_id: int) -> None: ...
    def abort(self) -> None: ...
    def reset(self) -> None: ...
    def next_barrier_name(self, current: str) -> Optional[str]: ...
```

### `wait(name, worker_id)`

Blocks until all `num_workers` workers have called `wait()` with the same `name`. Logs the wait under the per-worker `WorkerBarrier` logger context. Raises `ValueError` if `name` is not in `BARRIER_NAMES`. Returns immediately if the barrier set is disabled or broken.

### `drain_remaining(start_from, worker_id)`

Releases all barriers from `start_from` onward (inclusive). Called by a worker's exception handler: when worker `k` fails at sub-step `j`, it calls `drain_remaining(name_at_j, k)` so it still "arrives" at every barrier from `j` onward, letting peers proceed instead of deadlocking at the next barrier.

Internally this is a loop of `barrier.wait()` calls — it does not break the barriers, only contributes this worker's missing arrival.

### `abort()`

Instantly breaks every barrier (raises `BrokenBarrierError` on all current and future waiters) and marks subsequent `wait()` calls as no-ops. Called from the population layer when a worker is confirmed dead/stuck (e.g. its `DatabaseEnvironment.is_alive()` returns `False`). Unlike `drain_remaining`, `abort()` does *not* try to honour the protocol — it tears down the whole synchronization mechanism for the generation.

### `reset()`

Recreates the barriers for the next generation. Called once per generation by [`Population.train_generation()`](../../src/tuners/pbt/population.py).

### `next_barrier_name(current)`

Returns the name of the barrier immediately after `current`, or `None` if `current` is the last barrier. Used by orchestrator error paths to construct the correct `start_from` argument for `drain_remaining()`.

---

## Graceful degradation

A live worker thread that crashes mid-evaluation must not deadlock the other threads at the next barrier. Three safeguards make this work.

```text
                       ┌──────────────────────────────────┐
                       │   Worker thread runs evaluation  │
                       └──────────────┬───────────────────┘
                                      │
                    ┌─────────────────┼─────────────────┐
                    │                 │                 │
              succeeded            crashed         truly hung
                    │                 │                 │
                    ▼                 ▼                 ▼
            wait() through      drain_remaining     population
            B1..B17 normally    (start_from = next  layer detects
                                barrier after the   via env health
                                failed sub-step)    check, calls
                                                    abort()
                    │                 │                 │
                    └─────────────────┼─────────────────┘
                                      ▼
                       Peers proceed past every barrier
                       (either through real arrivals
                       or through drained / aborted ones).
```

### Path 1 — clean completion

Every worker calls `wait("connected", id)` … `wait("disconnected", id)` in order. The orchestrator's normal control flow handles this.

### Path 2 — caught exception (`drain_remaining`)

The orchestrator wraps each sub-step in a `try/except`. If, say, sub-step B7 raises:

```python
except Exception:
    barriers.drain_remaining("benchmark_ready", worker_id)
    raise
```

Now this worker has "arrived" at B7, B8, B9, …, B17 from the barrier's point of view. Peers can finish their generation; the failing worker propagates the exception up to the population layer, which logs it and lets `rescue_dead_workers()` handle the recovery on the next generation boundary.

### Path 3 — true hang (`abort`)

If a worker thread is stuck (e.g. PostgreSQL is unresponsive and even the failure path doesn't return), peers will block forever at the next barrier. The population layer's health checker — `DatabaseEnvironment.is_alive(worker_id)` — runs on its own thread; when it confirms a worker is dead, the population calls `barriers.abort()`. Every waiter immediately receives `BrokenBarrierError`, the orchestrator catches it, every per-worker thread exits its evaluation, and the generation ends with a `rescue_dead_workers()` call.

---

## Why no timeout

`threading.Barrier` accepts a per-wait `timeout` argument. We deliberately don't use it.

**The problem.** A timeout that's short enough to detect a true hang (e.g. 60 s) will fire spuriously on legitimately slow operations:

- A TPC-H Q21 on a small-RAM container can take 5–10 minutes.
- A first-time `dbgen` data load can take many minutes.
- A `restart_instance()` after a `postmaster` knob change can take 30–60 s on its own; coupled with a slow disk, the B3 barrier might legitimately need to wait several minutes.

Any timeout small enough to be useful for hangs is small enough to false-positive on these. False positives convert "everything is fine, just slow" into a broken-barrier event that triggers `rescue_dead_workers()` for *all* workers — losing the generation.

**The solution.** Wait indefinitely; detect liveness through a different channel (env health), which knows the difference between "PostgreSQL is busy" and "PostgreSQL is dead." When the health channel confirms death, call `abort()`. The barriers themselves never time out.

The trade-off — a truly hung evaluation could in principle hold the generation forever — is bounded by the env-level health check, which doesn't depend on the barriers' progress.

---

## Sequential mode

`GenerationBarrier(num_workers=N, enabled=False)` produces a no-op barrier object. `wait()` returns immediately; `drain_remaining()` and `abort()` are also no-ops. This is used in:

- single-worker debugging (`--population 1` plus `--no-sync`),
- unit tests that exercise the orchestrator's body without setting up real concurrency,
- the BO baseline's parallel path, which manages its own ask-tell synchronization and doesn't need lockstep barriers.

Code that consumes the barrier object never has to branch on the enabled flag — the no-op API is identical.

---

## Integration with the orchestrator

```text
Population.train_generation()
  │
  ├─► barriers.reset()                                # fresh barriers for this generation
  │
  ├─► ThreadPoolExecutor(max_workers=N)
  │     │
  │     │ for each worker:
  │     │   orchestrator.evaluate_worker(worker)
  │     │     │
  │     │     ├─► connect()
  │     │     ├─► barriers.wait("connected", worker_id)       # B1
  │     │     ├─► apply_config()
  │     │     ├─► barriers.wait("config_applied", worker_id)  # B2
  │     │     ├─► maybe_restart()
  │     │     ├─► barriers.wait("restarted", worker_id)       # B3
  │     │     │   ... (B4–B17) ...
  │     │     └─► return (metrics, score_breakdown)
  │     │
  │     │ on exception in orchestrator:
  │     │   barriers.drain_remaining(failed_sub_step, worker_id)
  │     │   re-raise
  │     │
  │     │ population's health-check thread (separate):
  │     │   if any environment.is_alive() == False:
  │     │     barriers.abort()
  │
  ├─► exploit_and_explore()
  └─► record_generation() / rescue_dead_workers() / should_stop()
```

The orchestrator owns the `try/except` + `drain_remaining` plumbing; the population owns `reset`, `abort`, and the health-check thread. The barrier object itself owns no state beyond the `threading.Barrier` instances and a single `_broken` flag.

---

## Design decisions

### 1. One barrier per sub-step, not one per phase

We could coarsen to 3–4 barriers (e.g. `apply_done`, `measurement_done`, `score_done`). We don't, because the **measurement-window overlap guarantee** is what matters scientifically, and that requires sub-step-level alignment around B7–B10. The B1–B17 granularity is a cheap-to-pay generalisation of that need to the whole evaluation.

### 2. Fixed barrier names, not dynamic

`BARRIER_NAMES` is a hard-coded list. Adding a barrier means editing both the list and the call sites. This makes the protocol auditable: the doc table above is enforced by the type system, not a convention. A dynamic registry would risk silent name drift between orchestrator and barrier object.

### 3. No timeout

See [Why no timeout](#why-no-timeout). The cost of a false positive (lost generation) is high; the cost of a true hang (bounded by the env health check) is low.

### 4. Graceful degradation has two paths, not one

`drain_remaining` and `abort` are distinct because they describe different failures. `drain_remaining` is for "this worker is failing cleanly and propagating an exception" — peers still trust the protocol. `abort` is for "the protocol is broken; tear it down" — peers no longer trust the protocol. Coalescing them would lose that distinction and prevent the orchestrator from cleanly logging which workers failed for which reasons.

### 5. Barrier object owns minimal state

The barrier object only knows about `num_workers`, `BARRIER_NAMES`, and a `_broken` flag. It does not know about generations, workers, or scoring. Population owns the lifecycle (`reset`, `abort`); orchestrator owns the call sites (`wait`, `drain_remaining`). This keeps the file small and testable.

---

## Related documentation

- **[PBT Core Components](pbt-core.md)** — how the population drives the barriers each generation.
- **[Workload Orchestrator](workload-orchestrator.md)** — the orchestrator's body and where each `wait()` call sits.
- **[Performance Evaluation](performance-evaluation.md)** — the measurement window the barriers protect.
- **[Environment Backends](environment-backends.md)** — the `is_alive()` health-check that informs `abort()`.
- **[ADR-003 — Lockstep generation barriers](decisions/ADR-003-lockstep-generation-barriers.md)** — the design decision record.

### File locations

- `GenerationBarrier`, `BARRIER_NAMES`: [src/tuners/engine/barriers.py](../../src/tuners/engine/barriers.py)
- Orchestrator call sites: [src/tuners/engine/orchestrator.py](../../src/tuners/engine/orchestrator.py)
- Tests: [tests/unit/tuners/engine/test_barriers.py](../../tests/unit/tuners/engine/test_barriers.py), [tests/unit/tuners/pbt/test_dead_rescue_convergence_and_restart.py](../../tests/unit/tuners/pbt/test_dead_rescue_convergence_and_restart.py)
