import logging
import json
from typing import Optional
from pathlib import Path
import numpy as np
import matplotlib.pyplot as plt
from matplotlib.figure import Figure

from src.utils.metrics import PerformanceMetrics
from src.utils.rescoring import rescore_metrics_globally
from src.visualization.theme import PBTuneTheme
from src.visualization.colors import get_method_style
from src.visualization.export import export_figure
from src.visualization.types import FigureSpec, ExportFormat
from src.visualization.exceptions import DataLoadError
from src.visualization.registry import register_figure
from src.visualization.loaders import (
    load_sessions, load_session, load_bo_trace, aggregate_seeds, SessionTrace, BOTrace, MultiSeedAggregate, load_comparison, ComparisonData, RAW_METRIC_KEYS
)
from src.visualization.loaders.session import _extract_raw_value
from src.visualization.utils import (
    despine, add_panel_labels, add_baseline_line, auto_grid, set_integer_ticks
)

logger = logging.getLogger(__name__)

FIG_ID = "convergence_curve"
_DIVERGENCE_ANNOTATION_THRESHOLD = 2.0


def _expand_json_paths(paths: list[str], pattern: str) -> list[Path]:
    expanded: list[Path] = []
    for raw_path in paths:
        path = Path(raw_path)
        if path.is_dir():
            expanded.extend(sorted(path.glob(pattern), key=lambda p: p.name))
        elif path.exists():
            expanded.append(path)
    return expanded


def _metric_from_dict(metrics_dict: dict | None) -> PerformanceMetrics | None:
    if not metrics_dict:
        return None

    valid_keys = PerformanceMetrics.__dataclass_fields__.keys()
    filtered = {k: v for k, v in metrics_dict.items() if k in valid_keys}
    if not filtered:
        return None

    try:
        return PerformanceMetrics(**filtered)
    except Exception:
        return None


def _collect_worker_metrics(paths: list[Path]) -> tuple[list[PerformanceMetrics], dict]:
    metrics: list[PerformanceMetrics] = []
    metadata: dict = {}

    for path in paths:
        try:
            with open(path, "r", encoding="utf-8") as f:
                data = json.load(f)
        except Exception as exc:
            logger.debug("Skipping %s during shared rescoring setup: %s", path, exc)
            continue

        if not metadata:
            tuning_session = data.get("tuning_session", {})
            metadata = {
                "workload": tuning_session.get("workload_type", "oltp"),
                "benchmark": tuning_session.get("benchmark_name"),
                "scoring_policy": data.get(
                    "scoring_policy", tuning_session.get("scoring_policy")
                ),
                "scoring_policy_version": data.get(
                    "scoring_policy_version",
                    tuning_session.get("scoring_policy_version"),
                ),
                "metric_reference_version": data.get(
                    "metric_reference_version",
                    tuning_session.get("metric_reference_version"),
                ),
            }

        history = data.get("generation_history", data.get("evaluation_history", []))
        for step in history:
            direct_metric = _metric_from_dict(step.get("metrics"))
            if direct_metric is not None:
                metrics.append(direct_metric)
                continue

            for worker in step.get("worker_scores", []) or []:
                worker_metric = _metric_from_dict(worker.get("metrics"))
                if worker_metric is not None:
                    metrics.append(worker_metric)

    return metrics, metadata


def _collect_comparison_arm_metrics(
    comparison_path: str | None,
) -> tuple[dict[str, list[PerformanceMetrics]], dict]:
    if comparison_path is None:
        return {}, {}

    path = Path(comparison_path)
    if not path.exists():
        return {}, {}

    try:
        with open(path, "r", encoding="utf-8") as f:
            data = json.load(f)
    except Exception as exc:
        logger.debug(
            "Skipping %s during shared comparison rescoring setup: %s", path, exc
        )
        return {}, {}

    metrics_by_arm: dict[str, list[PerformanceMetrics]] = {}
    for arm_name, runs in data.get("runs_by_arm", {}).items():
        for run in runs:
            metric = _metric_from_dict(run.get("metrics"))
            if metric is not None:
                metrics_by_arm.setdefault(arm_name, []).append(metric)

    scoring_metadata = data.get("scoring_metadata", {})
    metadata = {
        "workload": scoring_metadata.get("workload"),
        "benchmark": scoring_metadata.get("benchmark"),
        "scoring_policy": scoring_metadata.get("scoring_policy"),
        "scoring_policy_version": scoring_metadata.get("scoring_policy_version"),
        "metric_reference_version": scoring_metadata.get("metric_reference_version"),
    }
    metadata = {key: value for key, value in metadata.items() if value is not None}
    return metrics_by_arm, metadata


