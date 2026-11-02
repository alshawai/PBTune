import logging
from pathlib import Path
from typing import Any

import numpy as np
from matplotlib.figure import Figure
import matplotlib.pyplot as plt

from src.visualization.theme import PBTuneTheme
from src.visualization.colors import get_method_style
from src.visualization.export import export_figure
from src.visualization.types import FigureSpec, ExportFormat
from src.visualization.registry import register_figure
from src.visualization.loaders.comparison import load_comparison, load_multi_arm_comparison

logger = logging.getLogger(__name__)

FIG_ID = "bootstrap_ci_forest"
_METHOD_ORDER = ["BO-SMAC", "PBTune"]  # Default is the baseline, so it's not plotted as a bar
_TARGET_METRICS = [
    ("score", "Composite Score"),
    ("throughput", "Throughput (txn/sec)"),
    ("latency_p95", "95th %-tile Latency (ms)"),
    ("latency_p99", "99th %-tile Latency (ms)"),
    ("memory_utilization", "Memory Util (%)")
]

def _bootstrap_ci(data: np.ndarray, n_resamples: int = 10000, ci: float = 95.0) -> tuple[float, float, float]:
    """
    Compute bootstrap confidence interval for the mean.
    Returns (mean, lower_bound, upper_bound).
    """
    if len(data) == 0:
        return 0.0, 0.0, 0.0
    if len(data) == 1:
        return float(data[0]), float(data[0]), float(data[0])

    # Sample with replacement
    indices = np.random.randint(0, len(data), size=(n_resamples, len(data)))
    samples = data[indices]
    means = np.mean(samples, axis=1)

    lower_pct = (100.0 - ci) / 2.0
    upper_pct = 100.0 - lower_pct

    lower = float(np.percentile(means, lower_pct))
    upper = float(np.percentile(means, upper_pct))
    mean_val = float(np.mean(data))

    return mean_val, lower, upper

def _get_relative_improvement(tuned_data: np.ndarray, default_data: np.ndarray, metric_key: str) -> np.ndarray:
    """
    Calculate relative improvement of tuned_data over the mean of default_data.
    Inverts the calculation if the metric is lower-is-better (e.g., latency).
    Returns array of % improvement.
    """
    if len(default_data) == 0 or len(tuned_data) == 0:
        return np.zeros_like(tuned_data)

    default_mean = np.mean(default_data)
    if default_mean == 0:
        return np.zeros_like(tuned_data)

    if metric_key.startswith("latency") or metric_key in ("error_rate", "memory_utilization"):
        # Lower is better: (Default - Tuned) / Default
        improvement = (default_mean - tuned_data) / default_mean
    else:
        # Higher is better: (Tuned - Default) / Default
        improvement = (tuned_data - default_mean) / default_mean

    return improvement * 100.0

