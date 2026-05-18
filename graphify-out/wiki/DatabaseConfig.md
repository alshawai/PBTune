# DatabaseConfig

> God node · 63 connections · `src/config/database.py`

**Community:** [[Database Config & Connection]]

## Connections by Relation

### calls
- [[main()]] `INFERRED`
- [[_make_environment()]] `INFERRED`
- [[get_db_config()]] `INFERRED`
- [[_make_env()]] `INFERRED`
- [[_make_worker()]] `INFERRED`
- [[._wait_for_ready()]] `INFERRED`
- [[_make_evaluator()]] `INFERRED`
- [[_make_db_config()]] `INFERRED`
- [[_make_tuner()]] `INFERRED`
- [[test_ensure_benchmark_ready_raises_if_schema_still_invalid()]] `INFERRED`
- [[_make_workload_orchestrator()]] `INFERRED`
- [[test_ensure_database_exists_handles_connection_failure_without_name_error()]] `INFERRED`
- [[.setup_worker_instances()]] `INFERRED`
- [[test_cleanup_constructs_manager_and_stops_instances()]] `INFERRED`
- [[patch_pbttuner_knob_loader()]] `INFERRED`

### contains
- [[database.py]] `EXTRACTED`

### method
- [[.to_dict()]] `EXTRACTED`
- [[.__repr__()]] `EXTRACTED`
- [[.get_connection_string()]] `EXTRACTED`
- [[.get_sqlalchemy_url()]] `EXTRACTED`

### rationale_for
- [[Database configuration loaded from environment variables.      This class provid]] `EXTRACTED`

### uses
- [[ComparisonRunner]] `INFERRED`
- [[DockerEnvironment]] `INFERRED`
- [[Population]] `INFERRED`
- [[WorkloadOrchestrator]] `INFERRED`
- [[Worker]] `INFERRED`
- [[SysbenchExecutor]] `INFERRED`
- [[BareMetalEnvironment]] `INFERRED`
- [[PostgreSQLKnobRetriever]] `INFERRED`
- [[BenchmarkExecutor]] `INFERRED`
- [[KnobApplicator]] `INFERRED`
- [[PopulationConfig]] `INFERRED`
- [[TestReliabilityGate]] `INFERRED`
- [[TPCHExecutor]] `INFERRED`
- [[DatabaseEnvironment]] `INFERRED`
- [[_DummyEnvironment]] `INFERRED`
- [[_HealthyBenchmarkExecutor]] `INFERRED`
- [[_ClosedConnection]] `INFERRED`
- [[WorkloadExecutor]] `INFERRED`
- [[_FailingBenchmarkExecutor]] `INFERRED`
- [[_InvalidBenchmarkExecutor]] `INFERRED`

---

*Part of the graphify knowledge wiki. See [[index]] to navigate.*