def _build_shared_metric_config(
    pbt_paths: list[str],
    bo_paths: list[str],
    comparison_path: str | None = None,
):
    all_paths = _expand_json_paths(pbt_paths, "pbt_results_*.json")
    all_paths.extend(_expand_json_paths(bo_paths, "bo_results_*.json"))

    metrics, metadata = _collect_worker_metrics(all_paths)
    comparison_arm_metrics, comparison_metadata = _collect_comparison_arm_metrics(
        comparison_path
    )
    for arm_metrics in comparison_arm_metrics.values():
        metrics.extend(arm_metrics)

    if not metrics:
        return None

    if not metadata:
        metadata = comparison_metadata

    metric_config, _, rescoring_metadata = rescore_metrics_globally(
        metrics=metrics,
        workload=metadata.get("workload", "oltp"),
        benchmark=metadata.get("benchmark"),
        scoring_policy=metadata.get("scoring_policy"),
        scoring_policy_version=metadata.get("scoring_policy_version"),
        metric_reference_version=metadata.get("metric_reference_version"),
    )
    logger.info(
        "Shared convergence rescoring config built from %d observations (%s)",
        len(metrics),
        rescoring_metadata.get("mode", "global"),
    )
    return metric_config


def _rescore_comparison_arm(
    comparison_path: str | None,
    arm_name: str,
    metric_config,
) -> np.ndarray | None:
    if metric_config is None:
        return None

    metrics_by_arm, _ = _collect_comparison_arm_metrics(comparison_path)
    arm_metrics = metrics_by_arm.get(arm_name, [])
    if not arm_metrics:
        return None

    return np.array(
        [metric_config.compute_score_value(metric) for metric in arm_metrics],
        dtype=float,
    )


def _step_values_at(x_source, y_source, x_grid) -> np.ndarray:
    x_arr = np.asarray(x_source, dtype=float)
    y_arr = np.asarray(y_source, dtype=float)
    grid = np.asarray(x_grid, dtype=float)
    indices = np.searchsorted(x_arr, grid, side="right") - 1
    values = np.full(len(grid), np.nan)
    valid = indices >= 0
    values[valid] = y_arr[indices[valid]]
    return values


def _find_divergence_point(
    pbt_x,
    pbt_y,
    bo_x,
    bo_y,
    threshold,
    min_consecutive=3,
) -> tuple[float, float, float] | None:
    start = max(float(np.min(pbt_x)), float(np.min(bo_x)))
    end = min(float(np.max(pbt_x)), float(np.max(bo_x)))
    if start >= end:
        return None

    x_grid = np.array(
        sorted(
            {
                float(x)
                for x in np.concatenate([np.asarray(pbt_x), np.asarray(bo_x)])
                if start <= float(x) <= end
            }
        )
    )
    if len(x_grid) == 0:
        return None

    pbt_step = _step_values_at(pbt_x, pbt_y, x_grid)
    bo_step = _step_values_at(bo_x, bo_y, x_grid)

    consecutive = 0
    for i, (pbt_val, bo_val) in enumerate(zip(pbt_step, bo_step, strict=True)):
        if np.isnan(pbt_val) or np.isnan(bo_val):
            consecutive = 0
            continue

        if pbt_val - bo_val > threshold:
            consecutive += 1
            if consecutive >= min_consecutive:
                div_idx = i - min_consecutive + 1
                return x_grid[div_idx], pbt_step[div_idx], bo_step[div_idx]
        else:
            consecutive = 0
    return None


def _plot_method_curve(ax, x, y, std, method_key, label, shade_band):
    style = get_method_style(method_key)
    style_subset = {"color": style["color"], "linestyle": style.get("linestyle", "-")}
    ax.plot(x, y, label=label, **style_subset)
    if shade_band and std is not None and not np.all(std == 0):
        ax.fill_between(x, y - std, y + std, alpha=0.15, color=style["color"])


def _add_divergence_annotation(ax, x_div, y_pbt, y_bo):
    ax.axvline(x=x_div, color="gray", linestyle="--", alpha=0.7)
    ax.annotate(
        "PBT separates",
        xy=(x_div, y_pbt),
        xytext=(x_div + (ax.get_xlim()[1] - ax.get_xlim()[0]) * 0.1, y_pbt + 2.0),
        arrowprops=dict(
            facecolor="gray", arrowstyle="->", connectionstyle="arc3,rad=.2"
        ),
        bbox=dict(boxstyle="round,pad=0.3", fc="white", ec="gray", alpha=0.8)
    )


