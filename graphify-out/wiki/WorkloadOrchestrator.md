# WorkloadOrchestrator

> God node · 37 connections · `src/tuner/benchmark/orchestrator.py`

**Community:** [[Benchmark Orchestrator]]

## Connections by Relation

### calls
- [[.__init__()]] `INFERRED`
- [[.run()]] `INFERRED`
- [[_make_evaluator()]] `INFERRED`
- [[_make_workload_orchestrator()]] `INFERRED`

### contains
- [[orchestrator.py]] `EXTRACTED`

### method
- [[.evaluate_worker()]] `EXTRACTED`
- [[.__repr__()]] `EXTRACTED`
- [[.connect()]] `EXTRACTED`
- [[.disconnect()]] `EXTRACTED`
- [[.apply_configuration()]] `EXTRACTED`
- [[._vacuum_after_dml()]] `EXTRACTED`
- [[.refine_workload_features_from_generation()]] `EXTRACTED`
- [[._perform_restart()]] `EXTRACTED`
- [[._refine_workload_features()]] `EXTRACTED`
- [[.collect_system_metrics()]] `EXTRACTED`
- [[._ensure_benchmark_ready()]] `EXTRACTED`
- [[._apply_reliability_gate()]] `EXTRACTED`

### rationale_for
- [[Configuration for WorkloadOrchestrator behavior.      Parameters     ----------]] `EXTRACTED`
- [[Main WorkloadOrchestrator class for workload execution and performance measureme]] `EXTRACTED`

### uses
- [[PerformanceMetrics]] `INFERRED`
- [[DatabaseConfig]] `INFERRED`
- [[PBTTuner]] `INFERRED`
- [[Worker]] `INFERRED`
- [[MetricConfig]] `INFERRED`
- [[WorkloadType]] `INFERRED`
- [[BenchmarkExecutor]] `INFERRED`
- [[KnobApplicator]] `INFERRED`
- [[TestReliabilityGate]] `INFERRED`
- [[DatabaseEnvironment]] `INFERRED`
- [[BOBaselineRunner]] `INFERRED`
- [[_HealthyBenchmarkExecutor]] `INFERRED`
- [[_ClosedConnection]] `INFERRED`
- [[WorkloadExecutor]] `INFERRED`
- [[_FailingBenchmarkExecutor]] `INFERRED`
- [[_InvalidBenchmarkExecutor]] `INFERRED`
- [[TuningMode]] `INFERRED`
- [[ApplicatorConfig]] `INFERRED`

---

*Part of the graphify knowledge wiki. See [[index]] to navigate.*