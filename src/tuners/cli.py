"""
Shared CLI building blocks for the unified tuners package.

Every strategy entry point (LHS-design today; PBT/BO once they adopt the
unified lifecycle) needs the same strategy-agnostic argument surface: workload
settings, instance management, per-worker resource overrides, scoring
provenance, and output/logging. This module owns that surface in one place so
the per-strategy CLIs shrink to *just* their strategy-specific knobs (e.g.
``--design-size`` for LHS) plus a call to :func:`add_common_groups`.

Public API
----------
add_common_groups(parser)
    Register the shared argument groups (Workload, Instance Management,
    Per-Worker Resources, Scoring & Normalization, Output & Logging) on an
    existing ``ArgumentParser``.
build_benchmark_config(args)
    Materialize a :class:`~src.utils.types.BenchmarkConfig` from parsed args,
    honoring the "None means fall back to the dataclass default" convention.
build_lifecycle_config(args, *, strategy)
    Materialize a :class:`~src.tuners.utils.types.TunerLifecycleConfig`,
    threading every shared knob (tuning mode, per-worker resources, scoring
    provenance) onto it.
resolve_output_root(args, *, strategy)
    Resolve the strategy/tier-scoped results directory via
    :func:`~src.tuners.utils.output_paths.resolve_tuner_output_root`.
"""

from __future__ import annotations

import argparse
from dataclasses import replace
from pathlib import Path

from src.config.data_root import resolve_data_root
from src.tuners.utils.output_paths import resolve_tuner_output_root
from src.tuners.utils.profiles import PROFILES
from src.tuners.utils.types import TunerLifecycleConfig, TuningStrategy
from src.utils.logger import add_html_file_logging
from src.utils.types import (
    TuningMode,
    clone_benchmark_config,
)


def add_common_groups(parser: argparse.ArgumentParser) -> argparse.ArgumentParser:
    """
    Register the strategy-agnostic argument groups on ``parser``.

    Adds Tuning Configuration, Workload Settings, Instance Management,
    Per-Worker Resources, Scoring & Normalization, and Output & Logging.
    Strategy-specific groups (e.g. an LHS "Design Configuration") are added
    separately by the caller.

    Returns the same parser for chaining.
    """
    _add_tuning_group(parser)
    _add_workload_group(parser)
    _add_instance_group(parser)
    _add_resource_group(parser)
    _add_scoring_group(parser)
    _add_output_group(parser)
    return parser


