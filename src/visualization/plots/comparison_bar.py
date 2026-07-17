"""
Grouped bar chart with hatch patterns for multi-workload method comparison.

Replicates the CDBTune+ reference paper style: 3×2 subplot grid
(RW / RO / WO × Throughput / 99th %-tile Latency), dense hatched bars with
error-bar caps, and a shared top-spanning legend.
"""

import json
import logging
from pathlib import Path
from typing import Any

import numpy as np
import matplotlib.pyplot as plt
from matplotlib.figure import Figure

from src.visualization.theme import PBTuneTheme
from src.visualization.colors import get_method_style
from src.visualization.export import export_figure
from src.visualization.types import FigureSpec, ExportFormat
from src.visualization.registry import register_figure
from src.visualization.utils import add_panel_labels, add_top_shared_legend

logger = logging.getLogger(__name__)

FIG_ID = "comparison_bar"

# ── Default method display order (left → right within each group) ────
_DEFAULT_METHOD_ORDER = [
    "default",
    "cdbtune",
    "ottertune",
    "bestconfig",
    "dba",
    "pbtune",
    "bo_smac",
]

# ── Pretty-print display names ──────────────────────────────────────
_DISPLAY_NAMES: dict[str, str] = {
    "default":    "Default",
    "cdbtune":    "CDBTune+",
    "ottertune":  "OtterTune",
    "bestconfig": "BestConfig",
    "dba":        "DBA",
    "pbtune":     "PBTune",
    "bo_smac":    "BO-SMAC",
    "bo":         "BO-SMAC",
    "pbt":        "PBTune",
    "llamatune":  "LlamaTune",
    "qtune":      "QTune",
    "gptuner":    "GPTuner",
}

# ── Metrics we plot ─────────────────────────────────────────────────
_METRIC_SPECS: list[dict[str, Any]] = [
    {
        "key": "throughput",
        "label": "Throughput (txn/sec)",
        "higher_is_better": True,
    },
    {
        "key": "latency_p99",
        "label": "99th %-tile Latency (ms)",
        "higher_is_better": False,
    },
]


def _load_workload_data(
    comparison_paths: dict[str, str],
) -> dict[str, dict[str, dict[str, list[float]]]]:
    """Load per-workload, per-arm, per-metric raw values.

    Parameters
    ----------
    comparison_paths : dict[str, str]
        Mapping from workload label (e.g. ``"rw"``) to the path of the
        corresponding multi-arm comparison JSON.

    Returns
    -------
    dict
        ``{workload: {arm_name: {metric_key: [values]}}}``
    """
    data: dict[str, dict[str, dict[str, list[float]]]] = {}

    for workload, path_str in comparison_paths.items():
        path = Path(path_str)
        if not path.exists():
            logger.warning("Comparison file not found for %s: %s", workload, path)
            continue

        try:
            with open(path, "r", encoding="utf-8") as f:
                raw = json.load(f)
        except Exception as exc:
            logger.warning("Failed to read %s: %s", path, exc)
            continue

        runs_by_arm = raw.get("runs_by_arm", {})
        workload_data: dict[str, dict[str, list[float]]] = {}

        for arm_name, runs in runs_by_arm.items():
            metrics_by_key: dict[str, list[float]] = {}
            for run in runs:
                metrics = run.get("metrics", {})
                for spec in _METRIC_SPECS:
                    val = metrics.get(spec["key"])
                    if val is not None:
                        metrics_by_key.setdefault(spec["key"], []).append(float(val))
            if metrics_by_key:
                workload_data[arm_name] = metrics_by_key

        if workload_data:
            data[workload] = workload_data

    return data


def _resolve_method_order(
    data: dict[str, dict[str, dict[str, list[float]]]],
) -> list[str]:
    """Determine which methods are present across all workloads."""
    all_arms: set[str] = set()
    for workload_data in data.values():
        all_arms.update(workload_data.keys())

    ordered = [m for m in _DEFAULT_METHOD_ORDER if m in all_arms]
    remaining = sorted(all_arms - set(ordered))
    return ordered + remaining


