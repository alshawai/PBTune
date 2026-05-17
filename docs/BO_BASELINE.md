# Bayesian Optimization Baseline Runner

## Overview

The BO baseline runner implements a Bayesian Optimization (BO) approach to PostgreSQL configuration tuning using SMAC3 (Sequential Model-based Algorithm Configuration). This provides a controlled comparison baseline against the PBT-based tuner for academic peer review.

## Quick Start

### Basic Usage

```bash
# Match a completed PBT tuning session for stable comparison
python -m src.scripts.bo_baseline \
  --pbt-session results/oltp/oltp_read_write/pbt_runs/minimal/tuning_sessions/pbt_results_20260504_1825.json \
  --seed 42

# Minimal test (3 iterations, 10 seconds evaluation)
python -m src.scripts.bo_baseline \
  --tier minimal \
  --iterations 3 \
  --benchmark sysbench \
  --duration 10 \
  --warmup 5

# Standard tuning (50 iterations)
python -m src.scripts.bo_baseline \
  --tier core \
  --iterations 50 \
  --benchmark sysbench \
  --workload oltp \
  --sysbench-workload oltp_read_write

# Comprehensive tuning (100 iterations, TPC-H)
python -m src.scripts.bo_baseline \
  --tier standard \
  --iterations 100 \
  --benchmark tpch \
  --scale-factor 1.0
```

## Architecture

### Components

1. **config.py** - BOConfig dataclass with all tuning parameters
2. **search_space.py** - Translates KnobSpace ↔ ConfigSpace
3. **objective.py** - SMAC3-compatible objective function
4. **result_writer.py** - Serializes results in PBT-compatible JSON
5. **runner.py** - Main orchestrator (BOBaselineRunner)
6. **__main__.py** - CLI entry point

### Facade Selection

The SMAC3 facade controls the underlying surrogate model used for Bayesian Optimization. You can specify this using the `--bo-surrogate` argument:

- **Random Forest (`--bo-surrogate rf`)**: Uses `HyperparameterOptimizationFacade`. Handles high-dimensional, mixed spaces better and is robust to flat penalty regions. Includes 20% random interleaving (`ProbabilityRandomDesign`) to prevent surrogate over-exploitation. Default behavior.
- **Gaussian Process (`--bo-surrogate gp`)**: Uses `BlackBoxFacade`. Employs a Gaussian Process surrogate with Matérn 5/2 kernel and Expected Improvement acquisition function. Better for low-dimensional, continuous spaces.

Both facades use:
- `deterministic=False` — database benchmarks have inherent measurement variance; this forces SMAC to re-evaluate incumbents and prevents overconfidence.
- `SobolInitialDesign` — quasi-random initial points for uniform pilot-phase coverage of the search space.

### Pilot + Freeze Normalization

The scoring function (`feature_driven_v2`) requires normalization ranges (e.g., max TPS, min latency). Dynamically updating these ranges mid-run corrupts the surrogate model's training signal because historical cost values become stale.

The BO runner uses a **Pilot + Freeze** strategy:

1. **Pilot Phase** (first N iterations, controlled by `--range-update-interval`): SMAC's Sobol initial design evaluates diverse configurations using default fallback ranges. Raw metrics are recorded.
2. **Freeze Event** (exactly once, at the end of the pilot): `metric_config.expand_ranges_for_metrics()` calibrates normalization bounds from all pilot observations.
3. **Frozen Phase** (remaining iterations): Normalization bounds are locked. The surrogate model trains on a stable cost surface.

Post-hoc global rescoring (via `pbt_vs_bo_comparison.py`) uses the saved raw `PerformanceMetrics` to recompute scores with globally calibrated ranges, so the frozen in-run scores do not affect final comparison validity.

### Parallel BO Evaluation and Resource Equalization

The BO baseline now supports batched parallel evaluation so it can mirror the
worker count used by a reference PBT session.

- `--parallel-workers N` sets the number of PostgreSQL instances evaluated in
  parallel.
- When `--pbt-session` is provided, BO copies `num_parallel_workers` from the
  reference session unless `--parallel-workers` explicitly overrides it.
