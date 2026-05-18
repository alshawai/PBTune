# Session Tests

> 12 nodes · cohesion 0.20

## Key Concepts

- **get_db_config()** (16 connections) — `src/config/database.py`
- **collect_memory_utilization()** (9 connections) — `src/utils/environments/base.py`
- **database.py** (6 connections) — `src/config/database.py`
- **get_instance()** (3 connections) — `src/config/database.py`
- **_ConfigHolder** (2 connections) — `src/config/database.py`
- **from_env()** (2 connections) — `src/config/database.py`
- **Database Configuration Utility ================================  Centralized uti** (1 connections) — `src/config/database.py`
- **Holds the singleton database configuration instance.** (1 connections) — `src/config/database.py`
- **Get the database configuration singleton.      This ensures that configuration i** (1 connections) — `src/config/database.py`
- **Collect PostgreSQL RSS utilization ratio against worker memory budget.** (1 connections) — `src/utils/environments/bare_metal.py`
- **Get the runtime connection configuration for a defined worker.** (1 connections) — `src/utils/environments/base.py`
- **Collect container memory utilization ratio using cgroup usage/limit.** (1 connections) — `src/utils/environments/docker.py`

## Relationships

- [[TPC-H DBGEN Tables]] (22 shared connections)
- [[Metric Config & Composite]] (9 shared connections)
- [[Bare Metal Environment]] (7 shared connections)
- [[Database Config & Connection]] (2 shared connections)
- [[TPC-H Query Executor]] (2 shared connections)
- [[Scoring Policies]] (2 shared connections)

## Source Files

- `src/config/database.py`
- `src/utils/environments/bare_metal.py`
- `src/utils/environments/base.py`
- `src/utils/environments/docker.py`

## Audit Trail

- EXTRACTED: 42 (95%)
- INFERRED: 2 (5%)
- AMBIGUOUS: 0 (0%)

---

*Part of the graphify knowledge wiki. See [[index]] to navigate.*