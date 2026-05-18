# DockerEnvironment

> God node · 49 connections · `src/utils/environments/docker.py`

**Community:** [[TPC-H Query Executor]]

## Connections by Relation

### calls
- [[create()]] `INFERRED`

### contains
- [[docker.py]] `EXTRACTED`

### method
- [[.__init__()]] `EXTRACTED`
- [[setup_instances()]] `EXTRACTED`
- [[restore_snapshot()]] `EXTRACTED`
- [[._container_name()]] `EXTRACTED`
- [[get_db_config()]] `EXTRACTED`
- [[stop_instance()]] `EXTRACTED`
- [[create_snapshot()]] `EXTRACTED`
- [[start_instance()]] `EXTRACTED`
- [[restart_instance()]] `EXTRACTED`
- [[._wait_for_ready()]] `EXTRACTED`
- [[stop_all()]] `EXTRACTED`
- [[collect_memory_utilization()]] `EXTRACTED`
- [[._launch_worker_container()]] `EXTRACTED`
- [[.rebuild_worker_instance()]] `EXTRACTED`
- [[recover_instance()]] `EXTRACTED`
- [[verify_instances()]] `EXTRACTED`
- [[._default_snapshot_id()]] `EXTRACTED`
- [[._remove_baseline_snapshot()]] `EXTRACTED`
- [[._remove_worker_container()]] `EXTRACTED`
- [[.snapshot_exists()]] `EXTRACTED`

### rationale_for
- [[Docker-backed PostgreSQL environment supporting multi-worker parallelism.      C]] `EXTRACTED`

### uses
- [[DatabaseConfig]] `INFERRED`
- [[BenchmarkExecutor]] `INFERRED`
- [[TPCHExecutor]] `INFERRED`
- [[DatabaseEnvironment]] `INFERRED`
- [[InstanceConfig]] `INFERRED`
- [[_DummySchemaProvider]] `INFERRED`
- [[EnvironmentFactory]] `INFERRED`
- [[_SysbenchLikeSchemaProvider]] `INFERRED`

---

*Part of the graphify knowledge wiki. See [[index]] to navigate.*