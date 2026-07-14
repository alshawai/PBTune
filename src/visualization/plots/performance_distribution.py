import logging
from pathlib import Path
import numpy as np
from matplotlib.figure import Figure
import seaborn as sns
from scipy.stats import mannwhitneyu

from src.visualization.theme import PBTuneTheme
from src.visualization.colors import get_method_style
from src.visualization.export import export_figure
from src.visualization.types import FigureSpec, ExportFormat
from src.visualization.registry import register_figure
from src.visualization.loaders import (
    load_sessions, load_session, load_bo_trace, load_comparison
)
from src.visualization.plots.convergence_curve import (
    _build_shared_metric_config,
    _rescore_comparison_arm,
)

logger = logging.getLogger(__name__)

FIG_ID = "performance_distribution"
_METHOD_ORDER = ["Default", "BO-SMAC", "PBTune"]
_SIG_THRESHOLDS = [(0.001, "***"), (0.01, "**"), (0.05, "*")]

def _try_import_raincloud():
    try:
        import ptitprince
        return ptitprince
    except ImportError:
        logger.debug("ptitprince not available, falling back to seaborn violin+strip")
        return None

def _significance_label(p: float) -> str:
    for threshold, label in _SIG_THRESHOLDS:
        if p <= threshold:
            return label
    return "ns"

def _add_significance_bracket(ax, x1, x2, y, label):
    h = 0.02 * (ax.get_ylim()[1] - ax.get_ylim()[0])
    ax.plot([x1, x1, x2, x2], [y, y+h, y+h, y], lw=1.5, c='black')
    ax.text((x1+x2)*0.5, y+h + h*0.2, label, ha='center', va='bottom', color='black')

def _collect_final_scores(
    pbt_sessions,
    bo_traces,
    comparison_data,
    comparison_path: str | None = None,
    metric_config=None,
    metric_key: str | None = None,
) -> dict[str, np.ndarray]:
    res = {}
    target_metric = metric_key or "score"
    if comparison_path is not None and metric_config is not None:
        for arm_name, method_name in (
            ("default", "Default"),
            ("bo", "BO-SMAC"),
            ("pbt", "PBTune"),
        ):
            arm_scores = _rescore_comparison_arm(
                comparison_path, arm_name, metric_config
            )
            if arm_scores is not None:
                res[method_name] = arm_scores

    if comparison_data is not None:
        is_multi = hasattr(comparison_data, "summaries_by_arm")
        
        # Load Default
        if "Default" in res:
            pass
        elif is_multi and "default" in comparison_data.summaries_by_arm and target_metric in comparison_data.summaries_by_arm["default"]:
            stat_summary = comparison_data.summaries_by_arm["default"][target_metric]
            res["Default"] = np.array(stat_summary.values) if stat_summary.values and len(stat_summary.values) > 1 else np.array([stat_summary.mean])
        elif not is_multi and target_metric in comparison_data.default_summaries:
            stat_summary = comparison_data.default_summaries[target_metric]
            res["Default"] = np.array(stat_summary.values) if stat_summary.values and len(stat_summary.values) > 1 else np.array([stat_summary.mean])
            
        # Try to load BO from comparison
        if "BO-SMAC" in res:
            pass
        elif is_multi and "bo" in comparison_data.summaries_by_arm and target_metric in comparison_data.summaries_by_arm["bo"]:
            stat_summary = comparison_data.summaries_by_arm["bo"][target_metric]
            res["BO-SMAC"] = np.array(stat_summary.values) if stat_summary.values and len(stat_summary.values) > 1 else np.array([stat_summary.mean])
        else:
            if target_metric == "score":
                res["BO-SMAC"] = np.array([t.best_scores[-1] for t in bo_traces])
            else:
                from src.visualization.loaders.session import _extract_raw_value
                res["BO-SMAC"] = np.array([_extract_raw_value(t.best_metrics[-1], target_metric) for t in bo_traces])
            
        # Try to load PBT from comparison
        if "PBTune" in res:
            pass
        elif is_multi and "pbt" in comparison_data.summaries_by_arm and target_metric in comparison_data.summaries_by_arm["pbt"]:
            stat_summary = comparison_data.summaries_by_arm["pbt"][target_metric]
            res["PBTune"] = np.array(stat_summary.values) if stat_summary.values and len(stat_summary.values) > 1 else np.array([stat_summary.mean])
        else:
            if target_metric == "score":
                res["PBTune"] = np.array([s.best_scores[-1] for s in pbt_sessions])
            else:
                from src.visualization.loaders.session import _extract_raw_value
                res["PBTune"] = np.array([_extract_raw_value(s.best_metrics[-1], target_metric) for s in pbt_sessions])
            
    else:
        if target_metric == "score":
            res["BO-SMAC"] = np.array([t.best_scores[-1] for t in bo_traces])
            res["PBTune"] = np.array([s.best_scores[-1] for s in pbt_sessions])
        else:
            from src.visualization.loaders.session import _extract_raw_value
            res["BO-SMAC"] = np.array([_extract_raw_value(t.best_metrics[-1], target_metric) for t in bo_traces])
            res["PBTune"] = np.array([_extract_raw_value(s.best_metrics[-1], target_metric) for s in pbt_sessions])
    
    return res

