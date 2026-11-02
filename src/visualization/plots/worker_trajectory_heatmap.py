"""Worker trajectory heatmap plot."""

import json
import logging
from pathlib import Path

import numpy as np
import matplotlib.pyplot as plt

from src.tuners.utils.session_schema import get_history
from src.visualization.theme import PBTuneTheme
from src.visualization.export import export_figure
from src.visualization.types import FigureSpec, ExportFormat
from src.visualization.registry import register_figure
from src.visualization.loaders.session import load_session
from src.visualization.loaders.discovery import discover_session_traces
from src.visualization.utils import set_integer_ticks

logger = logging.getLogger(__name__)

def generate_worker_heatmap(
    data_dir: Path | str | None = None,
    output_dir: str = "figures/",
    venue: str = "pvldb",
    formats: list[str] | None = None,
    theme: PBTuneTheme | None = None,
    **kwargs,
):
    if not data_dir:
        raise ValueError("data_dir is required for worker_trajectory_heatmap plot.")
        
    data_dir_path = Path(data_dir)
    json_files = discover_session_traces(data_dir_path)
    if not json_files and (data_dir_path / "traces").is_dir():
        json_files = discover_session_traces(data_dir_path / "traces")
        
    if not json_files:
        raise ValueError(f"No session JSON traces found in {data_dir_path} or its 'traces' subdirectory.")
        
    session_path = json_files[0]
    logger.info("Generating worker trajectory heatmap from %s", session_path)
    
    # 1. Load data
    trace = load_session(session_path)
    
    with open(session_path, "r", encoding="utf-8") as f:
        data = json.load(f)
        
    worker_ids = set()
    for gen in get_history(data):
        for ws in gen.get("worker_scores", []):
            if "worker_id" in ws:
                worker_ids.add(ws["worker_id"])
    
    sorted_worker_ids = sorted(list(worker_ids))
    worker_id_to_idx = {wid: idx for idx, wid in enumerate(sorted_worker_ids)}
    
    # 2. Extract matrix [n_workers x n_gens]
    matrix = np.vstack(trace.worker_scores)
    
    # MANUAL PATCH: If worker 0 has all 0.0 scores, compute it here to bypass loader bugs
    if 0 in worker_id_to_idx and np.all(matrix[worker_id_to_idx[0]] == 0.0):
        from src.utils.scoring import create_scoring_engine
        from src.utils.metrics import PerformanceMetrics
        
        w0_idx = worker_id_to_idx[0]
        engine = create_scoring_engine(trace.metric_config)
        for i, gen in enumerate(get_history(data)):
            for ws in gen.get("worker_scores", []):
                if ws.get("worker_id") == 0 and "metrics" in ws:
                    valid_keys = PerformanceMetrics.__dataclass_fields__.keys()
                    filtered = {k: v for k, v in ws["metrics"].items() if k in valid_keys}
                    try:
                        pm = PerformanceMetrics(**filtered)
                        if hasattr(trace.metric_config, "compute_score_value"):
                            score = trace.metric_config.compute_score_value(pm)
                        else:
                            score = engine.compute_breakdown(pm).final_score
                        matrix[w0_idx][i] = score
                    except Exception:
                        pass

    # Mask out exactly 0.0 (which usually means missing/failed/baseline worker) 
    # to prevent it from crushing the color scale for the rest of the workers.
    matrix[matrix == 0.0] = np.nan
    n_workers, n_gens = matrix.shape
    
    _theme = theme or PBTuneTheme(venue=venue)
    
    with _theme.apply():
        fig, ax = _theme.subplots(1, 1, size_hint="single")
        
        # Adjust figure size for aspect ~0.6 (height/width)
        w, h = fig.get_size_inches()
        fig.set_size_inches(w, w * 0.6)
        
        # Plot heatmap
        im = ax.imshow(
            matrix,
            aspect="auto",
            origin="lower",
            cmap=plt.cm.viridis,
            extent=[0.5, n_gens + 0.5, -0.5, n_workers - 0.5]
        )
        
        # Add colorbar
        cbar = fig.colorbar(im, ax=ax, pad=0.02)
        cbar.set_label("Composite Score")
        
        # 3. Add exploit events
        marker_plotted = False
        for event in trace.exploit_events:
            gen = event.get("generation")
            # In session traces, exploit target is often 'target' or 'target_worker_id'
            target_id = event.get("target", event.get("target_worker_id"))
            
            if gen is not None and target_id in worker_id_to_idx:
                target_idx = worker_id_to_idx[target_id]
                
                # Plot small red marker
                ax.plot(
                    gen, 
                    target_idx, 
                    marker="v", 
                    color="red", 
                    markersize=5, 
                    label="Exploit Event" if not marker_plotted else None,
                    linestyle="None"
                )
                marker_plotted = True
                
        # 4. Format axes
        ax.set_xlabel("Generation")
        ax.set_ylabel("Worker")
        
        set_integer_ticks(ax, axis="x")
        
        ax.set_yticks(range(n_workers))
        short_labels = [str(wid).replace("worker_", "W") if str(wid).startswith("worker_") else f"W{wid}" for wid in sorted_worker_ids]
        ax.set_yticklabels(short_labels)
        
        if marker_plotted:
            ax.legend(loc="upper right", framealpha=0.9)
            
        fig.tight_layout()
        
        fmt_list = [ExportFormat(f) for f in (formats or ["pdf", "png"])]
        export_figure(fig, output_dir, "worker_trajectory_heatmap", formats=fmt_list)
        logger.info("Export complete for worker_trajectory_heatmap")
        
    return fig


register_figure(
    FigureSpec(
        fig_id="worker_trajectory_heatmap",
        paper_label="fig:worker_heatmap",
        title="Worker Trajectory Heatmap",
        section="evaluation",
        category="dynamics",
        size_hint="single",
        generator=generate_worker_heatmap,
        data_requirements=["data_dir"],
        description="Heatmap showing worker scores over generations with exploit events."
    )
)