def generate(
    comparison_path: str | None = None,
    data_dir: Path | str | None = None,
    output_dir: str = "figures/",
    venue: str = "pvldb",
    formats: list[str] | None = None,
    theme: PBTuneTheme | None = None,
    **kwargs,
) -> Figure:
    logger.info("Generating %s figure", FIG_ID)

    if not comparison_path and data_dir:
        data_dir_path = Path(data_dir)
        # Try finding in <data_dir>/comparisons/extensive/
        comps = sorted(
            (data_dir_path / "comparisons" / "extensive").glob(
                "multi_arm_comparison_*.json"
            )
        )
        if not comps:
            # Try <data_dir>/comparisons/oltp_read_write/extensive/ (performance_distribution format)
            comps = sorted(
                (data_dir_path / "comparisons" / "oltp_read_write" / "extensive").glob(
                    "multi_arm_comparison_*.json"
                )
            )
        if comps:
            comparison_path = str(comps[-1])

    if not comparison_path:
        raise ValueError("A comparison_path or data_dir with comparison JSONs is required to extract multi-metric arrays.")

    # Load comparison data
    try:
        comp_data = load_multi_arm_comparison(comparison_path)
        is_multi = True
    except Exception:
        comp_data = load_comparison(comparison_path)
        is_multi = False

    _theme = theme or PBTuneTheme(venue=venue)
    with _theme.apply():
        fig, ax = _theme.figure(size_hint="single", aspect=1.0)

        y_positions = np.arange(len(_TARGET_METRICS))[::-1]  # Reverse to plot top-to-bottom
        y_labels = []

        method_offsets = {"BO-SMAC": -0.15, "PBTune": 0.15}

        for i, (metric_key, metric_name) in enumerate(_TARGET_METRICS):
            y_pos = y_positions[i]
            y_labels.append(metric_name)

            # Extract default data
            default_data = np.array([])
            if is_multi and "default" in comp_data.summaries_by_arm and metric_key in comp_data.summaries_by_arm["default"]:
                default_data = np.array(comp_data.summaries_by_arm["default"][metric_key].values)
            elif not is_multi and metric_key in comp_data.default_summaries:
                default_data = np.array(comp_data.default_summaries[metric_key].values)

            # Fallback if default_data is scalar or empty
            if len(default_data) == 0 and is_multi and "default" in comp_data.summaries_by_arm and metric_key in comp_data.summaries_by_arm["default"]:
                 default_data = np.array([comp_data.summaries_by_arm["default"][metric_key].mean])
            elif len(default_data) == 0 and not is_multi and metric_key in comp_data.default_summaries:
                 default_data = np.array([comp_data.default_summaries[metric_key].mean])

            for method in _METHOD_ORDER:
                arm_key = "bo" if method == "BO-SMAC" else "pbt"
                method_data = np.array([])

                if is_multi and arm_key in comp_data.summaries_by_arm and metric_key in comp_data.summaries_by_arm[arm_key]:
                    method_data = np.array(comp_data.summaries_by_arm[arm_key][metric_key].values)
                elif not is_multi and method == "PBTune" and metric_key in comp_data.tuned_summaries:
                    # In standard comparison, 'tuned' usually means PBT
                    method_data = np.array(comp_data.tuned_summaries[metric_key].values)
                
                # Fallback to scalar
                if len(method_data) == 0 and is_multi and arm_key in comp_data.summaries_by_arm and metric_key in comp_data.summaries_by_arm[arm_key]:
                    method_data = np.array([comp_data.summaries_by_arm[arm_key][metric_key].mean])
                elif len(method_data) == 0 and not is_multi and method == "PBTune" and metric_key in comp_data.tuned_summaries:
                    method_data = np.array([comp_data.tuned_summaries[metric_key].mean])

                if len(method_data) > 0 and len(default_data) > 0:
                    improvements = _get_relative_improvement(method_data, default_data, metric_key)
                    mean_imp, lower_imp, upper_imp = _bootstrap_ci(improvements)

                    style_key = "bo_smac" if method == "BO-SMAC" else "pbtune"
                    color = get_method_style(style_key)["color"]
                    
                    y_offset = y_pos + method_offsets[method]
                    
                    # errorbar for the actual CI
                    xerr = [[mean_imp - lower_imp], [upper_imp - mean_imp]]
                    ax.errorbar(
                        x=[mean_imp], 
                        y=[y_offset], 
                        xerr=xerr,
                        fmt='o', 
                        color=color, 
                        ecolor='black',
                        capsize=0,
                        elinewidth=3,
                        markersize=12,
                        zorder=5,
                    )
                    
                    # Add dummy scatter for clean legend (only once per method)
                    if i == 0:
                        ax.scatter([], [], color=color, s=150, label=method)

        ax.set_yticks(y_positions)
        ax.set_yticklabels(y_labels)

        # Add vertical line at 0 (no improvement)
        ax.axvline(x=0, color='black', linestyle='--', alpha=0.5, zorder=1)

        # Use a symmetric log scale so extreme values (like -1000% memory) don't crush the standard metrics.
        # This acts linearly between -10 and 10, and logarithmically outside, making small tight CIs visible!
        ax.set_xscale('symlog', linthresh=10)
        import matplotlib.ticker as ticker
        ax.xaxis.set_major_formatter(ticker.ScalarFormatter()) # Keep regular numbers instead of 10^2

        ax.set_xlabel("Relative Improvement vs Default (%)", labelpad=15)
        
        # Avoid duplicate labels in legend
        handles, labels = ax.get_legend_handles_labels()
        by_label = dict(zip(labels, handles))
        if by_label:
            ax.legend(by_label.values(), by_label.keys(), loc='upper left')

        # Add explicit padding to the X-axis so large dots at the extreme ends don't spill out of the frame
        ax.margins(x=0.15)

        # Add title explicitely
        ax.set_title("Relative Improvement by Metric (95% Bootstrap CI)", pad=15)

        # Use subplots_adjust to ensure enough room at bottom and top instead of tight_layout
        fig.subplots_adjust(bottom=0.2, top=0.9, left=0.4, right=0.95)

        fmt_list = [ExportFormat(f) for f in (formats or ["pdf", "png"])]
        export_figure(fig, output_dir, FIG_ID, formats=fmt_list)
        logger.info("Export complete for %s", FIG_ID)

    return fig

register_figure(FigureSpec(
    fig_id=FIG_ID,
    paper_label="fig:bootstrap_ci",
    title="Relative Improvement by Metric (95% Bootstrap CI)",
    section="evaluation",
    category="statistical",
    size_hint="single",
    generator=generate,
    data_requirements=["session_json", "baseline_json", "comparison_json"],
    description="Forest plot showing bootstrap confidence intervals of relative improvement across multiple metrics."
))
