# PostgreSQL Parameter Contexts — Detailed Reference

## Context Types

PostgreSQL parameters are categorized by how changes take effect:

### `postmaster` — Requires Full Restart
- Changes only take effect after a full instance restart
- Examples: `shared_buffers`, `max_connections`, `max_worker_processes`, `wal_buffers`
- **Impact**: Service interruption during restart (1-5s typically)
- **Code path**: `KnobApplicator.apply()` writes the value via `ALTER SYSTEM SET` (persisted to `postgresql.auto.conf`); activation requires a full restart performed by the environment backend's `restart_instance()` (driven by the orchestrator's `_perform_restart`), not by the applicator
- **Critical**: Batch ALL postmaster knobs into a single restart per evaluation cycle

### `sighup` — Requires Reload Only
- Changes take effect after a config reload (`SELECT pg_reload_conf()`, no downtime)
- Examples: `effective_cache_size`, `random_page_cost`, `work_mem`, `maintenance_work_mem`
- **Code path**: `KnobApplicator.apply()` writes the value via `ALTER SYSTEM SET` (persisted to `postgresql.auto.conf`), then reloads via `KnobApplicator._reload_configuration()`, which runs `SELECT pg_reload_conf()`
- **Note**: Some sighup knobs require active sessions to reconnect to pick up changes

### `user` — Session-Level SET
- Changes take effect immediately for new sessions via `SET parameter = value`
- Examples: (varies by version)
- **Code path**: Direct SQL execution on the connection
- **Note**: Only affects the current session; not persisted

## Apply Configuration Flow

```
KnobApplicator.apply(knob_config):
    1. Validate each knob against pg_settings (vartype / min_val / max_val / enumvals / context)
    2. Write ALL knobs via ALTER SYSTEM SET (persisted to postgresql.auto.conf)
    3. If any sighup knob changed: reload via SELECT pg_reload_conf()
    4. Postmaster knobs are flagged in ApplicationResult.restart_required; the orchestrator
       triggers a full restart through the environment backend's restart_instance()
    5. Read back via KnobApplicator.verify(), which queries pg_settings for the applied (quantised) values
```

## Restart Minimization Strategy

The restart policy module (`src/tuners/engine/restart_policy.py`) handles:
- Tracking which postmaster knobs have changed since last restart
- Batching restarts to minimize service interruptions
- Detecting when restart is actually needed (only if postmaster values differ)
- Selectable behavior via `TuningMode` {ONLINE, OFFLINE, ADAPTIVE}
  (exposed on the tuner CLI as `--tuning-mode`)

The legacy `RestartCostModel` was archived to `prototypes/restart_cost_model/`.

## Multi-Instance Port Scheme

Each PBT worker gets its own PostgreSQL instance:
- Base port: 5440 (worker i gets port `5440 + worker_id`)
- Data directory: `{base_dir}/{benchmark_subpath}/worker_{worker_id}/pgdata/` (default `base_dir`: `./.instances`)
