# Environment Backends

> Last reviewed: 2026-06-07

See also: [Documentation Index](../README.md), [PBT Core Components](pbt-core.md), [Workload Orchestrator](workload-orchestrator.md), [Hardware-Aware Normalization](hardware-aware-normalization.md), [ADR-004](decisions/ADR-004-docker-cpu-subset-isolation.md)

## Overview

The environment layer abstracts the lifecycle of the **PostgreSQL instances each worker tunes against**. Two backends share one `DatabaseEnvironment` interface:

- **`DockerEnvironment`** ([src/utils/environments/docker.py](../../src/utils/environments/docker.py)) — one container per worker, with CPU subset pinning, RAM caps, and network isolation. **Recommended for any methodologically rigorous run** (publication-facing comparisons, multi-seed campaigns, the BO-vs-PBT baseline).
- **`BareMetalEnvironment`** ([src/utils/environments/bare_metal.py](../../src/utils/environments/bare_metal.py)) — `initdb`-managed data directories on the host, separate ports, no kernel-level resource isolation. Used when Docker is unavailable, for fast smoke tests, or in environments where the user explicitly accepts reduced isolation.

The factory ([`EnvironmentFactory.create`](../../src/utils/environments/factory.py)) auto-selects: try Docker first; fall back to bare-metal with a banner-style warning if Docker is unreachable, with `--no-docker`, or on errors during container creation.

A single `DatabaseEnvironment` instance manages **all** worker instances for a run; per-worker calls take a `worker_id`. The interface is deliberately uniform across backends — orchestrator and population code never branches on backend type.

---

## Table of Contents

