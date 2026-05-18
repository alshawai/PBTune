# Metric Instrumentation

> 40 nodes · cohesion 0.08

## Key Concepts

- **.run()** (41 connections) — `src/tuner/main.py`
- **PBTTuner** (39 connections) — `src/tuner/main.py`
- **BOBaselineRunner** (17 connections) — `src/scripts/bo_baseline/runner.py`
- **._create_workload_executor()** (8 connections) — `src/tuner/main.py`
- **WorkloadFileLoader** (6 connections) — `src/tuner/benchmark/workload.py`
- **convert_numpy_types()** (6 connections) — `src/tuner/main.py`
- **._get_runtime_supported_knobs()** (6 connections) — `src/tuner/main.py`
- **._prune_unsupported_runtime_knobs()** (6 connections) — `src/tuner/main.py`
- **.run_generation()** (6 connections) — `src/tuner/main.py`
- **log_section_header()** (5 connections) — `src/utils/logger/helpers.py`
- **._build_scoring_payload()** (5 connections) — `src/tuner/main.py`
- **._build_warm_start_configs()** (5 connections) — `src/tuner/main.py`
- **.save_final_results()** (5 connections) — `src/tuner/main.py`
- **._build_smac_output_root()** (4 connections) — `src/scripts/bo_baseline/runner.py`
- **.print_final_summary()** (4 connections) — `src/tuner/main.py`
- **Save intermediate results during training** (4 connections) — `src/tuner/main.py`
- **._apply_pbt_knob_filter()** (3 connections) — `src/scripts/bo_baseline/runner.py`
- **._compute_warm_start_perturbation_factors()** (3 connections) — `src/tuner/main.py`
- **._get_stop_reason()** (3 connections) — `src/tuner/main.py`
- **Utility to load workload definitions from files.      Supports JSON and YAML for** (1 connections) — `src/tuner/benchmark/workload.py`
- **Prune knobs unavailable on runtime PostgreSQL.** (1 connections) — `src/scripts/bo_baseline/runner.py`
- **Restrict BO search to the knob names present in the reference PBT run.** (1 connections) — `src/scripts/bo_baseline/runner.py`
- **Build the SMAC output directory root under results.** (1 connections) — `src/scripts/bo_baseline/runner.py`
- **Run Bayesian Optimization tuning.          Returns         -------         Dict[** (1 connections) — `src/scripts/bo_baseline/runner.py`
- **Create appropriate workload executor based on benchmark type.** (1 connections) — `src/scripts/bo_baseline/runner.py`
- *... and 15 more nodes in this community*

## Relationships

- [[BO Baseline & Workload]] (122 shared connections)
- [[Evolution Algorithms]] (8 shared connections)
- [[Workload Orchestrator]] (5 shared connections)
- [[Cross-Module Rationale]] (5 shared connections)
- [[Benchmark Orchestrator]] (5 shared connections)
- [[BO Config & Worker]] (5 shared connections)
- [[Benchmark Executor Base]] (4 shared connections)
- [[Comparison Runner]] (4 shared connections)
- [[Sysbench Executor Tests]] (3 shared connections)
- [[Docker Manifest Tests]] (3 shared connections)
- [[Population Initialization]] (3 shared connections)
- [[Population Tests]] (3 shared connections)

## Source Files

- `src/evaluation/runner.py`
- `src/scripts/bo_baseline/runner.py`
- `src/tuner/benchmark/workload.py`
- `src/tuner/main.py`
- `src/utils/logger/helpers.py`

## Audit Trail

- EXTRACTED: 141 (72%)
- INFERRED: 56 (28%)
- AMBIGUOUS: 0 (0%)

---

*Part of the graphify knowledge wiki. See [[index]] to navigate.*