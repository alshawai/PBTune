# ADR-003: Lockstep Generation Barriers for Measurement Fairness

- Status: Accepted (partially corrected — see [Addendum 2026-09-15](#addendum-2026-09-15-the-out-of-band-liveness-detector-was-documented-but-never-built))
- Date: 2026-05-30

## Context

Population-Based Training runs N workers in parallel on a single host. Each worker holds its own configuration and is evaluated against its own PostgreSQL instance, but workers share the host's CPU, memory, and disk bandwidth.

Without explicit synchronisation, the workers' critical paths diverge. One worker finishes `pg_reload_conf()` in milliseconds while another spends 45 seconds in a postmaster-context restart. One worker starts its measurement window when peers are still warming up; another finishes its measurement window before peers have even started. The score difference within a generation then conflates two distinct effects:

1. The genuine effect of the worker's knob configuration (which the optimiser must learn from).
2. The artefactual effect of when the worker happened to measure relative to the contention from peers (which has nothing to do with the configuration).

Effect 2 is asymmetric: workers that measured under lighter contention systematically score higher, regardless of their knobs. This is a confounder the PBT exploit/explore step would amplify — it would propagate "good" configurations that simply happened to land in light-contention windows.

We considered three responses:

- Accept the noise, evaluate sequentially. Defensible but it gives up the parallel speedup that is the main reason PBT scales.
- Run each worker on a dedicated host. Correct, but the project's research scope explicitly targets single-host evaluation.
- Synchronise the workers so their measurement windows overlap. This is what other empirical-evaluation frameworks do; we adopt the same pattern.

## Decision

Introduce a `GenerationBarrier` object that holds one `threading.Barrier` per sub-step of `WorkloadOrchestrator.evaluate_worker()`. Every worker thread calls `barrier.wait(name, worker_id)` at the end of each sub-step. The thread cannot advance until every worker in the generation has arrived. There are 17 sub-steps, labelled B1 through B17 (see [generation-barriers](../generation-barriers.md) for the full table).

Three secondary decisions follow:

1. **No per-barrier timeout.** Legitimate operations span seconds to many minutes (TPC-H Q21, dbgen data loads, postmaster restarts on slow disks). Any timeout small enough to detect a true hang false-positives on these.
2. **Two exception-driven graceful-degradation paths.** A worker that catches its own exception calls `drain_remaining(start_from, worker_id)` to release its remaining barrier slots so peers do not deadlock. If instead the exception propagates out of the worker to the population's `future.result()`, `Population.evaluate_generation()` calls `abort()` — the single PBT call site — which instantly breaks every barrier on every waiter. Both paths are driven by a *raised* exception: there is no background health-check thread and no `DatabaseEnvironment.is_alive()`.
3. **Sequential mode is `enabled=False`.** A no-op `GenerationBarrier` lets the same orchestrator body run under `--population 1` and in unit tests without branching on synchronisation.

## Consequences

Positive:

- Workers' measurement windows (B9) overlap by construction. The score difference within a generation reflects only the configuration difference, modulo run-to-run noise.
- The PBT exploit/explore step propagates real signal instead of scheduling artefacts.
- Parallel evaluation remains usable for publication-facing comparisons.
- The barrier protocol is auditable: `BARRIER_NAMES` is a hard-coded list and every call site uses one of those names.

Trade-offs:

- The slowest worker dictates the generation's wall-clock time at every barrier. Stragglers cost peers idle wait time.
- The barrier cannot itself tell "PostgreSQL is busy" from "PostgreSQL is dead," so liveness is surfaced *out of band* by the synchronous layers around it: benchmark-level bounds (TPC-H's failsafe `statement_timeout`, sysbench's subprocess `communicate(timeout=…)`, the bounded B15 `VACUUM ANALYZE`) and the environment lifecycle operations (`verify_instances`, `_wait_until_connectable`, `recover_instance` / `rebuild_worker_instance`, `connect_timeout` on connection attempts, Docker's SDK-level operation timeouts). Each turns a dead or unreachable instance into an *exception*, which then trips `drain_remaining` or `abort()`.
- A worker that fails by *raising* unblocks its peers immediately. A worker that hangs *without* raising — a blocking call that neither returns nor raises and is bounded by no timeout — is the residual case the barrier does not cover; it is a deliberately accepted trade-off, detailed in the [Addendum](#addendum-2026-09-15-the-out-of-band-liveness-detector-was-documented-but-never-built).

## Alternatives Considered

1. **Per-barrier timeout to bound hangs.**

   Rejected because any timeout short enough to detect hangs would false-positive on legitimately long queries, converting "everything is fine, just slow" into a broken-barrier event that loses the generation. Liveness is instead surfaced by the synchronous timeouts already present in the benchmark and environment layers (see *Consequences*), which raise on a dead instance without penalising a slow one.

2. **Coarser barriers (e.g. one barrier each before and after the measurement window).**

   Rejected because the divergence problem reappears between coarse barriers: a worker that finishes restart 20 seconds early starts warmup 20 seconds early and finishes B8 well before peers begin their warmup. Fine-grained barriers at every sub-step are the cheapest way to guarantee overlap.

3. **Process-level synchronisation via shared memory or a coordinator process.**

   Rejected because the orchestrator already runs all workers in one Python process via `ThreadPoolExecutor`. Adding inter-process coordination would multiply complexity for no functional benefit.

## Migration Notes

The barrier is opt-in: the orchestrator only calls `barriers.wait(...)` when a `GenerationBarrier` instance is passed in. Callers that want sequential evaluation pass a barrier object with `enabled=False`, which is a structural no-op. Existing tests that mock the orchestrator's body are unaffected.

The session JSON now records, per generation, the wall-clock duration of each barrier and whether the barrier was broken — this is what enabled the analysis showing measurement-window overlap is achieved in practice. See [generation-barriers](../generation-barriers.md) and [tests/unit/tuners/engine/test_barriers.py](../../../tests/unit/tuners/engine/test_barriers.py).

---

## Addendum (2026-09-15): the out-of-band liveness detector was documented but never built

The originally-accepted decision named an out-of-band liveness detector — a
`DatabaseEnvironment.is_alive()` method polled by a population-level health-check thread —
as the counterpart to rejecting per-barrier timeouts. **That detector was never built.** A
pickaxe over the whole history (`git log -S is_alive --all -- 'src'`) returns no source
commit that ever added it, and no health-check thread, watchdog, or liveness poller has
ever existed under `src/`; the references were documentation-only, describing an intended
mechanism as if it had shipped. They were removed from the companion docs, and this ADR was
given the present addendum, in **#138** (`6f7f4b8`, "docs: remove fictional PBT liveness and
lifecycle methods"). The rest of the ADR — the B1–B17 barrier set, the no-timeout rationale,
`drain_remaining`, and the `enabled=False` sequential mode — was accurate throughout and
remains current.

> **History note.** This addendum says *documented but never built* rather than *built and
> later removed as redundant*: the pickaxe shows no source commit ever added the detector,
> so what #138 removed was fictional documentation, not a working mechanism retired for
> redundancy. The `is_alive` string appears only in `docs/` history (introduced by the
> early architecture-doc commits, removed by `6f7f4b8`), never in `src/`.

What actually ships (all verified against current source):

- `barriers.abort()` has exactly one PBT call site: the `except` clause around
  `future.result()` in `Population.evaluate_generation()`
  ([src/tuners/pbt/population.py](../../../src/tuners/pbt/population.py)). It fires only
  when a worker's evaluation **raises**.
- `drain_remaining(start_from, worker_id)` is the graceful path for a worker that catches
  its own exception, contributing its missing arrivals so peers are not deadlocked.
- Liveness is surfaced synchronously by the layers *around* the barrier, never by a poller:
  benchmark-level bounds (TPC-H `statement_timeout`, sysbench subprocess `communicate`
  timeout, the bounded B15 `VACUUM ANALYZE`) and the environment lifecycle operations
  (`verify_instances`, `_wait_until_connectable`, `recover_instance` /
  `rebuild_worker_instance`, `connect_timeout`, Docker SDK operation timeouts). Each turns a
  dead or unreachable instance into an exception that then trips `drain_remaining` or
  `abort()`.

**What this does and does not cover.** Because both escape paths are exception-driven, the
design covers every failure that *raises* — crashes, refused or dropped connections, the
external benchmarks' own bounded query/subprocess timeouts, unresponsive containers. It does
**not** cover a genuinely silent hang: a synchronous call on a worker's main thread that
blocks forever while neither returning nor raising, bounded by no timeout — for example a
query on an already-established connection to a server wedged at the socket-read level, where
`connect_timeout` (which bounds only the initial handshake) does not apply and no
`statement_timeout` is set (the internal `WorkloadExecutor` sets none on its measurement
queries). Such a worker holds its peers at the next barrier indefinitely. This is a
**deliberately accepted residual**, not an oversight: a per-barrier timeout small enough to
catch it would false-positive on legitimately long operations and lose whole generations
(see *Alternatives*), so the no-timeout decision stands. Closing the residual would need a
genuine out-of-band liveness thread (calling `verify_instances()` on an interval and
aborting on confirmed death), which remains unimplemented.