def generate(
    pbt_paths: list[str] | None = None,
    bo_paths: list[str] | None = None,
    comparison_path: str | None = None,
    output_dir: str = "figures/",
    venue: str = "pvldb",
    formats: list[str] | None = None,
    show_significance: bool = False,
    data_dir: Path | str | None = None,
    theme: PBTuneTheme | None = None,
    metric_key: str | None = None,
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
            
    bo_traces = []
    for path in bo_paths:
        path_obj = Path(path)
        if path_obj.is_dir():
            for trace_path in sorted(path_obj.glob("bo_results_*.json"), key=lambda p: p.name):
                bo_traces.append(load_bo_trace(trace_path, metric_config=shared_metric_config, metric_key=metric_key))
        else:
            bo_traces.append(load_bo_trace(path, metric_config=shared_metric_config, metric_key=metric_key))
        
    comp_data = None
    if comparison_path is not None:
        try:
            comp_data = load_comparison(comparison_path)
        except Exception:
            from src.visualization.loaders.comparison import load_multi_arm_comparison
            comp_data = load_multi_arm_comparison(comparison_path)
        
    scores_dict = _collect_final_scores(
        sessions,
        bo_traces,
        comp_data,
        comparison_path=comparison_path,
        metric_config=shared_metric_config,
        metric_key=metric_key,
    )
    
    # Log sample sizes
    n_pbt = len(scores_dict.get("PBTune", []))
    n_bo = len(scores_dict.get("BO-SMAC", []))
    n_def = len(scores_dict.get("Default", []))
    logger.info("Data loaded: PBT n=%d, BO n=%d, Default n=%d", n_pbt, n_bo, n_def)
    
    _theme = theme or PBTuneTheme(venue=venue)
    with _theme.apply():
        fig, ax = _theme.figure(size_hint="single", aspect=0.85)
        
        pt = _try_import_raincloud()
        
        # Prepare data for plotting
        plot_methods = [m for m in _METHOD_ORDER if m in scores_dict]
        
        if pt is not None:
            data_x = []
            data_y = []
            palette = {}
            for m in plot_methods:
                vals = scores_dict[m]
                data_x.extend([m]*len(vals))
                data_y.extend(vals)
                
                method_key = "default" if m == "Default" else ("bo_smac" if m == "BO-SMAC" else "pbtune")
                palette[m] = get_method_style(method_key)["color"]
                
            pt.RainCloud(x=data_x, y=data_y, palette=palette, bw=.2, width_viol=.6, ax=ax, orient="v", alpha=.65)
            
            # Plot scalar default if needed
            if "Default" in plot_methods and len(scores_dict["Default"]) == 1:
                default_val = scores_dict["Default"][0]
                ax.axhline(y=default_val, color=get_method_style("default")["color"], linestyle=":", alpha=0.8)
                ax.scatter([plot_methods.index("Default")], [default_val], marker="D", s=100, color=get_method_style("default")["color"], zorder=5)

        else:
            data_x = []
            data_y = []
            palette = {}
            for m in plot_methods:
                if m == "Default" and len(scores_dict[m]) == 1:
                    continue # skip violin for scalar
                vals = scores_dict[m]
                data_x.extend([m]*len(vals))
                data_y.extend(vals)
                
                method_key = "default" if m == "Default" else ("bo_smac" if m == "BO-SMAC" else "pbtune")
                palette[m] = get_method_style(method_key)["color"]
                
            if len(data_x) > 0:
                sns.violinplot(x=data_x, y=data_y, palette=palette, inner=None, ax=ax, alpha=0.5)
                sns.stripplot(x=data_x, y=data_y, palette=palette, size=6, ax=ax, jitter=True, alpha=0.8)
                
            # Plot scalar default if needed
            if "Default" in plot_methods and len(scores_dict["Default"]) == 1:
                default_val = scores_dict["Default"][0]
                ax.axhline(y=default_val, color=get_method_style("default")["color"], linestyle=":", alpha=0.8)
                ax.scatter([plot_methods.index("Default")], [default_val], marker="D", s=100, color=get_method_style("default")["color"], zorder=5)

        if show_significance:
            pairs = [("Default", "BO-SMAC"), ("BO-SMAC", "PBTune"), ("Default", "PBTune")]
            
            y_max = max([np.max(v) for v in scores_dict.values()]) if len(scores_dict) > 0 else 1.0
            
            # staggered heights: first at y_max * 1.05, second at y_max * 1.12, third at y_max * 1.19
            heights = [y_max * 1.05, y_max * 1.12, y_max * 1.19]
            h_idx = 0
            
            for m1, m2 in pairs:
                if m1 in plot_methods and m2 in plot_methods:
                    v1 = scores_dict[m1]
                    v2 = scores_dict[m2]
                    if len(v1) >= 2 and len(v2) >= 2:
                        u, p = mannwhitneyu(v1, v2, alternative="two-sided")
                        sig_lbl = _significance_label(p)
                        logger.info("Mann-Whitney U (%s vs %s): U=%.2f, p=%.4e → %s", m1, m2, u, p, sig_lbl)
                        
                        x1 = plot_methods.index(m1)
                        x2 = plot_methods.index(m2)
                        
                        _add_significance_bracket(ax, x1, x2, heights[h_idx], sig_lbl)
                        h_idx += 1
                        
            if h_idx > 0:
                ax.set_ylim(top=heights[h_idx-1] + (y_max * 0.1))
                
        ax.set_xlabel("Method")
        
        y_label = "Final Best Composite Score"
        if metric_key == "throughput": y_label = "Throughput (txn/sec)"
        elif metric_key == "latency_p99": y_label = "99th %-tile Latency (ms)"
        elif metric_key == "latency_p95": y_label = "95th %-tile Latency (ms)"
        ax.set_ylabel(y_label)
        

        fig.tight_layout()
        
        fmt_list = [ExportFormat(f) for f in (formats or ["pdf", "png"])]
        export_figure(fig, output_dir, FIG_ID, formats=fmt_list)
        logger.info("Export complete for %s", FIG_ID)
        
    return fig

register_figure(FigureSpec(
    fig_id=FIG_ID,
    paper_label="fig:performance_dist",
    title="Performance Distribution by Method",
    section="evaluation",
    category="performance",
    size_hint="single",
    generator=generate,
    data_requirements=["session_json", "baseline_json", "comparison_json"],
    description="Violin/raincloud plot of final scores with significance brackets."
))
