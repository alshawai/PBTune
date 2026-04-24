"""
Bayesian Optimization (BO) baseline runner powered by SMAC.

This script executes a traditional sequential Bayesian Optimization loop to find
optimal database knobs. It is designed specifically to act as a 1:1 baseline
comparison against the multi-agent Population-Based Training (PBT) tuner.

By reusing the exact same evaluation pipeline, workload executors, hardware
detection, and parameter scaling logic, it ensures fair comparisons between
the sequential BO search and evolutionary PBT.

Usage Examples:
    # Run a rapid evaluation using Sysbench (OLTP) on a minimal knob tier
    python -m src.scripts.run_bo_comparison --benchmark sysbench --config rapid --tier minimal

    # Run a longer evaluation using TPC-H (OLAP) with 100 SMAC evaluations
    python -m src.scripts.run_bo_comparison --benchmark tpch --config standard --max-evals 100

    # Custom durations, random seed, and forcing container recreation
    python -m src.scripts.run_bo_comparison --workload mixed --seed 42 --force-recreate-instances --duration 60
"""

from __future__ import annotations

import argparse
import json
import sys
import time
from datetime import datetime
from pathlib import Path
from typing import Any, Optional, TypedDict

class BOResult(TypedDict):
    bo_session: dict[str, Any]
    best_configuration: dict[str, Any]
    evaluation_history: list[dict[str, Any]]
    system_info: dict[str, Any]

from src.config.database import DatabaseConfig
from src.tuner.config import (
    PBTConfig,
    RAPID_CONFIG,
    RESEARCH_CONFIG,
    STANDARD_CONFIG,
    THOROUGH_CONFIG,
    EXTREME_CONFIG,
    get_knob_space,
)
from src.tuner.core.worker import Worker
from src.tuner.evaluator.evaluator import (
    Evaluator,
    EvaluatorConfig,
    WorkloadExecutor,
    WorkloadFileLoader,
)
from src.tuner.evaluator.metrics import WorkloadType, create_metric_config
from src.tuner.utils.hardware_info import detect_worker_resources, get_system_info
from src.tuner.utils.instance_manager import PostgresInstanceManager
from src.tuner.utils.logger_config import get_logger, setup_logging
from src.utils.logger.banners import print_bo_startup_banner
from src.benchmarks.sysbench.executor import SysbenchExecutor
from src.benchmarks.tpch.executor import TPCHExecutor

from src.scripts.bo import (
    BOEngine,
    PBTObjectiveAdapter,
    build_configspace,
    convert_numpy_types,
)

logger = get_logger(__name__)


def _build_pbt_config(args: argparse.Namespace) -> PBTConfig:
    """Build the runtime tuning configuration from profile + CLI overrides."""
    config_map = {
        "rapid": RAPID_CONFIG,
        "standard": STANDARD_CONFIG,
        "thorough": THOROUGH_CONFIG,
        "research": RESEARCH_CONFIG,
        "extreme": EXTREME_CONFIG,
    }
    config_dict = config_map[args.config].to_dict()

    if args.duration is not None:
        config_dict["evaluation_duration"] = args.duration
    if args.warmup is not None:
        config_dict["warmup_duration"] = args.warmup
    if args.scale_factor is not None:
        config_dict["scale_factor"] = args.scale_factor
    if args.sysbench_tables is not None:
        config_dict["sysbench_tables"] = args.sysbench_tables
    if args.sysbench_table_size is not None:
        config_dict["sysbench_table_size"] = args.sysbench_table_size
    if args.seed is not None:
        config_dict["random_seed"] = args.seed

    return PBTConfig(**config_dict)


def _build_workload_executor(
    workload_type: WorkloadType,
    benchmark: Optional[str],
    workload_file: Optional[str],
    pbt_config: PBTConfig,
) -> tuple[WorkloadExecutor, WorkloadType, str]:
    """Create the workload executor using the same logic as main.py."""
    if benchmark == "sysbench":
        return (
            SysbenchExecutor(
                tables=pbt_config.sysbench_tables,
                table_size=pbt_config.sysbench_table_size,
            ),
            WorkloadType.OLTP,
            "sysbench",
        )

    if benchmark == "tpch":
        return (
            TPCHExecutor(scale_factor=pbt_config.scale_factor),
            WorkloadType.OLAP,
            "tpch",
        )

    if workload_file:
        return WorkloadFileLoader.load_from_file(workload_file), workload_type, workload_type.value

    template_map = {
        WorkloadType.OLTP: "workloads/oltp.json",
        WorkloadType.OLAP: "workloads/olap.json",
        WorkloadType.MIXED: "workloads/mixed.json",
    }
    return (
        WorkloadFileLoader.load_from_file(template_map[workload_type]),
        workload_type,
        workload_type.value,
    )


