# PBT Literature & Papers

> 11 nodes · cohesion 0.22

## Key Concepts

- **setup_instances()** (20 connections) — `src/utils/environments/base.py`
- **create_snapshot()** (16 connections) — `src/utils/environments/base.py`
- **._wait_for_ready()** (10 connections) — `src/utils/environments/docker.py`
- **._resolve_snapshot_path()** (6 connections) — `src/utils/environments/bare_metal.py`
- **._kill_stale_port_holder()** (3 connections) — `src/utils/environments/bare_metal.py`
- **Resolve a snapshot identifier to an absolute snapshot directory path.** (1 connections) — `src/utils/environments/bare_metal.py`
- **Kill any host process listening on the target port.** (1 connections) — `src/utils/environments/bare_metal.py`
- **Set up N database instances on the bare metal host.** (1 connections) — `src/utils/environments/bare_metal.py`
- **Create a baseline snapshot from the specified worker instance.** (1 connections) — `src/utils/environments/base.py`
- **Wait until PostgreSQL is accepting connections.** (1 connections) — `src/utils/environments/docker.py`
- **Create and start the Docker containers for N workers.** (1 connections) — `src/utils/environments/docker.py`

## Relationships

- [[Bare Metal Environment]] (19 shared connections)
- [[Scoring Policies]] (18 shared connections)
- [[TPC-H Query Executor]] (8 shared connections)
- [[Workload README]] (7 shared connections)
- [[Metric Config & Composite]] (5 shared connections)
- [[BO Config & Worker]] (2 shared connections)
- [[TPC-H DBGEN Tables]] (1 shared connections)
- [[Database Config & Connection]] (1 shared connections)

## Source Files

- `src/utils/environments/bare_metal.py`
- `src/utils/environments/base.py`
- `src/utils/environments/docker.py`

## Audit Trail

- EXTRACTED: 56 (92%)
- INFERRED: 5 (8%)
- AMBIGUOUS: 0 (0%)

---

*Part of the graphify knowledge wiki. See [[index]] to navigate.*