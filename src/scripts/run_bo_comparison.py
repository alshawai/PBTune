"""BO baseline runner using the same evaluation pipeline as PBT."""

from __future__ import annotations

import argparse
import importlib
import json
import sys
import time
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Any, Dict, Optional

import numpy as np

from src.config.database import DatabaseConfig
from src.tuner.config import (
    KnobDefinition,
    KnobScale,
    KnobSpace,
    KnobType,
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
from src.tuner.utils.logger_config import get_logger, setup_logging, print_startup_banner
from src.benchmarks.sysbench.executor import SysbenchExecutor
from src.benchmarks.tpch.executor import TPCHExecutor


logger = get_logger(__name__)


def _load_configspace_symbols() -> tuple[Any, Any, Any, Any]:
    """Load ConfigSpace symbols lazily to support environments without it preinstalled."""
    configspace_module = importlib.import_module("ConfigSpace")
    hyperparameters_module = importlib.import_module("ConfigSpace.hyperparameters")

    return (
        configspace_module.ConfigurationSpace,
        hyperparameters_module.UniformIntegerHyperparameter,
        hyperparameters_module.UniformFloatHyperparameter,
        hyperparameters_module.CategoricalHyperparameter,
    )


def _load_smac_symbols() -> tuple[Any, Any]:
    """Load SMAC symbols lazily to avoid static import failures."""
    smac_module = importlib.import_module("smac")
    return smac_module.HyperparameterOptimizationFacade, smac_module.Scenario


def convert_numpy_types(obj: Any) -> Any:
    """Recursively convert numpy values to JSON-safe native Python types."""
    if isinstance(obj, np.bool_):
        return bool(obj)
    if isinstance(obj, (np.integer, np.int64, np.int32, np.int16, np.int8)):
        return int(obj)
    if isinstance(obj, (np.floating, np.float64, np.float32, np.float16)):
        return float(obj)
    if isinstance(obj, np.ndarray):
        return obj.tolist()
    if isinstance(obj, dict):
        return {key: convert_numpy_types(value) for key, value in obj.items()}
    if isinstance(obj, (list, tuple)):
        return [convert_numpy_types(item) for item in obj]
    return obj


def _configuration_to_dict(configuration: Any) -> Dict[str, Any]:
    """Convert ConfigSpace/SMAC configuration objects to a plain dictionary."""
    if configuration is None:
        return {}

    if hasattr(configuration, "keys") and hasattr(configuration, "__getitem__"):
        try:
            return {key: configuration[key] for key in configuration.keys()}
        except Exception:
            pass

    if hasattr(configuration, "get_dictionary"):
        return dict(configuration.get_dictionary())

    if hasattr(configuration, "get"):
        maybe_items = configuration.get("items")
        if maybe_items is not None:
            return dict(configuration)

    if isinstance(configuration, dict):
        return dict(configuration)

    return dict(configuration)


def knob_to_hyperparameter(knob: KnobDefinition) -> Any:
    """Translate a single KnobDefinition into a ConfigSpace hyperparameter."""
    _, IntegerHyperparameter, FloatHyperparameter, CategoricalHyperparameter = (
        _load_configspace_symbols()
    )

    if knob.knob_type == KnobType.INTEGER:
        if knob.min_value is None or knob.max_value is None:
            raise ValueError(f"Integer knob '{knob.name}' is missing min/max bounds.")

        return IntegerHyperparameter(
            name=knob.name,
            lower=int(knob.min_value),
            upper=int(knob.max_value),
            log=(knob.scale == KnobScale.LOG and float(knob.min_value) > 0),
        )

    if knob.knob_type == KnobType.REAL:
        if knob.min_value is None or knob.max_value is None:
            raise ValueError(f"Real knob '{knob.name}' is missing min/max bounds.")

        return FloatHyperparameter(
            name=knob.name,
            lower=float(knob.min_value),
            upper=float(knob.max_value),
            log=(knob.scale == KnobScale.LOG and float(knob.min_value) > 0),
        )

    if knob.knob_type == KnobType.ENUM:
        if not knob.enum_values:
            raise ValueError(f"Enum knob '{knob.name}' is missing enum values.")
        return CategoricalHyperparameter(name=knob.name, choices=list(knob.enum_values))

    if knob.knob_type == KnobType.BOOLEAN:
        choices = list(knob.enum_values) if knob.enum_values else [True, False]
        return CategoricalHyperparameter(name=knob.name, choices=choices)

    raise ValueError(f"Unsupported knob type '{knob.knob_type}' for knob '{knob.name}'.")


def build_configspace_from_knob_space(knob_space: KnobSpace, seed: int | None = None) -> Any:
    """Build a ConfigSpace object from project KnobSpace metadata."""
    ConfigurationSpace, _, _, _ = _load_configspace_symbols()
    config_space = ConfigurationSpace(seed=seed)

    hyperparameters = []
    for knob_name in knob_space.get_knob_names():
        hyperparameters.append(knob_to_hyperparameter(knob_space[knob_name]))

    config_space.add_hyperparameters(hyperparameters)
    return config_space


def _extract_runhistory_entries(runhistory: Any) -> list[dict[str, Any]]:
    """Extract evaluation history entries from SMAC runhistory in a robust way."""
    history: list[dict[str, Any]] = []
    ids_config = getattr(runhistory, "ids_config", {})

    items: list[tuple[Any, Any]] = []
    if hasattr(runhistory, "items"):
        try:
            items = list(runhistory.items())
        except Exception:
            items = []

    # Backward-compatible fallback for alternative runhistory layouts.
    if not items:
        data = getattr(runhistory, "data", {})
        if hasattr(data, "items"):
            items = list(data.items())

    for index, (run_key, run_value) in enumerate(items, start=1):
        config_id = getattr(run_key, "config_id", None)
        configuration = ids_config.get(config_id)
        config_dict = _configuration_to_dict(configuration) if configuration is not None else {}

        cost = float(getattr(run_value, "cost", float("nan")))
        score = -cost
        eval_time = float(getattr(run_value, "time", 0.0))
        start_time = float(getattr(run_value, "starttime", 0.0))
        end_time = float(getattr(run_value, "endtime", 0.0))
        status = str(getattr(run_value, "status", "UNKNOWN"))
        additional_info = getattr(run_value, "additional_info", None)

        history.append(
            {
                "iteration": index,
                "config": convert_numpy_types(config_dict),
                "cost": cost,
                "score": score,
                "evaluation_time_seconds": eval_time,
                "start_time_seconds": start_time,
                "end_time_seconds": end_time,
                "status": status,
                "additional_info": convert_numpy_types(additional_info),
            }
        )

    return history


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


@dataclass
class EvaluatorAdapter:
    """Thin adapter to expose an evaluator.evaluate(config_dict) interface."""

    evaluator: Evaluator
    worker: Worker

    def evaluate(self, config_dict: Dict[str, Any]) -> tuple[Any, float]:
        self.worker.knob_config = config_dict
        if hasattr(self.evaluator, "evaluate"):
            result = self.evaluator.evaluate(config_dict)
            if isinstance(result, tuple) and len(result) >= 2:
                return result[0], float(result[1])

        metrics, score, _ = self.evaluator.evaluate_worker(self.worker, apply_config=True)
        return metrics, score


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
        self.adapter: Optional[EvaluatorAdapter] = None

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

        self.adapter = EvaluatorAdapter(evaluator=self.evaluator, worker=self.worker)

    def _objective(self, configuration: Any, seed: int = 0) -> float:
        """SMAC target function: return cost to minimize (negative score)."""
        if self.adapter is None:
            raise RuntimeError("Runner is not initialized. Call setup() first.")

        config_dict = _configuration_to_dict(configuration)
        _, score = self.adapter.evaluate(config_dict)
        return -float(score)

    def run(self) -> Dict[str, Any]:
        """Execute SMAC optimization and persist BO-comparison artifacts."""
        self.setup()
        self.start_time = time.time()

        HyperparameterOptimizationFacade, Scenario = _load_smac_symbols()
        config_space = build_configspace_from_knob_space(self.knob_space, seed=self.args.seed)

        scenario_seed = self.args.seed if self.args.seed is not None else 42
        scenario = Scenario(
            configspace=config_space,
            deterministic=True,
            n_trials=self.args.max_evals,
            seed=scenario_seed,
        )

        initial_design = HyperparameterOptimizationFacade.get_initial_design(
            scenario=scenario,
            n_configs=self.args.initial_design_size,
        )

        smac = HyperparameterOptimizationFacade(
            scenario=scenario,
            target_function=self._objective,
            initial_design=initial_design,
            overwrite=True,
        )

        incumbent = smac.optimize()
        runhistory = smac.runhistory
        history = _extract_runhistory_entries(runhistory)

        incumbent_dict = _configuration_to_dict(incumbent)
        best_cost = None
        if hasattr(runhistory, "get_cost"):
            try:
                best_cost = float(runhistory.get_cost(incumbent))
            except Exception:
                best_cost = None
        if best_cost is None and history:
            best_cost = min(entry["cost"] for entry in history)

        best_score = -float(best_cost) if best_cost is not None else None

        result = {
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
        "--workload-file",
        type=str,
        help="Path to custom workload file (JSON/YAML). Overrides --workload.",
    )
    workload_exclusive.add_argument(
        "--benchmark",
        type=str,
        choices=["sysbench", "tpch"],
        help="Run external benchmark pipeline (sysbench or tpch).",
    )
    workload_group.add_argument(
        "--duration",
        type=float,
        help="Evaluation duration in seconds per BO trial.",
    )
    workload_group.add_argument(
        "--warmup",
        type=float,
        help="Warmup duration in seconds before measurement.",
    )
    workload_group.add_argument(
        "--scale-factor",
        type=float,
        default=None,
        help="TPC-H scale factor. Only used with --benchmark tpch.",
    )
    workload_group.add_argument(
        "--sysbench-tables",
        type=int,
        default=None,
        help="Sysbench table count. Only used with --benchmark sysbench.",
    )
    workload_group.add_argument(
        "--sysbench-table-size",
        type=int,
        default=None,
        help="Sysbench rows per table. Only used with --benchmark sysbench.",
    )

    bo_group = parser.add_argument_group("Bayesian Optimization")
    bo_group.add_argument(
        "--max-evals",
        type=int,
        default=50,
        help="Maximum BO evaluations (SMAC n_trials).",
    )
    bo_group.add_argument(
        "--seed",
        type=int,
        default=42,
        help="Random seed for SMAC and workload reproducibility.",
    )
    bo_group.add_argument(
        "--initial-design-size",
        type=int,
        default=10,
        help="Number of initial design points before model-guided BO.",
    )

    infra_group = parser.add_argument_group("Instance Management")
    infra_group.add_argument(
        "--force-recreate-instances",
        action="store_true",
        help="Force recreation of PostgreSQL instances (default: reuse existing).",
    )
    infra_group.add_argument(
        "--cleanup-instances",
        action="store_true",
        help="Remove PostgreSQL instance data after completion.",
    )
    infra_group.add_argument(
        "--skip-schema-init",
        action="store_true",
        help="Skip schema initialization from template database.",
    )

    output_group = parser.add_argument_group("Output")
    output_group.add_argument(
        "--verbose",
        type=str,
        default="INFO",
        choices=["DEBUG", "INFO", "WARNING", "ERROR", "TRACE"],
        help="Logging verbosity level.",
    )
    output_group.add_argument(
        "--output-dir",
        type=str,
        default="results",
        help="Base output directory for BO result artifacts.",
    )

    return parser.parse_args()


def main() -> int:
    """CLI entrypoint for BO baseline comparison runs."""
    args = parse_args()
    print_startup_banner()
    setup_logging(verbosity=args.verbose, enable_colors=True, show_module=True)

    runner = BORunner(args)

    try:
        runner.run()
        logger.info("BO baseline run completed successfully")
        return 0
    except Exception as exc:
        logger.error("BO baseline run failed: %s", exc, exc_info=True)
        return 1
    finally:
        runner.close()


if __name__ == "__main__":
    sys.exit(main())
