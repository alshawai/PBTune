# ComparisonRunner

> God node · 58 connections · `src/evaluation/runner.py`

**Community:** [[Comparison Runner]]

## Connections by Relation

### calls
- [[main()]] `INFERRED`
- [[.test_hardware_relative_knobs_are_resolved_from_fractions()]] `INFERRED`
- [[.test_session_values_used_when_cli_omits()]] `INFERRED`
- [[.test_cli_values_override_session()]] `INFERRED`
- [[.test_cli_sysbench_workload_overrides_session()]] `INFERRED`
- [[.test_session_sysbench_workload_used_when_cli_omits()]] `INFERRED`
- [[.test_preflight_raises_with_build_hint_when_image_missing()]] `INFERRED`
- [[.test_preflight_accepts_image_after_successful_pull()]] `INFERRED`
- [[.test_default_output_dir_uses_workload_type()]] `INFERRED`
- [[.test_metadata_tier_preferred_over_path_tier()]] `INFERRED`
- [[.test_metadata_tier_field_supported()]] `INFERRED`
- [[.test_unknown_workload_falls_back_to_mixed()]] `INFERRED`
- [[.test_default_output_dir_uses_explicit_sysbench_workload()]] `INFERRED`
- [[.test_tpch_output_dir_unchanged()]] `INFERRED`
- [[.test_custom_output_dir_is_used_as_is()]] `INFERRED`
- [[.test_preflight_noop_when_docker_disabled()]] `INFERRED`
- [[.test_log_output_path_uses_logs_subdir()]] `INFERRED`

### contains
- [[runner.py]] `EXTRACTED`

### method
- [[.__init__()]] `EXTRACTED`
- [[.run()]] `EXTRACTED`
- [[._run_single()]] `EXTRACTED`
- [[._resolve_output_dir()]] `EXTRACTED`
- [[._create_executor()]] `EXTRACTED`
- [[._resolve_tier_slug_from_session()]] `EXTRACTED`
- [[._validate_docker_prerequisites()]] `EXTRACTED`
- [[._resolve_effective_benchmark_params()]] `EXTRACTED`
- [[._resolve_sysbench_workload()]] `EXTRACTED`
- [[._resolve_tuned_knobs()]] `EXTRACTED`
- [[._save_result()]] `EXTRACTED`
- [[._run_paired_comparisons()]] `EXTRACTED`
- [[._build_environment()]] `EXTRACTED`
- [[._resolve_log_output_path()]] `EXTRACTED`
- [[._resolve_tier_slug()]] `EXTRACTED`
- [[._print_summary()]] `EXTRACTED`

### rationale_for
- [[Orchestrates the full default-vs-tuned benchmark comparison.      Args:]] `EXTRACTED`

### uses
- [[PerformanceMetrics]] `INFERRED`
- [[DatabaseConfig]] `INFERRED`
- [[WorkerResources]] `INFERRED`
- [[SysbenchExecutor]] `INFERRED`
- [[ComparisonConfig]] `INFERRED`
- [[TestComputeComparisonStatistics]] `INFERRED`
- [[BenchmarkExecutor]] `INFERRED`
- [[KnobApplicator]] `INFERRED`
- [[TestLoadTuningSession]] `INFERRED`
- [[TestStatisticalPrimitives]] `INFERRED`
- [[TuningSessionData]] `INFERRED`
- [[TPCHExecutor]] `INFERRED`
- [[TestOutputPathResolution]] `INFERRED`
- [[TestCLI]] `INFERRED`
- [[ComparisonResult]] `INFERRED`
- [[RunResult]] `INFERRED`
- [[TestBenchmarkParameterResolution]] `INFERRED`
- [[DockerEnvironmentError]] `INFERRED`
- [[TestRunnerHelpers]] `INFERRED`
- [[TestDockerPrerequisites]] `INFERRED`

---

*Part of the graphify knowledge wiki. See [[index]] to navigate.*