"""
CLI entry point for the Bayesian Optimization tuner.

Examples
--------
Quick smoke test (minimal knobs, rapid profile)::

    python -m src.tuners.bo --tier minimal --config rapid

Standard sysbench OLTP session with the RF surrogate::

    python -m src.tuners.bo --tier core --config standard \\
        --benchmark sysbench --sysbench-workload oltp_read_write

Matched comparison against a previous PBT run (copies benchmark, workload,
durations, tier, tuning mode, knob names, and co-tenancy degree)::

    python -m src.tuners.bo \\
        --pbt-session results/sessions/oltp_read_write/pbt/core/traces/trace_YYYYMMDD_HHMM.json

Both doors reach this same ``main(argv)``:

* ``python -m src.tuners.bo`` (direct) — via :mod:`src.tuners.bo.__main__`.
* ``python -m src.tuners bo`` (routed) — via the top-level router in
  :mod:`src.tuners.__main__`.

Unlike PBT/LHS, BO does not reuse :func:`~src.tuners.cli.add_common_groups`: it
keeps its own richer argument surface (``--pbt-session`` parity sync,
``--cotenancy-degree``, ``--bo-surrogate``, preset-driven benchmark configs)
whose defaulting is owned by :meth:`~src.tuners.bo.config.BOConfig.from_args`.
This module parses that surface, resolves a :class:`BOConfig`, then *derives*
the strategy-agnostic :class:`~src.tuners.utils.types.TunerLifecycleConfig` from
the resolved config so the shared :class:`~src.tuners.base.BaseTuner` lifecycle
drives the run.
"""

from __future__ import annotations

import argparse
from pathlib import Path
from typing import Optional

from src.tuners.bo.config import BOConfig
from src.tuners.bo.tuner import BOTuner
from src.tuners.cli import attach_session_html_log
from src.tuners.utils.output_paths import resolve_tuner_output_root
from src.tuners.utils.types import TunerLifecycleConfig, TuningStrategy
from src.config.data_root import resolve_data_root
from src.utils.logger import (
    get_color_context,
    get_logger,
    set_colors_enabled,
    setup_logging,
)
from src.utils.session_clock import format_session_id
from src.utils.types import TuningMode

LOGGER = get_logger("Entry")
COLORS = get_color_context()


