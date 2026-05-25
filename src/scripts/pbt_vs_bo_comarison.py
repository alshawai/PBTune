#!/usr/bin/env python3
"""
pbt_vs_bo_comparison.py

Analyzes and compares the results of Population-Based Training (PBT) and
Bayesian Optimization (BO) for PostgreSQL auto-tuning. Supports dynamic
global rescoring, timeseries alignment for artifact-free convergence plots,
statistical significance testing, and generates publication-ready plots.
"""

from __future__ import annotations

import json
import logging
from dataclasses import fields
from pathlib import Path
from typing import List, Optional

from dateutil import parser as dateutil_parser
import matplotlib.pyplot as plt
import numpy as np
import pandas as pd
import seaborn as sns
from scipy import stats

from src.utils.metrics import PerformanceMetrics
from src.utils.rescoring import rescore_metrics_globally

# Configure academic plotting aesthetics
sns.set_theme(style="whitegrid", context="paper", font_scale=1.2)
plt.rcParams.update(
    {
        "font.family": "serif",
        "figure.figsize": (8, 6),
        "axes.titlesize": 16,
        "axes.labelsize": 14,
        "legend.fontsize": 12,
        "xtick.labelsize": 11,
        "ytick.labelsize": 11,
    }
)

logger = logging.getLogger(__name__)
METRIC_FIELD_NAMES = {field.name for field in fields(PerformanceMetrics)}

METHOD_COLORS = {"PBT": "C0", "BO": "C1"}


def align_timeseries_to_grid(
    df: pd.DataFrame,
    resolution_hz: float = 2.0,
    time_col: str = "WallTimeSeconds",
    score_col: str = "GlobalScore",
    method_col: str = "Method",
    seed_col: str = "Seed",
) -> pd.DataFrame:
    """Resample every (Method, Seed) trajectory onto a shared uniform time grid.

    Because each run logs evaluations at slightly different continuous
    timestamps, direct aggregation across seeds produces "sawtooth" artifacts
    in the mean line.  This function fixes the problem by:

    1. Building a shared, evenly-spaced time grid from t=0 to the global
       maximum wall-clock time.
    2. For each (Method, Seed) group, using :func:`numpy.searchsorted` with
       ``side='right'`` to perform a *forward-fill* (step-function)
       interpolation: the score at any grid point equals the score at the
       most-recent evaluation that occurred at or before that time.
    3. Grid points that fall *before* the first evaluation of a run are
       assigned ``NaN`` so they are excluded from aggregation.

    Parameters
    ----------
    df : pd.DataFrame
        Flat DataFrame with at least the four columns listed below.
    resolution_hz : float
        Number of blocks per second (default 2.0, for half-second resolution).
    time_col, score_col, method_col, seed_col : str
        Column names (defaults match the project convention).

    Returns
    -------
    pd.DataFrame
        New DataFrame with columns ``[method_col, seed_col, time_col, score_col]``
        where ``time_col`` values are aligned to the shared grid.
    """
    t_max = df[time_col].max()
    # Ensure at least 2 blocks so linspace works correctly even for very short runs
    num_blocks = max(2, int(t_max * resolution_hz))
    time_grid = np.linspace(0.0, t_max, num_blocks)

    aligned_rows: list[dict] = []

    for (method, seed), group in df.groupby([method_col, seed_col]):
        group_sorted = group.sort_values(time_col)
        times = group_sorted[time_col].to_numpy(dtype=float)
        scores = group_sorted[score_col].to_numpy()

        # searchsorted('right') gives the index of the first element > grid_t,
        # so (idx - 1) is the last evaluation at or before grid_t.
        indices = [
            int(np.searchsorted(times, grid_t, side="right") - 1)
            for grid_t in time_grid
        ]

        for grid_t, idx in zip(time_grid, indices, strict=True):
            if idx < 0:
                # Grid point is before the run's first evaluation → skip
                score = np.nan
            else:
                score = scores[idx]

            aligned_rows.append(
                {
                    method_col: method,
                    seed_col: seed,
                    time_col: grid_t,
                    score_col: score,
                }
            )

    return pd.DataFrame(aligned_rows)