1. [`DatabaseEnvironment` interface](#databaseenvironment-interface)
2. [`EnvironmentFactory`](#environmentfactory)
3. [`DockerEnvironment`](#dockerenvironment)
4. [`BareMetalEnvironment`](#baremetalenvironment)
5. [Snapshot lifecycle](#snapshot-lifecycle)
6. [Instance cloning during exploit](#instance-cloning-during-exploit)
7. [Health checks and dead-worker rescue](#health-checks-and-dead-worker-rescue)
8. [Design decisions](#design-decisions)
9. [Related documentation](#related-documentation)

---

## `DatabaseEnvironment` interface

**Location**: [src/utils/environments/base.py](../../src/utils/environments/base.py)

```python
class DatabaseEnvironment(ABC):
    def __init__(
        self,
        run_id: str,
        db_config: DatabaseConfig,
        schema_provider: BenchmarkExecutor,
        force_recreate_baseline: bool = False,
    ): ...

    # Lifecycle
    @abstractmethod def setup_instances(self, num_workers: int) -> None: ...
    @abstractmethod def cleanup(self, remove_data: bool = False) -> None: ...

    # Per-worker control
    @abstractmethod def start_instance(self, worker_id: int) -> bool: ...
    @abstractmethod def stop_instance(self, worker_id: int, mode: str = "fast") -> bool: ...
    @abstractmethod def restart_instance(self, worker_id: int, quiet: bool = False) -> bool: ...
    @abstractmethod def recover_instance(self, worker_id: int) -> bool: ...
    @abstractmethod def rebuild_worker_instance(self, worker_id: int) -> bool: ...

    # Cohort control
    @abstractmethod def stop_all(self, mode: str = "fast") -> bool: ...
    @abstractmethod def verify_instances(self) -> None: ...

    # Snapshots
    @abstractmethod def create_snapshot(self, worker_id: int = 0) -> str: ...
    @abstractmethod def restore_snapshot(self, worker_id: int, snapshot_id: str) -> bool: ...

    # Exploit support
    @abstractmethod def clone_instances(self, source_worker_id: int, target_worker_ids: list[int]) -> bool: ...

    # Connection + telemetry
    @abstractmethod def get_db_config(self, worker_id: int) -> DatabaseConfig: ...
    @abstractmethod def collect_memory_utilization(self, worker_id: int) -> float: ...
    @abstractmethod def collect_cache_hit_ratio(self, worker_id: int) -> float: ...
    @abstractmethod def reset_statistics(self, worker_id: int) -> bool: ...
```

`InstanceConfig` is the per-worker record (`worker_id`, `port`, `data_dir`, `running`). The base class provides shared helpers for schema initialisation, connection-readiness polling, and persisted-configuration reset (so a per-worker `postgresql.auto.conf` doesn't leak knobs from a previous session).

### What every backend guarantees

| Guarantee | How |
| --- | --- |
| **One PostgreSQL per worker** | Distinct port (`base_port + worker_id`), distinct data directory. |
| **Worker isolation by file system** | Per-worker data dir under `.instances/<run_id>/<benchmark_subpath>/worker_N/`. |
| **Repeatable schema** | Schema initialisation delegated to the `BenchmarkExecutor` (Sysbench / TPC-H / template). The schema-provider's `validate()` is run at every start to detect drift. |
| **Repeatable baseline** | Per-run baseline snapshot taken once, restored periodically (`snapshot_restore_interval`) to combat data drift over long sessions. |
| **Liveness reporting** | `verify_instances()` walks every worker and confirms it accepts connections; the population's health-check thread uses this to detect dead instances. |

Backends differ in **how strong** the isolation is — see the per-backend sections below.

---

## `EnvironmentFactory`

**Location**: [src/utils/environments/factory.py](../../src/utils/environments/factory.py)

```python
EnvironmentFactory.create(
    schema_provider: BenchmarkExecutor,
    use_docker: bool = True,
    base_dir: Path = Path("./.instances"),
    base_port: int = 5440,
    db_config: Optional[DatabaseConfig] = None,
    worker_resources: Optional[WorkerResources] = None,
    run_id: str = "tuner-run",
    container_prefix: str = "pbt-worker",
    image_name: Optional[str] = None,
    force_recreate_baseline: bool = False,
) -> DatabaseEnvironment
```

The factory:

1. Resolves the PostgreSQL Docker image (auto-detects host PG major version, picks `postgres:<major>` from the official image, with `pgvector`-equipped variants for newer Postgres releases).
2. Tries `docker.from_env().ping()`. If Docker is reachable, returns a `DockerEnvironment`.
3. Falls back to `BareMetalEnvironment` on `ImportError`, `docker.errors.DockerException`, `OSError`, `RuntimeError`, or `ValueError` — with a prominent isolation warning.
4. Always falls back to bare-metal if `use_docker=False`.

`worker_resources` carries the per-worker CPU/RAM slice computed by [`detect_worker_resources()`](../../src/utils/hardware_info.py) and is propagated into the Docker container limits or used to bound bare-metal memory accounting.

---

## `DockerEnvironment`

**Strongest isolation.** One container per worker, started from the resolved PostgreSQL image, with:

- **Distinct port** (`base_port + worker_id`) bound to `127.0.0.1`.
- **Distinct PGDATA volume** mounted from `.instances/<run_id>/<benchmark_subpath>/worker_N/pgdata/`.
- **CPU subset pinning** via `--cpuset-cpus` (computed from the host CPU count and the number of parallel workers — see [ADR-004](decisions/ADR-004-docker-cpu-subset-isolation.md)).
- **RAM cap** via `--memory` derived from `worker_resources.ram_bytes`.
- **Network mode** appropriate for `host.docker.internal` semantics so the orchestrator (running on the host) can reach the container.
- **Container name** `pbt-worker_<worker_id>_<run_id_short>` so cleanup is greppable.

### CPU subset pinning

Without pinning, two workers on the same host can both grab the same physical core, time-slice each other, and their measurement windows interfere. With `--cpuset-cpus=2,3` on worker 0 and `--cpuset-cpus=4,5` on worker 1, the kernel scheduler keeps each worker's queries on its own cores, and the only contention they share is memory bandwidth and disk — both already accounted for by the lockstep barriers around the measurement window.

The CPU budget is derived from `worker_resources.cpu_cores`. If the host has fewer cores than `population_size × cpu_cores`, the factory logs a warning and proceeds with overlapping subsets — the user has chosen to oversubscribe.

### Container lifecycle

| Method | What happens |
| --- | --- |
| `setup_instances(N)` | Create N containers from a baseline snapshot (or from scratch if no snapshot yet). Each container runs `postgres` as PID 1. |
| `start_instance(k)` / `stop_instance(k)` | `container.start()` / `container.stop(timeout=mode-derived)`. |
| `restart_instance(k)` | Stop + start. Used after `postmaster`-context knob changes. |
| `recover_instance(k)` | Restart with longer timeout; used by the dead-worker rescue path before it gives up and rebuilds. |
| `rebuild_worker_instance(k)` | Tear down container + volume; recreate from baseline snapshot. |
| `clone_instances(src, [dst1, dst2…])` | Stop sources and dests; rsync `src` PGDATA to each destination; restart all. The fast-path used by exploit. |
| `cleanup(remove_data=True)` | Remove all containers and (optionally) all PGDATA dirs for this `run_id`. |

The Docker class also derives bounded timeouts for snapshot creation, snapshot restoration, and Docker-API calls to keep a single hung container from stalling cleanup.

### Telemetry

`collect_memory_utilization(k)` reads the container's RSS via the Docker API (or falls back to `psutil` against the postgres process inside the container), normalised by `worker_memory_budget_bytes`. `collect_cache_hit_ratio(k)` runs `SELECT … FROM pg_statio_user_tables` over the container.

---

## `BareMetalEnvironment`

**Weaker isolation.** Per-worker `initdb` data directories on the host filesystem, started/stopped via `pg_ctl`. There is no CPU pinning and no kernel-level memory cap — the OS scheduler is free to interleave the workers, and the `worker_memory_budget_bytes` value is purely advisory (used for normalisation, not enforcement).

```text
.instances/<run_id>/<benchmark_subpath>/
├── _baseline/                  # one shared snapshot directory
└── worker_0/pgdata/            # per-worker data dirs
    worker_1/pgdata/
    ...
```

`start_instance(k)` resolves the data dir, kills any stale port holder, runs `pg_ctl -D <data_dir> -p <port> -l <log> start`, then polls until the port is connectable. `stop_instance(k)` runs `pg_ctl stop -m <mode>` with `mode ∈ {smart, fast, immediate}`.

Bare-metal is sufficient when:

- Docker isn't available (CI without a Docker daemon, restrictive corporate hosts, some WSL configurations).
- The user is running a quick smoke test where measurement-window precision doesn't matter.
- Profiling Postgres directly with host-level tools is needed, since there's no container boundary.

It is **not** sufficient for:

- Comparing PBT against the BO baseline. Both methods need identical resource constraints; only Docker enforces them.
- Multi-seed campaigns whose results will appear in a paper. The `comparison_metadata.evaluation_environment` field will record `bare-metal-fallback` and the post-hoc evaluation suite will mark the run as reduced-isolation.

The factory emits a banner-style warning whenever it returns a bare-metal environment so the user notices the regression.

---

## Snapshot lifecycle

Both backends implement the same conceptual flow:

```text
session start
  │
  ├─► setup_instances(N)               # create N empty PG instances
  ├─► initialize_schema(0)             # run benchmark schema on worker 0
  ├─► create_snapshot(0)               # take baseline snapshot
  ├─► clone_instances(0, [1..N-1])     # propagate to other workers
  │
  ├─► training loop:
  │     │
  │     │ every snapshot_restore_interval generations:
  │     │   for each worker:
  │     │     restore_snapshot(worker_id, "_baseline")
  │     │   stop_all() / restart all
  │     │
  │     └─► train_generation(...)
  │
  └─► cleanup(remove_data=...)
```

### Why baseline snapshots

PBT runs span hours. Across hundreds of evaluations, a Sysbench OLTP workload writes meaningful data into `sbtest1`, an OLAP workload's `pg_class.relpages` drifts as `VACUUM ANALYZE` runs, and the buffer cache stabilises into shapes specific to the configurations seen so far. Without periodic restoration, the late-generation measurements are run against a different database state than the early ones — confounding the score.

Restoring to a baseline snapshot every `snapshot_restore_interval` generations resets the database to a known starting point. The interval is configurable: too short and the cost of restoration dominates; too long and drift contaminates the score. Default is `10` generations; the BO baseline scales this by population size when matching a PBT reference (see [BO_BASELINE.md](../guides/bo-baseline.md)).

### Backend differences

| | Docker | Bare-metal |
| --- | --- | --- |
| Snapshot artefact | Tar archive of the baseline PGDATA volume | Cold copy of the baseline PGDATA directory |
| Restoration | Stop container, rsync from snapshot tar, start container | Stop instance, rsync from snapshot dir, start instance |
| Atomicity | Snapshot is read-only; rsync from a separate volume | Snapshot is read-only; rsync from a separate dir |
| Time budget | `_derive_snapshot_timeout()` / `_derive_restore_ready_timeout()` | Bounded by the per-call timeout passed to the rsync helper |

---

## Instance cloning during exploit

Since commit `4165ceb`, exploit no longer just copies knob values — it also **clones the elite's data directory** so the inheritor begins evaluation in the same warmed-up state. Without cloning, the next evaluation includes a long warmup tail that has nothing to do with knob quality.

The orchestration:

```text
Population.exploit_and_explore():
  pairs = truncation_selection(workers, ...)
  group = collections.defaultdict(list)
  for poor_idx, elite_idx in pairs:
      group[elite_idx].append(poor_idx)

  for elite_idx, dst_ids in group.items():
      environment.clone_instances(elite_idx, dst_ids)

  for poor_idx, elite_idx in pairs:
      workers[poor_idx].clone_from(workers[elite_idx], generation, environment)
      workers[poor_idx].perturb(...)
```

Grouping multiple destinations under one source lets the backend share scan/copy passes when the same elite is the source for several poor workers — small but useful when exploit pairings cluster.

### Backend implementations

- **Docker**: stop source + dests → use `docker cp` or a host-side rsync from the source's bind-mounted PGDATA → restart dests.
- **Bare-metal**: stop source + dests → rsync source's data dir to each dest's data dir → start all.

`clone_instances` returns a single `bool` — partial failure across destinations is reported in the population's log but treated as a generation-level retry rather than a hard error, since the dead-worker rescue path will pick up the failed clone on the next generation boundary.

---

## Health checks and dead-worker rescue

Each backend exposes the per-worker `is_alive(worker_id)` pattern via `verify_instances()` and the `recover_instance` / `rebuild_worker_instance` recovery ladder. The population layer uses these to:

1. Periodically check every worker's PostgreSQL process.
2. If a worker is unhealthy, attempt `recover_instance` (cheaper, restart-only).
3. If recovery fails, attempt `rebuild_worker_instance` (more expensive, reclones from baseline).
4. If rebuild fails, the worker is marked dead; the population's `rescue_dead_workers()` will resample a fresh config and a fresh instance on the next generation boundary.

The same chain runs *during* a generation when `GenerationBarrier.abort()` is called — the `recover` / `rebuild` ladder has time to operate before the next generation starts.

---

## Design decisions

### 1. One interface, two backends

The orchestrator and population code never branches on backend type. A future third backend (Kubernetes pods, AWS RDS, Aurora) plugs in as a third `DatabaseEnvironment` subclass. The factory's branching is the only place that knows which is which.

### 2. Docker as default, bare-metal as opt-in fallback

Docker is the only backend with kernel-enforced CPU/RAM isolation, which is required for scientifically valid concurrent-worker measurements. Bare-metal is reachable via `--no-docker` for users who explicitly accept reduced isolation; a warning banner is logged in that case. Auto-fallback on Docker errors is convenient for development but prints the same warning.

### 3. CPU subset pinning, not weight-based shares

`--cpuset-cpus` gives each worker a hard subset of physical cores. The alternative — `--cpus=2.0` weight-based shares — leaves the kernel scheduler free to migrate threads between cores, which causes cache-locality drift and inflates measurement variance. Hard pinning costs flexibility (can't oversubscribe) but buys repeatability.

### 4. Baseline snapshot per session, not per generation

A per-generation snapshot would be the most defensible against drift but would make Docker setup unbearably slow. A per-session baseline snapshot, restored every `snapshot_restore_interval` generations, is the pragmatic middle ground — defaults at `10` keep the amortised cost low while preventing the worst drift.

### 5. Instance cloning groups destinations under sources

Multiple poor workers exploiting the same elite become one rsync pass with a fan-out at the destination. This is a meaningful saving when the population has converged on a few elites but not yet plateaued.

### 6. Recovery ladder before declaring a worker dead

`recover_instance` (restart) is cheaper than `rebuild_worker_instance` (reclone). The ladder lets transient failures heal without losing the worker's lineage; persistent failures escalate to a full rebuild, then to dead-worker rescue.

---

## Related documentation

- **[PBT Core Components](pbt-core.md)** — how the population drives the environment.
- **[Workload Orchestrator](workload-orchestrator.md)** — how an evaluation invokes start/stop/restart.
- **[Hardware-Aware Normalization](hardware-aware-normalization.md)** — `WorkerResources` and warm-start.
- **[Generation Barriers](generation-barriers.md)** — how `is_alive()` informs `abort()`.
- **[ADR-004 — Docker CPU subset isolation](decisions/ADR-004-docker-cpu-subset-isolation.md)** — design decision.
- **[BENCHMARKING.md](../reference/benchmarking.md)** — schema providers (Sysbench / TPC-H / template).
- **[BO_BASELINE.md](../guides/bo-baseline.md)** — how the BO baseline reuses the same environment layer.

### File locations

- `DatabaseEnvironment`, `InstanceConfig`: [src/utils/environments/base.py](../../src/utils/environments/base.py)
- `DockerEnvironment`: [src/utils/environments/docker.py](../../src/utils/environments/docker.py)
- `BareMetalEnvironment`: [src/utils/environments/bare_metal.py](../../src/utils/environments/bare_metal.py)
- `EnvironmentFactory`: [src/utils/environments/factory.py](../../src/utils/environments/factory.py)
- `WorkerResources` detection: [src/utils/hardware_info.py](../../src/utils/hardware_info.py)
- Cleanup script: [src/scripts/cleanup_instances.py](../../src/scripts/cleanup_instances.py)
- Tests: [tests/unit/utils/test_docker_environment.py](../../tests/unit/utils/test_docker_environment.py), [tests/unit/utils/test_bare_metal_memory_utilization.py](../../tests/unit/utils/test_bare_metal_memory_utilization.py), [tests/unit/utils/test_environment_base.py](../../tests/unit/utils/test_environment_base.py)
