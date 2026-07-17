"""Knob dependence plots using SHAP values."""

from pathlib import Path
from typing import Optional, cast
import textwrap

import numpy as np
from matplotlib.axes import Axes
from matplotlib.figure import Figure

from src.visualization import export_figure, register_figure
from src.visualization.exceptions import DataLoadError
from src.visualization.loaders import ImportanceData, load_importance_from_dir
from src.visualization.loaders import discover_session_traces
from src.visualization.theme import PBTuneTheme
from src.visualization.types import ExportFormat, FigureSpec
from src.visualization.utils import auto_grid

DEFAULT_IMPORTANCE_DIRS = (
    Path("sessions") / "oltp_read_write" / "pbt" / "extensive" / "traces",
    Path("sessions") / "olap" / "pbt" / "extensive" / "traces",
)


def _resolve_importance_dir(data_dir: Path) -> Path:
    """Resolve the directory that contains PBT tuning sessions for importance."""
    if data_dir.is_dir():
        if discover_session_traces(data_dir):
            return data_dir

    for candidate in DEFAULT_IMPORTANCE_DIRS:
        candidate_path = data_dir / candidate
        if candidate_path.is_dir():
            if discover_session_traces(candidate_path):
                return candidate_path

    raise DataLoadError(
        "No tuning session data found. Expected trace_*.json under "
        f"{data_dir / DEFAULT_IMPORTANCE_DIRS[0]} (or the OLAP equivalent)."
    )


def _interaction_feature(importance: ImportanceData, knob_name: str) -> Optional[str]:
    """Pick the strongest interacting knob for color encoding."""
    labels = importance.pairwise_labels
    if knob_name not in labels:
        return None

    matrix = np.asarray(importance.pairwise_matrix)
    if matrix.size == 0:
        return None

    idx = labels.index(knob_name)
    symmetric = np.maximum(matrix, matrix.T)
    row = symmetric[idx].copy()
    row[idx] = 0.0

    if np.allclose(row, 0.0):
        return None

    return labels[int(np.argmax(row))]


def _shorten_label(name: str, max_len: int = 28) -> str:
    """Abbreviate long knob names for compact figure labels."""
    replacements = [
        ("autovacuum_", "av_"),
        ("checkpoint_", "ckpt_"),
        ("vacuum_", "vac_"),
        ("_scale_factor", "_scale"),
        ("_completion_target", "_target"),
        ("_freeze_min_age", "_freeze_age"),
        ("_min_age", "_min_age"),
        ("_cost_page_hit", "_cph"),
    ]
    shortened = name
    for old, new in replacements:
        shortened = shortened.replace(old, new)

    if len(shortened) > max_len:
        shortened = shortened[: max_len - 3] + "..."

    return shortened


def _manual_dependence(
    ax: Axes,
    fig: Figure,
    x_vals: np.ndarray,
    y_vals: np.ndarray,
    color_vals: np.ndarray,
    color_label: str,
) -> None:
    """Fallback dependence plot using matplotlib scatter points."""
    scatter = ax.scatter(
        x_vals,
        y_vals,
        c=color_vals,
        cmap="viridis",
        s=12,
        alpha=0.7,
        edgecolors="none",
    )
    ax.axhline(0.0, color="#6B7280", linestyle="--", linewidth=0.8)
    auto_grid(ax, axis="both")

    colorbar = fig.colorbar(scatter, ax=ax, pad=0.01, fraction=0.04)
    colorbar.ax.tick_params(labelsize=7)


def _chunk_items(items: list[str], chunk_size: int) -> list[list[str]]:
    """Split a list into fixed-size chunks."""
    return [items[i : i + chunk_size] for i in range(0, len(items), chunk_size)]