def _add_tuning_group(parser: argparse.ArgumentParser) -> None:
    group = parser.add_argument_group("Tuning Configuration")
    group.add_argument(
        "--config",
        type=str,
        default="standard",
        choices=list(PROFILES.keys()),
        help=(
            "Execution profile supplying default worker count and benchmark "
            "settings, overridable by individual flags (default: standard)"
        ),
    )
    group.add_argument(
        "--tier",
        type=str,
        default="minimal",
        choices=["minimal", "core", "standard", "extensive"],
        help="Knob space tier (default: minimal)",
    )
    group.add_argument(
        "--knob-source",
        type=str,
        default="expert",
        choices=["expert", "data_driven"],
        help="Knob source (default: expert)",
    )
    group.add_argument(
        "--parallel-workers",
        type=int,
        default=None,
        help=(
            "Number of PostgreSQL instances evaluated concurrently "
            "(default: profile-derived)"
        ),
    )
    group.add_argument(
        "--random-seed",
        type=int,
        default=42,
        help="Random seed for reproducible sampling (default: 42)",
    )
    group.add_argument(
        "--no-sync",
        dest="synchronize_workers",
        action="store_false",
        default=True,
        help=(
            "Disable lockstep barrier synchronization between workers. By "
            "default, workers wait at each sub-step so they advance in lockstep "
            "for fair resource sharing. Applies to every parallel/co-tenant "
            "strategy, not just PBT."
        ),
    )
    group.add_argument(
        "--disable-early-stopping",
        action="store_true",
        help=(
            "Disable the no-improvement early-stop gate (low-variance "
            "convergence and the round budget still apply)."
        ),
    )

    dist = parser.add_argument_group("Distributed Tuning (multi-device)")
    dist.add_argument(
        "--distributed",
        action="store_true",
        help=(
            "Run in DISTRIBUTED mode: one worker per dedicated device (see "
            "--inventory). Requires an identical device fleet. Local mode is "
            "unaffected when this flag is absent."
        ),
    )
    dist.add_argument(
        "--inventory",
        type=str,
        default=None,
        help=(
            "Path to the devices.yaml fleet inventory (required with "
            "--distributed). See configs/distributed/devices.example.yaml."
        ),
    )
    dist.add_argument(
        "--no-bootstrap",
        action="store_true",
        help=(
            "Skip SSH bootstrap; assume device agents are already running "
            "(distributed mode only)."
        ),
    )
    dist.add_argument(
        "--no-remote-deps",
        action="store_true",
        help="During bootstrap, skip 'pip install -r requirements.txt' on devices.",
    )
    dist.add_argument(
        "--eval-timeout",
        type=float,
        default=1800.0,
        help="Per-worker remote evaluation RPC timeout in seconds (default: 1800).",
    )
    dist.add_argument(
        "--agent-timeout",
        type=float,
        default=60.0,
        help="Per-agent control RPC timeout in seconds (default: 60).",
    )


def _add_workload_group(parser: argparse.ArgumentParser) -> None:
    group = parser.add_argument_group("Workload Settings")
    group.add_argument(
        "--benchmark",
        type=str,
        default="sysbench",
        choices=["sysbench", "tpch"],
        help="Benchmark driver (default: sysbench)",
    )
    group.add_argument(
        "--workload",
        type=str,
        default="oltp",
        choices=["oltp", "olap", "mixed"],
        help="Workload type for custom workloads (default: oltp)",
    )
    group.add_argument(
        "--workload-file",
        type=str,
        default=None,
        help="Path to a custom workload file (non-sysbench/tpch only)",
    )
    group.add_argument(
        "--duration",
        type=float,
        default=None,
        help="Measurement duration in seconds (overrides default)",
    )
    group.add_argument(
        "--warmup",
        type=float,
        default=None,
        help="Warmup duration in seconds (overrides default)",
    )
    group.add_argument(
        "--scale-factor",
        type=float,
        default=None,
        help="Benchmark scale factor (TPC-H / template)",
    )
    group.add_argument(
        "--sysbench-tables",
        type=int,
        default=None,
        help="Number of sysbench tables",
    )
    group.add_argument(
        "--sysbench-table-size",
        type=int,
        default=None,
        help="Rows per sysbench table",
    )
    group.add_argument(
        "--sysbench-workload",
        type=str,
        default=None,
        choices=["oltp_read_only", "oltp_read_write", "oltp_write_only"],
        help="Sysbench workload profile (default: oltp_read_write)",
    )