def generate(
    comparison_paths: dict[str, str] | None = None,
    output_dir: str = "figures/",
    venue: str = "pvldb",
    formats: list[str] | None = None,
    # Fallback kwargs consumed by the CLI dispatcher but not used here
    data_dir: Path | None = None,
    theme: PBTuneTheme | None = None,
    **_kwargs,
) -> Figure:
    """Generate the grouped bar chart comparison figure.

    Parameters
    ----------
    comparison_paths : dict[str, str]
        ``{"rw": "path/to/rw_comparison.json", "ro": "...", "wo": "..."}``
    """
    logger.info("Generating %s figure", FIG_ID)

    # ── Auto-discover comparison files when called from CLI ──────────
    if comparison_paths is None and data_dir is not None:
        comparison_paths = {}
        for workload_dir, label in [("oltp_read_write", "rw"), ("oltp_read_only", "ro"), ("oltp_write_only", "wo")]:
            d = Path(data_dir) / "comparisons" / workload_dir / "extensive"
            if d.exists():
                comps = sorted(d.glob("multi_arm_comparison_*.json"))
                if comps:
                    comparison_paths[label] = str(comps[-1])
        if not comparison_paths:
            # Try flat layout
            for candidate in sorted(Path(data_dir).glob("*comparison*.json")):
                comparison_paths[candidate.stem] = str(candidate)

    if not comparison_paths:
        logger.warning("No comparison data provided for %s. Returning empty figure.", FIG_ID)
        _theme = theme or PBTuneTheme(venue=venue)
        with _theme.apply():
            fig, ax = _theme.figure(size_hint="single")
            return fig

    data = _load_workload_data(comparison_paths)
    if not data:
        logger.warning("All comparison files were empty/invalid.")
        _theme = theme or PBTuneTheme(venue=venue)
        with _theme.apply():
            fig, ax = _theme.figure(size_hint="single")
            return fig

    methods = _resolve_method_order(data)
    workloads = list(data.keys())
    n_workloads = len(workloads)
    n_metrics = len(_METRIC_SPECS)

    _theme = theme or PBTuneTheme(venue=venue)

    with _theme.apply():
        fig, axes = _theme.subplots(
            n_workloads, n_metrics,
            size_hint="double",
            aspect=0.55 * n_workloads,
        )
        # Ensure axes is always 2D
        if n_workloads == 1 and n_metrics == 1:
            axes = np.array([[axes]])
        elif n_workloads == 1:
            axes = axes[np.newaxis, :]
        elif n_metrics == 1:
            axes = axes[:, np.newaxis]

        n_methods = len(methods)
        bar_width = 0.8 / max(n_methods, 1)

        for row_idx, workload in enumerate(workloads):
            wl_data = data[workload]
            for col_idx, metric_spec in enumerate(_METRIC_SPECS):
                ax = axes[row_idx, col_idx]
                metric_key = metric_spec["key"]

                for m_idx, method in enumerate(methods):
                    arm_data = wl_data.get(method, {})
                    vals = arm_data.get(metric_key, [])

                    if not vals:
                        continue

                    mean_val = float(np.mean(vals))
                    std_val = float(np.std(vals, ddof=1)) if len(vals) > 1 else 0.0

                    style = get_method_style(method)
                    display = _DISPLAY_NAMES.get(method, method)

                    x_pos = m_idx * bar_width
                    ax.bar(
                        x_pos,
                        mean_val,
                        width=bar_width * 0.9,
                        color=style["color"],
                        hatch=style.get("hatch", ""),
                        edgecolor="black",
                        linewidth=0.6,
                        label=display,
                        zorder=3,
                    )

                    # Error bar with caps
                    if std_val > 0:
                        ax.errorbar(
                            x_pos, mean_val, yerr=std_val,
                            fmt="none",
                            ecolor="black",
                            elinewidth=1.0,
                            capsize=3,
                            capthick=1.0,
                            zorder=4,
                        )

                # Axis formatting
                subtitle = f"{workload.upper()} ({metric_spec['label'].split('(')[0].strip()})"
                ax.set_title(subtitle, fontsize=plt.rcParams["axes.titlesize"])
                ax.set_ylabel(metric_spec["label"])
                ax.set_xticks([])  # No x-axis labels (methods shown in legend)

                # Start y-axis at 0
                ax.set_ylim(bottom=0)

        # ── Shared legend ────────────────────────────────────────────
        all_axes = axes.flat if hasattr(axes, "flat") else [axes]
        add_top_shared_legend(
            fig, list(all_axes),
            ncol=min(len(methods), 4),
            bbox_to_anchor=(0.5, 1.02),
        )

        # ── Panel labels ─────────────────────────────────────────────
        flat_axes = list(axes.flat) if hasattr(axes, "flat") else [axes]
        add_panel_labels(flat_axes)

        fig.tight_layout(rect=(0, 0, 1, 0.93))

    fmt_list = [ExportFormat(f) for f in (formats or ["pdf", "png"])]
    export_figure(fig, output_dir, FIG_ID, formats=fmt_list)
    logger.info("Export complete for %s", FIG_ID)
    return fig


register_figure(
    FigureSpec(
        fig_id=FIG_ID,
        paper_label="fig:comparison_bar",
        title="Grouped Bar Chart Comparison",
        section="evaluation",
        category="comparison",
        size_hint="double",
        generator=generate,
        data_requirements=["comparison_json"],
        description="Grouped bar chart with hatches for multi-workload method comparison.",
    )
)
