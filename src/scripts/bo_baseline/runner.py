"""Main Bayesian Optimization baseline runner orchestrator."""

import time
import json
from pathlib import Path
from typing import Optional, Dict, Any, Tuple
from datetime import datetime

from smac import BlackBoxFacade, HyperparameterOptimizationFacade
from smac.initial_design import SobolInitialDesign
from smac.random_design import ProbabilityRandomDesign
from smac.scenario import Scenario
from ConfigSpace import Configuration

from src.tuner.config import get_knob_space
from src.tuner.core.worker import Worker
from src.tuner.evaluator.evaluator import Evaluator, EvaluatorConfig
from src.tuner.evaluator.restart_policy import TuningMode
from src.tuner.evaluator.workload import WorkloadFileLoader
from src.benchmarks.sysbench.executor import SysbenchExecutor
from src.benchmarks.tpch.executor import TPCHExecutor
from src.utils.environments import EnvironmentFactory
from src.utils.metrics import WorkloadType, create_metric_config
from src.utils.hardware_info import get_system_info, detect_worker_resources
from src.utils.logger import setup_logging, get_logger, log_section_header
from src.config.database import get_db_config
from src.database.connection import get_connection

from src.scripts.bo_baseline.config import BOConfig
from src.scripts.bo_baseline.search_space import build_configspace, configspace_to_knobs
from src.scripts.bo_baseline.objective import create_objective
from src.scripts.bo_baseline.result_writer import write_bo_results

logger = get_logger(__name__)


