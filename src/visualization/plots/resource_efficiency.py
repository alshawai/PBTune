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

FIG_ID = "resource_efficiency"


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

    rows = []
    for s in sessions:
        metrics = s.metadata.get("best_config_metrics")
        if metrics is not None and metrics.memory_utilization is not None:
            rows.append(
                {
                    "Method": "PBTune",
                    "Memory": metrics.memory_utilization,
                }
            )

    for b in bo_traces:
        metrics = b.metadata.get("best_config_metrics")
        if metrics is not None and metrics.memory_utilization is not None:
            rows.append(
                {
                    "Method": "BO-SMAC",
                    "Memory": metrics.memory_utilization,
                }
            )

    _theme = theme or PBTuneTheme(venue=venue)
    
    if not rows:
        logger.warning("No resource metrics found. Returning empty figure.")
        with _theme.apply():
            fig, ax = _theme.figure(size_hint="single")
            return fig

    df = pd.DataFrame(rows)

    with _theme.apply():
        fig, ax = _theme.figure(size_hint="single")

        palette = {
            "PBTune": get_method_style("pbtune")["color"],
            "BO-SMAC": get_method_style("bo_smac")["color"],
        }

        # Boxplot to show distribution
        sns.boxplot(
            data=df,
            x="Method",
            y="Memory",
            hue="Method",
            palette=palette,
            width=0.4,
            ax=ax,
            boxprops={"alpha": 0.5},
            showfliers=False,
            legend=False,
        )

        # Stripplot to show individual seed outcomes
        sns.stripplot(
            data=df,
            x="Method",
            y="Memory",
            hue="Method",
            palette=palette,
            size=6,
            ax=ax,
            jitter=True,
            alpha=0.8,
            legend=False,
        )

        ax.set_xlabel("")
        ax.set_ylabel("Memory Utilization (bytes) $\\downarrow$")
        ax.set_title("Resource Efficiency (Final Configs)")


        fig.tight_layout()

    fmt_list = [ExportFormat(f) for f in (formats or ["pdf", "png"])]
    export_figure(fig, output_dir, FIG_ID, formats=fmt_list)
    return fig


register_figure(
    FigureSpec(
        fig_id=FIG_ID,
        paper_label="resource_efficiency",
        title="Resource Efficiency",
        section="evaluation",
        category="comparison",
        size_hint="single",
        generator=generate,
        data_requirements=["session_json", "baseline_json"],
        description="Boxplot of Memory Utilization for final best configurations",
    )
)
