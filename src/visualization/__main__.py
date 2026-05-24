"""
Command-line entry point for the visualization framework.
"""

import argparse
import inspect
from pathlib import Path
import sys

from src.visualization.registry import REGISTRY
from src.visualization.theme import PBTuneTheme
from src.visualization.types import ExportFormat
from src.visualization.exceptions import FigureRegistryError
from src.utils.logger import get_logger, setup_logging

LOGGER = get_logger("VisualizationCLI")

# Ensure all plots are discovered and registered
import src.visualization.plots  # noqa


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Generate PBTune paper figures.")

    parser.add_argument(
        "--list",
        action="store_true",
        help="List all available figures and exit.",
    )

    parser.add_argument(
        "--figure",
        type=str,
        help="Generate a specific figure by ID (e.g., 'convergence_curve'). If omitted, generates all.",
    )

    parser.add_argument(
        "--category",
        type=str,
        help="Generate all figures in a specific category.",
    )

    parser.add_argument(
        "--venue",
        type=str,
        default="pvldb",
        choices=["pvldb", "springer", "preview"],
        help="Target venue preset for sizing and typography (default: pvldb).",
    )

    parser.add_argument(
        "--data-dir",
        type=Path,
        default=Path("results"),
        help="Base directory containing PBT experiment data (default: results/).",
    )

    parser.add_argument(
        "--output-dir",
        type=Path,
        default=Path("papers/pbtune-pvldb/figures"),
        help="Directory to save generated figures (default: papers/pbtune-pvldb/figures/).",
    )

    parser.add_argument(
        "--format",
        type=str,
        nargs="+",
        default=["pdf", "png"],
        choices=["pdf", "png", "svg"],
        help="Formats to export (default: pdf png).",
    )

    parser.add_argument(
        "--importance-top-k",
        type=int,
        help="Number of knobs to show in the importance bar/beeswarm plots.",
    )

    parser.add_argument(
        "--dependence-top-k",
        type=int,
        help="Number of knobs to show in dependence plots.",
    )

    parser.add_argument(
        "--interaction-top-k",
        type=int,
        help="Number of knobs to include in the interaction heatmap.",
    )

    return parser.parse_args()


def main() -> int:
    setup_logging()
    args = parse_args()

    if args.list:
        print("\nRegistered Figures:")
        print(f"{'ID':<25} | {'Category':<15} | {'Size':<10} | {'Description'}")
        print("-" * 85)
        for spec in REGISTRY.list_all():
            print(
                f"{spec.fig_id:<25} | {spec.category:<15} | {spec.size_hint:<10} | {spec.description}"
            )
        return 0

    args.output_dir.mkdir(parents=True, exist_ok=True)

    # Resolve requested figures
    if args.figure:
        try:
            specs = [REGISTRY.get(args.figure)]
        except FigureRegistryError as e:
            LOGGER.error(e)
            return 1
    elif args.category:
        specs = REGISTRY.list_by_category(args.category)
        if not specs:
            LOGGER.error("No figures found in category '%s'.", args.category)
            return 1
    else:
        specs = REGISTRY.list_all()

    if not specs:
        LOGGER.warning("No figures found to generate.")
        return 0

    # Initialize shared components
    theme = PBTuneTheme(venue=args.venue)
    formats = [ExportFormat(f) for f in args.format]

    # Generate figures
    success_count = 0
    for spec in specs:
        LOGGER.info("Generating figure: %s (%s)", spec.fig_id, spec.paper_label)
        try:
            # We pass the unresolved data_dir to the generator.
            # It's up to each specific plot module to fetch the exact subdirectories it needs.
            extra_args = {
                "top_k_importance": args.importance_top_k,
                "top_k_dependence": args.dependence_top_k,
                "top_k_interactions": args.interaction_top_k,
            }
            sig = inspect.signature(spec.generator)
            filtered_args = {
                key: value
                for key, value in extra_args.items()
                if value is not None and key in sig.parameters
            }
            spec.generator(
                data_dir=args.data_dir,
                output_dir=args.output_dir,
                theme=theme,
                formats=formats,
                **filtered_args,
            )
            success_count += 1
        except Exception as e:
            LOGGER.exception("Failed to generate %s: %s", spec.fig_id, e)

    LOGGER.info(
        "Generation complete. Successfully generated %d/%d figures.",
        success_count,
        len(specs),
    )
    return 0 if success_count == len(specs) else 1


if __name__ == "__main__":
    sys.exit(main())
