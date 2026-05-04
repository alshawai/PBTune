---
name: environment-backends
description: >
  DatabaseEnvironment abstraction layer for PostgreSQL instance lifecycle management.
  Covers Docker and bare-metal backends, environment factory, instance creation/teardown,
  configuration application, health checks, and resource isolation. Use this skill when
  working on Docker containers, bare-metal PostgreSQL instances, environment selection,
  instance management, port allocation, or any code in src/utils/environments/.
---

# Environment Backends

The `DatabaseEnvironment` abstraction decouples tuning/evaluation logic from
the physical PostgreSQL instance management.

## Architecture

```
EnvironmentFactory.create(backend, worker_id, ...)
    ├── "docker"     → DockerEnvironment
    └── "bare_metal" → BareMetalEnvironment

Both implement DatabaseEnvironment ABC:
    .setup()          → Create/start instance
    .apply_config()   → Write knobs + restart/reload
    .health_check()   → Verify connectivity
    .teardown()       → Stop + cleanup
    .get_connection() → psycopg2 connection
```

## DockerEnvironment

- Fresh containers from `docker/eval.Dockerfile`
- Port: `base_port + worker_id` (default base: 5440)
- cgroup CPU/memory limits, tmpfs for WAL
- Use case: Evaluation pipeline (publication-quality isolation)

## BareMetalEnvironment

- Local `pg_ctl` / `initdb` management
- Data dirs: `{pg_data_base}/worker_{worker_id}/`
- Auto-detects `pg_ctl`/`initdb` via PATH
- Reuses existing data dirs if initialized
- Use case: PBT tuning loop (lower overhead)

## Config Application Flow

1. Separate knobs by context (postmaster vs sighup)
2. Write ALL knobs to postgresql.conf
3. postmaster changed → `pg_ctl restart`; sighup only → `pg_ctl reload`
4. Verify via `SELECT current_setting(knob_name)`

## Code Locations

| Component | File |
|-----------|------|
| ABC | `src/utils/environments/base.py` |
| Docker | `src/utils/environments/docker.py` |
| Bare-metal | `src/utils/environments/bare_metal.py` |
| Factory | `src/utils/environments/factory.py` |
| Applicator | `src/utils/applicator.py` |

## Key Constraints

- One instance per worker — never share
- Ports 5440-5460 must be free
- Docker fallback: auto bare-metal if Docker unavailable
- Always `teardown()` in `finally` — orphaned instances leak ports and disk