- If BO needs to derive the budget or worker count and the PBT session is
  missing `population_size`, `total_generations`, or `num_parallel_workers`, BO
  keeps the default or explicitly supplied CLI value for that setting.
- If the PBT session includes `worker_resources`, BO uses that per-worker
  resource slice for knob-range resolution instead of dividing the local host
  resources.
- The result JSON records completed `iterations`, `num_parallel_workers`,
  and `resource_equalization` so downstream comparison tools can confirm parity.
  BO does not record `population_size` because it is not population-based.

In parallel mode, BO uses SMAC3 ask-tell evaluation with a local
`ThreadPoolExecutor`, which keeps the database environment in the main process
while evaluating multiple candidates concurrently.

### Ask-Tell Execution Model

The runner uses two execution paths:

- **Sequential path (`--parallel-workers 1`)**: uses the standard
  `facade.optimize()` loop with the objective closure.
- **Parallel path (`--parallel-workers > 1`)**: uses explicit ask-tell control in
  `runner.py`.

In ask-tell mode, each batch follows this cycle:

1. `ask()` requests one `TrialInfo` per worker for the current batch.
2. Configurations are evaluated concurrently using `ThreadPoolExecutor`.
3. Each completed trial is returned to SMAC via
   `tell(trial_info, TrialValue(...))`.
4. After each batch, the surrogate is updated before the next `ask()` calls.

This design is intentional:

- The SMAC `Scenario` keeps `n_workers=1` to avoid Dask process workers.
- Parallelism is handled in-process via threads, which avoids pickling issues
  with environment objects such as Docker clients.
- Worker-local previous configuration state is tracked independently, so
  restart detection is isolated per BO worker.

## Configuration Options

### PBT Session Parity
- `--pbt-session PATH` - Reference PBT tuning-session JSON. When provided, BO copies comparable experimental settings from the PBT run:
  - `knob_tier`
  - `benchmark_name`
  - `workload_type`
  - `tuning_mode`
  - `sysbench_tables`
  - `sysbench_table_size`
  - `sysbench_workload`
  - `sysbench_duration_seconds`
  - `sysbench_warmup_seconds`
  - `tpch_scale_factor`
  - `tpch_warmup_passes`
  - knob names from `best_configuration.knobs`

When `--pbt-session` is used, `--tier` is optional. If `--iterations` is omitted, BO sets:

```text
iterations = population_size * total_generations
```

from the PBT session. Passing `--iterations` explicitly overrides this derived budget.

Example:

```bash
python -m src.scripts.bo_baseline \
  --pbt-session results/oltp/oltp_read_write/pbt_runs/minimal/tuning_sessions/pbt_results_20260504_1825.json \
  --seed 42
```

### Search Space
- `--tier {minimal|core|standard|extensive}` - Knob space tier. Required only when `--pbt-session` is not provided.

### BO Configuration
- `--iterations N` - Number of BO iterations. Defaults to `50`, or to `population_size * total_generations` when `--pbt-session` is used.
- `--seed INT` - Random seed for reproducibility (default: `42`)
- `--bo-surrogate {rf|gp}` - SMAC Surrogate model: Random Forest (`rf`) or Gaussian Process (`gp`). Default is `rf`.
- `--parallel-workers INT` - Number of parallel BO workers / PostgreSQL
  instances. Defaults to `1`, or to the PBT session's `num_parallel_workers` when
  `--pbt-session` is used.
- `--scoring-policy STR` - Custom scoring policy to use for metrics evaluation. Available options:
  - `fixed_v1`: Legacy static weights based on workload type (OLTP/OLAP/MIXED).
  - `feature_driven_v2`: Dynamic weights based on workload features and a coefficient matrix, evaluating variance, tail amplification, and DB stats.
  (default: predefined policy per workload).

### Benchmark Options
- `--benchmark {sysbench|tpch}` - Benchmark type (default: sysbench)
- `--workload {oltp|olap|mixed}` - Workload type (default: oltp)
- `--duration FLOAT` - Evaluation duration in seconds (default: 30)
- `--warmup FLOAT` - Warmup duration in seconds (default: 10)

