---
name: environment-backends
description: >
  DatabaseEnvironment abstraction layer for PostgreSQL instance lifecycle management.
  Covers the Docker, bare-metal and remote (distributed) backends, the environment
  factory, instance creation/teardown, snapshot and clone mechanics, configuration
  application, and resource isolation. Use this skill when working on Docker containers,
  bare-metal PostgreSQL instances, environment selection, instance management, port
  allocation, or any code in src/utils/environments/ or
  src/tuners/distributed/remote_environment.py.
---

# Environment Backends

The `DatabaseEnvironment` abstraction decouples tuning/evaluation logic from
the physical PostgreSQL instance management.

## Architecture

```
EnvironmentFactory.create(schema_provider, use_docker=True, base_dir, base_port=5440, ...)
    ├── use_docker=True  → DockerEnvironment (falls back to bare-metal if Docker errors)
    └── use_docker=False → BareMetalEnvironment

RemoteEnvironment (src/tuners/distributed/remote_environment.py) is a third
DatabaseEnvironment subclass used for distributed runs. It is constructed
directly, not through the factory.

DatabaseEnvironment ABC — abstract methods:
    setup_instances(num_workers, ...)   → Create/start N instances
    start_instance / stop_instance / stop_all
    restart_instance / recover_instance / rebuild_worker_instance
    verify_instances()                  → Confirm every worker accepts connections
    create_snapshot / restore_snapshot   → Baseline snapshot lifecycle
    clone_instances(source, targets)     → Copy PGDATA from one worker to others
    get_db_config(worker_id)             → DatabaseConfig (host is always 127.0.0.1)
    collect_memory_utilization / get_resource_allocations
    cleanup(remove_data=False)           → Stop + optionally delete data dirs

Concrete on the base class: initialize_schema(), collect_cache_hit_ratio(),
reset_statistics().
```

## DockerEnvironment

- Fresh containers from a resolved `postgres:<major>` image (detected from the host PG
  version; override with `PBT_POSTGRES_IMAGE` or `image_name`). `docker/eval.Dockerfile`
  is the *evaluation suite's* image, not this one.
- Port: `base_port + worker_id` (default base: 5440)
- cgroup isolation: `mem_limit`, `nano_cpus`, `cpuset_cpus`, plus blkio
  `device_read/write_bps` and `device_read/write_iops`. PGDATA is a bind-mounted host
  directory (`/pgdata/data` inside the container) — no tmpfs.
- Snapshot/clone: a throwaway container runs `cp -R /source/. /dest/` (no tar, no rsync)
- Use case: tuning loop and evaluation pipeline (publication-quality isolation)

## BareMetalEnvironment

- Local `pg_ctl` / `initdb` management
- Data dirs: `{base_dir}/{benchmark_subpath}/worker_{worker_id}/pgdata/` — default
  `base_dir` is `./.instances`; `benchmark_subpath` is e.g. `sysbench/t10_s100000` or
  `tpch/sf_1.0`
- Snapshot/clone/restore: `rsync -a --delete`
- Auto-detects `pg_ctl`/`initdb` via PATH
- Reuses existing data dirs if initialized
- Use case: PBT tuning loop (lower overhead)

## RemoteEnvironment

- Fleet-wide backend for distributed runs: proxies every lifecycle call to a per-device
  HTTP agent (`AgentClient`), fanning out concurrently over a thread pool
- One worker per device; `get_db_config` returns that device's endpoint
- Built directly with `clients`, `devices` and a `SetupRequest` template — not via
  `EnvironmentFactory.create()`
- Snapshot/clone are config-only (each device resets to its own local baseline)
- Location: `src/tuners/distributed/remote_environment.py`

## Config Application Flow

1. Validate knobs against `pg_settings` (type / bounds / context)
2. Apply ALL knobs via `ALTER SYSTEM SET` (persisted to `postgresql.auto.conf`)
3. postmaster changed → full restart via env backend `restart_instance()` (bare-metal `pg_ctl stop`+`start`; Docker restarts the container); sighup only → `SELECT pg_reload_conf()`
4. Read back via `KnobApplicator.verify()` over `pg_settings`

## Code Locations

| Component | File |
|-----------|------|
| ABC | `src/utils/environments/base.py` |
| Docker | `src/utils/environments/docker.py` |
| Bare-metal | `src/utils/environments/bare_metal.py` |
| Remote (distributed) | `src/tuners/distributed/remote_environment.py` |
| Factory | `src/utils/environments/factory.py` |
| Applicator | `src/utils/applicator.py` |

## Key Constraints

- One instance per worker — never share
- Ports `5440 + worker_id` for each of the N workers must be free — no fixed ceiling
- Docker fallback: auto bare-metal if Docker unavailable
- Always `cleanup(remove_data=...)` in `finally` — orphaned instances leak ports and disk