class BOBaselineRunner:
    """Bayesian Optimization baseline runner for PostgreSQL tuning."""

    def __init__(self, config: BOConfig):
        """
        Initialize BO baseline runner.

        Parameters
        ----------
        config : BOConfig
            Configuration for BO tuning
        """
        self.config = config
        setup_logging(verbosity=config.verbose)
        self.logger = get_logger(__name__)

        # Collect system info
        self.system_info = get_system_info()
        self.worker_resources = detect_worker_resources()

        # Load knob space
        self.knob_space = get_knob_space(config.knob_tier)
        self.knob_space.resolve_hardware_ranges(self.worker_resources)

        # Database config
        self.db_config = get_db_config()

        # Metric config
        workload_type = WorkloadType(config.workload_type)
        self.metric_config = create_metric_config(workload_type.value)

        self.logger.info(f"BO Baseline Runner initialized for tier: {config.knob_tier}")

    def _create_workload_executor(self):
        """Create appropriate workload executor based on benchmark type."""
        if self.config.benchmark == "sysbench":
            return SysbenchExecutor(
                script=self.config.sysbench_workload,
                tables=self.config.sysbench_tables,
                table_size=self.config.sysbench_table_size,
            )
        elif self.config.benchmark == "tpch":
            return TPCHExecutor(scale_factor=self.config.scale_factor)
        else:
            raise ValueError(f"Unknown benchmark: {self.config.benchmark}")

    def _get_runtime_supported_knobs(self, worker_id: int = 0) -> Tuple[set, str]:
        """Get runtime pg_settings knob names and server version."""
        db_config = self.env.get_db_config(worker_id)

        conn = None
        cursor = None
        try:
            conn = get_connection(config=db_config, connect_timeout=5)
            cursor = conn.cursor()
            cursor.execute("SELECT current_setting('server_version')")
            version_row = cursor.fetchone()
            server_version = str(version_row[0]) if version_row else "unknown"

            cursor.execute("SELECT name FROM pg_settings")
            supported_knobs = {str(row[0]) for row in cursor.fetchall()}
            return supported_knobs, server_version
        except Exception as exc:
            self.logger.warning(
                f"Failed to inspect runtime pg_settings: {exc}"
            )
            return set(self.knob_space.knobs.keys()), "unknown"
        finally:
            if cursor is not None:
                cursor.close()
            if conn is not None:
                conn.close()

    def _prune_unsupported_runtime_knobs(self) -> None:
        """Prune knobs unavailable on runtime PostgreSQL."""
        supported_knobs, server_version = self._get_runtime_supported_knobs(worker_id=0)
        configured_knobs = set(self.knob_space.knobs.keys())
        unsupported_knobs = sorted(configured_knobs - supported_knobs)

        if not unsupported_knobs:
            self.logger.info(
                f"✓ Runtime knob compatibility check passed against PostgreSQL {server_version} "
                f"({len(configured_knobs)} knobs)"
            )
            return

        for knob_name in unsupported_knobs:
            self.knob_space.knobs.pop(knob_name, None)

        preview = unsupported_knobs[:20]
        suffix = " ..." if len(unsupported_knobs) > len(preview) else ""
        self.logger.warning(
            f"Pruned {len(unsupported_knobs)} unsupported knobs for PostgreSQL {server_version}: "
            f"{', '.join(preview)}{suffix}"
        )

        if len(self.knob_space) == 0:
            raise RuntimeError(
                "No runtime-compatible knobs remain after pg_settings compatibility pruning."
            )

        self.logger.info(f"✓ Continuing with {len(self.knob_space)} runtime-compatible knobs")

    def _apply_pbt_knob_filter(self) -> None:
        """Restrict BO search to the knob names present in the reference PBT run."""
        if not self.config.pbt_knob_names:
            return

        requested_knobs = set(self.config.pbt_knob_names)
        available_knobs = set(self.knob_space.knobs.keys())
        missing_knobs = sorted(requested_knobs - available_knobs)
        if missing_knobs:
            raise RuntimeError(
                "Reference PBT run used knobs that are unavailable to BO after "
                f"tier/runtime pruning: {', '.join(missing_knobs)}"
            )

        removed_knobs = sorted(available_knobs - requested_knobs)
        for knob_name in removed_knobs:
            self.knob_space.knobs.pop(knob_name, None)

        self.logger.info(
            "✓ Restricted BO search space to %d knobs from reference PBT session",
            len(self.knob_space.knobs),
        )

    def run(self) -> Dict[str, Any]:
        """
        Run Bayesian Optimization tuning.

        Returns
        -------
        Dict[str, Any]
            Results dictionary
        """
        log_section_header(self.logger, "Bayesian Optimization Baseline Tuning")

        start_time = time.time()

        try:
            # Create workload executor
            self.logger.info("Creating workload executor...")
            workload_executor = self._create_workload_executor()

            # Create environment
            self.logger.info("Setting up database environment...")
            snapshot_id = f"{self.config.benchmark}_{self.config.workload_type}"
            self.env = EnvironmentFactory.create(
                schema_provider=workload_executor,
                use_docker=self.config.use_docker,
                base_dir=Path(f"./pg_instances/{self.config.benchmark}"),
                base_port=5440,
                db_config=self.db_config,
                worker_resources=self.worker_resources,
                run_id=snapshot_id,
                image_name=self.config.docker_image,
                force_recreate_baseline=self.config.force_recreate_baseline,
            )

            # Setup single instance
            self.logger.info("Setting up PostgreSQL instance...")
            self.env.setup_instances(num_workers=1)

            # Prune unsupported knobs
            self._prune_unsupported_runtime_knobs()
            self._apply_pbt_knob_filter()

            # Create evaluator
            self.logger.info("Creating evaluator...")
            evaluator_config = EvaluatorConfig(
                workload_type=WorkloadType(self.config.workload_type),
                metric_config=self.metric_config,
                db_config=self.db_config,
                warmup_duration=self.config.warmup_duration,
                measurement_duration=self.config.evaluation_duration,
                tuning_mode=TuningMode(self.config.tuning_mode),
            )
            evaluator = Evaluator(evaluator_config, workload_executor, self.env)

            # Create worker
            worker = Worker(
                worker_id=0,
                knob_space=self.knob_space,
            )
            worker.db_config = self.env.get_db_config(0)

            # Build ConfigSpace
            self.logger.info("Building ConfigSpace...")
            configspace = build_configspace(self.knob_space, seed=self.config.random_seed)

            # Create iteration log
            iteration_log = []

            # Pilot phase size: use range_update_interval as the freeze point
            pilot_size = max(
                self.config.range_update_interval,
                len(self.knob_space.knobs) + 1,
            )
            pilot_size = min(pilot_size, self.config.n_iterations)

            # Create objective function with freeze-after-pilot logic
            objective = create_objective(
                evaluator=evaluator,
                worker=worker,
                knob_space=self.knob_space,
                metric_config=self.metric_config,
                iteration_log=iteration_log,
                pilot_phase_size=pilot_size,
            )

            # Create SMAC scenario — deterministic=False because database
            # benchmarks have inherent measurement variance
            self.logger.info(f"Creating SMAC scenario with {self.config.n_iterations} iterations...")
            scenario = Scenario(
                configspace=configspace,
                n_trials=self.config.n_iterations,
                seed=self.config.random_seed,
                deterministic=False,
                output_directory=Path(f"./smac_output/run_{datetime.now().strftime('%Y%m%d_%H%M%S')}_{self.config.random_seed}"),
            )

            # Sobol initial design for uniform pilot-phase coverage
            initial_design = SobolInitialDesign(
                scenario=scenario,
                n_configs=pilot_size,
            )

            # Select facade based on surrogate arg
            num_knobs = len(self.knob_space.knobs)
            if self.config.bo_surrogate.lower() == "gp":
                self.logger.info(
                    f"Using BlackBoxFacade (GP) for {num_knobs} knobs, "
                    f"pilot_size={pilot_size}"
                )
                facade = BlackBoxFacade(
                    scenario,
                    objective,
                    initial_design=initial_design,
                    logging_level=False,
                )
                bo_surrogate = "gp"
            else:
                self.logger.info(
                    f"Using HyperparameterOptimizationFacade (RF) for {num_knobs} knobs, "
                    f"pilot_size={pilot_size}"
                )
                # 20% random interleaving prevents surrogate over-exploitation
                random_design = ProbabilityRandomDesign(
                    probability=0.2, seed=self.config.random_seed
                )
                facade = HyperparameterOptimizationFacade(
                    scenario,
                    objective,
                    initial_design=initial_design,
                    random_design=random_design,
                    logging_level=False,
                )
                bo_surrogate = "rf"

            # Run optimization
            self.logger.info("Starting Bayesian Optimization...")
            try:
                incumbent = facade.optimize()
            except KeyboardInterrupt:
                self.logger.warning("Optimization interrupted by user")
                incumbent = facade.runhistory.get_incumbent_config()

            # Write results
            self.logger.info("Writing results...")
            total_time = time.time() - start_time

            results = write_bo_results(
                knob_space=self.knob_space,
                config=self.config,
                worker_resources=self.worker_resources,
                system_info=self.system_info,
                iteration_log=iteration_log,
                total_time=total_time,
                output_dir=self.config.output_dir,
                bo_surrogate=bo_surrogate,
            )

            self.logger.info(f"BO tuning completed in {total_time:.2f} seconds")
            self.logger.info(f"Best score: {results['best_configuration']['score']:.4f}")

            return results

        finally:
            # Cleanup
            if hasattr(self, "env"):
                self.logger.info("Cleaning up environment...")
                try:
                    self.env.cleanup()
                except Exception as e:
                    self.logger.warning(f"Error during cleanup: {e}")


