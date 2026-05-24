"""Knob interaction heatmap from pairwise fANOVA scores."""

from pathlib import Path

import numpy as np
from matplotlib.axes import Axes
from matplotlib.figure import Figure
from src.visualization import export_figure, register_figure
from src.visualization.exceptions import DataLoadError
from src.visualization.loaders import ImportanceData, load_importance_from_dir
from src.visualization.theme import PBTuneTheme
from src.visualization.types import ExportFormat, FigureSpec
from src.visualization.utils import despine

DEFAULT_IMPORTANCE_DIRS = (
    Path("oltp") / "pbt_runs" / "extensive" / "tuning_sessions",
    Path("olap") / "pbt_runs" / "extensive" / "tuning_sessions",
)


def _shorten_label(name: str, max_len: int = 24) -> str:
    """Abbreviate long knob names for compact heatmap labels."""
    replacements = [
        ("autovacuum_", "av_"),
        ("checkpoint_", "ckpt_"),
        ("vacuum_", "vac_"),
        ("_scale_factor", "_scale"),
        ("_completion_target", "_target"),
        ("_freeze_min_age", "_freeze_age"),
        ("_cost_page_hit", "_cph"),
        ("_multixact", "_mx"),
    ]
    shortened = name
    for old, new in replacements:
        shortened = shortened.replace(old, new)

    if len(shortened) > max_len:
        shortened = shortened[: max_len - 3] + "..."

    return shortened


def _resolve_importance_dir(data_dir: Path) -> Path:
    """Resolve the directory that contains PBT tuning sessions for importance."""
    if data_dir.is_dir():
        json_files = list(data_dir.glob("pbt_results_*.json"))
        if json_files:
            return data_dir

    for candidate in DEFAULT_IMPORTANCE_DIRS:
        candidate_path = data_dir / candidate
        if candidate_path.is_dir():
            json_files = list(candidate_path.glob("pbt_results_*.json"))
            if json_files:
                return candidate_path

    raise DataLoadError(
        "No tuning session data found. Expected pbt_results_*.json under "
        f"{data_dir / DEFAULT_IMPORTANCE_DIRS[0]} (or the OLAP equivalent)."
    )


def _render_heatmap(
    importance: ImportanceData,
    theme: PBTuneTheme,
    top_k_interactions: int | None = None,
) -> tuple[Figure, Axes]:
    """Render a heatmap using seaborn if available, otherwise imshow.

    Returns:
        Tuple of (fig, ax) for export.
    """
    with theme.apply():
        fig, ax = theme.figure(size_hint="single", aspect=0.9)

        matrix = np.asarray(importance.pairwise_matrix)
        labels = importance.pairwise_labels

        if top_k_interactions is not None:
            resolved_top_k = max(1, top_k_interactions)
            top_labels = [
                name
                for name in importance.knob_names
                if name in labels
            ][:resolved_top_k]
            index_map = {name: idx for idx, name in enumerate(labels)}
            indices = [index_map[name] for name in top_labels if name in index_map]
            if indices:
                matrix = matrix[np.ix_(indices, indices)]
                labels = top_labels
        short_labels = [_shorten_label(label) for label in labels]

        if matrix.size == 0 or len(labels) == 0:
            ax.text(
                0.5,
                0.5,
                "Pairwise interaction data unavailable",
                ha="center",
                va="center",
                transform=ax.transAxes,
            )
            ax.set_axis_off()
            return fig, ax

        try:
            import seaborn as sns

            annot = matrix.shape[0] <= 12
            sns.heatmap(
                matrix,
                ax=ax,
                cmap="mako",
                xticklabels=False,
                yticklabels=short_labels,
                annot=annot,
                fmt=".2f",
                cbar_kws={"label": "Interaction importance"},
            )
        except ImportError:
            im = ax.imshow(matrix, cmap="viridis")
            fig.colorbar(im, ax=ax, label="Interaction importance")
            ax.set_xticks([])
            ax.set_yticks(range(len(short_labels)))
            ax.set_yticklabels(short_labels)

        if top_k_interactions is None:
            title = "fANOVA pairwise interaction importance"
        else:
            title = f"fANOVA pairwise interaction importance (top {len(labels)})"
        ax.set_title(title)
        ax.set_xlabel("")
        ax.set_ylabel("Knob")
        ax.tick_params(axis="y", labelsize=7)
        despine(ax)

        return fig, ax


def generate_knob_interaction_heatmap(
    *,
    data_dir: Path | str,
    output_dir: Path | str,
    theme: PBTuneTheme,
    formats: list[ExportFormat],
    top_k_interactions: int | None = None,
) -> None:
    """Generate the knob interaction heatmap figure.

    Args:
        data_dir: Base directory containing results.
        output_dir: Destination for exported figures.
        theme: Visualization theme for sizing and styling.
        formats: Export formats.
    """
    base_dir = Path(data_dir)
    importance_dir = _resolve_importance_dir(base_dir)
    importance = load_importance_from_dir(importance_dir)

    fig, _ = _render_heatmap(importance, theme, top_k_interactions=top_k_interactions)

    export_figure(
        fig,
        output_dir=output_dir,
        fig_id="knob_interaction_heatmap",
        formats=formats,
        metadata={
            "correlation": importance.correlation,
            "data_dir": str(importance_dir),
        },
    )


register_figure(
    FigureSpec(
        fig_id="knob_interaction_heatmap",
        paper_label="fig:interaction",
        title="Knob interaction heatmap",
        section="analysis",
        category="importance",
        size_hint="single",
        generator=generate_knob_interaction_heatmap,
        data_requirements=["session_json"],
        description="Pairwise fANOVA interaction heatmap.",
    )
)
