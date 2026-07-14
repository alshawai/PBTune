"""Knob importance figure (fANOVA bars + SHAP beeswarm)."""

from pathlib import Path
from typing import Iterable, cast

import numpy as np
from matplotlib.axes import Axes
from matplotlib.figure import Figure
from matplotlib.colors import Normalize

from src.utils.logger import get_logger
from src.visualization import METRIC_COLORS, export_figure, register_figure
from src.visualization.exceptions import DataLoadError
from src.visualization.loaders import ImportanceData, load_importance_from_dir
from src.visualization.theme import PBTuneTheme
from src.visualization.types import ExportFormat, FigureSpec
from src.visualization.utils import auto_grid, despine

LOGGER = get_logger("KnobImportancePlot")

DEFAULT_IMPORTANCE_DIRS = (
    Path("sessions") / "oltp_read_write" / "pbt" / "extensive" / "traces",
    Path("sessions") / "olap" / "pbt" / "extensive" / "traces",
)


def _shorten_label(name: str, max_len: int = 28) -> str:
    """Abbreviate long knob names for compact figure labels."""
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


def _label_font_size(knob_count: int, base: float = 9.0) -> float:
    """Scale label font size as the number of knobs grows.

    Uses a simple linear decay: size = base - 0.6 * max(0, k - 10),
    clamped to a minimum of 6 pt.
    """
    decay = 0.6 * max(0, knob_count - 10)
    return max(6.0, base - decay)


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


def _match_knobs(
    importance: ImportanceData, knob_names: Iterable[str]
) -> tuple[list[str], np.ndarray, np.ndarray]:
    """Align SHAP values and config data with the requested knob names.

    Args:
        importance: Loaded importance data.
        knob_names: Desired knob names in display order.

    Returns:
        Tuple of (valid_knobs, shap_subset, feature_values).
    """
    analysis_columns = [
        name
        for name in importance.config_df.columns
        if importance.config_df[name].nunique() > 1
    ]
    analysis_df = importance.config_df[analysis_columns]
    column_index = {name: idx for idx, name in enumerate(analysis_columns)}
    valid_knobs = [name for name in knob_names if name in column_index]

    if not valid_knobs:
        raise DataLoadError("None of the requested knobs exist in config data.")

    indices = [column_index[name] for name in valid_knobs]
    shap_values = np.asarray(importance.shap_values)
    shap_subset = shap_values[:, indices]
    feature_values = analysis_df[valid_knobs].to_numpy()
    return valid_knobs, shap_subset, feature_values


def _try_shap_beeswarm(
    ax: Axes,
    shap_values: np.ndarray,
    feature_values: np.ndarray,
    feature_names: list[str],
) -> bool:
    """Try to render a SHAP beeswarm plot using shap if available."""
    try:
        import shap
    except ImportError:
        return False

    try:
        import inspect

        if "ax" not in inspect.signature(shap.plots.beeswarm).parameters:
            return False
        explanation = shap.Explanation(
            values=shap_values,
            data=feature_values,
            feature_names=feature_names,
        )
        shap.plots.beeswarm(
            explanation,
            ax=ax,
            show=False,
            max_display=len(feature_names),
        )
        return True
    except Exception as exc:
        LOGGER.debug("shap beeswarm failed, falling back to manual plot: %s", exc)
        return False


def _manual_beeswarm(
    ax: Axes,
    fig: Figure,
    shap_values: np.ndarray,
    feature_values: np.ndarray,
    feature_names: list[str],
    label_gap: float,
) -> None:
    """Fallback beeswarm rendering using matplotlib scatter points."""
    rng = np.random.default_rng(0)
    y_positions = np.arange(len(feature_names)) * label_gap
    last_scatter = None
    color_norm = Normalize(vmin=0.0, vmax=1.0)

    # Approximate beeswarm by jittering points along the categorical axis.
    for idx, _ in enumerate(feature_names):
        jitter = rng.normal(0, 0.12 * label_gap, size=shap_values.shape[0])
        last_scatter = ax.scatter(
            shap_values[:, idx],
            y_positions[idx] + jitter,
            c=feature_values[:, idx],
            cmap="viridis",
            norm=color_norm,
            s=10,
            alpha=0.7,
            edgecolors="none",
        )

    ax.set_yticks(y_positions)
    ax.set_yticklabels(feature_names)
    ax.set_ylim(-0.5 * label_gap, y_positions[-1] + 0.5 * label_gap)
    ax.axvline(0.0, color="#6B7280", linestyle="--", linewidth=0.8)
    auto_grid(ax, axis="x")

    if last_scatter is not None:
        colorbar = fig.colorbar(last_scatter, ax=ax, pad=0.02)
        colorbar.set_label("Feature value (normalized)")
        colorbar.set_ticks([0.0, 1.0])
        colorbar.set_ticklabels(["Low", "High"])