class BORunner:
    """Bayesian Optimization runner with SMAC over project evaluator pipeline."""

    def __init__(self, args: argparse.Namespace):
        self.args = args
        self.timestamp = datetime.now().strftime("%Y%m%d_%H%M")
        self.start_time = 0.0

        self.pbt_config = _build_pbt_config(args)
        self.knob_space = get_knob_space(args.tier)
        self.knob_space.resolve_hardware_ranges(
            detect_worker_resources(max_parallel_workers=1)
        )

        workload_map = {
            "oltp": WorkloadType.OLTP,
            "olap": WorkloadType.OLAP,
            "mixed": WorkloadType.MIXED,
        }
        initial_workload_type = workload_map[args.workload]

        self.workload_executor, self.workload_type, self.benchmark_name = _build_workload_executor(
            initial_workload_type,
            args.benchmark,
            args.workload_file,
            self.pbt_config,
        )

        self.db_config = DatabaseConfig.from_env()
        self.metric_config = create_metric_config(self.workload_type.value)
        self.evaluator_config = EvaluatorConfig(
            workload_type=self.workload_type,
            metric_config=self.metric_config,
            db_config=self.db_config,
            warmup_duration=self.pbt_config.warmup_duration,
            measurement_duration=self.pbt_config.evaluation_duration,
            cooldown_duration=3.0,
            enable_restart=True,
            restart_interval=10,
            random_seed=args.seed,
            warmup_passes=self.pbt_config.warmup_passes,
        )
        self.evaluator = Evaluator(self.evaluator_config, self.workload_executor)

        self.instance_manager = PostgresInstanceManager(
            base_dir=Path(f"./pg_instances/{self.benchmark_name}"),
            base_port=5440,
            template_db_config=None if args.skip_schema_init else self.db_config,
            schema_provider=self.workload_executor,
        )

        workload_for_dir = "olap" if args.benchmark == "tpch" else self.workload_type.value
        self.output_dir = Path(args.output_dir) / workload_for_dir / "bo_runs" / args.tier
        self.output_dir.mkdir(parents=True, exist_ok=True)

        self.worker: Optional[Worker] = None
        self.adapter: Optional[PBTObjectiveAdapter] = None

    def setup(self) -> None:
        """Create and initialize the single worker instance used by sequential BO."""
        instances = self.instance_manager.setup_instances(
            num_workers=1,
            force_recreate=self.args.force_recreate_instances,
        )

        self.worker = Worker(worker_id=0, knob_space=self.knob_space, ready_interval=1)
        instance = instances[0]
        in_docker = getattr(self.instance_manager, "in_docker", False)

        self.worker.port = instance.port
        self.worker.db_config = DatabaseConfig(
            host="pbt-worker-0" if in_docker else "localhost",
            port=5432 if in_docker else instance.port,
            dbname=self.db_config.dbname,
            user=self.db_config.user,
            password=self.db_config.password,
        )

        self.adapter = PBTObjectiveAdapter(evaluator=self.evaluator, worker=self.worker, logger=logger)

    def run(self) -> BOResult:
        """Execute SMAC optimization and persist BO-comparison artifacts."""
        self.setup()
        self.start_time = time.time()

        config_space = build_configspace(self.knob_space, seed=self.args.seed)
        
        engine = BOEngine(
            config_space=config_space,
            objective_function=self.adapter.bo_objective_function,
            max_evaluations=self.args.max_evals,
            initial_design_size=self.args.initial_design_size,
            seed=self.args.seed if self.args.seed is not None else 42,
        )

        incumbent_dict, best_cost, history = engine.optimize()
        best_score = -float(best_cost) if best_cost is not None else None

        result: BOResult = {
            "bo_session": {
                "optimizer": "smac",
                "knob_tier": self.args.tier,
                "num_knobs": len(self.knob_space),
                "workload_type": self.workload_type.value,
                "benchmark_name": self.benchmark_name,
                "scale_factor": self.pbt_config.scale_factor,
                "sysbench_tables": self.pbt_config.sysbench_tables,
                "sysbench_table_size": self.pbt_config.sysbench_table_size,
                "max_evaluations": self.args.max_evals,
                "initial_design_size": self.args.initial_design_size,
                "seed": self.args.seed,
                "total_time_seconds": time.time() - self.start_time,
                "timestamp": self.timestamp,
            },
            "best_configuration": {
                "score": best_score,
                "cost": best_cost,
                "knobs": convert_numpy_types(self.knob_space.config_to_fractions(incumbent_dict)),
            },
            "evaluation_history": convert_numpy_types(history),
            "system_info": get_system_info(),
        }

        output_path = self.output_dir / f"bo_results_{self.timestamp}.json"
        output_path.parent.mkdir(parents=True, exist_ok=True)
        with open(output_path, "w", encoding="utf-8") as handle:
            json.dump(convert_numpy_types(result), handle, indent=2)

        logger.info("Saved BO comparison results to %s", output_path)
        return result

    def close(self) -> None:
        """Stop all managed instances and optionally remove instance data."""
        try:
            self.instance_manager.stop_all()
        finally:
            if self.args.cleanup_instances:
                self.instance_manager.cleanup(remove_data=True)