### Sysbench-Specific
- `--sysbench-tables INT` - Number of tables (default: 4)
- `--sysbench-table-size INT` - Table size (default: 100000)
- `--sysbench-workload {oltp_read_only|oltp_read_write|oltp_write_only}` - Workload (default: oltp_read_write)

### TPC-H-Specific
- `--scale-factor FLOAT` - Scale factor (default: 1.0)
- `--tpch-warmup-passes INT` - Warmup passes (default: 1)

### Instance Options
- `--no-docker` - Use bare-metal PostgreSQL instead of Docker
- `--docker-image IMAGE` - Custom Docker image name
- `--force-recreate-instances` - Force recreate PostgreSQL instances
- `--force-recreate-baseline` - Force recreate baseline snapshot
- `--tuning-mode {offline|online|adaptive}` - Tuning mode (default: offline)

### Output Options
- `--output-dir PATH` - Output directory (default: results)
- `--verbose {DEBUG|INFO|WARNING|ERROR}` - Logging level (default: INFO)
- `--range-update-interval INT` - Pilot phase size (default: 10)

## Parameter Reference

| Parameter | Purpose | Default / Source |
| --- | --- | --- |
| `--pbt-session` | Loads a PBT tuning-session file and copies the settings needed for a stable BO comparison. | Not set |
| `--tier` | Selects the knob tier when running BO independently. | Required unless `--pbt-session` is set |
| `--iterations` | Sets the BO evaluation budget. | `50`, or PBT `population_size * total_generations` |
| `--seed` | Controls BO random seed and SMAC scenario seed. | `42` |
| `--benchmark` | Chooses `sysbench` or `tpch`. | `sysbench`, or PBT `benchmark_name` |
| `--workload` | Chooses metric scoring context: `oltp`, `olap`, or `mixed`. | `oltp`, or PBT `workload_type` |
| `--duration` | Measurement duration per evaluated configuration. | `30`, or PBT `sysbench_duration_seconds` |
| `--warmup` | Warmup duration before measurement. | `10`, or PBT `sysbench_warmup_seconds` |
| `--sysbench-tables` | Number of sysbench tables to prepare. | `4`, or PBT `sysbench_tables` |
| `--sysbench-table-size` | Rows per sysbench table. | `100000`, or PBT `sysbench_table_size` |
| `--sysbench-workload` | Sysbench script/workload. | `oltp_read_write`, or PBT `sysbench_workload` |
| `--scale-factor` | TPC-H scale factor. | `1.0`, or PBT `tpch_scale_factor` |
| `--tpch-warmup-passes` | TPC-H query warmup passes. | `1`, or PBT `tpch_warmup_passes` |
| `--no-docker` | Uses bare-metal PostgreSQL instead of Docker. | Docker enabled |
| `--docker-image` | Overrides PostgreSQL Docker image. | Environment default |
| `--force-recreate-instances` | Recreates worker database instances. | Disabled |
| `--force-recreate-baseline` | Recreates baseline snapshot state. | Disabled |
| `--tuning-mode` | Controls restart/application behavior. | `offline`, or PBT `tuning_mode` |
| `--output-dir` | Root directory for BO result files. | `results` |
| `--verbose` | Logging verbosity. | `INFO` |
| `--range-update-interval` | Pilot phase size: initial-design iterations before freezing normalization ranges. | `10` |
| `--scoring-policy` | Specific scoring policy to apply to metric evaluation (`fixed_v1` or `feature_driven_v2`). | Set by metric default |

## Output Format

Results are written to:
```
{output_dir}/{workload_type}/bo_runs/{tier}/tuning_sessions/bo_results_{timestamp}.json
```

