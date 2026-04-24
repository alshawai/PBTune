---
description: 'Step-by-step guide for executing the Bayesian Optimization (BO) baseline runner.'
---

# Running the Bayesian Optimization (BO) Runner

> Last updated: 2026-04-25

This guide explains how to execute the Bayesian Optimization (BO) comparison runner (`src/scripts/run_bo_comparison.py`). Since the BO runner replaces the multi-agent evolutionary approach of PBT with a classical Sequential Model-Based Algorithm Configuration (SMAC) loop, it operates on a single database instance but uses the exact same underlying evaluation pipeline to ensure fairness.

---

## 1. Quick Start

Ensure your virtual environment is active and all dependencies are installed. You must have Docker running if you are relying on containerized PostgreSQL instances.

### Basic OLTP Run (Sysbench)
Run a quick test using the standard configuration profile and minimal knob tier:
```bash
python -m src.scripts.run_bo_comparison --benchmark sysbench --config rapid --tier minimal --max-evals 20
```

### Basic OLAP Run (TPC-H)
Optimize a data warehousing workload evaluating 50 sequential configurations:
```bash
python -m src.scripts.run_bo_comparison --benchmark tpch --config standard --tier core --max-evals 50
```

---

## 2. Command Line Arguments

The BO script features parity with many `main.py` (PBT tuner) flags, along with SMAC-specific overrides.

### Configuration & Search Space

| Argument | Description | Choices / Defaults |
|----------|-------------|--------------------|
| `--tier` | Defines the size/complexity of the PostgreSQL configuration space. | `minimal` (default), `core`, `standard`, `extensive` |
| `--config` | The tuning profile which determines experiment sizing like warmup times. | `rapid`, `standard` (default), `thorough`, `research`, `extreme` |

### Workload & Benchmarking

| Argument | Description | Notes |
|----------|-------------|-------|
| `--workload` | Choose the declarative workload mapping. | `oltp` (default), `olap`, `mixed` |
| `--benchmark` | Direct execution mapping for formal benchmarks. | `sysbench` or `tpch` |
| `--workload-file` | Path to a custom JSON workload definition. | Overrides `--workload` and `--benchmark`. |
| `--scale-factor` | The size of the database. | E.g., `1.0` for 1GB TPC-H |
| `--sysbench-tables` | Number of tables for Sysbench workloads. | - |
| `--sysbench-table-size`| Number of rows per Sysbench table. | - |

### BO Optimizations (SMAC Specific)

| Argument | Description | Default |
|----------|-------------|---------|
| `--max-evals` | **Crucial:** The total number of configurations SMAC will test before terminating. | `50` |
| `--initial-design-size` | Number of purely random searches before the surrogate Random Forest model takes over. | `10` |
| `--seed` | Random seed for configuration generation and SMAC repeatability. | `None` (System time) |

### Environment & Overrides
You can forcibly adapt or cleanup the testing environment using these flags:

- `--force-recreate-instances`: Tears down existing Docker containers and re-initializes fresh templates prior to starting BO tuning.
- `--skip-schema-init`: Tells the runner that the databases are already instantiated and populated, skipping the lengthy `pgbench`/`sysbench` preparation phase.
- `--cleanup-instances`: Automatically purges the PostgreSQL containers and volumes after the BO script completes. Useful for CI/CD pipelines.
- `--duration` / `--warmup`: Overrides the timing parameters inherited from the `--config` profile.

---

## 3. Output and Artifacts

Upon completion, the BO runner aggregates its execution history and mathematical trajectory into a JSON artifact. 

By default, files are emitted to:  
`results/<workload>/bo_runs/<tier>/bo_results_<timestamp>.json`

This payload matches the exact schema expected by `src/scripts/plot_bo_vs_pbt.py` and features:
- **`bo_session`**: Runner metadata (seed, evaluation counts, timestamp).
- **`best_configuration`**: The incumbent parameters and resulting score.
- **`evaluation_history`**: An array tracking all sampled parameters over time, allowing plotting of the convergence curve.
- **`system_info`**: Details on the host hardware (CPU, Memory).

## 4. Advanced Example: The "Fair Match"

To compare BO against a PBT run that lasted exactly 100 evaluations (e.g., 10 generations of 10 workers), you must ensure the BO runner evaluates the space an equivalent number of times to produce a mathematically fair comparison:

```bash
# Set max-evals to 100 to parallel a 10x10 PBT compute budget
python -m src.scripts.run_bo_comparison \
    --benchmark sysbench \
    --tier standard \
    --config thorough \
    --max-evals 100 \
    --initial-design-size 20 \
    --seed 42 \
    --cleanup-instances
```

You can then pass the output JSON to the plotting script alongside your PBT run to visually demonstrate convergence differences.