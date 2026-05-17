"""
evaluate_tuning — CLI entry point
===================================

Run as a module:

    python -m src.evaluation --session <path> [OPTIONS]

Examples:

    # Standard comparison: Docker containers, 5 repetitions, auto-detect benchmark
    python -m src.evaluation \\
        --session results/olap/pbt_runs/extensive/tuning_sessions/pbt_results_20260326_2115.json

    # Ten repetitions for tighter confidence intervals
    python -m src.evaluation \\
        --session results/olap/pbt_runs/extensive/tuning_sessions/pbt_results_20260326_2115.json \\
        --repetitions 10

    # Override benchmark type (normally auto-detected from session)
    python -m src.evaluation \\
        --session results/oltp/oltp_read_write/pbt_runs/standard/tuning_sessions/pbt_results_20260402_1559.json \\
        --benchmark sysbench

    # Bare-metal fallback (no Docker — reduced isolation)
    python -m src.evaluation \\
        --session results/olap/pbt_runs/extensive/tuning_sessions/pbt_results_20260326_2115.json \\
        --no-docker

    # Custom output directory
    python -m src.evaluation \\
        --session results/olap/pbt_runs/extensive/tuning_sessions/pbt_results_20260326_2115.json \\
        --output-dir /tmp/eval_results
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

from src.evaluation.exceptions import EvaluationError
from src.evaluation.runner import ComparisonRunner
from src.evaluation.types import ComparisonConfig
from src.utils.logger import setup_logging, get_logger


def _build_parser() -> argparse.ArgumentParser:
    """Build and return the argument parser for the evaluation CLI."""
    parser = argparse.ArgumentParser(
        prog="python -m src.evaluation",
        description=(
            "Compare a PBT-tuned PostgreSQL configuration against the default\n"
            "PostgreSQL settings using statistically rigorous repeated benchmarks.\n\n"
            "Results are saved to results/{workload}/comparisons/{tier}/comparison_{ts}.json\n"
            "and logs to results/{workload}/comparisons/{tier}/logs/evaluation_{ts}.html"
        ),
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog=(
            "Statistical methodology:\n"
            "  - Wilcoxon signed-rank test (non-parametric, paired)\n"
            "  - Bootstrap 95% CI on paired median differences (10,000 resamples)\n"
            "  - Holm correction for secondary endpoints (latency/throughput/memory)\n"
            "  - Paired Cohen's d effect size\n"
            "  - Both mean ± std and median ± IQR reported\n\n"
            "Examples:\n"
            "  python -m src.evaluation \\\n"
            "    --session results/olap/pbt_runs/extensive/tuning_sessions/"
            "pbt_results_20260326_2115.json\n\n"
            "  python -m src.evaluation \\\n"
            "    --session results/oltp/oltp_read_write/pbt_runs/standard/tuning_sessions/"
            "pbt_results_20260402_1559.json \\\n"
            "    --repetitions 10 --benchmark sysbench\n"
        ),
    )

    parser.add_argument(
        "--session",
        required=True,
        metavar="PATH",
        type=Path,
        help=(
            "Path to the PBT tuning session results JSON file.\n"
            "e.g. results/olap/pbt_runs/extensive/tuning_sessions/pbt_results_20260326_2115.json\n"
            "or results/oltp/oltp_read_write/pbt_runs/core/tuning_sessions/pbt_results_20260402_1559.json"
        ),
    )
    parser.add_argument(
        "--bo-session",
        metavar="PATH",
        type=Path,
        default=None,
        help=(
            "Path to BO baseline results JSON for 3-way comparison "
            "(Default vs BO vs PBT). When provided, runs a multi-arm "
            "evaluation instead of the standard 2-way comparison."
        ),
    )

    bench_grp = parser.add_argument_group("benchmark options")
    bench_grp.add_argument(
        "--benchmark",
        metavar="TYPE",
        choices=["sysbench", "tpch"],
        default=None,
        help=(
            "Benchmark type: 'sysbench' or 'tpch'. "
            "Auto-detected from the tuning session when not specified."
        ),
    )
    bench_grp.add_argument(
        "--repetitions",
        metavar="N",
        type=int,
        default=5,
        help="Number of independent runs per configuration (default: 5).",
    )
    bench_grp.add_argument(
        "--tpch-scale-factor",
        metavar="F",
        type=float,
        default=None,
        help=("TPC-H scale factor. Auto-detected from session when not specified."),
    )
    bench_grp.add_argument(
        "--sysbench-duration",
        metavar="S",
        type=int,
        default=None,
        help=(
            "Sysbench measurement duration in seconds. "
            "Auto-detected from session when not specified."
        ),
    )
    bench_grp.add_argument(
        "--sysbench-tables",
        metavar="N",
        type=int,
        default=None,
        help=(
            "Number of sysbench tables. Auto-detected from session when not specified."
        ),
    )
    bench_grp.add_argument(
        "--sysbench-table-size",
        metavar="N",
        type=int,
        default=None,
        help=(
            "Rows per sysbench table. Auto-detected from session when not specified."
        ),
    )
    bench_grp.add_argument(
        "--sysbench-workload",
        metavar="MODE",
        choices=["oltp_read_only", "oltp_read_write", "oltp_write_only"],
        default=None,
        help=(
            "Sysbench workload script mode. "
            "Auto-detected from session metadata when not specified."
        ),
    )
    bench_grp.add_argument(
        "--sysbench-warmup-seconds",
        metavar="S",
        type=int,
        default=None,
        help=(
            "Sysbench warmup duration in seconds. "
            "Auto-detected from session when not specified."
        ),
    )
    bench_grp.add_argument(
        "--tpch-warmup-passes",
        metavar="N",
        type=int,
        default=None,
        help=(
            "TPC-H warmup passes before measurement. "
            "Auto-detected from session when not specified."
        ),
    )
    bench_grp.add_argument(
        "--seed",
        metavar="N",
        type=int,
        default=50_000,
        help=(
            "Base deterministic seed for paired runs (default: 50000). "
            "Each repetition uses base_seed + run_number - 1 for both default and tuned runs."
        ),
    )

    scoring_grp = parser.add_argument_group("scoring options")
    scoring_grp.add_argument(
        "--scoring-policy",
        metavar="POLICY",
        type=str,
        default=None,
        help="Override scoring policy (e.g., 'feature_driven_v2'). Default: from session.",
    )
    scoring_grp.add_argument(
        "--scoring-policy-version",
        metavar="VER",
        type=str,
        default=None,
        help="Override scoring policy version. Default: from session.",
    )
    scoring_grp.add_argument(
        "--metric-reference-version",
        metavar="VER",
        type=str,
        default=None,
        help="Override metric reference version. Default: from session.",
    )

    env_grp = parser.add_argument_group("environment options")
    env_grp.add_argument(
        "--no-docker",
        action="store_true",
        default=False,
        help=(
            "Use bare-metal evaluation instead of Docker containers.\n"
            "WARNING: No resource isolation — results may be noisy."
        ),
    )
    env_grp.add_argument(
        "--docker-image",
        metavar="IMAGE",
        default="pbt-eval",
        help="Docker image name/tag for evaluation containers (default: pbt-eval).",
    )

    out_grp = parser.add_argument_group("output options")
    out_grp.add_argument(
        "--output-dir",
        metavar="PATH",
        type=Path,
        default=None,
        help=(
            "Override the base output directory for evaluation artifacts.\n"
            "Default: comparison JSON in results/{workload}/comparisons/{tier}/\n"
            "and HTML logs in results/{workload}/comparisons/{tier}/logs/"
        ),
    )

    parser.add_argument(
        "--verbose",
        choices=["DEBUG", "INFO", "WARNING", "ERROR"],
        default="INFO",
        help="Console log level (default: INFO).",
    )
    parser.add_argument(
        "-v",
        action="store_true",
        default=False,
        help="Shortcut for --log-level DEBUG.",
    )

    return parser


def main(argv: list[str] | None = None) -> int:
    """
    CLI entry point for the evaluate_tuning module.

    Args:
        argv: Argument list (defaults to sys.argv[1:]).

    Returns:
        Exit code: 0 on success, 1 on failure.
    """
    parser = _build_parser()
    args = parser.parse_args(argv)

    log_level = "DEBUG" if args.v else args.verbose
    setup_logging(verbosity=log_level)
    logger = get_logger("ComparisonRunner")

    if args.repetitions < 2:
        parser.error(
            "--repetitions must be at least 2 (need paired observations for Wilcoxon)."
        )
    if args.repetitions < 5:
        logger.warning(
            "Running only %d repetitions. Wilcoxon signed-rank requires "
            "N ≥ 5 for p < 0.05 (two-sided). Consider --repetitions 5.",
            args.repetitions,
        )

    config = ComparisonConfig(
        tuning_session_path=args.session,
        benchmark=args.benchmark,  # None when not specified → auto-detect
        repetitions=args.repetitions,
        scale_factor=args.tpch_scale_factor,
        sysbench_duration=args.sysbench_duration,
        sysbench_tables=args.sysbench_tables,
        sysbench_table_size=args.sysbench_table_size,
        sysbench_workload=args.sysbench_workload,
        sysbench_warmup_seconds=args.sysbench_warmup_seconds,
        tpch_warmup_passes=args.tpch_warmup_passes,
        pair_seed=args.seed,
        use_docker=not args.no_docker,
        docker_image=args.docker_image,
        output_dir=args.output_dir,
        scoring_policy=args.scoring_policy,
        scoring_policy_version=args.scoring_policy_version,
        metric_reference_version=args.metric_reference_version,
        bo_session_path=args.bo_session,
    )

    try:
        runner = ComparisonRunner(config)
        if config.bo_session_path:
            logger.info("BO session provided — running multi-arm comparison.")
            runner.run_multi_arm()
        else:
            runner.run()
        logger.info("Evaluation complete. Exit 0.")
        return 0

    except EvaluationError as exc:
        logger.error("Evaluation failed: %s", exc)
        return 1
    except KeyboardInterrupt:
        logger.info("Interrupted by user.")
        return 130
    except (
        Exception
    ) as exc:  # broad catch intentional: CLI must not crash with traceback
        logger.exception("Unexpected error during evaluation: %s", exc)
        return 1


if __name__ == "__main__":
    sys.exit(main())
