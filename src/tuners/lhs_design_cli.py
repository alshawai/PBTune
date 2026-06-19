"""CLI entry point for the LHS-design importance-sampling tuner.

Examples
--------
Quick smoke test (small design, minimal knobs)::

    python -m src.tuners.lhs_design --tier minimal --benchmark tpch \\
        --design-size 8 --parallel-workers 2

Importance-design sweep for SCALPEL (larger design, core knobs)::

    python -m src.tuners.lhs_design --tier core --benchmark sysbench \\
        --sysbench-workload oltp_read_write --design-size 64 \\
        --parallel-workers 4

The session JSON is written under
``{output_dir}/{workload}/[{sysbench_workload}/]lhs_runs/{tier}/tuning_sessions/``
in a schema compatible with the analysis and evaluation loaders.
"""

from __future__ import annotations

import argparse
from dataclasses import replace
from pathlib import Path
from typing import Optional

from src.config.data_root import resolve_data_root
from src.tuners.lhs_design import LHSDesignTuner
from src.tuners.utils.output_paths import resolve_tuner_output_root
from src.tuners.utils.types import TunerLifecycleConfig, TuningStrategy
from src.utils.logger import (
    get_logger,
    print_startup_banner,
    set_colors_enabled,
    setup_logging,
)
from src.utils.session_clock import format_session_id
from src.utils.types import STANDARD_BENCHMARK_CONFIG, clone_benchmark_config

LOGGER = get_logger("LHSDesignCLI")


def parse_args(argv: Optional[list[str]] = None) -> argparse.Namespace:
    """Parse LHS-design tuner CLI arguments."""
    parser = argparse.ArgumentParser(
        description="LHS-design importance-sampling PostgreSQL tuner",
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )

    design_group = parser.add_argument_group("Design Configuration")
    design_group.add_argument(
        "--tier",
        type=str,
        default="minimal",
        choices=["minimal", "core", "standard", "extensive"],
        help="Knob space tier (default: minimal)",
    )
    design_group.add_argument(
        "--knob-source",
        type=str,
        default="expert",
        choices=["expert", "data_driven"],
        help="Knob source (default: expert)",
    )
    design_group.add_argument(
        "--design-size",
        type=int,
        default=32,
        help="Number of LHS design points to evaluate (default: 32)",
    )
    design_group.add_argument(
        "--parallel-workers",
        type=int,
        default=1,
        help="Number of PostgreSQL instances evaluated concurrently (default: 1)",
    )
    design_group.add_argument(
        "--random-seed",
        type=int,
        default=42,
        help="Random seed for the LHS design (default: 42)",
    )

    workload_group = parser.add_argument_group("Workload Settings")
    workload_group.add_argument(
        "--benchmark",
        type=str,
        default="sysbench",
        choices=["sysbench", "tpch"],
        help="Benchmark driver (default: sysbench)",
    )
    workload_group.add_argument(
        "--workload",
        type=str,
        default="oltp",
        choices=["oltp", "olap", "mixed"],
        help="Workload type for custom workloads (default: oltp)",
    )
    workload_group.add_argument(
        "--workload-file",
        type=str,
        default=None,
        help="Path to a custom workload file (non-sysbench/tpch only)",
    )
    workload_group.add_argument(
        "--duration",
        type=float,
        default=None,
        help="Measurement duration in seconds (overrides default)",
    )
    workload_group.add_argument(
        "--warmup",
        type=float,
        default=None,
        help="Warmup duration in seconds (overrides default)",
    )
    workload_group.add_argument(
        "--scale-factor",
        type=float,
        default=None,
        help="Benchmark scale factor (TPC-H / template)",
    )
    workload_group.add_argument(
        "--sysbench-tables", type=int, default=None, help="Number of sysbench tables"
    )
    workload_group.add_argument(
        "--sysbench-table-size", type=int, default=None, help="Rows per sysbench table"
    )
    workload_group.add_argument(
        "--sysbench-workload",
        type=str,
        default=None,
        choices=["oltp_read_only", "oltp_read_write", "oltp_write_only"],
        help="Sysbench workload profile (default: oltp_read_write)",
    )

    instance_group = parser.add_argument_group("Instance Management")
    instance_group.add_argument(
        "--no-docker",
        action="store_true",
        help="Run on bare-metal PostgreSQL instead of Docker",
    )
    instance_group.add_argument(
        "--cleanup-instances",
        action="store_true",
        help="Remove PostgreSQL instance data after completion",
    )
    instance_group.add_argument(
        "--data-dir",
        type=str,
        default=None,
        help="Base directory for PostgreSQL instances (overrides PBT_DATA_ROOT)",
    )

    output_group = parser.add_argument_group("Output & Logging")
    output_group.add_argument(
        "--output-dir", type=str, default="results", help="Base results directory"
    )
    output_group.add_argument(
        "--verbose",
        type=str,
        default="INFO",
        choices=["DEBUG", "INFO", "WARNING", "ERROR", "TRACE"],
        help="Logging verbosity (default: INFO)",
    )
    output_group.add_argument(
        "--no-color", action="store_true", help="Disable colored logging output"
    )

    return parser.parse_args(argv)


