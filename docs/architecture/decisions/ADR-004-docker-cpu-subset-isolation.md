# ADR-004: Docker CPU Subset Isolation for Concurrent Workers

- Status: Accepted
- Date: 2026-06-04

## Context

When the PBT tuner runs N workers in parallel on a single host, each worker drives its own PostgreSQL instance. The kernel scheduler is, by default, free to migrate any worker's PostgreSQL threads to any physical core. Without core-level isolation, two issues arise:

1. **Measurement noise from thread migration.** A worker's PostgreSQL backends bounce between cores, losing cache locality with every migration. The measured latency variance is dominated by scheduler decisions rather than configuration quality. This is invisible to the tuning loop but inflates the standard deviation of every per-generation score.
2. **Cross-worker contention is unbounded.** Two workers competing for the same physical core time-slice each other transparently. The slower worker's measurement reflects whatever portion of that core's cycles it received, which depends on what the faster worker happened to be doing. This is the exact confounder the [generation barriers (ADR-003)](./ADR-003-lockstep-generation-barriers.md) cannot fix — they ensure measurement windows overlap, but they cannot ensure the workers' threads land on disjoint cores during those windows.

The bare-metal backend cannot enforce core isolation at all. `pg_ctl` starts a PostgreSQL postmaster as a host process; the kernel scheduler treats it like any other process. Per-worker `WorkerResources` is therefore advisory only — it correctly bounds knob-range resolution and memory-utilisation normalisation, but it cannot prevent the two issues above.

Docker is the only available mechanism in this codebase that gives us kernel-level core enforcement without a custom kernel module or a privileged-process scheduler hack.

## Decision

When the [`DockerEnvironment` backend](../environment-backends.md#dockerenvironment) creates per-worker containers, it sets `--cpuset-cpus` to a contiguous range of physical cores derived from `worker_resources.cpu_cores` and the host CPU count. Each worker's container can run only on the cores assigned to it; the kernel scheduler will not migrate threads outside the cpuset.

The algorithm:

1. Detect host cores via `os.cpu_count()`.
2. Reserve 20% headroom (`host_cores * 0.8`, rounded down) for the OS and the tuning loop itself.
3. Divide the remaining cores evenly across `num_parallel_workers` to derive each worker's `cpu_cores`.
4. Assign worker `k` the contiguous range `[k * cpu_cores, (k+1) * cpu_cores)`, expressed as a `--cpuset-cpus` string.

Memory is enforced in the same idiom: `--memory=worker_resources.ram_bytes` caps the container's RSS to the per-worker slice (the cgroup OOM-kills before peers are affected).

Bare-metal explicitly does not enforce this and is marked as reduced-isolation in the comparison JSON. The factory emits a banner-style warning whenever it falls back to bare-metal so users notice the regression.

## Consequences

Positive:

- Each worker's PostgreSQL threads stay on the cores assigned to it. Cache locality is preserved within the measurement window.
- Cross-worker contention through the scheduler is eliminated. Workers can still contend on memory bandwidth and disk, but those are accounted for by the barrier-enforced measurement-window overlap.
- Publication-facing comparisons (PBT vs BO baseline, multi-seed campaigns) have a defensible isolation story: every evaluation in the comparison was run under the same `--cpuset-cpus` and `--memory` constraints.
- The session JSON records `worker_resources` per worker, so reviewers can audit the slice that was actually used.

Trade-offs:

- The maximum useful `num_parallel_workers` is bounded by the host CPU count divided by the per-worker `cpu_cores`. Users asking for more parallelism than the host can cleanly accommodate trigger an oversubscription warning and end up with overlapping cpusets (degrading back to the cross-worker contention case).
- Bare-metal evaluations are now visibly reduced-isolation in the comparison output. This is desirable but means some historical results (run before Docker isolation was available) cannot be directly mixed with current Docker-isolated results in a publication table without flagging the methodology shift.
- The Docker dependency becomes load-bearing for publication-facing work. CI environments without a Docker daemon fall back to bare-metal and emit the warning.

## Alternatives Considered

1. **`--cpus=N.M` weight-based shares instead of `--cpuset-cpus`.**

   Rejected. The kernel honours `--cpus` by adjusting cpu.cfs_quota_us — it does not pin threads to specific cores. Threads still migrate, cache locality is still lost, and the cross-worker scheduling artefact persists. `--cpus` is appropriate for fair resource sharing among production workloads; it is wrong for measurement-grade isolation.

2. **`taskset` on bare-metal postgres processes.**

   Rejected. `taskset` sets an affinity mask but the user has to manage it per-process, per-restart. The bare-metal lifecycle (initdb / pg_ctl restart / `restart_instance`) would need to taskset the new postmaster on every restart, and `taskset` cannot bound memory. This would re-implement half of Docker's container abstraction in shell.

3. **One worker per Docker host, orchestrated externally.**

   Rejected. The project's research scope is single-host PBT; multi-host orchestration is out of scope for this codebase. It would also reintroduce the very network-coordination complexity the lockstep barriers were designed to avoid.

4. **Run sequentially and skip isolation entirely.**

   Rejected. Sequential evaluation forfeits the parallel speedup that makes PBT competitive with BO on wall-clock time. The point of the project is that parallel evaluation is methodologically defensible when properly isolated.

## Migration Notes

The factory continues to accept `use_docker=False` and `--no-docker` for users who explicitly opt out. Both paths log an isolation warning and tag the resulting comparison JSON's `evaluation_environment` field as `bare-metal-fallback`. The post-hoc evaluation suite's reproducibility checklist already surfaces this field — reviewers can filter on it.

Sessions tuned before Docker isolation was available still load through the existing scoring-policy compatibility branch. Their session JSON does not record `worker_resources` per worker; the analysis pipeline treats them as host-resource sessions when computing data-driven tier importances. Hardware-validation results that mix Docker-isolated and bare-metal sessions should be interpreted carefully — the [hardware-aware Kendall's τ stability metric](../knob-importance-analysis.md) becomes harder to interpret when one of the "hardware profiles" is "no isolation at all."

See [environment-backends](../environment-backends.md) and [hardware-aware-normalization §7](../hardware-aware-normalization.md#7-docker-cpu-subset-enforcement) for the implementation details, and [tests/unit/utils/test_docker_environment.py](../../../tests/unit/utils/test_docker_environment.py) for the cpuset derivation tests.