def generate_knob_dependence(
    *,
    data_dir: Path | str,
    output_dir: Path | str,
    theme: PBTuneTheme,
    formats: list[ExportFormat],
    top_k_dependence: int | None = None,
) -> None:
    """Generate the knob dependence figure.

    Args:
        data_dir: Base directory containing results.
        output_dir: Destination for exported figures.
        theme: Visualization theme for sizing and styling.
        formats: Export formats.
    """
    base_dir = Path(data_dir)
    importance_dir = _resolve_importance_dir(base_dir)
    importance = load_importance_from_dir(importance_dir)

    shap_values = np.asarray(importance.shap_values)
    if shap_values.size == 0 or importance.config_df.empty:
        raise DataLoadError("SHAP values unavailable for dependence plotting.")

    resolved_top_k = 4 if top_k_dependence is None else max(1, top_k_dependence)
    top_k = min(resolved_top_k, len(importance.knob_names))
    top_knobs = importance.knob_names[:top_k]

    analysis_columns = [
        name
        for name in importance.config_df.columns
        if importance.config_df[name].nunique() > 1
    ]
    analysis_df = importance.config_df[analysis_columns]
    column_index = {name: idx for idx, name in enumerate(analysis_columns)}
    feature_values = analysis_df.to_numpy()

    panels_per_fig = 4
    knob_chunks = _chunk_items(top_knobs, panels_per_fig)
    total_pages = len(knob_chunks)
    label_max_len = 24 if total_pages > 1 else 28

    for page_index, knob_chunk in enumerate(knob_chunks, start=1):
        with theme.apply():
            fig, axes = theme.subplots(
                nrows=2,
                ncols=2,
                size_hint="double",
                aspect=0.85 if total_pages > 1 else 0.7,
            )
            axes = np.ravel(axes)

            for idx in range(len(knob_chunk)):
                ax = cast(Axes, axes[idx])
                knob = knob_chunk[idx]
                if knob not in column_index:
                    ax.text(
                        0.5,
                        0.5,
                        f"Missing knob: {knob}",
                        ha="center",
                        va="center",
                        transform=ax.transAxes,
                    )
                    ax.set_axis_off()
                    continue

                feature_idx = column_index[knob]
                interaction = _interaction_feature(importance, knob) or knob
                color_idx = column_index.get(interaction, feature_idx)

                _manual_dependence(
                    ax,
                    fig,
                    feature_values[:, feature_idx],
                    shap_values[:, feature_idx],
                    feature_values[:, color_idx],
                    interaction,
                )

                short_knob = _shorten_label(knob, max_len=label_max_len)
                short_interaction = _shorten_label(interaction, max_len=label_max_len)
                title = f"{short_knob} (c:{short_interaction})"
                wrapped_title = textwrap.fill(title, width=26)
                ax.set_title(wrapped_title, pad=6, fontsize=8)
                row = idx // 2
                col = idx % 2
                if row == 1:
                    ax.set_xlabel("Value", fontsize=8)
                else:
                    ax.set_xlabel("")
                if col == 0:
                    ax.set_ylabel("SHAP", fontsize=8)
                else:
                    ax.set_ylabel("")
                ax.tick_params(axis="both", labelsize=7)

            for ax_obj in axes[len(knob_chunk):]:
                cast(Axes, ax_obj).set_axis_off()

            for idx, ax_obj in enumerate(axes[: len(knob_chunk)]):
                ax = cast(Axes, ax_obj)
                ax.text(
                    -0.08,
                    1.05,
                    f"({chr(ord('a') + idx)})",
                    transform=ax.transAxes,
                    fontsize=9,
                    fontweight="bold",
                    va="bottom",
                    ha="right",
                )
            fig.subplots_adjust(top=0.9, hspace=0.4, wspace=0.25)

        fig_id = "knob_dependence"
        if total_pages > 1:
            fig_id = f"{fig_id}_p{page_index}"

        export_figure(
            fig,
            output_dir=output_dir,
            fig_id=fig_id,
            formats=formats,
            metadata={
                "correlation": importance.correlation,
                "data_dir": str(importance_dir),
                "page": page_index,
                "total_pages": total_pages,
            },
        )


register_figure(
    FigureSpec(
        fig_id="knob_dependence",
        paper_label="fig:dependence",
        title="Knob dependence plots",
        section="analysis",
        category="importance",
        size_hint="double",
        generator=generate_knob_dependence,
        data_requirements=["session_json"],
        description="SHAP dependence plots for the top 4 knobs.",
    )
)