def build_tuner(args: argparse.Namespace) -> LHSDesignTuner:
    """Construct an :class:`LHSDesignTuner` from parsed CLI args."""
    base_bench = clone_benchmark_config(STANDARD_BENCHMARK_CONFIG)
    benchmark_config = replace(
        base_bench,
        benchmark=args.benchmark,
        workload_type=args.workload,
        workload_file=args.workload_file,
        evaluation_duration=(
            args.duration if args.duration is not None else base_bench.evaluation_duration
        ),
        warmup_duration=(
            args.warmup if args.warmup is not None else base_bench.warmup_duration
        ),
        scale_factor=(
            args.scale_factor if args.scale_factor is not None else base_bench.scale_factor
        ),
        sysbench_tables=(
            args.sysbench_tables
            if args.sysbench_tables is not None
            else base_bench.sysbench_tables
        ),
        sysbench_table_size=(
            args.sysbench_table_size
            if args.sysbench_table_size is not None
            else base_bench.sysbench_table_size
        ),
        sysbench_workload=(
            args.sysbench_workload
            if args.sysbench_workload is not None
            else base_bench.sysbench_workload
        ),
    )

    lifecycle = TunerLifecycleConfig(
        strategy=TuningStrategy.LHS,
        knob_tier=args.tier,
        knob_source=args.knob_source,
        num_parallel_workers=args.parallel_workers,
        cleanup_instances=args.cleanup_instances,
        use_docker=not args.no_docker,
        random_seed=args.random_seed,
    )

    data_root = Path(args.data_dir) if args.data_dir else resolve_data_root()
    timestamp = format_session_id()

    output_root = resolve_tuner_output_root(
        args.output_dir,
        strategy=TuningStrategy.LHS,
        workload_type=(
            "olap" if args.benchmark == "tpch" else args.workload
        ),
        benchmark=args.benchmark,
        sysbench_workload=benchmark_config.sysbench_workload,
        knob_tier=args.tier,
        knob_source=args.knob_source,
    )

    return LHSDesignTuner(
        lifecycle,
        benchmark=args.benchmark,
        benchmark_config=benchmark_config,
        design_size=args.design_size,
        timestamp=timestamp,
        output_root=output_root,
        workload_file=args.workload_file,
        data_root=data_root,
    )


def main(argv: Optional[list[str]] = None) -> int:
    """CLI main: parse args, run the LHS-design tuner, return an exit code."""
    args = parse_args(argv)

    set_colors_enabled(not args.no_color)
    print_startup_banner()
    setup_logging(verbosity=args.verbose, show_module=True)

    tuner = build_tuner(args)
    LOGGER.info(
        "Starting LHS-design sweep: tier=%s, design_size=%d, workers=%d, output=%s",
        args.tier,
        args.design_size,
        args.parallel_workers,
        tuner.output_root,
    )
    tuner.run()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