def parse_args(argv: Optional[list[str]] = None) -> argparse.Namespace:
    """Parse BO tuner CLI arguments.

    The full BO argument surface lives here (rather than in the shared
    :func:`~src.tuners.cli.add_common_groups`) because BO's defaulting is
    preset- and PBT-session-driven via :meth:`BOConfig.from_args`.
    """
    parser = argparse.ArgumentParser(
        prog="python -m src.tuners.bo",
        description="Bayesian Optimization PostgreSQL tuner (SMAC3 baseline)",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
Examples:
  # Quick test with minimal knobs
  python -m src.tuners.bo --tier minimal --config rapid

  # Standard tuning session with the RF surrogate
  python -m src.tuners.bo --tier core --config standard

  # Matched comparison against a previous PBT run
  python -m src.tuners.bo --pbt-session PATH_TO_PBT_TRACE.json

Actual runtime varies with hardware, configuration, and workload.
        """,
    )

    # Preset configuration
    parser.add_argument(
        "--config",
        choices=["rapid", "standard", "thorough", "research", "extreme"],
        default="standard",
        help="BO preset configuration (default: standard)",
    )
    parser.add_argument(
        "--benchmark-config",
        choices=["rapid", "standard", "thorough", "research", "extreme"],
        default=None,
        help=(
            "Benchmark/workload preset override. Defaults to the preset embedded "
            "in --config when omitted."
        ),
    )

    # Search space
    parser.add_argument(
        "--tier",
        choices=["minimal", "core", "standard", "extensive"],
        help="Knob space tier. Optional when --pbt-session is provided.",
    )
    parser.add_argument(
        "--knob-source",
        choices=["expert", "data_driven"],
        default=None,
        help=(
            "Knob source to use (expert, data_driven) (default: loaded from PBT "
            "session or expert)"
        ),
    )
    parser.add_argument(
        "--pbt-session",
        type=str,
        help=(
            "Reference PBT tuning-session JSON. BO will copy comparable "
            "benchmark, workload, duration, warmup, tier, tuning mode, and "
            "knob names from this run."
        ),
    )

    # BO configuration
    parser.add_argument(
        "--iterations",
        type=int,
        default=None,
        help=(
            "Number of BO iterations. Defaults to the preset value, or to "
            "PBT population_size * actual_generations when --pbt-session is used."
        ),
    )
    parser.add_argument(
        "--seed",
        type=int,
        default=None,
        help="Random seed (default: preset value)",
    )

    # Benchmark
    parser.add_argument(
        "--benchmark",
        choices=["sysbench", "tpch"],
        default=None,
        help="Benchmark type (default: preset value)",
    )
    parser.add_argument(
        "--workload",
        choices=["oltp", "olap", "mixed"],
        default=None,
        help="Workload type (default: preset value)",
    )
    parser.add_argument(
        "--duration",
        type=float,
        default=None,
        help="Evaluation duration in seconds (default: preset value)",
    )
    parser.add_argument(
        "--warmup",
        type=float,
        default=None,
        help="Warmup duration in seconds (default: preset value)",
    )

    # Sysbench options
    parser.add_argument(
        "--sysbench-tables",
        type=int,
        default=None,
        help="Number of sysbench tables (default: preset value)",
    )
    parser.add_argument(
        "--sysbench-table-size",
        type=int,
        default=None,
        help="Sysbench table size (default: preset value)",
    )
    parser.add_argument(
        "--sysbench-workload",
        choices=["oltp_read_only", "oltp_read_write", "oltp_write_only"],
        default=None,
        help="Sysbench workload (default: preset value)",
    )

    # TPC-H options
    parser.add_argument(
        "--scale-factor",
        type=float,
        default=None,
        help="TPC-H scale factor (default: preset value)",
    )
    parser.add_argument(
        "--tpch-warmup-passes",
        type=int,
        default=None,
        help="TPC-H warmup passes (default: preset value)",
    )

    # Instance options
    parser.add_argument(
        "--no-docker",
        action="store_true",
        help="Use bare-metal PostgreSQL instead of Docker",
    )
    parser.add_argument(
        "--docker-image",
        type=str,
        help="Custom Docker image name",
    )
    parser.add_argument(
        "--force-recreate-instances",
        action="store_true",
        help="Force recreate PostgreSQL instances",
    )
    parser.add_argument(
        "--force-recreate-baseline",
        action="store_true",
        help="Force recreate baseline snapshot",
    )
    parser.add_argument(
        "--tuning-mode",
        choices=["offline", "online", "adaptive"],
        default=None,
        help="Tuning mode (default: preset value)",
    )
    parser.add_argument(
        "--data-dir",
        type=str,
        default=None,
        help=(
            "Base directory for PostgreSQL instances and snapshots. "
            "Overrides PBT_DATA_ROOT env var."
        ),
    )

    # Output options
    parser.add_argument(
        "--output-dir",
        type=str,
        default=None,
        help="Output directory (default: results)",
    )
    parser.add_argument(
        "--colocate-output",
        action="store_true",
        help=(
            "Place results/logs under the data directory instead of the default "
            "./results/ directory"
        ),
    )
    parser.add_argument(
        "--bo-surrogate",
        choices=["rf", "gp"],
        default=None,
        help=(
            "SMAC surrogate model: Random Forest (rf) or Gaussian Process (gp). "
            "Default: preset value"
        ),
    )
    parser.add_argument(
        "--verbose",
        choices=["DEBUG", "INFO", "WARNING", "ERROR", "TRACE"],
        default=None,
        help="Logging level (default: preset value)",
    )
    parser.add_argument(
        "--no-color", action="store_true", help="Disable colored logging output"
    )
    parser.add_argument(
        "--range-update-interval",
        type=int,
        default=None,
        help=(
            "Pilot phase size: number of Sobol initial-design iterations before "
            "freezing normalization ranges (default: preset value)"
        ),
    )
    parser.add_argument(
        "--resource-division",
        type=int,
        default=None,
        help=(
            "Divides host capacity by this number to determine instance "
            "resources. If a PBT session is provided, this automatically takes "
            "the PBT session's parallel worker count."
        ),
    )
    parser.add_argument(
        "--worker-ram",
        type=str,
        default=None,
        help=(
            "RAM to allocate per worker (e.g., '3G', '512M', '1073741824'). "
            "When set, bypasses auto-detection."
        ),
    )
    parser.add_argument(
        "--worker-cpus",
        type=int,
        default=None,
        help=(
            "CPU cores to allocate per worker. When set, bypasses "
            "auto-detection."
        ),
    )
    parser.add_argument(
        "--worker-disk-read-bps",
        type=int,
        default=None,
        help="Per-worker disk read bandwidth in bytes/sec (cgroup blkio / io.max).",
    )
    parser.add_argument(
        "--worker-disk-write-bps",
        type=int,
        default=None,
        help="Per-worker disk write bandwidth in bytes/sec.",
    )
    parser.add_argument(
        "--worker-disk-read-iops",
        type=int,
        default=None,
        help="Per-worker disk read IOPS ceiling.",
    )
    parser.add_argument(
        "--worker-disk-write-iops",
        type=int,
        default=None,
        help="Per-worker disk write IOPS ceiling.",
    )
    probe_group = parser.add_mutually_exclusive_group()
    probe_group.add_argument(
        "--probe-disk",
        dest="probe_disk",
        action="store_true",
        default=True,
        help=(
            "Run a short fio probe at startup to calibrate per-worker disk "
            "I/O budget. Falls back to heuristic when fio is unavailable. "
            "Default: enabled."
        ),
    )
    probe_group.add_argument(
        "--no-probe-disk",
        dest="probe_disk",
        action="store_false",
        help="Skip the fio probe and use heuristic disk I/O budget directly.",
    )
    parser.add_argument(
        "--scoring-policy",
        type=str,
        default=None,
        choices=["fixed_v1", "feature_driven_v2"],
        help=(
            "Scoring policy to use (default: feature_driven_v2 via per-workload "
            "config)"
        ),
    )
    parser.add_argument(
        "--cotenancy-degree",
        type=int,
        default=None,
        help=(
            "Total concurrent instances (foreground BO trial + background load) "
            "during each measurement window, so BO sees the same single-host "
            "contention a PBT generation does. 1 disables background load. When "
            "--pbt-session is given this is FORCED to that session's "
            "num_parallel_workers (matched, mandatory) unless --no-cotenant is "
            "set; this flag only applies for standalone BO runs without a session."
        ),
    )
    parser.add_argument(
        "--no-cotenant",
        action="store_true",
        default=False,
        help=(
            "Disable co-tenancy background load entirely, even when --pbt-session "
            "is given. BO runs solo on the host (degree=1). Useful for ablation "
            "studies or debugging."
        ),
    )

    snapshot_group = parser.add_mutually_exclusive_group()
    snapshot_group.add_argument(
        "--enable-snapshots",
        dest="enable_snapshots",
        action="store_true",
        default=None,
        help="Enable periodic database snapshot restoration to prevent data drift.",
    )
    snapshot_group.add_argument(
        "--disable-snapshots",
        dest="enable_snapshots",
        action="store_false",
        default=None,
        help="Never restore the baseline snapshot between iterations.",
    )
    parser.add_argument(
        "--snapshot-restore-interval",
        type=int,
        default=None,
        help="Restore snapshots every N iterations.",
    )
    parser.add_argument(
        "--early-stopping-patience",
        type=int,
        default=None,
        help=(
            "Number of consecutive non-improving BO iterations before stopping "
            "early. Defaults to the preset value; auto-capped when --pbt-session "
            "sets the budget."
        ),
    )
    parser.add_argument(
        "--disable-early-stopping",
        action="store_true",
        help="Disable early stopping and always run all BO iterations.",
    )

    return parser.parse_args(argv)


def build_lifecycle_config(config: BOConfig) -> TunerLifecycleConfig:
    """Derive a ``TunerLifecycleConfig`` from a resolved :class:`BOConfig`.

    BO owns its defaulting in :meth:`BOConfig.from_args` (preset + PBT-session
    driven), so — unlike PBT/LHS — the lifecycle is built from the *resolved*
    config rather than raw args. ``num_parallel_workers`` carries the matched
    co-tenancy degree; :attr:`BOTuner.num_instances` reads it back to bring up
    the foreground instance plus ``degree - 1`` co-tenant loaders.
    """
    return TunerLifecycleConfig(
        strategy=TuningStrategy.BO,
        knob_tier=config.knob_tier,
        knob_source=config.knob_source,
        num_parallel_workers=max(1, int(config.cotenancy_degree)),
        cleanup_instances=False,
        use_docker=config.use_docker,
        docker_image=config.docker_image,
        random_seed=config.random_seed,
        synchronize_workers=True,
        disable_early_stopping=not config.early_stopping_enabled,
        tuning_mode=TuningMode(config.benchmark_config.tuning_mode),
        force_recreate_instances=config.force_recreate_instances,
        force_recreate_baseline=config.force_recreate_baseline,
        enable_snapshots=config.enable_snapshots,
        snapshot_restore_interval=config.snapshot_restore_interval,
        worker_ram=config.worker_ram,
        worker_cpus=config.worker_cpus,
        worker_disk_read_bps=config.worker_disk_read_bps,
        worker_disk_write_bps=config.worker_disk_write_bps,
        worker_disk_read_iops=config.worker_disk_read_iops,
        worker_disk_write_iops=config.worker_disk_write_iops,
        probe_disk=config.probe_disk,
        scoring_policy=config.scoring_policy,
    )


def resolve_output_root(config: BOConfig) -> Path:
    """Resolve the strategy/tier-scoped results directory from a ``BOConfig``."""
    data_root = Path(config.data_dir) if config.data_dir else resolve_data_root()
    base_output_dir = (
        data_root / "results" if config.colocate_output else Path(config.output_dir)
    )

    benchmark = config.benchmark_config.benchmark
    if benchmark == "sysbench":
        workload = config.benchmark_config.sysbench_workload or "oltp_read_write"
    elif benchmark == "tpch":
        workload = "olap"
    else:
        workload = config.benchmark_config.workload_type

    return resolve_tuner_output_root(
        base_output_dir,
        strategy=TuningStrategy.BO,
        workload=workload,
        knob_tier=config.knob_tier,
        knob_source=config.knob_source,
    )


def build_tuner(args: argparse.Namespace) -> BOTuner:
    """Construct a :class:`BOTuner` from parsed CLI args."""
    config = BOConfig.from_args(args)
    lifecycle = build_lifecycle_config(config)
    output_root = resolve_output_root(config)
    data_root = Path(config.data_dir) if config.data_dir else resolve_data_root()

    return BOTuner(
        lifecycle,
        bo_config=config,
        benchmark=config.benchmark_config.benchmark,
        benchmark_config=config.benchmark_config,
        timestamp=format_session_id(),
        output_root=output_root,
        data_root=data_root,
    )


def main(argv: Optional[list[str]] = None) -> int:
    """CLI main: parse args, run the BO tuner, return an exit code."""
    args = parse_args(argv)

    if args.tier is None and not args.pbt_session:
        LOGGER.error("Either --tier or --pbt-session must be provided")
        return 2

    set_colors_enabled(not args.no_color)
    verbosity = args.verbose if args.verbose is not None else "INFO"
    setup_logging(verbosity=verbosity, show_module=True)

    try:
        tuner = build_tuner(args)
        tuner.output_root.mkdir(parents=True, exist_ok=True)
        attach_session_html_log(tuner.output_root, timestamp=tuner.timestamp)

        LOGGER.info(
            "%sStarting BO tuning%s: tier=%s%s%s, surrogate=%s%s%s, "
            "iterations=%s%d%s, output=%s%s%s",
            COLORS.bold,
            COLORS.reset,
            COLORS.cyan,
            tuner.lifecycle.knob_tier,
            COLORS.reset,
            COLORS.cyan,
            tuner.bo_config.bo_surrogate,
            COLORS.reset,
            COLORS.cyan,
            tuner.bo_config.n_iterations,
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
