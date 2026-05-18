# Population

> God node · 40 connections · `src/tuner/core/population.py`

**Community:** [[PBT Worker Core]]

## Connections by Relation

### calls
- [[.__init__()]] `INFERRED`
- [[test_finalize_scores_grounds_best_to_current()]] `INFERRED`
- [[test_finalize_scores_overwrites_best_if_worse()]] `INFERRED`
- [[test_finalize_scores_always_rescores_workers()]] `INFERRED`
- [[test_record_generation_not_converged_after_all_dead_resample()]] `INFERRED`
- [[test_train_generation_rebuilds_worker_when_snapshot_restore_fails()]] `INFERRED`
- [[test_train_generation_raises_when_snapshot_restore_and_rebuild_fail()]] `INFERRED`
- [[test_saturation_detection_expands_ranges_for_high_latency_low_throughput()]] `INFERRED`
- [[test_warm_start_seeds_workers_partial()]] `INFERRED`
- [[test_warm_start_seeds_workers_full()]] `INFERRED`
- [[test_should_stop_ignores_no_improvement_when_disabled()]] `INFERRED`

### contains
- [[PopulationConfig]] `EXTRACTED`
- [[GenerationResult]] `EXTRACTED`
- [[_config_change_ratio()]] `EXTRACTED`
- [[_invoke_optional_worker_callback()]] `EXTRACTED`

### method
- [[.__repr__()]] `EXTRACTED`
- [[.train_generation()]] `EXTRACTED`
- [[.record_generation()]] `EXTRACTED`
- [[.rescue_dead_workers()]] `EXTRACTED`
- [[.get_population_summary()]] `EXTRACTED`
- [[.setup_worker_instances()]] `EXTRACTED`
- [[.evaluate_generation()]] `EXTRACTED`
- [[._choose_diverse_resample_config()]] `EXTRACTED`
- [[.exploit_and_explore()]] `EXTRACTED`
- [[.initialize()]] `EXTRACTED`
- [[.update_metric_ranges_if_needed()]] `EXTRACTED`
- [[._finalize_scores()]] `EXTRACTED`
- [[.get_best_configuration()]] `EXTRACTED`
- [[.setup_snapshots()]] `EXTRACTED`
- [[.should_stop()]] `EXTRACTED`

### rationale_for
- [[Population Class for Population Based Training (PBT) ===========================]] `EXTRACTED`
- [[Manages a population of Workers for Population Based Training.      The Populati]] `EXTRACTED`

### uses
- [[PerformanceMetrics]] `INFERRED`
- [[DatabaseConfig]] `INFERRED`
- [[PBTTuner]] `INFERRED`
- [[Worker]] `INFERRED`
- [[KnobSpace]] `INFERRED`
- [[_HealthyBenchmarkExecutor]] `INFERRED`
- [[_ClosedConnection]] `INFERRED`
- [[_MetricConfigStub]] `INFERRED`

---

*Part of the graphify knowledge wiki. See [[index]] to navigate.*