The JSON format is strictly compatible with the evaluation pipeline and identical to the PBT schema:
- `tuning_session` - Metadata about the BO run, including optimizer, scoring policy, and version information
- `scoring_policy` / `scoring_policy_version` - Global scoring engine properties
- `normalization_metadata` / `workload_features` - Details around the environment constraints and bounds
- `best_configuration` - Best knob config (fractionally normalized `[0.0, 1.0]`) and score found, including full `score_breakdown`
- `worker_resources` - Hardware constraints
- `generation_history` - Per-iteration convergence data and metric breakdown (`wall_clock_seconds`, `generation_elapsed_seconds`)
- `system_info` - System snapshot

The BO CLI `--seed` value is recorded as `tuning_session.seed` in the result
JSON so downstream comparison tools can report the actual tuning seed rather
than inferring one from file order.

When `--pbt-session` is used, `tuning_session` also records:
- `reference_pbt_session` - Path to the PBT session used as the reference
- `reference_pbt_knobs` - Knob names copied from `best_configuration.knobs`
- `num_parallel_workers` - Parallel BO worker count used for the run
- `resource_equalization` - Whether BO used the reference PBT worker resource
  slice

## Multi-Seed Evaluation

For statistical significance, run with multiple seeds:

```bash
for seed in 42 123 456 789 1024; do
  python -m src.scripts.bo_baseline \
    --pbt-session results/oltp/oltp_read_write/pbt_runs/minimal/tuning_sessions/pbt_results_20260504_1825.json \
    --seed $seed
done
```

Then use the evaluation pipeline to compare:

```bash
python -m src.evaluation \
  --session results/oltp/bo_runs/core/tuning_sessions/bo_results_*.json
```

## Normalization Strategy

During tuning, BO uses a **Pilot + Freeze** approach: the first N iterations (Sobol initial design) run with default fallback ranges, then `metric_config.expand_ranges_for_metrics()` is called exactly once to calibrate bounds from pilot observations. Ranges are frozen for the remainder of the run to preserve surrogate model integrity.

PBT uses rolling adaptive normalization (expanding ranges every generation). This difference is intentional — BO requires a stable cost surface for its surrogate, while PBT's population-based approach is robust to shifting targets.

For cross-method comparison, use `rescore_metrics_globally()` from `src/utils/rescoring.py` to pool raw metrics from both runs and rescore with a single globally calibrated `MetricConfig`.

## Testing

Run the test suite:

```bash
# All tests
python -m pytest tests/test_bo_baseline.py -v

# Specific test class
python -m pytest tests/test_bo_baseline.py::TestSearchSpaceTranslation -v

# Specific test
python -m pytest tests/test_bo_baseline.py::TestSearchSpaceTranslation::test_build_configspace_minimal -v
```

## Implementation Notes

### Search Space Translation

- Integer knobs with log scale: min clamped to 1
- Float knobs with log scale: min clamped to 1e-9
- Degenerate ranges (min == max): converted to Constant parameters
- Default values: validated to be within bounds, set to None if out of range

### Objective Function

- Cost = 100 - score (SMAC minimizes cost)
- Failure penalties:
  - Timeout: cost = 99.0
  - Dead instance: cost = 99.5
  - Unexpected error: cost = 100.0
- Restart detection: checks if any restart-required knobs changed
- Pilot+Freeze normalization: ranges calibrated once after initial design, then locked

### Result Serialization

- Compatible with `load_tuning_session()` from evaluation pipeline
- Generation history uses same schema as PBT (single-element worker arrays)
- Best configuration extracted from SMAC's incumbent
- Convergence tracking via generation_history

## Troubleshooting

### Import Errors
- Ensure ConfigSpace is installed: `pip install ConfigSpace>=1.1.0`
- Ensure SMAC3 is installed: `pip install smac>=2.2.0`
- Note: Import is `from ConfigSpace` (capital C), not `from configspace`

### Connection Errors
- Verify PostgreSQL instances are running on ports 5440+
- Check `.env` file for database credentials
- Use `--force-recreate-instances` to reset state

### Memory Issues
- Reduce `--iterations` for lower memory usage
- Use `--tier minimal` for quick testing
- Reduce `--duration` for faster iterations

### Long Runtimes
- Reduce `--duration` parameter for faster evaluation
- Use `--tier minimal` for quick prototyping
- Increase `--iterations` only after validating with smaller runs
