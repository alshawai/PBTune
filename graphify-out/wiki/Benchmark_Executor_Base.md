# Benchmark Executor Base

> 27 nodes · cohesion 0.13

## Key Concepts

- **BareMetalEnvironment** (30 connections) — `src/utils/environments/bare_metal.py`
- **_DummyEnvironment** (18 connections) — `tests/unit/utils/test_environment_base.py`
- **stop_instance()** (16 connections) — `src/utils/environments/base.py`
- **base.py** (16 connections) — `src/utils/environments/base.py`
- **start_instance()** (14 connections) — `src/utils/environments/base.py`
- **restart_instance()** (11 connections) — `src/utils/environments/base.py`
- **stop_all()** (9 connections) — `src/utils/environments/base.py`
- **recover_instance()** (8 connections) — `src/utils/environments/base.py`
- **verify_instances()** (8 connections) — `src/utils/environments/base.py`
- **cleanup()** (6 connections) — `src/utils/environments/base.py`
- **bare_metal.py** (2 connections) — `src/utils/environments/bare_metal.py`
- **Bare-Metal Environment Implementation ======================================  Pr** (1 connections) — `src/utils/environments/bare_metal.py`
- **Start a specific worker instance using pg_ctl.** (1 connections) — `src/utils/environments/bare_metal.py`
- **Stop a specific worker instance using pg_ctl.** (1 connections) — `src/utils/environments/bare_metal.py`
- **Stop all worker instances.** (1 connections) — `src/utils/environments/bare_metal.py`
- **Recover a worker instance by stopping and restarting it.** (1 connections) — `src/utils/environments/bare_metal.py`
- **Restart a specific worker's PostgreSQL instance via pg_ctl.** (1 connections) — `src/utils/environments/bare_metal.py`
- **Verify the status of all worker instances.** (1 connections) — `src/utils/environments/bare_metal.py`
- **Bare-metal PostgreSQL environment for multi-worker parallel operations.      Con** (1 connections) — `src/utils/environments/bare_metal.py`
- **Base Environment Interface ===========================  Provides the polymorphic** (1 connections) — `src/utils/environments/base.py`
- **Start a stopped container.** (1 connections) — `src/utils/environments/docker.py`
- **Stop a running container.** (1 connections) — `src/utils/environments/docker.py`
- **Stop all running containers associated with this environment.** (1 connections) — `src/utils/environments/docker.py`
- **Restart a failed container.** (1 connections) — `src/utils/environments/docker.py`
- **Restart a specific worker's Docker container.          Uses Docker's native rest** (1 connections) — `src/utils/environments/docker.py`
- *... and 2 more nodes in this community*

## Relationships

- [[Bare Metal Environment]] (95 shared connections)
- [[Scoring Policies]] (11 shared connections)
- [[Metric Config & Composite]] (10 shared connections)
- [[Workload README]] (8 shared connections)
- [[TPC-H Query Executor]] (8 shared connections)
- [[TPC-H DBGEN Tables]] (4 shared connections)
- [[Bare Metal Tests]] (4 shared connections)
- [[Sysbench Core]] (4 shared connections)
- [[BO Config & Worker]] (3 shared connections)
- [[Database Config & Connection]] (2 shared connections)
- [[Environment Factory]] (2 shared connections)
- [[Cross-Module Rationale]] (1 shared connections)

## Source Files

- `src/utils/environments/bare_metal.py`
- `src/utils/environments/base.py`
- `src/utils/environments/docker.py`
- `tests/unit/utils/test_environment_base.py`

## Audit Trail

- EXTRACTED: 138 (90%)
- INFERRED: 16 (10%)
- AMBIGUOUS: 0 (0%)

---

*Part of the graphify knowledge wiki. See [[index]] to navigate.*