import logging
from pathlib import Path

import numpy as np
import pandas as pd

from src.visualization.theme import PBTuneTheme
from src.visualization.types import FigureSpec
from src.visualization.registry import register_figure
from src.visualization.loaders.ablation import load_ablation_study
from src.visualization.loaders.multi_seed import aggregate_seeds

logger = logging.getLogger(__name__)


def _make_table_generator(variable_name: str, data_subpath: str = ""):
    """Factory function to create a table generator for a specific ablation study."""

    def generate(
        data_dir: Path | str | None = None,
        output_dir: str = "figures/",
        venue: str = "pvldb",
        formats: list[str] | None = None,
        theme: PBTuneTheme | None = None,
        **kwargs,
    ):
        if not data_dir:
            raise ValueError(f"data_dir is required for {variable_name} ablation table.")

        ablation_dir = Path(data_dir) / data_subpath / "ablations" / variable_name
        logger.info(f"Generating {variable_name} ablation table from {ablation_dir}")

        ablation = load_ablation_study(ablation_dir)
        
        try:
            sorted_groups = sorted(ablation.groups.items(), key=lambda x: float(x[0]))
        except ValueError:
            sorted_groups = sorted(ablation.groups.items())

        rows = []
        for val_name, traces in sorted_groups:
            agg = aggregate_seeds(traces)
            final_mean = agg.mean_best[-1]
            final_std = agg.std_best[-1] if agg.n_seeds > 1 else 0.0

            # Compute mean wall-clock time
            wall_times: list[float] = []
            best_scores: list[float] = []
            for t in traces:
                if len(t.wall_clock_seconds) > 0:
                    wall_times.append(float(t.wall_clock_seconds[-1]))
                if len(t.best_scores) > 0:
                    best_scores.append(float(np.max(t.best_scores)))

            mean_wall = np.mean(wall_times) if wall_times else float('nan')
            max_best = np.max(best_scores) if best_scores else float('nan')

            rows.append({
                "Value": val_name,
                "Mean Final Score": f"{final_mean:.3f} ± {final_std:.3f}",
                "Best Absolute Score": f"{max_best:.3f}",
                "Mean Wall-clock (s)": f"{mean_wall:.1f}",
                "Seeds": agg.n_seeds
            })

        df = pd.DataFrame(rows)
        out_path = Path(output_dir)
        out_path.mkdir(parents=True, exist_ok=True)

        fig_id = f"{variable_name}_ablation_table"
        tex_path = out_path / f"{fig_id}.tex"
        csv_path = out_path / f"{fig_id}.csv"

        tex_path.write_text(df.to_latex(index=False), encoding="utf-8")
        df.to_csv(csv_path, index=False)
        
        logger.info(f"Saved {fig_id} to {tex_path} and {csv_path}")

        # Return None since this is purely a table export, not a matplotlib figure
        return None

    return generate


# Register the two table figures
register_figure(
    FigureSpec(
        fig_id="perturbation_ablation_table",
        paper_label="tab:ablation_perturbation",
        title="Ablation: Perturbation Magnitude",
        section="evaluation",
        category="ablation",
        size_hint="single",
        generator=_make_table_generator("perturbation_magnitude"),
        data_requirements=["ablation_dir"],
        description="Table of final scores for perturbation factors",
    )
)

register_figure(
    FigureSpec(
        fig_id="knob_tier_ablation_table",
        paper_label="tab:ablation_tier",
        title="Ablation: Knob Tier",
        section="evaluation",
        category="ablation",
        size_hint="single",
        generator=_make_table_generator("knob_tier"),
        data_requirements=["ablation_dir"],
        description="Table of final scores for knob tiers",
    )
)

register_figure(
    FigureSpec(
        fig_id="population_size_ablation_table",
        paper_label="tab:ablation_pop",
        title="Ablation: Population Size Table",
        section="evaluation",
        category="ablation",
        size_hint="single",
        generator=_make_table_generator("population_size"),
        data_requirements=["ablation_dir"],
        description="Table of final scores for population size",
    )
)

