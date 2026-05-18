# Bare Metal Environment

> 30 nodes · cohesion 0.09

## Key Concepts

- **main()** (61 connections) — `src/tuner/main.py`
- **Analyzer** (13 connections) — `src/scripts/pbt_vs_bo_comarison.py`
- **.apply_global_rescoring()** (5 connections) — `src/scripts/pbt_vs_bo_comarison.py`
- **._build_timeseries_df()** (4 connections) — `src/scripts/pbt_vs_bo_comarison.py`
- **.export_timeseries()** (4 connections) — `src/scripts/pbt_vs_bo_comarison.py`
- **.generate_summary_table()** (4 connections) — `src/scripts/pbt_vs_bo_comarison.py`
- **.plot_convergence()** (4 connections) — `src/scripts/pbt_vs_bo_comarison.py`
- **.plot_pareto_front()** (3 connections) — `src/scripts/pbt_vs_bo_comarison.py`
- **.plot_resource_efficiency()** (3 connections) — `src/scripts/pbt_vs_bo_comarison.py`
- **.statistical_significance_test()** (3 connections) — `src/scripts/pbt_vs_bo_comarison.py`
- **.get_all_metrics()** (3 connections) — `src/scripts/pbt_vs_bo_comarison.py`
- **cleanup_instances.py** (3 connections) — `src/scripts/cleanup_instances.py`
- **analyze_knobs.py** (2 connections) — `src/scripts/analyze_knobs.py`
- **CLI entry point for the evaluate_tuning module.      Args:         argv: Argumen** (1 connections) — `src/evaluation/__main__.py`
- **PostgreSQL Knobs Analysis Script =================================  Comprehensiv** (1 connections) — `src/scripts/analyze_knobs.py`
- **Demonstrate all knobs retrieval and analysis.** (1 connections) — `src/scripts/analyze_knobs.py`
- **Cleanup script for PostgreSQL instances  Stops all running instances and optiona** (1 connections) — `src/scripts/cleanup_instances.py`
- **Main entry point for cleanup script.** (1 connections) — `src/scripts/cleanup_instances.py`
- **Returns all metric objects (including the final best) for global pooling.** (1 connections) — `src/scripts/pbt_vs_bo_comarison.py`
- **Manages cross-method analysis, rescoring, and visualization.** (1 connections) — `src/scripts/pbt_vs_bo_comarison.py`
- **Pools all metrics across methods/seeds and applies global rescoring.** (1 connections) — `src/scripts/pbt_vs_bo_comarison.py`
- **Converts evaluation timelines into a flat DataFrame for Seaborn lineplots.** (1 connections) — `src/scripts/pbt_vs_bo_comarison.py`
- **Generates convergence plots for Sample and Wall-Clock Efficiency.** (1 connections) — `src/scripts/pbt_vs_bo_comarison.py`
- **Creates a scatter plot comparing Throughput and Latency of the final best reps.** (1 connections) — `src/scripts/pbt_vs_bo_comarison.py`
- **Generates a boxplot showing Memory Utilization of final best configs.** (1 connections) — `src/scripts/pbt_vs_bo_comarison.py`
- *... and 5 more nodes in this community*

## Relationships

- [[Cleanup Scripts]] (50 shared connections)
- [[Docker Manifest Tests]] (33 shared connections)
- [[PBT vs BO Comparison]] (4 shared connections)
- [[Instance Management]] (4 shared connections)
- [[Worker Scoring Tests]] (4 shared connections)
- [[Connection Reuse]] (3 shared connections)
- [[Knob Validation Tests]] (3 shared connections)
- [[BO Baseline & Workload]] (3 shared connections)
- [[BO Config & Worker]] (2 shared connections)
- [[Benchmark Executor Base]] (2 shared connections)
- [[Comparison Runner]] (2 shared connections)
- [[Evolution Tests]] (2 shared connections)

## Source Files

- `src/evaluation/__main__.py`
- `src/scripts/analyze_knobs.py`
- `src/scripts/cleanup_instances.py`
- `src/scripts/pbt_vs_bo_comarison.py`
- `src/scripts/setup_database.py`
- `src/tuner/main.py`

## Audit Trail

- EXTRACTED: 109 (84%)
- INFERRED: 20 (16%)
- AMBIGUOUS: 0 (0%)

---

*Part of the graphify knowledge wiki. See [[index]] to navigate.*