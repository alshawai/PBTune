# Drift Detection

> 15 nodes · cohesion 0.19

## Key Concepts

- **_make_env()** (12 connections) — `tests/unit/utils/test_environment_base.py`
- **test_environment_base.py** (9 connections) — `tests/unit/utils/test_environment_base.py`
- **_SchemaProvider** (8 connections) — `tests/unit/utils/test_environment_base.py`
- **test_ensure_database_exists_handles_connection_failure_without_name_error()** (5 connections) — `tests/unit/utils/test_environment_base.py`
- **test_initialize_schema_resets_after_snapshot_restore()** (3 connections) — `tests/unit/utils/test_environment_base.py`
- **test_reset_persisted_configuration_restarts_when_pending_restart()** (3 connections) — `tests/unit/utils/test_environment_base.py`
- **test_reset_persisted_configuration_skips_restart_when_no_pending_changes()** (3 connections) — `tests/unit/utils/test_environment_base.py`
- **test_reset_statistics_uses_pg_stat_reset()** (3 connections) — `tests/unit/utils/test_environment_base.py`
- **Targeted tests for base environment error handling paths.** (1 connections) — `tests/unit/utils/test_environment_base.py`
- **reset_statistics should call pg_stat_reset() on the worker database.** (1 connections) — `tests/unit/utils/test_environment_base.py`
- **RESET ALL should trigger restart when PostgreSQL reports pending_restart entries** (1 connections) — `tests/unit/utils/test_environment_base.py`
- **RESET ALL should avoid restart when no pending_restart flags remain.** (1 connections) — `tests/unit/utils/test_environment_base.py`
- **Schema initialization should reset persisted settings before and after snapshot** (1 connections) — `tests/unit/utils/test_environment_base.py`
- **No-op schema provider stand-in.** (1 connections) — `tests/unit/utils/test_environment_base.py`
- **Operational errors should be logged and swallowed without secondary NameError.** (1 connections) — `tests/unit/utils/test_environment_base.py`

## Relationships

- [[Sysbench Core]] (40 shared connections)
- [[Bare Metal Environment]] (4 shared connections)
- [[Database Config & Connection]] (3 shared connections)
- [[Bare Metal Tests]] (3 shared connections)
- [[Benchmark Executor Base]] (2 shared connections)
- [[Metric Config & Composite]] (1 shared connections)

## Source Files

- `tests/unit/utils/test_environment_base.py`

## Audit Trail

- EXTRACTED: 48 (91%)
- INFERRED: 5 (9%)
- AMBIGUOUS: 0 (0%)

---

*Part of the graphify knowledge wiki. See [[index]] to navigate.*