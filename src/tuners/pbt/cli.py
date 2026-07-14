"""
CLI entry point for the Population-Based Training tuner.

Examples
--------
Quick smoke test (minimal knobs, rapid profile)::

    python -m src.tuners.pbt --tier minimal --config rapid

Standard sysbench OLTP session::

    python -m src.tuners.pbt --tier core --config standard \\
        --benchmark sysbench --sysbench-workload oltp_read_write

Warm-start from a previous run's best config::

    python -m src.tuners.pbt --tier core \\
        --warm-start results/olap/pbt_runs/extensive/best_configs/best_config_YYYYMMDD_HHMM.json

Both doors reach this same ``main(argv)``:

* ``python -m src.tuners.pbt`` (direct) — via :mod:`src.tuners.pbt.__main__`.
* ``python -m src.tuners pbt`` (routed) — via the top-level router in
  :mod:`src.tuners.__main__`.

The strategy-agnostic flags (tuning profile, workload, instance management,
per-worker resources, scoring provenance, output/logging) live in
:mod:`src.tuners.cli` and are shared with every other strategy entry point;
this module owns only the PBT-specific "PBT Configuration" group (warm-start,
population/generation overrides, the evolutionary hyperparameters) and the run
wiring.
"""

from __future__ import annotations

import argparse
from dataclasses import replace
from typing import Optional

from src.tuners.cli import (
    add_common_groups,
    attach_session_html_log,
    build_benchmark_config,
    build_lifecycle_config,
    resolve_data_dir,
    resolve_output_root,
)
from src.tuners.pbt.config import (
    PBTConfig,
    RAPID_CONFIG,
    STANDARD_CONFIG,
    THOROUGH_CONFIG,
    RESEARCH_CONFIG,
)
from src.tuners.pbt.tuner import PBTTuner
from src.tuners.utils.types import TuningStrategy
from src.utils.logger import (
    get_color_context,
    get_logger,
    set_colors_enabled,
    setup_logging,
)
from src.utils.session_clock import format_session_id

LOGGER = get_logger("Entry")
COLORS = get_color_context()

PBT_CONFIG_BY_PROFILE = {
    "rapid": RAPID_CONFIG,
    "standard": STANDARD_CONFIG,
    "thorough": THOROUGH_CONFIG,
    "research": RESEARCH_CONFIG,
}