def main():
    """CLI entry point."""
    import argparse

    parser = argparse.ArgumentParser(
        description="Bayesian Optimization baseline for PostgreSQL tuning"
    )

    # Required arguments
    parser.add_argument(
        "--tier",
        choices=["minimal", "core", "standard", "extensive"],
        help="Knob space tier. Optional when --pbt-session is provided.",
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

    # BO Configuration
    parser.add_argument(
        "--iterations",
        type=int,
        default=None,
        help=(
            "Number of BO iterations. Defaults to 50, or to "
            "PBT population_size * total_generations when --pbt-session is used."
        ),
    )
    parser.add_argument(
        "--seed",
        type=int,
        default=42,
        help="Random seed (default: 42)",
    )

    # Benchmark
    parser.add_argument(
        "--benchmark",
        choices=["sysbench", "tpch"],
        default="sysbench",
        help="Benchmark type (default: sysbench)",
    )
    parser.add_argument(
        "--workload",
        choices=["oltp", "olap", "mixed"],
        default="oltp",
        help="Workload type (default: oltp)",
    )
    parser.add_argument(
        "--duration",
        type=float,
        default=30.0,
        help="Evaluation duration in seconds (default: 30)",
    )
    parser.add_argument(
        "--warmup",
        type=float,
        default=10.0,
        help="Warmup duration in seconds (default: 10)",
    )

    # Sysbench options
    parser.add_argument(
        "--sysbench-tables",
        type=int,
        default=4,
        help="Number of sysbench tables (default: 4)",
    )
    parser.add_argument(
        "--sysbench-table-size",
        type=int,
        default=100000,
        help="Sysbench table size (default: 100000)",
    )
    parser.add_argument(
        "--sysbench-workload",
        choices=["oltp_read_only", "oltp_read_write", "oltp_write_only"],
        default="oltp_read_write",
        help="Sysbench workload (default: oltp_read_write)",
    )

    # TPC-H options
    parser.add_argument(
        "--scale-factor",
        type=float,
        default=1.0,
        help="TPC-H scale factor (default: 1.0)",
    )
    parser.add_argument(
        "--tpch-warmup-passes",
        type=int,
        default=1,
        help="TPC-H warmup passes (default: 1)",
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
        default="offline",
        help="Tuning mode (default: offline)",
    )

    # Output options
    parser.add_argument(
        "--output-dir",
        type=str,
        default="results",
        help="Output directory (default: results)",
    )
    parser.add_argument(
        "--bo-surrogate",
        choices=["rf", "gp"],
        default="rf",
        help="SMAC Surrogate model: Random Forest (rf) or Gaussian Process (gp). Default: rf",
    )
    parser.add_argument(
        "--verbose",
        choices=["DEBUG", "INFO", "WARNING", "ERROR"],
        default="INFO",
        help="Logging level (default: INFO)",
    )
    parser.add_argument(
        "--range-update-interval",
        type=int,
        default=10,
        help="Pilot phase size: number of Sobol initial-design iterations before freezing normalization ranges (default: 10)",
    )

    args = parser.parse_args()

    # Create config and run
    config = BOConfig.from_args(args)
    runner = BOBaselineRunner(config)
    results = runner.run()

    return results


if __name__ == "__main__":
    main()