class EvaluationPoint:
    """Represents a single evaluation point during tuning."""

    def __init__(self, metrics_dict: dict, wall_time: float, evals_so_far: int):
        self.wall_time = wall_time
        self.evals_so_far = evals_so_far
        self.global_score: Optional[float] = None
        # Safely instantiate PerformanceMetrics, ignoring unknown keys
        valid_keys = {k: v for k, v in metrics_dict.items() if k in METRIC_FIELD_NAMES}
        self.metrics = PerformanceMetrics(**valid_keys)


class TuningRun:
    """Parses and encapsulates the data of a single tuning run (one seed)."""

    def __init__(self, filepath: Path, method: str):
        self.filepath = filepath
        self.method = method.upper()
        self.seed: Optional[int] = None
        self.evaluations: List[EvaluationPoint] = []
        self.best_config_metrics: Optional[PerformanceMetrics] = None
        self.best_global_score: Optional[float] = None

        self.workload: str = "mixed"
        self.benchmark: str = "unknown"

        self._parse_json()

    def _parse_json(self) -> None:
        """Loads JSON and parses the sequence of evaluations."""
        with open(self.filepath, "r", encoding="utf-8") as f:
            data = json.load(f)

        session = data.get("tuning_session", {})
        self.seed = self._extract_seed(session)
        self.workload = session.get("workload_type", session.get("workload", "mixed"))
        self.benchmark = session.get(
            "benchmark_name", session.get("benchmark", "unknown")
        )
        if self.seed is None:
            logger.warning(
                "No seed metadata found in %s tuning session: %s",
                self.method,
                self.filepath,
            )

        best_cfg = data.get("best_configuration", {})
        valid_keys = {
            k: v
            for k, v in best_cfg.get("metrics", {}).items()
            if k in METRIC_FIELD_NAMES
        }
        self.best_config_metrics = PerformanceMetrics(**valid_keys)

        history = data.get("generation_history", [])
        evals_so_far = 0
        start_time = None

        for gen in history:
            # Parse timestamp for wall time calculation
            ts_str = gen.get("timestamp")
            if ts_str:
                current_time = dateutil_parser.isoparse(ts_str)
                if start_time is None:
                    # Give it a tiny offset (e.g. 20 seconds) for the very first step
                    start_time = current_time - pd.Timedelta(seconds=20)
                wall_time = (current_time - start_time).total_seconds()
            else:
                wall_time = 0.0

            workers = gen.get("worker_scores", [])
            for worker in workers:
                evals_so_far += 1
                self.evaluations.append(
                    EvaluationPoint(worker.get("metrics", {}), wall_time, evals_so_far)
                )

    @staticmethod
    def _extract_seed(session: dict) -> Optional[int]:
        """Return the tuning-session seed when it is persisted."""
        for key in ("seed", "random_seed"):
            value = session.get(key)
            if value is None:
                continue
            try:
                return int(value)
            except (TypeError, ValueError):
                logger.warning("Ignoring non-integer %s metadata: %r", key, value)
                return None
        return None

    def get_all_metrics(self) -> List[PerformanceMetrics]:
        """Returns all metric objects (including the final best) for global pooling."""
        metrics_list = [eval_pt.metrics for eval_pt in self.evaluations]
        if self.best_config_metrics:
            metrics_list.append(self.best_config_metrics)
        return metrics_list


