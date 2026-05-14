# Population Initialization

> 29 nodes · cohesion 0.13

## Key Concepts

- **DockerEnvironment** (49 connections) — `src/utils/environments/docker.py`
- **restore_snapshot()** (18 connections) — `src/utils/environments/base.py`
- **._container_name()** (17 connections) — `src/utils/environments/docker.py`
- **._launch_worker_container()** (9 connections) — `src/utils/environments/docker.py`
- **.rebuild_worker_instance()** (9 connections) — `src/utils/environments/docker.py`
- **_with_timeout()** (8 connections) — `src/utils/environments/docker.py`
- **._remove_worker_container()** (7 connections) — `src/utils/environments/docker.py`
- **._worker_port()** (6 connections) — `src/utils/environments/docker.py`
- **._pgdata_volume_name()** (5 connections) — `src/utils/environments/docker.py`
- **._recreate_worker_pgdata_volume()** (5 connections) — `src/utils/environments/docker.py`
- **._seed_pgdata_volume_from_snapshot()** (5 connections) — `src/utils/environments/docker.py`
- **._container_runtime_kwargs()** (4 connections) — `src/utils/environments/docker.py`
- **._ensure_container_running_after_timeout()** (4 connections) — `src/utils/environments/docker.py`
- **._ensure_container_stopped_after_timeout()** (4 connections) — `src/utils/environments/docker.py`
- **docker.py** (3 connections) — `src/utils/environments/docker.py`
- **Restore a targeted worker's data directory/volume from the baseline snapshot.** (1 connections) — `src/utils/environments/base.py`
- **Docker Environment Implementation ===================================  Provides** (1 connections) — `src/utils/environments/docker.py`
- **Build a deterministic Docker volume name for a worker's PGDATA.** (1 connections) — `src/utils/environments/docker.py`
- **Resolve a worker's host port.** (1 connections) — `src/utils/environments/docker.py`
- **Build runtime kwargs shared across worker container launches.** (1 connections) — `src/utils/environments/docker.py`
- **Remove an existing worker container if present.** (1 connections) — `src/utils/environments/docker.py`
- **Replace worker-specific PGDATA volume with a fresh volume.** (1 connections) — `src/utils/environments/docker.py`
- **Recover from Docker client timeout by checking container state.** (1 connections) — `src/utils/environments/docker.py`
- **Recover from Docker client timeout by checking stop completion.** (1 connections) — `src/utils/environments/docker.py`
- **Launch a worker container with shared timeout + recovery handling.** (1 connections) — `src/utils/environments/docker.py`
- *... and 4 more nodes in this community*

## Relationships

- [[Scoring Policies]] (71 shared connections)
- [[TPC-H Query Executor]] (27 shared connections)
- [[Workload README]] (25 shared connections)
- [[Bare Metal Environment]] (24 shared connections)
- [[Metric Config & Composite]] (7 shared connections)
- [[Cross-Module Rationale]] (4 shared connections)
- [[TPC-H DBGEN Tables]] (3 shared connections)
- [[Benchmark Executor Base]] (2 shared connections)
- [[Environment Factory]] (2 shared connections)
- [[BO Config & Worker]] (1 shared connections)
- [[Database Config & Connection]] (1 shared connections)

## Source Files

- `src/utils/environments/base.py`
- `src/utils/environments/docker.py`

## Audit Trail

- EXTRACTED: 157 (94%)
- INFERRED: 10 (6%)
- AMBIGUOUS: 0 (0%)

---

*Part of the graphify knowledge wiki. See [[index]] to navigate.*