def _add_instance_group(parser: argparse.ArgumentParser) -> None:
    group = parser.add_argument_group("Instance Management")
    group.add_argument(
        "--tuning-mode",
        type=str,
        default=None,
        choices=["online", "offline", "adaptive"],
        help=(
            "Tuning mode controlling restart behavior (default: offline). "
            "online = runtime knobs only, no restarts; "
            "offline = all knobs, restart every generation; "
            "adaptive = all knobs, restart every N generations"
        ),
    )
    group.add_argument(
        "--no-docker",
        action="store_true",
        help="Run on bare-metal PostgreSQL instead of Docker",
    )
    group.add_argument(
        "--docker-image",
        type=str,
        default=None,
        help=(
            "Docker image override for PostgreSQL workers (e.g. postgres:18). "
            "If omitted, auto-resolved from the host server version. Ignored "
            "with --no-docker."
        ),
    )
    group.add_argument(
        "--force-recreate-instances",
        action="store_true",
        help="Force recreation of PostgreSQL instances (default: reuse existing)",
    )
    group.add_argument(
        "--force-recreate-baseline",
        action="store_true",
        help=(
            "Force recreation of the shared baseline snapshot every per-worker "
            "instance is cloned from (default: reuse cached baseline)"
        ),
    )
    snapshot_group = group.add_mutually_exclusive_group()
    snapshot_group.add_argument(
        "--enable-snapshots",
        dest="enable_snapshots",
        action="store_true",
        default=None,
        help=(
            "Restore each worker to the pristine baseline snapshot on the "
            "per-profile cadence so every measurement window starts from "
            "identical DB state (default: enabled)."
        ),
    )
    snapshot_group.add_argument(
        "--disable-snapshots",
        dest="enable_snapshots",
        action="store_false",
        default=None,
        help="Never restore the baseline snapshot between generations.",
    )
    group.add_argument(
        "--snapshot-restore-interval",
        type=int,
        default=None,
        metavar="N",
        help=(
            "Baseline-snapshot restore cadence in generations. When unset, "
            "defaults to the per-profile value selected by --config "
            "(rapid=10, standard=5, thorough=1, research=1)."
        ),
    )
    group.add_argument(
        "--cleanup-instances",
        action="store_true",
        help="Remove PostgreSQL instance data after completion",
    )
    group.add_argument(
        "--data-dir",
        type=str,
        default=None,
        help="Base directory for PostgreSQL instances (overrides PBT_DATA_ROOT)",
    )


def _add_resource_group(parser: argparse.ArgumentParser) -> None:
    group = parser.add_argument_group("Per-Worker Resources")
    group.add_argument(
        "--worker-ram",
        type=str,
        default=None,
        help=(
            "RAM to allocate per worker (e.g., '3G', '512M', '1073741824'). "
            "When set, bypasses auto-detection. Total across all workers must "
            "not exceed host physical RAM."
        ),
    )
    group.add_argument(
        "--worker-cpus",
        type=int,
        default=None,
        help=(
            "CPU cores to allocate per worker. When set, bypasses "
            "auto-detection. Total across all workers must not exceed host "
            "physical CPU cores."
        ),
    )
    group.add_argument(
        "--worker-disk-read-bps",
        type=int,
        default=None,
        help=(
            "Per-worker disk read bandwidth in bytes/sec (cgroup blkio / "
            "io.max). When unset, auto-detected via fio probe or heuristic."
        ),
    )
    group.add_argument(
        "--worker-disk-write-bps",
        type=int,
        default=None,
        help="Per-worker disk write bandwidth in bytes/sec.",
    )
    group.add_argument(
        "--worker-disk-read-iops",
        type=int,
        default=None,
        help="Per-worker disk read IOPS ceiling.",
    )
    group.add_argument(
        "--worker-disk-write-iops",
        type=int,
        default=None,
        help="Per-worker disk write IOPS ceiling.",
    )
    probe_group = group.add_mutually_exclusive_group()
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


def _add_scoring_group(parser: argparse.ArgumentParser) -> None:
    group = parser.add_argument_group("Scoring & Normalization")
    group.add_argument(
        "--scoring-policy",
        type=str,
        default=None,
        choices=["fixed_v1", "feature_driven_v2"],
        help="Policy for performance score aggregation (default: engine default)",
    )
    group.add_argument(
        "--scoring-policy-version",
        type=str,
        default=None,
        help="Frozen policy version string for reproducibility (e.g., 'v2.1')",
    )
    group.add_argument(
        "--metric-reference-version",
        type=str,
        default=None,
        help="Frozen normalizer metadata reference version (e.g., 'v1.0')",
    )


