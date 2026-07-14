import json
import logging
from pathlib import Path

import pandas as pd
import seaborn as sns
from matplotlib.figure import Figure

from src.visualization.theme import PBTuneTheme
from src.visualization.colors import get_method_style
from src.visualization.export import export_figure
from src.visualization.types import FigureSpec, ExportFormat
from src.visualization.registry import register_figure
from src.visualization.loaders import (
    load_sessions, load_session, load_bo_trace, discover_bo_traces,
)


logger = logging.getLogger(__name__)

FIG_ID = "pareto_frontier"

# Maps multi-arm comparison arm names → display names used in the legend.
_ARM_TO_METHOD = {
    "default": "Default",
    "bo": "BO-SMAC",
    "pbt": "PBTune",
}

# Maps display names → internal style keys used by get_method_style().
_METHOD_STYLE_KEY = {
    "Default": "default",
    "PBTune": "pbtune",
    "BO-SMAC": "bo_smac",
}


def _rows_from_comparison(comparison_path: str) -> list[dict]:
    """Extract throughput / latency_p95 rows from a multi-arm comparison JSON."""
    path = Path(comparison_path)
    if not path.exists():
        logger.warning("Comparison report not found: %s", path)
        return []

    try:
        with open(path, "r", encoding="utf-8") as f:
            data = json.load(f)
    except Exception as exc:
        logger.warning("Failed to read comparison JSON %s: %s", path, exc)
        return []

    runs_by_arm = data.get("runs_by_arm", {})
    rows: list[dict] = []
    for arm_name, runs in runs_by_arm.items():
        method = _ARM_TO_METHOD.get(arm_name, arm_name)
        for run in runs:
            metrics = run.get("metrics", {})
            throughput = metrics.get("throughput")
            latency = metrics.get("latency_p95")
            if throughput is not None and latency is not None:
                rows.append(
                    {
                        "Method": method,
                        "Throughput": throughput,
                        "Latency (p95)": latency,
                    }
                )
    return rows


def _rows_from_sessions_and_traces(sessions, bo_traces) -> list[dict]:
    """Extract throughput / latency_p95 rows from loaded PBT sessions and BO traces."""
    rows: list[dict] = []
    for s in sessions:
        metrics = s.metadata.get("best_config_metrics")
        if metrics is not None and metrics.throughput is not None and metrics.latency_p95 is not None:
            rows.append(
                {
                    "Method": "PBTune",
                    "Throughput": metrics.throughput,
                    "Latency (p95)": metrics.latency_p95,
                }
            )

    for b in bo_traces:
        metrics = b.metadata.get("best_config_metrics")
        if metrics is not None and metrics.throughput is not None and metrics.latency_p95 is not None:
            rows.append(
                {
                    "Method": "BO-SMAC",
                    "Throughput": metrics.throughput,
                    "Latency (p95)": metrics.latency_p95,
                }
            )
    return rows


def generate(
    pbt_paths: list[str] | None = None,
    bo_paths: list[str] | None = None,
    comparison_path: str | None = None,
    output_dir: str = "figures/",
    venue: str = "pvldb",
    formats: list[str] | None = None,
    data_dir: Path | str | None = None,
    theme: PBTuneTheme | None = None,
    **kwargs,
) -> Figure:
    """Generate the Pareto-frontier scatter plot.

    Data can come from **either** source (or both):

    * ``--pbt`` / ``--bo`` raw result JSONs  →  reads ``best_configuration.metrics``
    * ``--comparison-path`` multi-arm JSON    →  reads ``runs_by_arm.*.metrics``

    When both are provided the comparison data takes priority; PBT/BO files
    are used as a fallback when the comparison JSON is missing or empty.
    """
    logger.info("Generating %s figure", FIG_ID)

    if not pbt_paths and data_dir:
        d = Path(data_dir) / "sessions" / "oltp_read_write"
        if d.exists():
            pbt_paths = [str(d / "pbt" / "extensive" / "traces")]
            bo_paths = [str(d / "bo" / "extensive" / "traces")]
            comps = sorted(
                (Path(data_dir) / "comparisons" / "oltp_read_write" / "extensive").glob(
                    "multi_arm_comparison_*.json"
                )
            )
            if comps and not comparison_path:
                comparison_path = str(comps[-1])

    pbt_paths = pbt_paths or []
    bo_paths = bo_paths or []

    # ── Collect rows from comparison-path (preferred) ──────────────────
    rows: list[dict] = []
    if comparison_path is not None:
        rows = _rows_from_comparison(comparison_path)
        if rows:
            logger.info(
                "Pareto data loaded from comparison report: %d points", len(rows)
            )

    # ── Fallback / supplement from raw PBT + BO files ──────────────────
    if not rows:
        sessions = []
        for path in pbt_paths:
            path_obj = Path(path)
            if path_obj.is_dir():
                sessions.extend(load_sessions(path))
            else:
                sessions.append(load_session(path))

        bo_traces = []
        for path in bo_paths:
            path_obj = Path(path)
            if path_obj.is_dir():
                for trace_path in discover_bo_traces(path_obj):
                    bo_traces.append(load_bo_trace(trace_path))
            else:
                bo_traces.append(load_bo_trace(path))

        rows = _rows_from_sessions_and_traces(sessions, bo_traces)
        if rows:
            logger.info(
                "Pareto data loaded from PBT/BO files: %d points", len(rows)
            )

    _theme = theme or PBTuneTheme(venue=venue)

    if not rows:
        logger.warning("No pareto metrics found. Returning empty figure.")
        with _theme.apply():
            fig, ax = _theme.figure(size_hint="single")
            return fig

    df = pd.DataFrame(rows)

    # Build a dynamic palette for whichever methods are present
    methods_present = df["Method"].unique().tolist()
    palette = {}
    for m in methods_present:
        style_key = _METHOD_STYLE_KEY.get(m, "default")
        palette[m] = get_method_style(style_key)["color"]

    with _theme.apply():
        fig, ax = _theme.figure(size_hint="single")

        sns.scatterplot(
            data=df,
            x="Throughput",
            y="Latency (p95)",
            hue="Method",
            style="Method",
            palette=palette,
            s=100,
            ax=ax,
            alpha=0.8,
            edgecolor="w",
        )

        ax.set_xlabel("Throughput (Queries/sec) $\\uparrow$")
        ax.set_ylabel("95th Percentile Latency (ms) $\\downarrow$")

        # Add legend
        ax.legend(title="", loc="best", frameon=True)


        fig.tight_layout()

    fmt_list = [ExportFormat(f) for f in (formats or ["pdf", "png"])]
    export_figure(fig, output_dir, FIG_ID, formats=fmt_list)
    logger.info("Export complete for %s", FIG_ID)
    return fig


register_figure(
    FigureSpec(
        fig_id=FIG_ID,
        paper_label="pareto_frontier",
        title="Pareto Frontier",
        section="evaluation",
        category="comparison",
        size_hint="single",
        generator=generate,
        data_requirements=["session_json", "baseline_json"],
        description="Pareto frontier scatter plot of Throughput vs p95 Latency",
    )
)
