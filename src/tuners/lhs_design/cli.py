"""
CLI entry point for the LHS-design importance-sampling tuner.

Examples
--------
Quick smoke test (small design, minimal knobs)::

    python -m src.tuners.lhs_design --tier minimal --benchmark tpch \\
        --design-size 8 --parallel-workers 2

Importance-design sweep for SCALPEL (larger design, core knobs)::

    python -m src.tuners.lhs_design --tier core --benchmark sysbench \\
        --sysbench-workload oltp_read_write --design-size 64 \\
        --parallel-workers 4

The strategy-agnostic flags (workload, instance management, per-worker
resources, scoring provenance, output/logging) live in :mod:`src.tuners.cli`
and are shared with every other strategy entry point; this module owns only
the LHS-specific "Design Configuration" group and the run wiring.

The session JSON is written under
``{output_dir}/sessions/{workload}/lhs/{tier}/traces/``
in a schema compatible with the analysis and evaluation loaders.
"""

from __future__ import annotations

import argparse
from typing import Optional

from src.tuners.cli import (
    add_common_groups,
    attach_session_html_log,
    build_benchmark_config,
    build_lifecycle_config,
    resolve_data_dir,
    resolve_output_root,
)
from src.tuners.lhs_design.tuner import LHSDesignTuner
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

# Per-profile default design size. The shared profile (--config) supplies
# the strategy-agnostic defaults (worker count, benchmark config); each
# strategy owns its own {profile: scalar} map for the one hyperparameter it
# adds. These mirror the design-size scale PBT uses per profile.
LHS_DESIGN_SIZE_BY_PROFILE = {
    "rapid": 8,
    "standard": 32,
    "thorough": 512,
    "research": 1024,
}


def parse_args(argv: Optional[list[str]] = None) -> argparse.Namespace:
    """
    Parse LHS-design tuner CLI arguments.

    The LHS-specific "Design Configuration" group is defined here; the shared
    strategy-agnostic groups are registered by :func:`add_common_groups`.
    """
    parser = argparse.ArgumentParser(
        description="LHS-design importance-sampling PostgreSQL tuner",
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )

    design_group = parser.add_argument_group("Design Configuration")
    design_group.add_argument(
        "--design-size",
        type=int,
        default=None,
        help=(
            "Number of LHS design points to evaluate. When unset, defaults to "
            "the per-profile value selected by --config "
            "(rapid=8, standard=32, thorough=512, research=1024)."
        ),
    )

    add_common_groups(parser)

    return parser.parse_args(argv)


def build_tuner(args: argparse.Namespace) -> LHSDesignTuner:
    """Construct an :class:`LHSDesignTuner` from parsed CLI args."""
    benchmark_config = build_benchmark_config(args)

    lifecycle = build_lifecycle_config(args, strategy=TuningStrategy.LHS)

    output_root = resolve_output_root(
        args,
        strategy=TuningStrategy.LHS,
        sysbench_workload=benchmark_config.sysbench_workload,
    )

    design_size = (
        args.design_size
        if args.design_size is not None
        else LHS_DESIGN_SIZE_BY_PROFILE[args.config]
    )

    return LHSDesignTuner(
        lifecycle,
        benchmark=args.benchmark,
        benchmark_config=benchmark_config,
        design_size=design_size,
        timestamp=format_session_id(),
        output_root=output_root,
        workload_file=args.workload_file,
        data_root=resolve_data_dir(args),
    )


def main(argv: Optional[list[str]] = None) -> int:
    """CLI main: parse args, run the LHS-design tuner, return an exit code."""
    args = parse_args(argv)

    set_colors_enabled(not args.no_color)
    setup_logging(verbosity=args.verbose, show_module=True)

    tuner = build_tuner(args)

    # HTML log parity with PBT/BO: attach a timestamped HTML file handler under
    # the run's logs/ subdirectory (shared helper ensures the dir exists and is
    # placed alongside tuning_sessions/ and best_configs/). Placed before the
    # startup banner so the banner is captured in the HTML file.
    attach_session_html_log(
        tuner.output_root, stem="lhs_design", timestamp=tuner.timestamp
    )

    LOGGER.info(
        "%sStarting LHS-design sweep%s: tier=%s%s%s, design_size=%s%d%s, "
        "workers=%s%d%s, output=%s%s%s",
        COLORS.bold,
        COLORS.reset,
        COLORS.cyan,
        tuner.lifecycle.knob_tier,
        COLORS.reset,
        COLORS.cyan,
        tuner.design_size,
        COLORS.reset,
        COLORS.cyan,
        tuner.lifecycle.num_parallel_workers,
        COLORS.reset,
        COLORS.cyan,
        tuner.output_root,
        COLORS.reset,
    )
    tuner.run()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
