# Instance Management

> 21 nodes · cohesion 0.13

## Key Concepts

- **get_connection()** (24 connections) — `src/database/connection.py`
- **DatabaseEnvironment** (19 connections)
- **._reset_persisted_configuration()** (7 connections) — `src/utils/environments/base.py`
- **.initialize_schema()** (6 connections) — `src/utils/environments/base.py`
- **._checkpoint_instance()** (5 connections) — `src/utils/environments/docker.py`
- **.collect_cache_hit_ratio()** (4 connections) — `src/utils/environments/base.py`
- **._ensure_database_exists()** (4 connections) — `src/utils/environments/base.py`
- **.reset_statistics()** (4 connections) — `src/utils/environments/base.py`
- **._wait_until_connectable()** (4 connections) — `src/utils/environments/base.py`
- **._get_instance_subpath()** (2 connections) — `src/utils/environments/base.py`
- **Create a psycopg2 database connection.      Parameters     ----------     config** (1 connections) — `src/database/connection.py`
- **Create the application database if it doesn't exist.          After initdb, only** (1 connections) — `src/utils/environments/base.py`
- **Wait for PostgreSQL to accept connections after restart operations.** (1 connections) — `src/utils/environments/base.py`
- **Clear persisted ALTER SYSTEM settings and restart if pending_restart remains.** (1 connections) — `src/utils/environments/base.py`
- **Query pg_stat_database for buffer cache hit ratio.          Default implementati** (1 connections) — `src/utils/environments/base.py`
- **Reset PostgreSQL statistics counters for a worker instance.** (1 connections) — `src/utils/environments/base.py`
- **Abstract Base Class for managing isolated database environments.      Provides a** (1 connections) — `src/utils/environments/base.py`
- **Determine the logical subpath for runtime data based on the schema.** (1 connections) — `src/utils/environments/base.py`
- **Initialize schema by delegating to the schema_provider.          The provider's** (1 connections) — `src/utils/environments/base.py`
- **Issue a CHECKPOINT before snapshot creation to reduce recovery time on restore.** (1 connections) — `src/utils/environments/docker.py`
- **Create a database connection.** (1 connections) — `src/knobs/retrieval.py`

## Relationships

- [[Metric Config & Composite]] (53 shared connections)
- [[TPC-H DBGEN Tables]] (7 shared connections)
- [[Bare Metal Environment]] (6 shared connections)
- [[Benchmark Executor Base]] (4 shared connections)
- [[Scoring & Weight Policies]] (3 shared connections)
- [[Database Operations]] (2 shared connections)
- [[Benchmark Orchestrator]] (2 shared connections)
- [[TPC-H Query Executor]] (2 shared connections)
- [[TPC-H Schema & Tables]] (1 shared connections)
- [[PostgreSQL Knob Retrieval]] (1 shared connections)
- [[Logger Colors]] (1 shared connections)
- [[Visualization Plotting]] (1 shared connections)

## Source Files

- `src/database/connection.py`
- `src/knobs/retrieval.py`
- `src/utils/environments/base.py`
- `src/utils/environments/docker.py`

## Audit Trail

- EXTRACTED: 57 (63%)
- INFERRED: 33 (37%)
- AMBIGUOUS: 0 (0%)

---

*Part of the graphify knowledge wiki. See [[index]] to navigate.*