def _normalize_feature_values(values: np.ndarray) -> np.ndarray:
    """Normalize each feature column to [0, 1] for consistent color mapping."""
    min_vals = np.nanmin(values, axis=0)
    max_vals = np.nanmax(values, axis=0)
    ranges = max_vals - min_vals
    safe_ranges = np.where(ranges == 0, 1.0, ranges)
    normalized = (values - min_vals) / safe_ranges
    return np.clip(normalized, 0.0, 1.0)


def generate_knob_importance(
    *,
    data_dir: Path | str,
    output_dir: Path | str,
    theme: PBTuneTheme,
    formats: list[ExportFormat],
    top_k_importance: int | None = None,
) -> None:
    """Generate the knob importance figure.

    Args:
        data_dir: Base directory containing results.
        output_dir: Destination for exported figures.
        theme: Visualization theme for sizing and styling.
        formats: Export formats.
    """
    base_dir = Path(data_dir)
    importance_dir = _resolve_importance_dir(base_dir)
    importance = load_importance_from_dir(importance_dir)

    resolved_top_k = 10 if top_k_importance is None else max(1, top_k_importance)
    top_k = min(resolved_top_k, len(importance.knob_names))
    top_knobs = importance.knob_names[:top_k]
    short_knobs = [_shorten_label(k) for k in top_knobs]
    label_size = _label_font_size(top_k)
    label_gap = 1.0 + 0.02 * max(0, min(20, top_k - 10))

    with theme.apply():
        fig, axes = theme.subplots(nrows=2, ncols=1, size_hint="single", aspect=1.2)
        axes = np.ravel(axes)
        bar_ax = cast(Axes, axes[0])
        swarm_ax = cast(Axes, axes[1])

        bar_positions = np.arange(top_k) * label_gap
        bar_ax.barh(
            bar_positions,
            importance.fanova_scores[:top_k],
            color=METRIC_COLORS["score"],
        )
        bar_ax.invert_yaxis()
        bar_ax.set_yticks(bar_positions)
        bar_ax.set_yticklabels(short_knobs)
        bar_ax.tick_params(axis="y", labelsize=label_size)
        bar_ax.set_xlabel("Marginal importance")
        bar_ax.set_title(
            f"fANOVA marginal importances (top {top_k})", fontsize=9, pad=6
        )
        auto_grid(bar_ax, axis="x")
        despine(bar_ax)

        shap_values = np.asarray(importance.shap_values)
        if shap_values.size == 0 or importance.config_df.empty:
            swarm_ax.text(
                0.5,
                0.5,
                "SHAP values unavailable",
                ha="center",
                va="center",
                transform=swarm_ax.transAxes,
            )
        else:
            valid_knobs, shap_subset, feature_values = _match_knobs(
                importance, top_knobs
            )
            short_valid = [_shorten_label(k) for k in valid_knobs]
            normalized_features = _normalize_feature_values(feature_values)
            used_shap = False
            if label_gap == 1.0:
                used_shap = _try_shap_beeswarm(
                    swarm_ax, shap_subset, normalized_features, short_valid
                )
            if not used_shap:
                _manual_beeswarm(
                    swarm_ax,
                    fig,
                    shap_subset,
                    normalized_features,
                    short_valid,
                    label_gap,
                )

            swarm_ax.tick_params(axis="y", labelsize=label_size)

        swarm_ax.set_title(f"SHAP beeswarm (top {top_k})", fontsize=9, pad=6)
        swarm_ax.set_xlabel("SHAP value (impact on score)")
        swarm_ax.set_ylabel("Knob")

        for idx, ax in enumerate([bar_ax, swarm_ax]):
            ax.text(
                -0.08,
                1.02,
                f"({chr(ord('a') + idx)})",
                transform=ax.transAxes,
                fontsize=9,
                fontweight="bold",
                va="bottom",
                ha="right",
            )

        fig.subplots_adjust(left=0.23, top=0.92, hspace=0.5)

    export_figure(
        fig,
        output_dir=output_dir,
        fig_id="knob_importance",
        formats=formats,
        metadata={
            "correlation": importance.correlation,
            "data_dir": str(importance_dir),
        },
    )


register_figure(
    FigureSpec(
        fig_id="knob_importance",
        paper_label="fig:importance",
        title="Knob importance overview",
        section="analysis",
        category="importance",
        size_hint="single",
        generator=generate_knob_importance,
        data_requirements=["session_json"],
        description="fANOVA bars with SHAP beeswarm (top 10 knobs).",
    )
)