def _find_baseline_crossing(
    x: np.ndarray,
    y: np.ndarray,
    baseline: float,
    higher_is_better: bool = True,
) -> tuple[float, float] | None:
    """Find the first x-value where y exceeds (or drops below) a baseline.

    Returns (x_cross, y_cross) or None if no crossing is found.
    Follows the LlamaTune Fig 9 convention of marking time-to-baseline-optimal.
    """
    for i in range(len(y)):
        exceeded = y[i] > baseline if higher_is_better else y[i] < baseline
        if exceeded:
            return float(x[i]), float(y[i])
    return None


def _add_baseline_crossing_marker(ax, x_cross, y_cross, label="Exceeds default"):
    """Add a red diamond marker at the baseline-crossing point (LlamaTune style)."""
    ax.plot(
        x_cross, y_cross,
        marker="D", markersize=8, color="#DC2626",
        markeredgecolor="white", markeredgewidth=1.2,
        zorder=10, label=label,
    )


def _compute_parallel_efficiency(
    sessions: list,
    n_workers: int,
) -> float | None:
    """Compute empirical parallel efficiency η from recorded generation timings.

    η = (mean_gen_elapsed) / (n_workers × mean_gen_elapsed)
      ≈ (P × G × T_iter_solo) / T_session_wall_clock   (when P=1 data unavailable)

    Without a P=1 control run we approximate T_iter_solo as
    mean(generation_elapsed_seconds) / n_workers, which gives a *lower bound*
    on true efficiency because lockstep overhead is baked into generation_elapsed.

    Returns η in [0, 1] or None if data is insufficient.
    """
    if not sessions or n_workers <= 1:
        return None

    all_gen_elapsed = []
    all_wall_clock = []
    for s in sessions:
        gen_elapsed = s.generation_elapsed_seconds
        wall_clock = s.wall_clock_seconds
        if len(gen_elapsed) > 0 and np.any(gen_elapsed > 0):
            all_gen_elapsed.append(gen_elapsed)
            all_wall_clock.append(wall_clock)

    if not all_gen_elapsed:
        return None

    # Average across seeds
    min_len = min(len(g) for g in all_gen_elapsed)
    gen_elapsed_mean = np.mean(
        np.vstack([g[:min_len] for g in all_gen_elapsed]), axis=0
    )
    wall_total = np.mean([w[min_len - 1] for w in all_wall_clock])

    if wall_total <= 0:
        return None

    # Ideal sequential time: each generation would run P iterations sequentially
    ideal_sequential_time = np.sum(gen_elapsed_mean) * n_workers
    # Actual wall-clock is wall_total
    # Speedup = ideal_sequential / actual, Efficiency = Speedup / P
    eta = ideal_sequential_time / (n_workers * wall_total)

    return float(np.clip(eta, 0.0, 1.0))