def _add_output_group(parser: argparse.ArgumentParser) -> None:
    group = parser.add_argument_group("Output & Logging")
    group.add_argument(
        "--output-dir", type=str, default="results", help="Base results directory"
    )
    group.add_argument(
        "--colocate-output",
        action="store_true",
        help=(
            "Place results/logs under the data directory instead of the "
            "default ./results/ directory"
        ),
    )
    group.add_argument(
        "--verbose",
        type=str,
        default="INFO",
        choices=["DEBUG", "INFO", "WARNING", "ERROR", "TRACE"],
        help="Logging verbosity (default: INFO)",
    )
    group.add_argument(
        "--no-color", action="store_true", help="Disable colored logging output"
    )


def build_benchmark_config(args: argparse.Namespace):
    """
    Materialize a ``BenchmarkConfig`` from parsed shared args.

    The base is the ``BenchmarkConfig`` of the profile selected by ``--config``
    (default ``standard``); each override then follows the "None means fall back
    to the profile default" convention so callers only deviate from the profile
    where a flag was actually supplied.
    """
    base = clone_benchmark_config(PROFILES[args.config].benchmark_config)
    tuning_mode = (
        TuningMode(args.tuning_mode)
        if getattr(args, "tuning_mode", None) is not None
        else base.tuning_mode
    )
    return replace(
        base,
        benchmark=args.benchmark,
        workload_type=args.workload,
        workload_file=args.workload_file,
        evaluation_duration=(
            args.duration if args.duration is not None else base.evaluation_duration
        ),
        warmup_duration=(
            args.warmup if args.warmup is not None else base.warmup_duration
        ),
        scale_factor=(
            args.scale_factor if args.scale_factor is not None else base.scale_factor
        ),
        sysbench_tables=(
            args.sysbench_tables
            if args.sysbench_tables is not None
            else base.sysbench_tables
        ),
        sysbench_table_size=(
            args.sysbench_table_size
            if args.sysbench_table_size is not None
            else base.sysbench_table_size
        ),
        sysbench_workload=(
            args.sysbench_workload
            if args.sysbench_workload is not None
            else base.sysbench_workload
        ),
        tuning_mode=tuning_mode,
    )


def build_lifecycle_config(
    args: argparse.Namespace,
    *,
    strategy: TuningStrategy,
) -> TunerLifecycleConfig:
    """
    Materialize a ``TunerLifecycleConfig`` from parsed shared args.

    Every cross-cutting knob is read off ``args``: the knob tier/source, random
    seed, tuning mode, per-worker resources, and scoring provenance. The worker
    count follows PBT's profile→override model — it seeds from the profile named
    by ``--config`` and is overridden by ``--parallel-workers`` only when that
    flag is supplied (its default is ``None``).
    """
    tuning_mode = (
        TuningMode(args.tuning_mode)
        if getattr(args, "tuning_mode", None) is not None
        else TuningMode.OFFLINE
    )
    num_parallel_workers = (
        args.parallel_workers
        if args.parallel_workers is not None
        else PROFILES[args.config].num_parallel_workers
    )
    enable_snapshots = (
        args.enable_snapshots if args.enable_snapshots is not None else True
    )
    snapshot_restore_interval = (
        args.snapshot_restore_interval
        if args.snapshot_restore_interval is not None
        else PROFILES[args.config].snapshot_restore_interval
    )
    return TunerLifecycleConfig(
        strategy=strategy,
        knob_tier=args.tier,
        knob_source=args.knob_source,
        num_parallel_workers=num_parallel_workers,
        cleanup_instances=args.cleanup_instances,
        use_docker=not args.no_docker,
        docker_image=getattr(args, "docker_image", None),
        random_seed=args.random_seed,
        synchronize_workers=getattr(args, "synchronize_workers", True),
        disable_early_stopping=getattr(args, "disable_early_stopping", False),
        tuning_mode=tuning_mode,
        force_recreate_instances=args.force_recreate_instances,
        force_recreate_baseline=args.force_recreate_baseline,
        enable_snapshots=enable_snapshots,
        snapshot_restore_interval=snapshot_restore_interval,
        worker_ram=args.worker_ram,
        worker_cpus=args.worker_cpus,
        worker_disk_read_bps=args.worker_disk_read_bps,
        worker_disk_write_bps=args.worker_disk_write_bps,
        worker_disk_read_iops=args.worker_disk_read_iops,
        worker_disk_write_iops=args.worker_disk_write_iops,
        probe_disk=args.probe_disk,
        scoring_policy=args.scoring_policy,
        scoring_policy_version=args.scoring_policy_version,
        metric_reference_version=args.metric_reference_version,
        distributed=getattr(args, "distributed", False),
        inventory=getattr(args, "inventory", None),
        bootstrap=not getattr(args, "no_bootstrap", False),
        remote_install_deps=not getattr(args, "no_remote_deps", False),
        eval_timeout=getattr(args, "eval_timeout", 1800.0),
        agent_timeout=getattr(args, "agent_timeout", 60.0),
    )


