import logging
from pathlib import Path

import numpy as np

from src.visualization.theme import PBTuneTheme
from src.visualization import METRIC_COLORS
from src.visualization.export import export_figure
from src.visualization.types import FigureSpec, ExportFormat
from src.visualization.registry import register_figure
from src.visualization.loaders.session import load_sessions
from src.visualization.utils import set_integer_ticks, auto_grid

logger = logging.getLogger(__name__)


def generate_population_diversity(
    data_dir: Path | str,
    output_dir: Path | str = "figures/",
    theme: PBTuneTheme | None = None,
    formats: list[ExportFormat] | None = None,
    **kwargs,
) -> None:
    """Generate the population diversity figure.

    Calculates the mean pairwise Euclidean distance between all workers at each
    generation, normalized into a [0, 1] knob space, to show how well PBT
    maintains exploration over time.
    """
    logger.info("Generating population_diversity figure from %s", data_dir)
    
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
    
    # Gather all knob bounds to normalize space to [0, 1]
    knob_mins = {}
    knob_maxs = {}
    
    for gen_configs in worker_configs:
        for wc in gen_configs:
            actual_config = wc.get("config", wc)
            for knob, val in actual_config.items():
                if knob in ("worker_id", "generation", "score", "step", "timestamp"): continue
                if isinstance(val, (int, float, bool)):
                    # handle bool as int
                    num_val = float(val)
                    if knob not in knob_mins:
                        knob_mins[knob] = num_val
                        knob_maxs[knob] = num_val
                    else:
                        knob_mins[knob] = min(knob_mins[knob], num_val)
                        knob_maxs[knob] = max(knob_maxs[knob], num_val)
                        
    # Filter out knobs that don't vary at all (max == min)
    valid_knobs = {k for k in knob_mins if knob_maxs[k] > knob_mins[k]}
    
    if not valid_knobs:
        logger.warning("No knobs with variance found.")
        return

    # Compute diversity metric per generation
    diversity_scores = []
    
    for gen_configs in worker_configs:
        vectors = []
        for wc in gen_configs:
            actual_config = wc.get("config", wc)
            vec = []
            for knob in valid_knobs:
                raw_val = actual_config.get(knob, knob_mins[knob])
                try:
                    val = float(raw_val)
                except (ValueError, TypeError):
                    val = knob_mins[knob]
                
                norm_val = (val - knob_mins[knob]) / (knob_maxs[knob] - knob_mins[knob])
                vec.append(norm_val)
            vectors.append(vec)
            
        vectors = np.array(vectors)
        num_workers = len(vectors)
        
        if num_workers <= 1:
            diversity_scores.append(0.0)
            continue
            
        distances = []
        for i in range(num_workers):
            for j in range(i + 1, num_workers):
                dist = np.linalg.norm(vectors[i] - vectors[j])
                distances.append(dist)
                
        mean_dist = np.mean(distances) if distances else 0.0
        diversity_scores.append(mean_dist)
        
    with _theme.apply():
        fig, ax = _theme.subplots(1, 1, size_hint="single")
        
        generations = np.arange(1, num_gens + 1)
        color = METRIC_COLORS.get("score", "#2ca02c")
        
        ax.plot(
            generations, 
            diversity_scores, 
            color=color, 
            linewidth=2.0, 
            marker="o", 
            markersize=4,
            label="Population Diversity"
        )
        
        ax.set_xlabel("Generation")
        ax.set_ylabel("Mean Pairwise Distance")
        max_val = max(diversity_scores) if diversity_scores else 1.0
        ax.set_ylim(bottom=0, top=max_val * 1.1)
        
        set_integer_ticks(ax, axis="x")
        auto_grid(ax)
        
        fig.tight_layout()
        export_figure(fig, output_dir, "population_diversity", formats=fmt_list)
        logger.info("Export complete for population_diversity")


register_figure(
    FigureSpec(
        fig_id="population_diversity",
        paper_label="fig:population_diversity",
        title="Population Diversity",
        section="evaluation",
        category="dynamics",
        size_hint="single",
        generator=generate_population_diversity,
        data_requirements=["session_json"],
        description="Tracks the diversity of the PBT population over time.",
    )
)