def generate(
    pbt_paths: list[str],
    bo_paths: list[str],
    comparison_path: str | None = None,
    output_dir: str = "figures/",
    venue: str = "pvldb",
    formats: list[str] | None = None,
    annotation: bool = True,
    metric_key: str | None = None,
) -> Figure:
    """Generate convergence curve figure.

    Parameters
    ----------
    metric_key : str | None
        Which value to plot on the Y-axis.  Accepted values:
        ``None`` / ``"score"`` – composite score (default, existing behaviour)
        ``"latency_p95"``     – raw P95 latency (ms, lower-is-better)
        ``"latency_p99"``     – raw P99 latency (ms, lower-is-better)
        ``"throughput"``      – raw throughput (TPS, higher-is-better)
    """
    # Normalise metric_key: None and "score" both mean composite-score mode
    if metric_key == "score":
        metric_key = None

    logger.info("Generating %s figure (metric_key=%s)", FIG_ID, metric_key or "score")

    shared_metric_config = _build_shared_metric_config(
        pbt_paths, bo_paths, comparison_path
    )

    sessions = []
    for path in pbt_paths:
        path_obj = Path(path)
        if path_obj.is_dir() and shared_metric_config is not None:
            for session_path in sorted(
                path_obj.glob("pbt_results_*.json"), key=lambda p: p.name
            ):
                sessions.append(
                    load_session(session_path, metric_config=shared_metric_config, metric_key=metric_key)
                )
        elif path_obj.is_dir():
            sessions.extend(load_sessions(path, metric_key=metric_key))
        else:
            sessions.append(load_session(path, metric_config=shared_metric_config, metric_key=metric_key))

    pbt_agg = aggregate_seeds(sessions)
    is_multi_seed = pbt_agg.n_seeds > 1
    if not is_multi_seed:
        logger.warning("Single PBT seed — no std band will be shown")
        
    bo_traces = []
    for path in bo_paths:
        path_obj = Path(path)
        if path_obj.is_dir():
            for trace_path in sorted(path_obj.glob("bo_results_*.json"), key=lambda p: p.name):
                bo_traces.append(load_bo_trace(trace_path, metric_config=shared_metric_config, metric_key=metric_key))
        else:
            bo_traces.append(load_bo_trace(path, metric_config=shared_metric_config, metric_key=metric_key))

    # Filter out empty traces (e.g. failed/aborted runs with 0 evaluations)
    bo_traces = [t for t in bo_traces if len(t.best_scores) > 0]
    if not bo_traces:
        raise DataLoadError("No non-empty BO baseline traces found")

    is_bo_multi_seed = len(bo_traces) > 1
    min_bo_len = min(len(t.best_scores) for t in bo_traces)
    bo_best_scores_stack = np.vstack([t.best_scores[:min_bo_len] for t in bo_traces])
    bo_mean_best = np.mean(bo_best_scores_stack, axis=0)
    bo_std_best = np.std(bo_best_scores_stack, axis=0, ddof=1) if is_bo_multi_seed else None
    
    bo_wall_stack = np.vstack([t.wall_clock_seconds[:min_bo_len] for t in bo_traces])
    bo_wall_mean = np.mean(bo_wall_stack, axis=0)
    
    # ── Resolve default baseline value ──────────────────────────────────
    default_score = None
    if metric_key is not None:
        # Raw-metric mode: pull from comparison arm metrics directly
        arm_metrics, _ = _collect_comparison_arm_metrics(comparison_path) if comparison_path else ({}, {})
        arm_list = arm_metrics.get("default", [])
        if arm_list:
            vals = [_extract_raw_value(m, metric_key) for m in arm_list]
            default_score = float(np.mean(vals))
            logger.info("Default %s from comparison arm: %.4f", metric_key, default_score)
        if default_score is None:
            # Fallback to first PBT generation value
            default_score = sessions[0].best_scores[0]
            logger.warning("Default %s inferred from PBT initial generation", metric_key)
    else:
        # Composite-score mode (existing logic)
        default_scores = _rescore_comparison_arm(
            comparison_path, "default", shared_metric_config
        )
        if default_scores is not None:
            default_score = float(np.mean(default_scores))
            logger.info("Default score rescored with shared config: %.4f", default_score)

        if default_score is None and comparison_path is not None:
            try:
                comp = load_comparison(comparison_path)
                default_score = list(comp.default_summaries.values())[0].mean
                logger.info("Default score loaded from comparison: %.4f", default_score)
            except Exception:
                from src.visualization.loaders.comparison import load_multi_arm_comparison
                comp = load_multi_arm_comparison(comparison_path)
                if "default" in comp.summaries_by_arm and comp.summaries_by_arm["default"]:
                    default_score = list(comp.summaries_by_arm["default"].values())[0].mean
                    logger.info("Default score loaded from multi-arm comparison: %.4f", default_score)

        if default_score is None:
            default_score = sessions[0].best_scores[0]
            logger.warning("Default score inferred from PBT initial generation — no comparison file provided")
        
    logger.info("Data loaded: %d PBT seeds, %d BO seeds", pbt_agg.n_seeds, len(bo_traces))
    
    theme = PBTuneTheme(venue=venue)
    with theme.apply():
        fig, (ax_left, ax_right) = theme.subplots(1, 2, size_hint="double", sharey=True)
        
        n_workers = sessions[0].metadata["n_workers"]
        pbt_evals = np.array(pbt_agg.generations) * n_workers
        _plot_method_curve(ax_left, pbt_evals, pbt_agg.mean_best, pbt_agg.std_best if is_multi_seed else None, "pbtune", "PBTune", is_multi_seed)
        
        bo_evals = np.array(bo_traces[0].evaluations[:min_bo_len])
        _plot_method_curve(ax_left, bo_evals, bo_mean_best, bo_std_best if is_bo_multi_seed else None, "bo_smac", "BO-SMAC", is_bo_multi_seed)
        
        if default_score is not None:
            default_style = get_method_style("default")
            ax_left.axhline(
                default_score, 
                color=default_style["color"], 
                linestyle=default_style["linestyle"], 
                label="Default", 
                zorder=1
            )
        
        # ── Dynamic Y-axis label ────────────────────────────────────────
        _Y_LABELS = {
            None: "Composite Score",
            "latency_p95": "Latency P95 (ms)",
            "latency_p99": "Latency P99 (ms)",
            "throughput": "Throughput (TPS)",
        }
        y_label = _Y_LABELS.get(metric_key, "Composite Score")

        ax_left.set_xlabel("Cumulative Evaluations")
        ax_left.set_ylabel(y_label)
        set_integer_ticks(ax_left, axis="x")
        auto_grid(ax_left)
        
        if is_multi_seed:
            min_gens = len(pbt_agg.generations)
            pbt_wall = np.mean(np.vstack([s.wall_clock_seconds[:min_gens] for s in sessions]), axis=0)
        else:
            pbt_wall = np.array(sessions[0].wall_clock_seconds)
            
        _plot_method_curve(ax_right, pbt_wall, pbt_agg.mean_best, pbt_agg.std_best if is_multi_seed else None, "pbtune", "PBTune", is_multi_seed)
        _plot_method_curve(ax_right, bo_wall_mean, bo_mean_best, bo_std_best if is_bo_multi_seed else None, "bo_smac", "BO-SMAC", is_bo_multi_seed)
        
        if default_score is not None:
            default_style = get_method_style("default")
            ax_right.axhline(
                default_score, 
                color=default_style["color"], 
                linestyle=default_style["linestyle"], 
                label="Default", 
                zorder=1
            )
        
        ax_right.set_xlabel("Wall-Clock Time (s)")
        
        if annotation:
            div_res = _find_divergence_point(
                pbt_wall,
                pbt_agg.mean_best,
                bo_wall_mean,
                bo_mean_best,
                _DIVERGENCE_ANNOTATION_THRESHOLD,
            )
            if div_res is not None:
                x_div, y_pbt, y_bo = div_res
                _add_divergence_annotation(ax_right, x_div, y_pbt, y_bo)

        # ── LlamaTune-style baseline-crossing marker ────────────────────
        if default_score is not None and annotation:
            # Determine direction: for latency metrics, lower is better
            higher_is_better = not (metric_key and metric_key.startswith("latency"))

            # Mark on evaluations axis (left panel)
            cross_evals = _find_baseline_crossing(
                pbt_evals, pbt_agg.mean_best, default_score,
                higher_is_better=higher_is_better,
            )
            if cross_evals is not None:
                _add_baseline_crossing_marker(
                    ax_left, cross_evals[0], cross_evals[1],
                    label=f"Exceeds default (eval {int(cross_evals[0])})",
                )
                logger.info(
                    "PBTune exceeds default at evaluation %d", int(cross_evals[0])
                )

            # Mark on wall-clock axis (right panel)
            cross_wall = _find_baseline_crossing(
                pbt_wall, pbt_agg.mean_best, default_score,
                higher_is_better=higher_is_better,
            )
            if cross_wall is not None:
                _add_baseline_crossing_marker(
                    ax_right, cross_wall[0], cross_wall[1],
                    label=f"Exceeds default ({cross_wall[0]:.0f}s)",
                )
                logger.info(
                    "PBTune exceeds default at %.0fs wall-clock", cross_wall[0]
                )

        # ── Parallelism factor η ────────────────────────────────────────
        eta = _compute_parallel_efficiency(sessions, n_workers)
        caption_parts = []
        if eta is not None:
            overhead_pct = (1.0 - eta) * 100
            caption_parts.append(
                f"Empirical parallel efficiency \u03b7 = {eta:.2f} "
                f"(~{overhead_pct:.0f}% overhead vs. ideal linear scaling, "
                f"P = {n_workers} workers)"
            )
            logger.info("Parallel efficiency η = %.3f (P=%d)", eta, n_workers)

        auto_grid(ax_right)
        
        add_panel_labels([ax_left, ax_right])
        ax_right.legend(loc="lower right")

        if caption_parts:
            fig.text(
                0.5, -0.02, "; ".join(caption_parts),
                ha="center", va="top", fontsize=7, style="italic",
                color="#4B5563",
            )

        fig.tight_layout(rect=[0, 0.03, 1, 1] if caption_parts else None)
        
        fmt_list = [ExportFormat(f) for f in (formats or ["pdf", "png"])]
        export_figure(fig, output_dir, FIG_ID, formats=fmt_list)
        logger.info("Export complete for %s", FIG_ID)
        
    return fig

register_figure(FigureSpec(
    fig_id=FIG_ID,
    paper_label="fig:convergence",
    title="Convergence Curves: PBT vs BO",
    section="evaluation",
    category="convergence",
    size_hint="double",
    generator=generate,
    data_requirements=["session_json", "baseline_json"],
    description="Score vs evaluations and wall-clock time comparison."
))
