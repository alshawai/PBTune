"""Main Bayesian Optimization baseline runner orchestrator."""

import time
from pathlib import Path
from typing import Dict, Any, Tuple, List, Optional
from concurrent.futures import ThreadPoolExecutor, as_completed

from smac import BlackBoxFacade, HyperparameterOptimizationFacade
from smac.initial_design import SobolInitialDesign
from smac.random_design import ProbabilityRandomDesign
from smac.scenario import Scenario
from smac.runhistory.dataclasses import TrialInfo, TrialValue

from src.tuner.config import get_knob_space
from src.tuner.core.worker import Worker
from src.tuner.benchmark.orchestrator import (
    WorkloadOrchestrator,
    WorkloadOrchestratorConfig,
)
from src.benchmarks.sysbench.executor import SysbenchExecutor
from src.benchmarks.tpch.executor import TPCHExecutor
from src.utils.environments import EnvironmentFactory
from src.utils.metrics import WorkloadType, create_metric_config
from src.utils.hardware_info import (
    get_system_info,
    detect_worker_resources,
    resolve_manual_worker_resources,
    WorkerResources,
)
from src.utils.logger import setup_logging, get_logger, log_section_header
from src.utils.session_clock import format_session_id
from src.utils.timing import TimingRecorder
from src.utils.types import build_session_environment
from src.config.database import get_db_config
from src.config.data_root import resolve_data_root
from src.database.connection import get_connection

from src.scripts.bo_baseline.config import BOConfig
from src.scripts.bo_baseline.search_space import build_configspace
from src.scripts.bo_baseline.objective import create_objective, evaluate_config
from src.scripts.bo_baseline.result_writer import (
    write_bo_results,
    resolve_bo_output_root,
)