def resolve_output_root(
    args: argparse.Namespace,
    *,
    strategy: TuningStrategy,
    sysbench_workload: str,
    ablation_variable: str | None = None,
    ablation_value: str | None = None,
) -> Path:
    """Resolve the strategy/tier-scoped results directory from shared args.

    Derives a single ``workload`` key from the benchmark / workload flags,
    then delegates to :func:`resolve_tuner_output_root`.  When
    ``--colocate-output`` is set, results are rooted at
    ``{data_root}/results`` so a session's outputs travel with its data;
    otherwise the ``--output-dir`` value (default ``results``) is used.

    ``sysbench_workload`` is passed explicitly (rather than read off
    ``args``) so callers can forward the *resolved* benchmark-config value,
    which already has the default applied when the flag was omitted.
    """
    base_output_dir = (
        resolve_data_dir(args) / "results"
        if args.colocate_output
        else Path(args.output_dir)
    )

    if args.benchmark == "sysbench":
        workload = sysbench_workload
    elif args.benchmark == "tpch":
        workload = "olap"
    else:
        workload = args.workload

    return resolve_tuner_output_root(
        base_output_dir,
        strategy=strategy,
        workload=workload,
        knob_tier=args.tier,
        knob_source=args.knob_source,
        ablation_variable=ablation_variable,
        ablation_value=ablation_value,
    )


def resolve_data_dir(args: argparse.Namespace) -> Path:
    """Resolve the data root from ``--data-dir`` or the environment default."""
    return Path(args.data_dir) if args.data_dir else resolve_data_root()


def attach_session_html_log(
    output_root: Path,
    *,
    timestamp: str,
) -> Path:
    """Attach an HTML log handler under the run's ``logs/`` subdirectory.

    Every strategy writes its session trace to ``{output_root}/traces/`` and
    its best config to ``{output_root}/best_configs/``; this places the matching
    HTML run-log at ``{output_root}/logs/session_{ts}.html`` so all three
    session artifacts share one root and one strategy-agnostic convention
    (``trace_*.json`` / ``best_*.json`` / ``session_*.html``). The stem is
    fixed — the strategy is already encoded in the ``sessions/<workload>/
    <strategy>/`` path — replacing the incumbent flat, per-strategy
    ``{output_root}/{stem}_{ts}.html`` that PBT and LHS both wrote.

    Parameters
    ----------
    output_root
        The strategy/tier-scoped results root (from :func:`resolve_output_root`).
    timestamp
        Session id used in the filename.

    Returns
    -------
    Path
        The normalized HTML log path actually written to.
    """
    log_dir = Path(output_root) / "logs"
    log_dir.mkdir(parents=True, exist_ok=True)
    log_path = log_dir / f"session_{timestamp}.html"
    return add_html_file_logging(output_file=log_path, show_module=True)
