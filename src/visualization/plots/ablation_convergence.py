import logging
from pathlib import Path

import numpy as np
import matplotlib.pyplot as plt

from src.visualization.theme import PBTuneTheme
from src.visualization.export import export_figure
from src.visualization.types import FigureSpec, ExportFormat
from src.visualization.registry import register_figure
from src.visualization.loaders.ablation import load_ablation_study
from src.visualization.loaders.multi_seed import aggregate_seeds
from src.visualization.utils import set_integer_ticks, auto_grid

logger = logging.getLogger(__name__)


def _make_ablation_generator(variable_name: str, data_subpath: str = ""):
    """Factory function to create a generator for a specific ablation study."""

    def generate(
        data_dir: Path | str | None = None,
        output_dir: str = "figures/",
        venue: str = "pvldb",
        formats: list[str] | None = None,
        theme: PBTuneTheme | None = None,
        **kwargs,
    ):
        if not data_dir:
            raise ValueError(f"data_dir is required for {variable_name} ablation plot.")

        ablation_dir = Path(data_dir) / data_subpath / "ablations" / variable_name
        logger.info(f"Generating {variable_name} ablation from {ablation_dir}")

        ablation = load_ablation_study(ablation_dir)
        _theme = theme or PBTuneTheme(venue=venue)

        with _theme.apply():
            fig, ax = _theme.subplots(1, 1, size_hint="single")

            # Sort groups by name or convert to numeric if possible for ordering
            try:
                sorted_groups = sorted(ablation.groups.items(), key=lambda x: float(x[0]))
            except ValueError:
                sorted_groups = sorted(ablation.groups.items())

            # Use a sequential colormap
            cmap = plt.get_cmap('viridis')
            colors = [cmap(i) for i in np.linspace(0, 0.9, len(sorted_groups))]

            for idx, (val_name, traces) in enumerate(sorted_groups):
                agg = aggregate_seeds(traces)
                is_multi_seed = agg.n_seeds > 1

                n_workers = traces[0].metadata.get("n_workers", 1)
                evals = np.array(agg.generations) * n_workers

                color = colors[idx]
                label_text = f"{variable_name}={val_name}"

                ax.plot(
                    evals,
                    agg.mean_best,
                    label=label_text,
                    color=color,
                    linewidth=2.0,
                    marker="o",
                    markersize=5,
                    markevery=max(1, len(evals) // 10),
                )

                if is_multi_seed:
                    ax.fill_between(
                        evals,
                        agg.mean_best - agg.std_best,
                        agg.mean_best + agg.std_best,
                        alpha=0.2,
                        color=color,
                    )

            ax.set_xlabel("Cumulative Evaluations")
            ax.set_ylabel("Composite Score")
            set_integer_ticks(ax, axis="x")
            auto_grid(ax)
            ax.legend(loc="best")

            fig.tight_layout()

            fmt_list = [ExportFormat(f) for f in (formats or ["pdf", "png"])]
            fig_id = f"{variable_name}_ablation"
            export_figure(fig, output_dir, fig_id, formats=fmt_list)
            logger.info("Export complete for %s", fig_id)

        return fig

    return generate


# Register the three convergence curve ablation figures
register_figure(
    FigureSpec(
        fig_id="population_size_ablation",
        paper_label="fig:ablation_pop",
        title="Ablation: Population Size",
        section="evaluation",
        category="ablation",
        size_hint="single",
        generator=_make_ablation_generator("population_size"),
        data_requirements=["ablation_dir"],
        description="Convergence curves for pop={2, 4, 8}",
    )
)

register_figure(
    FigureSpec(
        fig_id="exploit_quantile_ablation",
        paper_label="fig:ablation_quantile",
        title="Ablation: Exploit Quantile",
        section="evaluation",
        category="ablation",
        size_hint="single",
        generator=_make_ablation_generator("exploit_quantile"),
        data_requirements=["ablation_dir"],
        description="Convergence curves for q={0.1, 0.25, 0.5}",
    )
)

register_figure(
    FigureSpec(
        fig_id="scoring_policy_ablation",
        paper_label="fig:ablation_scoring",
        title="Ablation: Scoring Policy",
        section="evaluation",
        category="ablation",
        size_hint="single",
        generator=_make_ablation_generator("scoring_policy"),
        data_requirements=["ablation_dir"],
        description="Convergence curves comparing scoring policies",
    )
)
