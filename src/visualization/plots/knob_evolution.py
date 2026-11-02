import logging
import math
from pathlib import Path

import numpy as np
import matplotlib.pyplot as plt

from src.visualization.theme import PBTuneTheme
from src.visualization.export import export_figure
from src.visualization.types import FigureSpec, ExportFormat
from src.visualization.registry import register_figure
from src.visualization.loaders.session import load_sessions
from src.visualization.loaders.importance import load_importance_from_dir
from src.visualization.utils import set_integer_ticks, auto_grid

logger = logging.getLogger(__name__)


def generate_knob_evolution(
    data_dir: Path | str,
    output_dir: Path | str = "figures/",
    theme: PBTuneTheme | None = None,
    formats: list[ExportFormat] | None = None,
    top_k_importance: int = 6,
    **kwargs,
) -> None:
    """Generate the knob evolution multi-panel figure.

    Plots the trajectory of the top-K knobs over time for each worker, showing
    how workers explore the parameter space and converge or diverge.
    """
    logger.info("Generating knob_evolution figure from %s", data_dir)
    
    _theme = theme or PBTuneTheme(venue="pvldb")
    fmt_list = formats or [ExportFormat("pdf"), ExportFormat("png")]
    
    data_dir_path = Path(data_dir)
    sessions = load_sessions(data_dir_path)
    if not sessions:
        logger.warning("No session data found in %s", data_dir_path)
        return
        
    session = sessions[0]
    worker_configs = session.worker_configs
    
    if not worker_configs or not worker_configs[0]:
        logger.warning("No worker configs found in session.")
        return
        
    num_gens = len(worker_configs)
    
    # Identify unique worker IDs from the first generation
    # Or just track them by index if worker IDs aren't directly available in configs
    # We will assume each worker's position in the list corresponds to the same worker
    # across generations. If workers can die/spawn differently, this might be tricky,
    # but typically worker_configs[gen][worker_idx] maps cleanly.
    num_workers = len(worker_configs[0])
    
    top_knobs = []
    
    # 1. Try to load precomputed ImportanceData
    try:
        # Search up the tree for importance_results.json
        importance_json_path = None
        for p in [data_dir_path] + list(data_dir_path.parents):
            candidate = p / "analysis_results" / "importance_results.json"
            if candidate.exists():
                importance_json_path = candidate
                break
            candidate = p / "importance_results.json"
            if candidate.exists():
                importance_json_path = candidate
                break

        if importance_json_path:
            import json
            with open(importance_json_path, "r") as f:
                imp_data = json.load(f)
            marginals = imp_data.get("marginal_importances", {})
            sorted_knobs = sorted(marginals.keys(), key=lambda k: marginals[k], reverse=True)
            top_knobs = sorted_knobs[:top_k_importance]
            logger.info("Loaded top %d knobs from precomputed %s: %s", len(top_knobs), importance_json_path.name, top_knobs)
        else:
            importance_data = load_importance_from_dir(data_dir_path)
            top_knobs = importance_data.knob_names[:top_k_importance]
            logger.info("Loaded top %d knobs from on-the-fly ImportanceData: %s", len(top_knobs), top_knobs)
    except Exception as e:
        logger.info("ImportanceData not found or failed to load (%s). Falling back to variance.", e)
        
    # 2. Fallback: highest variance knobs
    if not top_knobs:
        knob_values = {} # knob -> list of all observed values
        
        for gen_configs in worker_configs:
            for wc in gen_configs:
                actual_config = wc.get("config", wc)
                for knob, val in actual_config.items():
                    if knob in ("worker_id", "generation", "score", "step", "timestamp"): continue
                    if isinstance(val, (int, float, bool)):
                        knob_values.setdefault(knob, []).append(float(val))
                        
        variances = []
        for knob, values in knob_values.items():
            if len(values) > 1:
                # normalize before variance to ensure fairness
                min_val, max_val = min(values), max(values)
                if max_val > min_val:
                    norm_vals = [(v - min_val) / (max_val - min_val) for v in values]
                    var = np.var(norm_vals)
                    variances.append((knob, var))
                    
        # Sort by variance descending
        variances.sort(key=lambda x: x[1], reverse=True)
        top_knobs = [k for k, _ in variances[:top_k_importance]]
        logger.info("Selected top %d knobs by variance: %s", len(top_knobs), top_knobs)
        
    if not top_knobs:
        logger.warning("No varying knobs found to plot.")
        return
        
    # 3. Setup dynamic grid
    k = len(top_knobs)
    ncols = min(3, k)
    nrows = math.ceil(k / ncols)
    
    with _theme.apply():
        fig, axes = _theme.subplots(nrows, ncols, size_hint="double")
        axes_flat = np.atleast_1d(axes).flatten()
        
        generations = np.arange(1, num_gens + 1)
        
        # Use a qualitative colormap for different workers
        cmap = plt.get_cmap("tab10")
        
        categorical_maps = {} # knob -> {string_val: int_id}

        for idx, knob in enumerate(top_knobs):
            ax = axes_flat[idx]
            
            # Collect trajectories to plot and map categoricals
            plotted_trajs = []
            for w_idx in range(num_workers):
                traj = []
                for gen_idx in range(num_gens):
                    if w_idx < len(worker_configs[gen_idx]):
                        wc = worker_configs[gen_idx][w_idx]
                        actual_config = wc.get("config", wc)
                        val = actual_config.get(knob, None)
                        if val is not None:
                            try:
                                num_val = float(val)
                            except (ValueError, TypeError):
                                # It's a categorical string, map it to an integer
                                cmap_dict = categorical_maps.setdefault(knob, {})
                                if val not in cmap_dict:
                                    cmap_dict[val] = len(cmap_dict)
                                num_val = float(cmap_dict[val])
                            traj.append(num_val)
                        else:
                            traj.append(np.nan)
                    else:
                        traj.append(np.nan)
                plotted_trajs.append(traj)

            # Plot all trajectories
            for w_idx, traj in enumerate(plotted_trajs):
                ax.plot(
                    generations, 
                    traj, 
                    color=cmap(w_idx % 10), 
                    alpha=0.5, 
                    linewidth=1.5,
                    marker="o",
                    markersize=4,
                    label=f"Worker {w_idx}" if idx == 0 else None
                )
                
            ax.set_title(knob)
            ax.grid(True, linestyle="--", alpha=0.3)
            if idx >= len(top_knobs) - ncols:
                ax.set_xlabel("Generation")
                
            # Format Y-axis for categoricals
            if knob in categorical_maps:
                cmap_dict = categorical_maps[knob]
                ax.set_yticks(list(cmap_dict.values()))
                ax.set_yticklabels(list(cmap_dict.keys()))
            
            set_integer_ticks(ax, axis="x")
            auto_grid(ax)
            
        # Hide any unused subplots
        for idx in range(k, len(axes_flat)):
            fig.delaxes(axes_flat[idx])
            
        # Add a global legend
        handles, labels = axes_flat[0].get_legend_handles_labels()
        if handles:
            fig.legend(handles, labels, loc='upper center', bbox_to_anchor=(0.5, 1.0), ncol=min(num_workers, 6), frameon=False)
            fig.tight_layout(rect=[0, 0, 1, 0.90])
        else:
            fig.tight_layout()
            
        export_figure(fig, output_dir, "knob_evolution", formats=fmt_list)
        logger.info("Export complete for knob_evolution")


register_figure(
    FigureSpec(
        fig_id="knob_evolution",
        paper_label="fig:knob_evolution",
        title="Knob Evolution",
        section="evaluation",
        category="dynamics",
        size_hint="double",
        generator=generate_knob_evolution,
        data_requirements=["session_json"],
        description="Trajectories of top-K knobs over generations for each worker.",
    )
)