def parse_args() -> argparse.Namespace:
    """Parse command-line arguments for BO baseline runs."""
    parser = argparse.ArgumentParser(
        description="SMAC-based BO baseline runner for direct PBT comparison",
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )

    config_group = parser.add_argument_group("Configuration")
    config_group.add_argument(
        "--tier",
        type=str,
        default="minimal",
        choices=["minimal", "core", "standard", "extensive"],
        help="Knob space tier (default: minimal)",
    )
    config_group.add_argument(
        "--config",
        type=str,
        default="standard",
        choices=["rapid", "standard", "thorough", "research", "extreme"],
        help="Tuning profile to reuse workload timing/scaling defaults.",
    )

    workload_group = parser.add_argument_group("Workload")
    workload_exclusive = workload_group.add_mutually_exclusive_group()
    workload_exclusive.add_argument(
        "--workload",
        type=str,
        default="oltp",
        choices=["oltp", "olap", "mixed"],
        help="Workload type (default: oltp)",
    )
    workload_exclusive.add_argument(
        "--benchmark",
        type=str,
        choices=["sysbench", "tpch"],
        help="Specific benchmark to use (overrides --workload)",
    )
    workload_exclusive.add_argument(
        "--workload-file",
        type=str,
        help="Path to custom custom JSON workload (overrides --workload and --benchmark)",
    )

    workload_group.add_argument(
        "--scale-factor",
        type=float,
        help="Database scale factor (overrides config defaults if set).",
    )
    workload_group.add_argument(
        "--sysbench-tables",
        type=int,
        help="Number of sysbench tables.",
    )
    workload_group.add_argument(
        "--sysbench-table-size",
        type=int,
        help="Rows per sysbench table.",
    )

    bo_group = parser.add_argument_group("Bayesian Optimization")
    bo_group.add_argument(
        "--max-evals",
        type=int,
        default=50,
        help="Maximum number of BO evaluations (SMAC trials).",
    )
    bo_group.add_argument(
        "--initial-design-size",
        type=int,
        default=10,
        help="Number of initial random points before BO kicks in.",
    )
    bo_group.add_argument(
        "--seed",
        type=int,
        default=None,
        help="Random seed for repeatable outcomes.",
    )

    timing_group = parser.add_argument_group("Timing Overrides")
    timing_group.add_argument(
        "--duration",
        type=int,
        help="Override the evaluation duration in seconds.",
    )
    timing_group.add_argument(
        "--warmup",
        type=int,
        help="Override the workload warmup duration in seconds.",
    )

    storage_group = parser.add_argument_group("Storage / Restart")
    storage_group.add_argument(
        "--output-dir",
        type=str,
        default="results",
        help="Base directory for JSON outputs (default: results).",
    )
    storage_group.add_argument(
        "--force-recreate-instances",
        action="store_true",
        help="Tear down existing PG containers and recreate them.",
    )
    storage_group.add_argument(
        "--skip-schema-init",
        action="store_true",
        help="Assume schema/data are already present instead of reloading templates.",
    )
    storage_group.add_argument(
        "--cleanup-instances",
        action="store_true",
        help="Stop and remove PostgreSQL containers after completion.",
    )

    parser.add_argument(
        "-v", "--verbose", action="store_true", help="Increase logging verbosity to DEBUG."
    )

    return parser.parse_args()


def main() -> int:
    args = parse_args()
    verbosity = "DEBUG" if args.verbose else "INFO"
    setup_logging(verbosity=verbosity, enable_colors=True)
    logger.info("Starting Bayesian Optimization (BO) Runner with SMAC backend")
    print_bo_startup_banner(logger)

    runner = BORunner(args)
    try:
        runner.run()
        return 0
    except Exception as e:
        logger.error("BO tuning failed with error: %s", e, exc_info=True)
        return 1
    finally:
        runner.close()


if __name__ == "__main__":
    sys.exit(main())
