# PBT vs BO Comparison Script

## Overview

`src/scripts/pbt_vs_bo_comarison.py` compares completed PBT and BO tuning-session JSON files. It loads the full evaluation history from every run, rescales all raw metrics using one global post-hoc scoring context, then writes CSV tables and publication-ready PDF plots.

The script does not modify iteration counts, truncate runs, or enforce that inputs have identical settings. It compares exactly the files passed through `--pbt` and `--bo`.

## Usage

```bash
python -m src.scripts.pbt_vs_bo_comarison \
  --pbt results/oltp/oltp_read_write/pbt_runs/minimal/tuning_sessions/pbt_results_20260504_1825.json \
        results/oltp/oltp_read_write/pbt_runs/minimal/tuning_sessions/pbt_results_20260504_1831.json \
        results/oltp/oltp_read_write/pbt_runs/minimal/tuning_sessions/pbt_results_20260504_1836.json \
  --bo results/oltp/bo_runs/minimal/tuning_sessions/bo_results_20260504_1921.json \
       results/oltp/bo_runs/minimal/tuning_sessions/bo_results_20260504_2002.json \
       results/oltp/bo_runs/minimal/tuning_sessions/bo_results_20260504_2043.json \
  --output-dir analysis
```

## Parameters

| Parameter | Required | Description |
| --- | --- | --- |
| `--pbt` | Yes | One or more PBT tuning-session JSON files. |
| `--bo` | Yes | One or more BO tuning-session JSON files. |
| `--output-dir` | No | Directory for generated CSV and PDF files. Defaults to `analysis`. |

## Input Format

Each input file should use the tuning-session schema produced by the tuner or BO baseline writer:

- `tuning_session` - Run metadata. The first input run provides the benchmark/workload context used for global rescoring.
- `best_configuration.metrics` - Final best metrics for the run.
- `generation_history[].worker_scores[].metrics` - Evaluation metrics over time.
- `generation_history[].timestamp` - Used to estimate elapsed wall-clock time.

BO outputs are written in the same shape as PBT outputs, with one worker score per BO iteration.

## Scoring Behavior

The script pools all metrics from all PBT and BO runs, then calls `rescore_metrics_globally()`. This recalibrates normalization ranges once across the combined dataset and assigns a comparable `GlobalScore` to every evaluation.

For convergence plots, each run's score is converted to best-so-far form:

```text
GlobalScore at evaluation N = max(global score from evaluations 1..N)
```

This makes the curve represent optimization progress rather than raw per-evaluation noise.

## Output Files

All files are written to `--output-dir`.

### `comparison_summary.csv`

Aggregated method-level summary.

Columns:
- `Method` - `PBT` or `BO`
- `Mean_Best_Score` - Mean final globally rescored best score
- `StdDev_Best_Score` - Standard deviation of final best scores
- `Mean_WallClock_Time` - Mean elapsed wall-clock time at the final evaluation
- `Mean_Throughput` - Mean throughput from final best configurations
- `Mean_LatencyP95` - Mean p95 latency from final best configurations
- `Mean_MemoryUtilization` - Mean memory utilization from final best configurations
- `Trials` - Number of runs for that method

### `comparison_runs.csv`

Per-run detail table.

Columns:
- `Method` - `PBT` or `BO`
- `Seed` - Positional seed index assigned from the input order
- `SourceFile` - Input JSON path
- `BestScore` - Final globally rescored best score
- `WallClockTime` - Final elapsed wall-clock time for that run
- `Throughput` - Throughput of the final best configuration
- `LatencyP95` - p95 latency of the final best configuration
- `MemoryUtilization` - Memory utilization of the final best configuration

### `comparison_timeseries.csv`

Rescored convergence data used by the convergence PDF.

Columns:
- `Method` - `PBT` or `BO`
- `Seed` - Positional seed index assigned from the input order
- `Evaluations` - Cumulative evaluation count within the run
- `WallTimeSeconds` - Elapsed wall-clock time inferred from generation timestamps
- `GlobalScore` - Best-so-far globally rescored score

### `statistical_significance.csv`

Mann-Whitney U test result over final best scores.

Columns:
- `Test` - Statistical test name
- `PBT_N` - Number of PBT runs
- `BO_N` - Number of BO runs
- `PBT_Mean` - Mean PBT final best score
- `PBT_StdDev` - PBT score standard deviation
- `BO_Mean` - Mean BO final best score
- `BO_StdDev` - BO score standard deviation
- `U_Statistic` - Mann-Whitney U statistic
- `P_Value` - Two-sided p-value
- `Alpha` - Significance threshold, currently `0.05`
- `Significant` - Whether `P_Value < Alpha`

### `publication_ready_convergence.pdf`

Two-panel convergence plot:

- Sample efficiency: best global score versus cumulative evaluations.
- Wall-clock efficiency: best global score versus elapsed seconds.

Each method is plotted with a mean line and standard-deviation error band when multiple runs are provided.

### `publication_ready_pareto.pdf`

Scatter plot of final best configurations:

- X axis: throughput, higher is better.
- Y axis: p95 latency, lower is better.
- Hue/style: method.

### `publication_ready_resource_efficiency.pdf`

Box/strip plot of memory utilization for final best configurations, grouped by method.

## Recommended Workflow

1. Run PBT and save one or more tuning-session files.
2. Run BO with `--pbt-session` so its benchmark, workload, knob set, and evaluation budget match the selected PBT session.
3. Pass the generated PBT and BO JSON files to `pbt_vs_bo_comarison.py`.
4. Use `comparison_summary.csv` for aggregate numbers, `comparison_runs.csv` for per-run checks, and the PDFs for figures.