def parse_args(argv: Optional[list[str]] = None) -> argparse.Namespace:
    """
    Parse PBT tuner CLI arguments.

    The PBT-specific "PBT Configuration" group is defined here; the shared
    strategy-agnostic groups are registered by :func:`add_common_groups`.
    """
    parser = argparse.ArgumentParser(
        description="Population-Based Training PostgreSQL tuner",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
Examples:
  # Quick test with minimal knobs (2-3 minutes)
  python -m src.tuners.pbt --tier minimal --config rapid

  # Standard tuning session (20-30 minutes)
  python -m src.tuners.pbt --tier core --config standard

  # Comprehensive tuning
  python -m src.tuners.pbt --tier standard --config thorough

  # Custom population/generation budget
  python -m src.tuners.pbt --tier minimal --population 8 --generations 50

Actual runtime varies with hardware, configuration, and workload.
        """,
    )

    pbt_group = parser.add_argument_group("PBT Configuration")
    pbt_group.add_argument(
        "--warm-start",
        type=str,
        metavar="PATH",
        default=None,
        help="Path to a previous run's best_config / session JSON for warm-starting",
    )
    pbt_group.add_argument(
        "--population",
        type=int,
        default=None,
        help="Population size (overrides the --config profile default)",
    )
    pbt_group.add_argument(
        "--generations",
        type=int,
        default=None,
        help="Number of generations (overrides the --config profile default)",
    )
    pbt_group.add_argument(
        "--exploit-quantile",
        type=float,
        default=None,
        help=(
            "Exploit/explore quantile for the evolution phase (0 < q < 0.5; "
            "default: profile-dependent, typically 0.2). Bottom q-fraction of "
            "ready workers copy from the top q-fraction."
        ),
    )
    pbt_group.add_argument(
        "--perturbation-factor",
        type=float,
        default=None,
        help=(
            "Perturbation spread factor for knob exploration (default: 0.2). A "
            "value of X sets the perturbation range to [1-X, 1+X] "
            "(e.g. 0.2 -> [0.8, 1.2])."
        ),
    )
    pbt_group.add_argument(
        "--resample-probability",
        type=float,
        default=None,
        help=(
            "Probability of fully resampling a knob from its prior during "
            "exploration instead of perturbing it (default: 0.1)."
        ),
    )
    pbt_group.add_argument(
        "--ablation-variable",
        type=str,
        default=None,
        help="Ablation study variable name (e.g. 'population_size')",
    )
    pbt_group.add_argument(
        "--ablation-value",
        type=str,
        default=None,
        help="Ablation study variable value (e.g. '4')",
    )

    add_common_groups(parser)

    return parser.parse_args(argv)


def build_pbt_config(args: argparse.Namespace) -> PBTConfig:
    """
    Materialize a ``PBTConfig`` from parsed args.

    Starts from the ``PBTConfig`` preset named by ``--config`` and overrides
    only the fields the unified :class:`~src.tuners.pbt.tuner.PBTTuner` reads
    off ``pbt_config`` (population/generations, the evolutionary
    hyperparameters). Everything else — scoring provenance, benchmark config,
    worker count, snapshot policy — flows through ``TunerLifecycleConfig`` /
    ``BenchmarkConfig`` in the unified path, so overriding it here would be
    dead. ``dataclasses.replace`` re-runs ``__post_init__``, so invalid
    overrides (e.g. ``--population 1``) are rejected at build time.
    """
    base = PBT_CONFIG_BY_PROFILE[args.config]

    if args.exploit_quantile is not None and not 0.0 < args.exploit_quantile < 0.5:
        raise SystemExit(
            f"--exploit-quantile must be in (0, 0.5); got {args.exploit_quantile}"
        )

    perturbation_factors = base.perturbation_factors
    if args.perturbation_factor is not None:
        perturbation_factors = (
            round(1.0 - args.perturbation_factor, 4),
            round(1.0 + args.perturbation_factor, 4),
        )

    return replace(
        base,
        population_size=(
            args.population if args.population is not None else base.population_size
        ),
        num_generations=(
            args.generations
            if args.generations is not None
            else base.num_generations
        ),
        num_parallel_workers=(
            args.parallel_workers
            if args.parallel_workers is not None
            else base.num_parallel_workers
        ),
        exploit_quantile=(
            args.exploit_quantile
            if args.exploit_quantile is not None
            else base.exploit_quantile
        ),
        perturbation_factors=perturbation_factors,
        resample_probability=(
            args.resample_probability
            if args.resample_probability is not None
            else 0.1  # Legacy default (preset default is 0.0)
        ),
    )


def build_tuner(args: argparse.Namespace) -> PBTTuner:
    """Construct a :class:`PBTTuner` from parsed CLI args."""
    benchmark_config = build_benchmark_config(args)
    lifecycle = build_lifecycle_config(args, strategy=TuningStrategy.PBT)
    pbt_config = build_pbt_config(args)

    output_root = resolve_output_root(
        args,
        strategy=TuningStrategy.PBT,
        sysbench_workload=benchmark_config.sysbench_workload,
        ablation_variable=args.ablation_variable,
        ablation_value=args.ablation_value,
    )

    return PBTTuner(
        lifecycle,
        pbt_config=pbt_config,
        benchmark=args.benchmark,
        benchmark_config=benchmark_config,
        timestamp=format_session_id(),
        output_root=output_root,
        workload_file=args.workload_file,
        data_root=resolve_data_dir(args),
        warm_start_path=args.warm_start,
        ablation_variable=args.ablation_variable,
        ablation_value=args.ablation_value,
    )


def main(argv: Optional[list[str]] = None) -> int:
    """CLI main: parse args, run the PBT tuner, return an exit code."""
    args = parse_args(argv)

    set_colors_enabled(not args.no_color)
    setup_logging(verbosity=args.verbose, show_module=True)

    try:
        tuner = build_tuner(args)

        tuner.output_root.mkdir(parents=True, exist_ok=True)
        attach_session_html_log(tuner.output_root, timestamp=tuner.timestamp)

        LOGGER.info(
            "%sStarting PBT tuning%s: tier=%s%s%s, population=%s%d%s, "
            "generations=%s%d%s, output=%s%s%s",
            COLORS.bold,
            COLORS.reset,
            COLORS.cyan,
            tuner.lifecycle.knob_tier,
            COLORS.reset,
            COLORS.cyan,
            tuner.pbt_config.population_size,
            COLORS.reset,
            COLORS.cyan,
            tuner.pbt_config.num_generations,
            COLORS.reset,
            COLORS.cyan,
            tuner.output_root,
            COLORS.reset,
        )
        tuner.run()

        LOGGER.info(
            "%s%sTuning completed successfully!%s",
            COLORS.bold,
            COLORS.green,
            COLORS.reset,
        )
        return 0

    except (RuntimeError, ValueError, ConnectionError) as exc:
        LOGGER.error("%sFatal ERROR:%s %s", COLORS.red, COLORS.reset, exc)
        LOGGER.debug("Exception details:", exc_info=True)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
