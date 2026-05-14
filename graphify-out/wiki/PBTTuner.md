# PBTTuner

> God node · 39 connections · `src/tuner/main.py`

**Community:** [[BO Baseline & Workload]]

## Connections by Relation

### calls
- [[main()]] `EXTRACTED`
- [[test_warm_start_provenance()]] `INFERRED`
- [[test_warm_start_cross_tier_minimal_to_core()]] `INFERRED`
- [[test_warm_start_cross_tier_core_to_minimal()]] `INFERRED`
- [[test_warm_start_invalid_absolute_values()]] `INFERRED`
- [[test_warm_start_accepts_tuning_session_results_json()]] `INFERRED`
- [[test_warm_start_rejects_malformed_tuning_session_json()]] `INFERRED`
- [[test_warm_start_deterministic_seed()]] `INFERRED`
- [[test_warm_start_graduated_perturbation()]] `INFERRED`

### contains
- [[main.py]] `EXTRACTED`

### method
- [[.__init__()]] `EXTRACTED`
- [[.run()]] `EXTRACTED`
- [[.evaluate_worker()]] `EXTRACTED`
- [[._create_workload_executor()]] `EXTRACTED`
- [[._get_runtime_supported_knobs()]] `EXTRACTED`
- [[._prune_unsupported_runtime_knobs()]] `EXTRACTED`
- [[.run_generation()]] `EXTRACTED`
- [[._build_scoring_payload()]] `EXTRACTED`
- [[.save_final_results()]] `EXTRACTED`
- [[._build_warm_start_configs()]] `EXTRACTED`
- [[._build_output_dir()]] `EXTRACTED`
- [[._build_failure_result()]] `EXTRACTED`
- [[Save intermediate results during training]] `EXTRACTED`
- [[.print_final_summary()]] `EXTRACTED`
- [[._normalize_snapshot_identifier()]] `EXTRACTED`
- [[._get_stop_reason()]] `EXTRACTED`
- [[._compute_warm_start_perturbation_factors()]] `EXTRACTED`

### rationale_for
- [[Main PBT Tuner application class.      Orchestrates the complete tuning workflow]] `EXTRACTED`

### uses
- [[PerformanceMetrics]] `INFERRED`
- [[WorkloadFeatureExtractor]] `INFERRED`
- [[Population]] `INFERRED`
- [[WorkloadOrchestrator]] `INFERRED`
- [[Worker]] `INFERRED`
- [[SysbenchExecutor]] `INFERRED`
- [[WorkloadType]] `INFERRED`
- [[PopulationConfig]] `INFERRED`
- [[TPCHExecutor]] `INFERRED`
- [[TuningMode]] `INFERRED`
- [[WorkloadFileLoader]] `INFERRED`

---

*Part of the graphify knowledge wiki. See [[index]] to navigate.*