LOGGER = get_logger("Runner")


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
        self.run_timestamp = format_session_id()
        log_output_file = self._build_log_output_file(self.run_timestamp)
        setup_logging(verbosity=config.verbose, output_file=log_output_file)
        self.logger = get_logger("Runner")

        self.data_root = resolve_data_root(cli_override=config.data_dir)

        # Determine effective output directory
        self.effective_output_dir = (
            self.data_root / "results"
            if config.colocate_output
            else Path(config.output_dir)
        )

        # Collect system info
        self.system_info = get_system_info(data_path=self.data_root)

        # Resource equalization: PBT-derived > manual CLI override > auto detection
        if config.pbt_worker_resources:
            self.worker_resources = WorkerResources(
                ram_bytes=int(config.pbt_worker_resources.get("ram_bytes", 0)),
                cpu_cores=int(config.pbt_worker_resources.get("cpu_cores", 1)),
                disk_type=str(config.pbt_worker_resources.get("disk_type", "unknown")),
            )
            self.logger.info(
                "Using PBT-derived per-worker resources: "
                f"{self.worker_resources.cpu_cores} cores, "
                f"{self.worker_resources.ram_bytes / (1024**3):.1f} GB RAM"
            )
        elif config.worker_ram is not None or config.worker_cpus is not None:
            self.worker_resources = resolve_manual_worker_resources(
                worker_ram=config.worker_ram,
                worker_cpus=config.worker_cpus,
                num_workers=config.resource_division,
                data_path=self.data_root,
            )
            self.logger.info(
                "Using manual per-worker resources: "
                f"{self.worker_resources.cpu_cores} cores, "
                f"{self.worker_resources.ram_bytes / (1024**3):.1f} GB RAM"
            )
        else:
            self.worker_resources = detect_worker_resources(
                max_parallel_workers=config.resource_division, data_path=self.data_root
            )
            self.logger.info(
                f"Dividing host resources by {config.resource_division}: "
                f"{self.worker_resources.cpu_cores} cores, "
                f"{self.worker_resources.ram_bytes / (1024**3):.1f} GB RAM per instance"
            )

        # Resolve granular workload type
        if config.benchmark_config.benchmark == "sysbench":
            resolved_workload_type = (
                config.benchmark_config.sysbench_workload or "oltp_read_write"
            )
        elif config.benchmark_config.benchmark == "tpch":
            resolved_workload_type = "olap"
        else:
            resolved_workload_type = config.benchmark_config.workload_type

        # Load knob space
        self.knob_space = get_knob_space(
            config.knob_tier,
            knob_source=config.knob_source,
            workload_type=resolved_workload_type,
        )
        self.knob_space.resolve_hardware_ranges(self.worker_resources)

        # Database config
        self.db_config = get_db_config()

        # Metric config
        workload_type = WorkloadType(config.benchmark_config.workload_type)
        self.metric_config = create_metric_config(
            workload_type.value, scoring_policy=config.scoring_policy
        )

        self.logger.info(f"BO Baseline Runner initialized for tier: {config.knob_tier}")

        # Bootstrap recorder collects pre-tuning setup spans (instance setup,
        # snapshot prep, knob pruning). Populated in :meth:`run` before the
        # ask/tell loop starts so :attr:`tuning_start_time` excludes them.
        self.bootstrap_timing = TimingRecorder()
        # BO control-loop recorder collects facade.ask / facade.tell spans
        # accumulated across the parallel ask/tell loop (sequential mode
        # cannot easily intercept facade.optimize()).
        self.bo_timing = TimingRecorder()
        # Captured at the start of optimize() (after bootstrap). Used to
        # report a leak-free ``tuning_time_seconds`` excluding bootstrap.
        self.tuning_start_time: Optional[float] = None

    def _create_workload_executor(self):
        """Create appropriate workload executor based on benchmark type."""
        if self.config.benchmark_config.benchmark == "sysbench":
            return SysbenchExecutor(
                script=self.config.benchmark_config.sysbench_workload,
                tables=self.config.benchmark_config.sysbench_tables,
                table_size=self.config.benchmark_config.sysbench_table_size,
            )
        elif self.config.benchmark_config.benchmark == "tpch":
            return TPCHExecutor(
                scale_factor=self.config.benchmark_config.scale_factor,
            )
        else:
            raise ValueError(
                f"Unknown benchmark: {self.config.benchmark_config.benchmark}"
            )

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
            self.logger.warning(f"Failed to inspect runtime pg_settings: {exc}")
            return set(self.knob_space.knobs.keys()), "unknown"
        finally:
            if cursor is not None:
                cursor.close()
            if conn is not None:
                conn.close()

    def _prune_unsupported_runtime_knobs(self) -> None:
        """Prune knobs unavailable on runtime PostgreSQL."""
        supported_knobs, server_version = self._get_runtime_supported_knobs(worker_id=0)
        # Persist the discovered server version on the env so SessionEnvironment
        # can pick it up without a separate connection round-trip.
        if server_version and server_version != "unknown":
            self.env.pg_server_version = server_version
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

        self.logger.info(
            f"✓ Continuing with {len(self.knob_space)} runtime-compatible knobs"
        )

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

    def _build_smac_output_root(self) -> Path:
        """Build the SMAC output directory root under results."""
        bo_root = resolve_bo_output_root(
            output_dir=self.effective_output_dir,
            benchmark_config=self.config.benchmark_config,
            knob_tier=self.config.knob_tier,
        )
        smac_root = bo_root / "smac_output"
        smac_root.mkdir(parents=True, exist_ok=True)
        return smac_root

    def _build_log_output_file(self, timestamp: str) -> Path:
        """Create the HTML log output file under results."""

        # At this point, effective_output_dir is not set yet, so we have to resolve it manually
        data_root = resolve_data_root(cli_override=self.config.data_dir)
        eff_output_dir = (
            data_root / "results"
            if self.config.colocate_output
            else Path(self.config.output_dir)
        )

        bo_root = resolve_bo_output_root(
            output_dir=eff_output_dir,
            benchmark_config=self.config.benchmark_config,
            knob_tier=self.config.knob_tier,
        )
        log_dir = bo_root / "logs"
        log_dir.mkdir(parents=True, exist_ok=True)
        return log_dir / f"bo_baseline_{timestamp}.html"

    def run(self) -> Dict[str, Any]:
        """
        Run Bayesian Optimization tuning with parallel evaluation.

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

            # Create environment with N instances
            self.logger.info("Setting up database environment...")
            snapshot_id = (
                f"{self.config.benchmark_config.benchmark}_"
                f"{self.config.benchmark_config.workload_type}"
            )
            self.env = EnvironmentFactory.create(
                schema_provider=workload_executor,
                use_docker=self.config.use_docker,
                base_dir=self.data_root,
                base_port=5440,
                db_config=self.db_config,
                worker_resources=self.worker_resources,
                run_id=snapshot_id,
                image_name=self.config.docker_image,
                force_recreate_baseline=self.config.force_recreate_baseline,
            )

            # Setup N instances
            num_instances = self.config.max_workers
            self.logger.info(f"Setting up {num_instances} PostgreSQL instance(s)...")
            with self.bootstrap_timing.span("setup_instances", num_workers=num_instances):
                self.env.setup_instances(
                    num_workers=num_instances, num_parallel_workers=num_instances
                )

            # Prune unsupported knobs
            with self.bootstrap_timing.span("prune_knobs"):
                self._prune_unsupported_runtime_knobs()
                self._apply_pbt_knob_filter()

            # Create workload orchestrator
            self.logger.info("Creating workload orchestrator...")
            orchestrator_config = WorkloadOrchestratorConfig(
                workload_type=WorkloadType(self.config.benchmark_config.workload_type),
                metric_config=self.metric_config,
                db_config=self.db_config,
                warmup_duration=self.config.benchmark_config.warmup_duration,
                measurement_duration=self.config.benchmark_config.evaluation_duration,
                tuning_mode=self.config.benchmark_config.tuning_mode,
                random_seed=self.config.random_seed,
            )
            orchestrator = WorkloadOrchestrator(
                orchestrator_config, workload_executor, self.env
            )

            # Create N workers, each bound to its own instance
            workers = []
            for i in range(num_instances):
                w = Worker(worker_id=i, knob_space=self.knob_space)
                w.db_config = self.env.get_db_config(i)
                workers.append(w)

            # Build ConfigSpace
            self.logger.info("Building ConfigSpace...")
            configspace = build_configspace(
                self.knob_space, seed=self.config.random_seed
            )

            # Create iteration log
            iteration_log: list = []

            # Pilot phase size
            pilot_size = min(
                self.config.range_update_interval, self.config.n_iterations
            )

            # For sequential mode (max_workers=1), use the original objective closure
            # For parallel mode (max_workers > 1), we'll use ask-tell
            if self.config.max_workers == 1:
                objective = create_objective(
                    orchestrator=orchestrator,
                    worker=workers[0],
                    knob_space=self.knob_space,
                    metric_config=self.metric_config,
                    iteration_log=iteration_log,
                    pilot_phase_size=pilot_size,
                    env=self.env,
                    enable_snapshots=self.config.enable_snapshots,
                    snapshot_restore_interval=self.config.snapshot_restore_interval,
                )
            else:
                objective = None

            # Create SMAC scenario with n_workers=1 (we handle parallelism manually)
            self.logger.info(
                f"Creating SMAC scenario with {self.config.n_iterations} iterations..."
            )
            scenario = Scenario(
                configspace=configspace,
                n_trials=self.config.n_iterations,
                seed=self.config.random_seed,
                deterministic=False,
                n_workers=1,
                output_directory=(
                    self._build_smac_output_root()
                    / f"run_{self.run_timestamp}_{self.config.random_seed}"
                ),
            )

            # Sobol initial design
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
            # Bootstrap is done; capture the post-bootstrap clock so
            # ``tuning_time_seconds`` excludes setup overhead.
            self.tuning_start_time = time.time()
            try:
                if self.config.max_workers == 1:
                    # Sequential mode: use standard facade.optimize()
                    facade.optimize()
                else:
                    # Parallel mode: ask-tell loop with ThreadPoolExecutor
                    self._run_parallel_optimization(
                        facade, orchestrator, workers, iteration_log, pilot_size
                    )
            except KeyboardInterrupt:
                self.logger.warning("Optimization interrupted by user")

            # Write results
            self.logger.info("Writing results...")
            tuning_time = time.time() - self.tuning_start_time
            total_time = time.time() - start_time

            session_environment = build_session_environment(
                env=self.env,
                num_parallel_workers=self.config.max_workers,
                population_size=self.config.n_iterations,
                system_info=self.system_info,
                use_docker=self.config.use_docker,
            )

            results = write_bo_results(
                knob_space=self.knob_space,
                config=self.config,
                worker_resources=self.worker_resources,
                system_info=self.system_info,
                iteration_log=iteration_log,
                total_time=total_time,
                output_dir=self.config.output_dir,
                metric_config=self.metric_config,
                bo_surrogate=bo_surrogate,
                session_environment=session_environment,
                tuning_time_seconds=tuning_time,
                bootstrap_timing=self.bootstrap_timing,
                run_timestamp=self.run_timestamp,
            )

            self.logger.info(f"BO tuning completed in {total_time:.2f} seconds")
            self.logger.info(
                f"Best score: {results['best_configuration']['score']:.4f}"
            )

            return results

        finally:
            # Cleanup
            if hasattr(self, "env"):
                self.logger.info("Cleaning up environment...")
                try:
                    self.env.cleanup()
                except Exception as e:
                    self.logger.warning(f"Error during cleanup: {e}")

    def _run_parallel_optimization(
        self,
        facade,
        orchestrator: WorkloadOrchestrator,
        workers: list,
        iteration_log: list,
        pilot_size: int,
    ) -> None:
        """
        Run parallel BO optimization using ask-tell + ThreadPoolExecutor.

        Parameters
        ----------
        facade : BlackBoxFacade or HyperparameterOptimizationFacade
            SMAC facade in ask-tell mode
        orchestrator : WorkloadOrchestrator
            Workload orchestrator
        workers : list
            List of Worker instances
        iteration_log : list
            Mutable iteration log
        pilot_size : int
            Pilot phase size for normalization freezing
        """
        n_workers = len(workers)
        iteration_count = 0
        previous_configs: List[Optional[Dict[str, Any]]] = [None] * n_workers
        ranges_frozen = False

        while iteration_count < self.config.n_iterations:
            batch_size = min(n_workers, self.config.n_iterations - iteration_count)

            # Handle snapshot restoration before evaluating batch
            if (
                self.config.enable_snapshots
                and iteration_count > 0
                and iteration_count % self.config.snapshot_restore_interval == 0
            ):
                self.logger.info(
                    "Restoring database snapshots for iteration %d (interval: %d)",
                    iteration_count,
                    self.config.snapshot_restore_interval,
                )
                try:
                    failed_workers = []
                    for w in workers:
                        restored = self.env.restore_snapshot(w.worker_id)
                        if not restored:
                            self.logger.error(
                                "Snapshot restore failed for worker %d", w.worker_id
                            )
                            failed_workers.append(w.worker_id)
                    if failed_workers:
                        self.logger.error(
                            "Snapshot restore failed for workers: %s", failed_workers
                        )
                    else:
                        self.logger.info("✓ Database snapshots restored successfully")
                except Exception as e:
                    self.logger.error(
                        "Failed to restore databases from snapshots: %s", e
                    )

            # Ask for batch of configs.
            # Each facade.ask() is bracketed so the per-iteration overhead is
            # the real measured BO-side time (no fabricated proxies).
            ask_timings: List[float] = []
            trial_infos: List[TrialInfo] = []
            for _ in range(batch_size):
                t0 = time.monotonic()
                ti = facade.ask()
                elapsed = time.monotonic() - t0
                ask_timings.append(elapsed)
                self.bo_timing.add(
                    "bo_overhead_ask", elapsed, batch_size=batch_size
                )
                trial_infos.append(ti)
            # Amortize ask cost evenly across the batch — each trial in the
            # batch sees its share of the wall-clock the BO spent picking it.
            ask_overhead_per_trial = (
                sum(ask_timings) / batch_size if batch_size > 0 else 0.0
            )

            # Evaluate batch in parallel
            def evaluate_trial(worker_idx: int, trial_info: TrialInfo):
                worker = workers[worker_idx]
                try:
                    (
                        cost,
                        knob_config,
                        metrics,
                        score,
                        score_breakdown,
                        restarted,
                        wall_time,
                        eval_timing,
                    ) = evaluate_config(
                        trial_info.config,
                        worker,
                        orchestrator,
                        self.knob_space,
                        previous_configs[worker_idx],
                    )
                    previous_configs[worker_idx] = knob_config

                    return (
                        trial_info,
                        cost,
                        wall_time,
                        knob_config,
                        metrics,
                        score,
                        score_breakdown,
                        restarted,
                        eval_timing,
                    )
                except Exception as e:
                    self.logger.error(
                        f"Error evaluating config on worker {worker_idx}: {e}",
                        exc_info=True,
                    )
                    return (
                        trial_info,
                        100.0,
                        0.0,
                        {},
                        None,
                        0.0,
                        None,
                        False,
                        TimingRecorder(),
                    )

            with ThreadPoolExecutor(max_workers=batch_size) as executor:
                futures = {
                    executor.submit(evaluate_trial, i, info): i
                    for i, info in enumerate(trial_infos)
                }

                for future in as_completed(futures):
                    (
                        trial_info,
                        cost,
                        wall_time,
                        knob_config,
                        metrics,
                        score,
                        score_breakdown,
                        restarted,
                        eval_timing,
                    ) = future.result()

                    # Tell SMAC the result, bracketed so per-iteration
                    # bo_overhead is measured rather than estimated.
                    t0 = time.monotonic()
                    facade.tell(
                        trial_info,
                        TrialValue(cost=cost, time=wall_time),
                    )
                    tell_elapsed = time.monotonic() - t0
                    self.bo_timing.add(
                        "bo_overhead_tell", tell_elapsed, batch_size=batch_size
                    )

                    bo_overhead_seconds = ask_overhead_per_trial + tell_elapsed

                    # Log iteration
                    iteration_entry = {
                        "iteration": iteration_count,
                        "config": knob_config,
                        "metrics": metrics.to_dict() if metrics is not None else {},
                        "score": score if score is not None else 0.0,
                        "score_breakdown": score_breakdown,
                        "cost": cost,
                        "wall_clock_seconds": wall_time,
                        "restarted": restarted,
                        "timestamp": time.time(),
                        "timing": eval_timing.to_dict(include_summary=False),
                        "bo_overhead_seconds": bo_overhead_seconds,
                    }
                    iteration_log.append(iteration_entry)

                    iteration_count += 1

            # Pilot+Freeze: calibrate ranges exactly once after pilot phase
            if not ranges_frozen and iteration_count >= pilot_size:
                from src.utils.metrics import PerformanceMetrics

                all_metrics = [
                    PerformanceMetrics(**entry["metrics"])
                    for entry in iteration_log
                    if entry["metrics"]
                ]
                if all_metrics:
                    self.metric_config.update_ranges(all_metrics)
                    self.logger.info(
                        "Normalization ranges frozen after %d pilot iterations",
                        iteration_count,
                    )
                ranges_frozen = True


def main():
    """CLI entry point."""
    import argparse

    parser = argparse.ArgumentParser(
        description="Bayesian Optimization baseline for PostgreSQL tuning"
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

    # Required arguments
    parser.add_argument(
        "--tier",
        choices=["minimal", "core", "standard", "extensive"],
        help="Knob space tier. Optional when --pbt-session is provided.",
    )
    parser.add_argument(
        "--knob-source",
        choices=["expert", "data_driven"],
        default=None,
        help="Knob source to use (expert, data_driven) (default: loaded from PBT session or expert)",
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
            "Overrides PBT_DATA_ROOT env var. (default: ./.instances)"
        ),
    )

    # Output options
    parser.add_argument(
        "--output-dir",
        type=str,
        default=None,
        help="Output directory (default: preset value)",
    )
    parser.add_argument(
        "--colocate-output",
        action="store_true",
        help="Place results/logs under the data directory instead of the default ./results/ directory",
    )
    parser.add_argument(
        "--bo-surrogate",
        choices=["rf", "gp"],
        default=None,
        help=(
            "SMAC Surrogate model: Random Forest (rf) or Gaussian Process (gp). "
            "Default: preset value"
        ),
    )
    parser.add_argument(
        "--verbose",
        choices=["DEBUG", "INFO", "WARNING", "ERROR"],
        default=None,
        help="Logging level (default: preset value)",
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
        "--batched-bo",
        action="store_true",
        help="Run Bayesian Optimization in parallel using ask-tell mode. If omitted, runs sequentially.",
    )
    parser.add_argument(
        "--resource-division",
        type=int,
        default=None,
        help="Divides host capacity by this number to determine instance resources. If a PBT session is provided, this automatically takes the PBT session's parallel worker count.",
    )
    parser.add_argument(
        "--worker-ram",
        type=str,
        default=None,
        help=(
            "RAM to allocate per worker (e.g., '3G', '512M', '1073741824'). "
            "When set, bypasses auto-detection. Total across all workers must "
            "not exceed host physical RAM."
        ),
    )
    parser.add_argument(
        "--worker-cpus",
        type=int,
        default=None,
        help=(
            "CPU cores to allocate per worker. "
            "When set, bypasses auto-detection. Total across all workers must "
            "not exceed host physical CPU cores."
        ),
    )
    parser.add_argument(
        "--scoring-policy",
        type=str,
        default=None,
        help="Scoring policy to use. Options: 'fixed_v1', 'feature_driven_v2' (default: preset value per workload)",
    )
    parser.add_argument(
        "--enable-snapshots",
        action="store_true",
        default=None,
        help="Enable periodic database snapshot restoration to prevent data drift.",
    )
    parser.add_argument(
        "--snapshot-restore-interval",
        type=int,
        default=None,
        help="Restore snapshots every N iterations.",
    )

    args = parser.parse_args()

    # Create config and run
    config = BOConfig.from_args(args)
    runner = BOBaselineRunner(config)
    results = runner.run()

    return results


if __name__ == "__main__":
    main()
