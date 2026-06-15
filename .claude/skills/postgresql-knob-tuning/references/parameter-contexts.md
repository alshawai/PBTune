# PostgreSQL Parameter Contexts — Detailed Reference

## Context Types

PostgreSQL parameters are categorized by how changes take effect:

### `postmaster` — Requires Full Restart
- Changes only take effect after `pg_ctl restart`
- Examples: `shared_buffers`, `max_connections`, `max_worker_processes`, `wal_buffers`
- **Impact**: Service interruption during restart (1-5s typically)
- **Code path**: `KnobApplicator.apply_configuration()` writes to `postgresql.conf`, then calls `_restart_postgresql()` which uses `pg_ctl -D <data_dir> restart -m fast`
- **Critical**: Batch ALL postmaster knobs into a single restart per evaluation cycle

### `sighup` — Requires Reload Only
- Changes take effect after `pg_ctl reload` (no downtime)
- Examples: `effective_cache_size`, `random_page_cost`, `work_mem`, `maintenance_work_mem`
- **Code path**: `KnobApplicator.apply_configuration()` writes to `postgresql.conf`, then `pg_ctl -D <data_dir> reload`
- **Note**: Some sighup knobs require active sessions to reconnect to pick up changes

### `user` — Session-Level SET
- Changes take effect immediately for new sessions via `SET parameter = value`
- Examples: (varies by version)
- **Code path**: Direct SQL execution on the connection
- **Note**: Only affects the current session; not persisted

## Apply Configuration Flow

```
KnobApplicator.apply_configuration(config, postmaster_dict, sighup_dict):
    1. Separate knobs by context
    2. Write ALL knobs to postgresql.conf (both postmaster + sighup)
    3. If any postmaster knobs changed: pg_ctl restart -m fast
    4. Else if only sighup knobs changed: pg_ctl reload
    5. Verify configuration applied: SELECT current_setting(name) for each knob
```

## Restart Minimization Strategy

The restart policy module (`src/tuner/benchmark/restart_policy.py`) handles:
- Tracking which postmaster knobs have changed since last restart
- Batching restarts to minimize service interruptions
- Detecting when restart is actually needed (only if postmaster values differ)
- Selectable behavior via `TuningMode` {ONLINE, OFFLINE, ADAPTIVE}
  (exposed on the tuner CLI as `--tuning-mode`)

The legacy `RestartCostModel` was archived to `prototypes/restart_cost_model/`.

## Multi-Instance Port Scheme

Each PBT worker gets its own PostgreSQL instance:
- Base port: 5440 (worker i gets port `5440 + worker_id`)
- Data directory: `{pg_data_base}/worker_{worker_id}/`