class Analyzer:
    """Manages cross-method analysis, rescoring, and visualization."""

    def __init__(
        self,
        pbt_runs: List[TuningRun],
        bo_runs: List[TuningRun],
        output_dir: Path,
    ):
        self.pbt_runs = pbt_runs
        self.bo_runs = bo_runs
        self.all_runs = pbt_runs + bo_runs
        self.output_dir = output_dir
        self.output_dir.mkdir(parents=True, exist_ok=True)

        if not self.all_runs:
            raise ValueError("No runs provided for analysis.")

        # Determine benchmark context for rescoring
        self.benchmark = self.all_runs[0].benchmark
        self.workload = self.all_runs[0].workload

    def apply_global_rescoring(self) -> None:
        """Pools all metrics across methods/seeds and applies global rescoring."""
        logger.info("Extracting raw metrics across all runs for global rescoring...")
        pooled_metrics = []
        for run in self.all_runs:
            pooled_metrics.extend(run.get_all_metrics())

        logger.info("Applying rescore_metrics_globally...")
        _metric_cfg, rescored_values, metadata = rescore_metrics_globally(
            pooled_metrics, benchmark=self.benchmark, workload=self.workload
        )

        # Distribute the scores back to their respective objects
        score_idx = 0
        for run in self.all_runs:
            for eval_pt in run.evaluations:
                # Update current best score seen so far to emulate learning progress
                current_score = rescored_values[score_idx]
                if eval_pt.evals_so_far == 1:
                    eval_pt.global_score = current_score
                else:
                    prev_best = run.evaluations[eval_pt.evals_so_far - 2].global_score
                    if prev_best is None:
                        eval_pt.global_score = current_score
                    elif current_score is None:
                        eval_pt.global_score = prev_best
                    else:
                        eval_pt.global_score = max(prev_best, current_score)
                score_idx += 1

            if run.best_config_metrics:
                run.best_global_score = rescored_values[score_idx]
                score_idx += 1

        logger.info("Global rescoring complete. Metadata: %s", metadata)

    def _build_timeseries_df(self) -> pd.DataFrame:
        """Converts evaluation timelines into a flat DataFrame for Seaborn lineplots."""
        rows = []
        for run in self.all_runs:
            for eval_pt in run.evaluations:
                rows.append(
                    {
                        "Method": run.method,
                        "Seed": run.seed,
                        "Evaluations": eval_pt.evals_so_far,
                        "WallTimeSeconds": eval_pt.wall_time,
                        "GlobalScore": eval_pt.global_score,
                    }
                )
        return pd.DataFrame(rows)

    def plot_convergence(self) -> None:
        """Generates convergence plots for Sample and Wall-Clock Efficiency.

        The Wall-Clock Efficiency plot automatically aligns timeseries to a
        shared uniform grid using a step-function interpolation to prevent
        'sawtooth' aggregation artifacts caused by mismatched logging
        timestamps across different evaluation seeds.
        """
        df = self._build_timeseries_df()

        fig, (ax1, ax2) = plt.subplots(1, 2, figsize=(16, 6))

        # 1. Sample Efficiency
        sns.lineplot(
            data=df,
            x="Evaluations",
            y="GlobalScore",
            hue="Method",
            palette=METHOD_COLORS,
            errorbar="sd",
            ax=ax1,
            marker="o",
            markersize=4,
        )
        ax1.set_title("Sample Efficiency\n(Global Score vs. Total Evaluations)")
        ax1.set_xlabel("Cumulative Evaluations")
        ax1.set_ylabel("Max Global Composite Score")

        # 2. Wall-Clock Efficiency (aligned to a shared time grid)
        df_aligned = align_timeseries_to_grid(df)
        sns.lineplot(
            data=df_aligned,
            x="WallTimeSeconds",
            y="GlobalScore",
            hue="Method",
            palette=METHOD_COLORS,
            errorbar="sd",
            ax=ax2,
        )
        ax2.set_title("Wall-Clock Efficiency\n(Global Score vs. Elapsed Time)")
        ax2.set_xlabel("Elapsed Wall-Clock Time (s)")
        ax2.set_ylabel("Max Global Composite Score")

        plt.tight_layout()
        output_file = self.output_dir / "publication_ready_convergence.pdf"
        plt.savefig(output_file, dpi=300, bbox_inches="tight")
        plt.close(fig)
        logger.info("Saved %s", output_file)

    def plot_pareto_front(self) -> None:
        """Creates a scatter plot comparing Throughput and Latency of the final best reps."""
        rows = []
        for run in self.all_runs:
            if run.best_config_metrics:
                rows.append(
                    {
                        "Method": run.method,
                        "Seed": run.seed,
                        "Throughput": run.best_config_metrics.throughput,
                        "Latency (p95)": run.best_config_metrics.latency_p95,
                    }
                )

        if not rows:
            logger.warning("No best configuration metrics found for Pareto plot.")
            return

        df = pd.DataFrame(rows)
        plt.figure()
        sns.scatterplot(
            data=df,
            x="Throughput",
            y="Latency (p95)",
            hue="Method",
            style="Method",
            palette=METHOD_COLORS,
            s=150,
            alpha=0.8,
            edgecolor="black",
        )

        plt.title("Pareto Front of Best Configurations")
        plt.xlabel("Throughput (Queries/sec) $\\uparrow$")
        plt.ylabel("95th Percentile Latency (ms) $\\downarrow$")

        plt.tight_layout()
        output_file = self.output_dir / "publication_ready_pareto.pdf"
        plt.savefig(output_file, dpi=300, bbox_inches="tight")
        plt.close()
        logger.info("Saved %s", output_file)

    def plot_resource_efficiency(self) -> None:
        """Generates a boxplot showing Memory Utilization of final best configs."""
        rows = []
        for run in self.all_runs:
            if run.best_config_metrics:
                rows.append(
                    {
                        "Method": run.method,
                        "Memory Utilization": run.best_config_metrics.memory_utilization,
                    }
                )

        if not rows:
            return

        df = pd.DataFrame(rows)
        plt.figure(figsize=(6, 6))
        sns.boxplot(
            data=df,
            x="Method",
            y="Memory Utilization",
            hue="Method",
            palette=METHOD_COLORS,
            width=0.4,
            showmeans=True,
        )
        sns.stripplot(
            data=df,
            x="Method",
            y="Memory Utilization",
            color="black",
            alpha=0.6,
            jitter=True,
        )

        plt.title("Resource Efficiency\n(Memory Utilization of Best Configs)")
        plt.ylabel("Memory Utilization (Fraction)")

        plt.tight_layout()
        output_file = self.output_dir / "publication_ready_resource_efficiency.pdf"
        plt.savefig(output_file, dpi=300, bbox_inches="tight")
        plt.close()
        logger.info("Saved %s", output_file)

    def statistical_significance_test(self, alpha: float = 0.05) -> pd.DataFrame:
        """Runs the Mann-Whitney U test on final best scores."""
        pbt_scores = [
            r.best_global_score
            for r in self.pbt_runs
            if r.best_global_score is not None
        ]
        bo_scores = [
            r.best_global_score for r in self.bo_runs if r.best_global_score is not None
        ]

        if not pbt_scores or not bo_scores:
            logger.warning(
                "Skipping statistical test because one method has no scored runs "
                "(PBT=%d, BO=%d).",
                len(pbt_scores),
                len(bo_scores),
            )
            return pd.DataFrame()

        stat, p_value = stats.mannwhitneyu(
            pbt_scores, bo_scores, alternative="two-sided"
        )

        logger.info("=== Statistical Significance (Mann-Whitney U) ===")
        logger.info(
            "PBT best scores (N=%d): %.4f ± %.4f",
            len(pbt_scores),
            np.mean(pbt_scores),
            np.std(pbt_scores),
        )
        logger.info(
            "BO best scores (N=%d): %.4f ± %.4f",
            len(bo_scores),
            np.mean(bo_scores),
            np.std(bo_scores),
        )
        logger.info("U-Statistic: %s, p-value: %.5f", stat, p_value)

        if p_value < alpha:
            winner = "PBT" if np.mean(pbt_scores) > np.mean(bo_scores) else "BO"
            logger.info(
                "Result: SIGNIFICANT at α=%s. Method %s is superior.",
                alpha,
                winner,
            )
        else:
            logger.info("Result: NOT SIGNIFICANT at α=%s.", alpha)

        result = pd.DataFrame(
            [
                {
                    "Test": "Mann-Whitney U",
                    "PBT_N": len(pbt_scores),
                    "BO_N": len(bo_scores),
                    "PBT_Mean": np.mean(pbt_scores),
                    "PBT_StdDev": np.std(pbt_scores),
                    "BO_Mean": np.mean(bo_scores),
                    "BO_StdDev": np.std(bo_scores),
                    "U_Statistic": stat,
                    "P_Value": p_value,
                    "Alpha": alpha,
                    "Significant": p_value < alpha,
                }
            ]
        )
        output_file = self.output_dir / "statistical_significance.csv"
        result.round(6).to_csv(output_file, index=False)
        logger.info("Statistical test written to %s", output_file)
        return result

    def generate_summary_table(self) -> None:
        """Exports an aggregated statistical summary to CSV."""
        rows = []
        for run in self.all_runs:
            if not run.best_config_metrics:
                continue
            # Get the final wall time measured
            final_time = run.evaluations[-1].wall_time if run.evaluations else 0.0

            rows.append(
                {
                    "Method": run.method,
                    "Seed": run.seed,
                    "SourceFile": str(run.filepath),
                    "BestScore": run.best_global_score,
                    "WallClockTime": final_time,
                    "Throughput": run.best_config_metrics.throughput,
                    "LatencyP95": run.best_config_metrics.latency_p95,
                    "MemoryUtilization": run.best_config_metrics.memory_utilization,
                }
            )

        df = pd.DataFrame(rows)
        detail_file = self.output_dir / "comparison_runs.csv"
        df.round(6).to_csv(detail_file, index=False)

        # Compute aggregates per method
        summary = (
            df.groupby("Method")
            .agg(
                Mean_Best_Score=("BestScore", "mean"),
                StdDev_Best_Score=("BestScore", "std"),
                Mean_WallClock_Time=("WallClockTime", "mean"),
                Mean_Throughput=("Throughput", "mean"),
                Mean_LatencyP95=("LatencyP95", "mean"),
                Mean_MemoryUtilization=("MemoryUtilization", "mean"),
                Trials=("BestScore", "count"),
            )
            .round(4)
            .reset_index()
        )

        summary_file = self.output_dir / "comparison_summary.csv"
        summary.to_csv(summary_file, index=False)
        logger.info(
            "\nComparison Summary written to %s:\n%s", summary_file, summary.to_string()
        )

    def export_timeseries(self) -> None:
        """Exports the rescored convergence data used by the plots."""
        df = self._build_timeseries_df()
        output_file = self.output_dir / "comparison_timeseries.csv"
        df.round(6).to_csv(output_file, index=False)
        logger.info("Timeseries data written to %s", output_file)


