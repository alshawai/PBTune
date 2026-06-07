# ADR-003: Lockstep Generation Barriers for Measurement Fairness

- Status: Accepted
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
2. **Two graceful-degradation paths.** A worker that catches a clean exception calls `drain_remaining(start_from, worker_id)` to release its remaining barrier slots so peers do not deadlock. A worker confirmed dead by the population's health-check thread triggers `abort()`, which instantly breaks every barrier on every waiter.
3. **Sequential mode is `enabled=False`.** A no-op `GenerationBarrier` lets the same orchestrator body run under `--population 1` and in unit tests without branching on synchronisation.

## Consequences

Positive:

- Workers' measurement windows (B9) overlap by construction. The score difference within a generation reflects only the configuration difference, modulo run-to-run noise.
- The PBT exploit/explore step propagates real signal instead of scheduling artefacts.
- Parallel evaluation remains usable for publication-facing comparisons.
- The barrier protocol is auditable: `BARRIER_NAMES` is a hard-coded list and every call site uses one of those names.

Trade-offs:

- The slowest worker dictates the generation's wall-clock time at every barrier. Stragglers cost peers idle wait time.
- The orchestrator and population layers must cooperate on liveness detection (via `DatabaseEnvironment.is_alive`) because the barrier itself cannot distinguish "PostgreSQL is busy" from "PostgreSQL is dead."
- A truly hung worker holds the generation until the health-check thread calls `abort()`. The bound on hang time is the health-check interval, not the barrier itself.

## Alternatives Considered

1. **Per-barrier timeout instead of out-of-band liveness detection.**

   Rejected because any timeout short enough to detect hangs would false-positive on legitimately long queries, converting "everything is fine, just slow" into a broken-barrier event that loses the generation.

2. **Coarser barriers (e.g. one barrier each before and after the measurement window).**

   Rejected because the divergence problem reappears between coarse barriers: a worker that finishes restart 20 seconds early starts warmup 20 seconds early and finishes B8 well before peers begin their warmup. Fine-grained barriers at every sub-step are the cheapest way to guarantee overlap.

3. **Process-level synchronisation via shared memory or a coordinator process.**

   Rejected because the orchestrator already runs all workers in one Python process via `ThreadPoolExecutor`. Adding inter-process coordination would multiply complexity for no functional benefit.

## Migration Notes

The barrier is opt-in: the orchestrator only calls `barriers.wait(...)` when a `GenerationBarrier` instance is passed in. Callers that want sequential evaluation pass a barrier object with `enabled=False`, which is a structural no-op. Existing tests that mock the orchestrator's body are unaffected.

The session JSON now records, per generation, the wall-clock duration of each barrier and whether the barrier was broken — this is what enabled the analysis showing measurement-window overlap is achieved in practice. See [generation-barriers](../generation-barriers.md) and [tests/unit/core/test_barriers.py](../../../tests/unit/core/test_barriers.py).