def main(
    pbt_paths: List[str],
    bo_paths: List[str],
    output_dir: Path = Path("analysis"),
):
    """Main execution entry point."""
    logger.info("Loading PBT tuning runs...")
    pbt_runs = [TuningRun(Path(p), "PBT") for p in pbt_paths]

    logger.info("Loading BO tuning runs...")
    bo_runs = [TuningRun(Path(p), "BO") for p in bo_paths]

    analyzer = Analyzer(pbt_runs, bo_runs, output_dir=output_dir)

    # 1. Critical Requirement: Global Rescoring
    analyzer.apply_global_rescoring()
    analyzer.export_timeseries()

    # Generate Visualizations & Analysis
    analyzer.plot_convergence()
    analyzer.plot_pareto_front()
    analyzer.plot_resource_efficiency()

    # Compute Statistics
    analyzer.statistical_significance_test()
    analyzer.generate_summary_table()


if __name__ == "__main__":
    import argparse

    logging.basicConfig(level=logging.INFO, format="%(levelname)s: %(message)s")

    parser = argparse.ArgumentParser(description="Analyze PBT vs BO tuning runs.")
    parser.add_argument(
        "--pbt", nargs="+", required=True, help="List of PBT JSON files"
    )
    parser.add_argument("--bo", nargs="+", required=True, help="List of BO JSON files")
    parser.add_argument(
        "--output-dir",
        type=Path,
        default=Path("analysis"),
        help="Directory for CSV and PDF analysis outputs",
    )
    args = parser.parse_args()

    main(args.pbt, args.bo, args.output_dir)
