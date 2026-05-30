"""Main Bayesian Optimization baseline runner orchestrator."""

import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Dict, Any, Tuple, List, Optional
from datetime import datetime

from ConfigSpace import Configuration
from smac import BlackBoxFacade, HyperparameterOptimizationFacade
from smac.initial_design import SobolInitialDesign
from smac.random_design import ProbabilityRandomDesign
from smac.scenario import Scenario
from smac.runhistory.dataclasses import TrialInfo, TrialValue
from smac.runhistory.enumerations import StatusType

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
    WorkerResources,
)
from src.utils.logger import setup_logging, get_logger, log_section_header
from src.config.database import get_db_config
from src.config.data_root import resolve_data_root
from src.database.connection import get_connection

from src.scripts.bo_baseline.config import BOConfig
from src.scripts.bo_baseline.search_space import (
    build_configspace,
    configspace_to_knobs,
    knobs_to_configspace,
)
from src.scripts.bo_baseline.objective import evaluate_config
from src.scripts.bo_baseline.result_writer import (
    write_bo_results,
    resolve_bo_output_root,
)

LOGGER = get_logger("Runner")


@dataclass
class PilotResult:
    """Container for an un-scored pilot observation.

    Stores both the raw SMAC suggestion and the resolved (DB-quantized)
    configuration so that Phase 3 can inject ground-truth values into the
    surrogate model after Phase 2 calibration.
    """

    raw_config: Configuration       # raw suggestion from smac.ask()
    resolved_config: Configuration  # actual executed params (valid Configuration)
    raw_metrics: dict               # PerformanceMetrics.to_dict() output
    eval_time: float                # wall-clock seconds
    status: StatusType = field(default=StatusType.SUCCESS)


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
        self.run_timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
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

        # Resource equalization: use PBT-derived resources if available, else divide host resources
        if config.pbt_worker_resources:
            self.worker_resources = WorkerResources(
                ram_bytes=int(config.pbt_worker_resources.get("ram_bytes", 0)),
                cpu_cores=int(config.pbt_worker_resources.get("cpu_cores", 1)),
                disk_type=str(config.pbt_worker_resources.get("disk_type", "unknown")),
            )
            self.logger.info(
                "Using PBT-derived per-worker resources: %d cores, %.1f GB RAM",
                self.worker_resources.cpu_cores,
                self.worker_resources.ram_bytes / (1024**3),
            )
            self.logger.debug("PBT-derived worker resources object: %s", self.worker_resources)
        else:
            self.worker_resources = detect_worker_resources(
                max_parallel_workers=config.resource_division, data_path=self.data_root
            )
            self.logger.info(
                "Dividing host resources by %s: %d cores, %.1f GB RAM per instance",
                config.resource_division,
                self.worker_resources.cpu_cores,
                self.worker_resources.ram_bytes / (1024**3),
            )
            self.logger.debug("Host-divided worker resources object: %s", self.worker_resources)

        # Load knob space
        self.knob_space = get_knob_space(config.knob_tier)
        self.knob_space.resolve_hardware_ranges(self.worker_resources)

        # Database config
        self.db_config = get_db_config()

        # Metric config
        workload_type = WorkloadType(config.benchmark_config.workload_type)
        self.metric_config = create_metric_config(
            workload_type.value, scoring_policy=config.scoring_policy
        )

        self.logger.info("BO Baseline Runner initialized for tier: %s", config.knob_tier)

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
        except Exception as e:
            self.logger.error("BO setup/execution failed: %s", e, exc_info=True)
            raise

    def _run_sequential_optimization(
        self,
        facade,
        orchestrator: WorkloadOrchestrator,
        worker: Worker,
        iteration_log: list,
        pilot_size: int,
        sobol_configs: list,
    ) -> None:
        """
        Run sequential BO optimization using a four-phase bootstrapping architecture.

        Phase 1 — Pilot Collection:
            Iterate the pre-generated Sobol configs (passed in as ``sobol_configs``).
            Evaluate each to get raw metrics but do NOT score them and do NOT touch
            the facade.  Accumulate ``PilotResult`` objects.

            Why we do NOT use ``facade.ask()`` here: calling ``ask()`` without a
            matching ``tell()`` leaves the intensifier with ghost "running" trials.
            With ``n_workers=1``, SMAC will refuse further asks until those trials
            are closed, which would block Phase 4 entirely.

        Phase 2 — Calibration:
            Fit normalization ranges from all successful pilot observations exactly
            once. Reload the scoring engine so Phases 3 and 4 use frozen ranges.

        Phase 3 — Warm-Start Injection:
            Re-score each successful pilot result with the frozen engine. Inject
            via ``facade.tell()`` using the *resolved* config (unsolicited
            observations — no prior ``ask()`` needed).  This primes the surrogate.

        Phase 4 — BO Loop:
            Standard ``facade.ask()`` / ``facade.tell()`` for the remaining
            iterations.  The facade's initial design is empty (``n_configs=0``),
            so the first ``ask()`` immediately enters BO (acquisition) mode.
            ``update_ranges()`` is NEVER called again.

        Parameters
        ----------
        facade : BlackBoxFacade or HyperparameterOptimizationFacade
            SMAC facade in ask-tell mode (created with empty initial design)
        orchestrator : WorkloadOrchestrator
            Workload orchestrator
        worker : Worker
            Worker instance (single — strictly sequential)
        iteration_log : list
            Mutable iteration log shared with the caller
        pilot_size : int
            Number of pilot configurations (== len(sobol_configs))
        sobol_configs : list[Configuration]
            Pre-generated Sobol configurations from
            ``SobolInitialDesign.select_configurations()``
        """
        from src.utils.metrics import PerformanceMetrics

        previous_config = None

        # ── Phase 1: Pilot Collection ─────────────────────────────────────────
        self.logger.info(
            "=== Phase 1: Pilot Collection (%d iterations) ===", pilot_size
        )
        pilot_results: list[PilotResult] = []

        # Iterate the pre-generated Sobol configs — facade is NOT touched here.
        for pilot_idx, sobol_config in enumerate(sobol_configs):
            if (
                self.config.enable_snapshots
                and pilot_idx > 0
                and pilot_idx % self.config.snapshot_restore_interval == 0
            ):
                self._restore_snapshot_safe(worker)

            try:
                (
                    _cost,       # None — scoring deferred
                    knob_config,
                    metrics,
                    _score,      # None — scoring deferred
                    _breakdown,  # None — scoring deferred
                    _restarted,
                    wall_time,
                ) = evaluate_config(
                    sobol_config,
                    worker,
                    orchestrator,
                    self.knob_space,
                    previous_config,
                    skip_scoring=True,
                )
                previous_config = knob_config

                if metrics is not None:
                    resolved_cs_config = knobs_to_configspace(
                        knob_config, self.knob_space, facade.scenario.configspace
                    )
                    status = StatusType.SUCCESS
                else:
                    resolved_cs_config = sobol_config  # fallback
                    status = StatusType.CRASHED

                pilot_results.append(PilotResult(
                    raw_config=sobol_config,
                    resolved_config=resolved_cs_config,
                    raw_metrics=metrics.to_dict() if metrics is not None else {},
                    eval_time=wall_time,
                    status=status,
                ))

            except Exception as exc:
                self.logger.error(
                    "Pilot iteration %d failed: %s", pilot_idx, exc, exc_info=True
                )
                pilot_results.append(PilotResult(
                    raw_config=sobol_config,
                    resolved_config=sobol_config,
                    raw_metrics={},
                    eval_time=0.0,
                    status=StatusType.CRASHED,
                ))

            self.logger.info(
                "Pilot %d/%d: status=%s, wall_time=%.2fs",
                pilot_idx + 1,
                pilot_size,
                pilot_results[-1].status.name,
                pilot_results[-1].eval_time,
            )
        # facade is untouched — no ghost pending trials.

        # ── Phase 2: Calibration ──────────────────────────────────────────────
        self.logger.info("=== Phase 2: Calibration ===")

        successful_pilots = [
            r for r in pilot_results
            if r.status == StatusType.SUCCESS and r.raw_metrics
        ]

        if len(successful_pilots) == 0:
            raise RuntimeError(
                "Zero pilot evaluations succeeded. Cannot calibrate normalization ranges. "
                "Check database connectivity and benchmark configuration."
            )
        elif len(successful_pilots) < 3:
            self.logger.warning(
                "Only %d pilot evaluation(s) succeeded (minimum 3 recommended for reliable normalization). "
                "Continuing with degraded calibration.", len(successful_pilots)
            )

        all_metrics = [PerformanceMetrics(**r.raw_metrics) for r in successful_pilots]
        self.metric_config.update_ranges(all_metrics)
        self.logger.info(
            "Normalization ranges frozen from %d pilot observations", len(all_metrics)
        )
        try:
            orchestrator.reload_scoring_engine()
        except Exception:
            self.logger.error(
                "Failed to rebuild scoring engine after calibration", exc_info=True
            )
            raise

        # ── Phase 3: Warm-Start Injection ─────────────────────────────────────
        self.logger.info(
            "=== Phase 3: Warm-Start Injection (%d observations) ===",
            len(successful_pilots),
        )
        engine = orchestrator._get_scoring_engine()

        for idx, result in enumerate(pilot_results):
            if result.status != StatusType.SUCCESS or not result.raw_metrics:
                self.logger.debug("Skipping failed pilot %d during injection", idx)
                continue

            pilot_metrics = PerformanceMetrics(**result.raw_metrics)
            score_breakdown = engine.compute_breakdown(
                pilot_metrics, worker_logger=worker.logger
            )
            score = score_breakdown.final_score
            cost = max(0.0, min(100.0, 100.0 - score))

            facade.tell(
                TrialInfo(config=result.resolved_config, seed=self.config.random_seed),
                TrialValue(cost=cost, time=result.eval_time, status=result.status),
            )
            self.logger.debug(
                "Injected pilot %d: score=%.2f, cost=%.2f", idx, score, cost
            )

            iteration_log.append({
                "iteration": idx,
                "config": configspace_to_knobs(
                    result.resolved_config, self.knob_space
                ),
                "metrics": result.raw_metrics,
                "score": score,
                "score_breakdown": score_breakdown,
                "cost": cost,
                "wall_clock_seconds": result.eval_time,
                "restarted": False,
                "timestamp": time.time(),
                "phase": "pilot",
            })

        incumbents = facade.intensifier.get_incumbents()
        assert len(incumbents) > 0, (
            "No incumbent found after pilot injection. "
            "Verify that StatusType and Configuration identity are correct."
        )
        self.logger.info(
            "Phase 3 complete: %d incumbent(s), %d observations injected",
            len(incumbents),
            len(successful_pilots),
        )

        # ── Phase 4: BO Optimization Loop ─────────────────────────────────────
        remaining = self.config.n_iterations - pilot_size
        self.logger.info("=== Phase 4: BO Loop (%d iterations) ===", remaining)
        # update_ranges() is NEVER called again — scoring engine stays frozen.

        for bo_idx in range(remaining):
            iteration_count = pilot_size + bo_idx

            if (
                self.config.enable_snapshots
                and iteration_count > 0
                and iteration_count % self.config.snapshot_restore_interval == 0
            ):
                self._restore_snapshot_safe(worker)

            trial_info = facade.ask()

            try:
                (
                    cost,
                    knob_config,
                    metrics,
                    score,
                    score_breakdown,
                    restarted,
                    wall_time,
                ) = evaluate_config(
                    trial_info.config,
                    worker,
                    orchestrator,
                    self.knob_space,
                    previous_config,
                )
                previous_config = knob_config
            except Exception as exc:
                self.logger.error("Error evaluating config: %s", exc, exc_info=True)
                cost, wall_time, knob_config, metrics, score, score_breakdown, restarted = (
                    100.0, 0.0, {}, None, 0.0, None, False
                )

            facade.tell(
                trial_info,
                TrialValue(cost=cost, time=wall_time, status=StatusType.SUCCESS),
            )

            # Inject repaired (DB-quantized) config via facade.tell() if it differs
            original_knob_config = configspace_to_knobs(
                trial_info.config, self.knob_space
            )
            if knob_config != original_knob_config:
                try:
                    repaired_cs_config = knobs_to_configspace(
                        knob_config, self.knob_space, facade.scenario.configspace
                    )
                    facade.tell(
                        TrialInfo(
                            config=repaired_cs_config, seed=self.config.random_seed
                        ),
                        TrialValue(
                            cost=cost, time=wall_time, status=StatusType.SUCCESS
                        ),
                    )
                    self.logger.debug(
                        "Injected repaired config via facade.tell() (quantized values differ)"
                    )
                except Exception as exc:
                    self.logger.warning("Failed to inject repaired config: %s", exc)

            iteration_log.append({
                "iteration": iteration_count,
                "config": knob_config,
                "metrics": metrics.to_dict() if metrics is not None else {},
                "score": score if score is not None else 0.0,
                "score_breakdown": score_breakdown,
                "cost": cost,
                "wall_clock_seconds": wall_time,
                "restarted": restarted,
                "timestamp": time.time(),
                "phase": "bo",
            })

            self.logger.info(
                "Iteration %d/%d [BO]: score=%.2f, cost=%.2f, wall_time=%.2fs",
                iteration_count + 1,
                self.config.n_iterations,
                score or 0.0,
                cost,
                wall_time,
            )

    def _prune_unsupported_runtime_knobs(self) -> None:
        """Prune knobs unavailable on runtime PostgreSQL."""
        supported_knobs, server_version = self._get_runtime_supported_knobs(worker_id=0)
        configured_knobs = set(self.knob_space.knobs.keys())
        unsupported_knobs = sorted(configured_knobs - supported_knobs)

        if not unsupported_knobs:
            self.logger.info(
                "✓ Runtime knob compatibility check passed against PostgreSQL %s (%d knobs)",
                server_version,
                len(configured_knobs),
            )
            return

        for knob_name in unsupported_knobs:
            self.knob_space.knobs.pop(knob_name, None)

        preview = unsupported_knobs[:20]
        suffix = " ..." if len(unsupported_knobs) > len(preview) else ""
        self.logger.warning(
            "Pruned %d unsupported knobs for PostgreSQL %s: %s%s",
            len(unsupported_knobs),
            server_version,
            ", ".join(preview),
            suffix,
        )

        if len(self.knob_space) == 0:
            raise RuntimeError(
                "No runtime-compatible knobs remain after pg_settings compatibility pruning."
            )

        self.logger.info(
            "✓ Continuing with %d runtime-compatible knobs", len(self.knob_space)
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
        Run Bayesian Optimization tuning (strictly sequential).

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
            self.logger.debug("Workload executor initialized: %s", type(workload_executor).__name__)

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
            self.logger.debug("Database environment created: %s", type(self.env).__name__)

            # Single worker bound to the single PostgreSQL instance
            num_instances = 1
            self.logger.info("Setting up 1 PostgreSQL instance...")
            self.env.setup_instances(num_workers=num_instances)

            # Prune unsupported knobs
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
            self.logger.debug("Orchestrator configuration: %s", orchestrator_config)

            # Single worker
            worker = Worker(worker_id=0, knob_space=self.knob_space)
            worker.db_config = self.env.get_db_config(0)

            # Build ConfigSpace
            self.logger.info("Building ConfigSpace...")
            configspace = build_configspace(
                self.knob_space, seed=self.config.random_seed
            )
            self.logger.debug("ConfigSpace initialized with %d dimensions", len(configspace.get_hyperparameters()))

            # Create iteration log
            iteration_log: list = []

            # Pilot phase size
            pilot_size = min(
                self.config.range_update_interval, self.config.n_iterations
            )

            # Pre-generate Sobol configs BEFORE creating the facade.
            #
            # Why: calling facade.ask() in Phase 1 (without a matching tell()) leaves
            # SMAC's intensifier with ghost "running" trials.  With n_workers=1, SMAC
            # refuses to issue new asks until those trials are closed, which would block
            # Phase 4 entirely.  Generating configs directly from SobolInitialDesign
            # bypasses the ask-tell loop so the facade stays clean until Phase 3.
            self.logger.info(
                "Pre-generating %d Sobol pilot configurations...", pilot_size
            )
            # Temporary scenario used only for Sobol generation (same seed/space).
            _sobol_scenario = Scenario(
                configspace=configspace,
                n_trials=pilot_size,
                seed=self.config.random_seed,
                deterministic=False,
                n_workers=1,
                output_directory=self._build_smac_output_root() / "_sobol_gen",
            )
            _sobol_design = SobolInitialDesign(
                scenario=_sobol_scenario,
                n_configs=pilot_size,
                max_ratio=1.0,  # Prevent SMAC from limiting pilot to 25% of n_trials
            )
            sobol_configs = _sobol_design.select_configurations()
            self.logger.info(
                "Generated %d Sobol configs for pilot phase", len(sobol_configs)
            )

            # Since we use ask-tell loops, provide a dummy objective to satisfy
            # the Facade constructor (it should never be called directly).
            def objective(config, seed=0):
                raise NotImplementedError(
                    "Objective should not be called directly in ask-tell mode"
                )

            # Create SMAC scenario (full budget; n_trials covers pilot + BO)
            self.logger.info(
                "Creating SMAC scenario with %d iterations...", self.config.n_iterations
            )
            scenario = Scenario(
                configspace=configspace,
                n_trials=self.config.n_iterations,
                seed=self.config.random_seed,
                deterministic=False,
                n_workers=1,
                output_directory=(
                    self._build_smac_output_root()
                    / f"run_{datetime.now().strftime('%Y%m%d_%H%M%S')}_{self.config.random_seed}"
                ),
            )

            # The facade is created with an EMPTY initial design (n_configs=0).
            # The pilot observations are injected via facade.tell() in Phase 3,
            # which primes the surrogate.  Phase 4's first ask() therefore enters
            # BO (acquisition) mode immediately — no duplicate Sobol suggestions.
            empty_design = SobolInitialDesign(scenario=scenario, n_configs=0)

            # Select facade based on surrogate arg
            num_knobs = len(self.knob_space.knobs)
            if self.config.bo_surrogate.lower() == "gp":
                self.logger.info(
                    "Using BlackBoxFacade (GP) for %d knobs, pilot_size=%d",
                    num_knobs,
                    pilot_size,
                )
                facade = BlackBoxFacade(
                    scenario,
                    objective,
                    initial_design=empty_design,
                    logging_level=False,
                )
                bo_surrogate = "gp"
            else:
                self.logger.info(
                    "Using HyperparameterOptimizationFacade (RF) for %d knobs, pilot_size=%d",
                    num_knobs,
                    pilot_size,
                )
                random_design = ProbabilityRandomDesign(
                    probability=0.2, seed=self.config.random_seed
                )
                facade = HyperparameterOptimizationFacade(
                    scenario,
                    objective,
                    initial_design=empty_design,
                    random_design=random_design,
                    logging_level=False,
                )
                bo_surrogate = "rf"

            # Run optimization
            self.logger.info("Starting Bayesian Optimization...")
            try:
                self._run_sequential_optimization(
                    facade, orchestrator, worker, iteration_log, pilot_size, sobol_configs
                )
            except KeyboardInterrupt:
                self.logger.warning("Optimization interrupted by user")

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
                metric_config=self.metric_config,
                bo_surrogate=bo_surrogate,
            )

            self.logger.info("BO tuning completed in %.2f seconds", total_time)
            self.logger.info(
                "Best score: %.4f", results["best_configuration"]["score"]
            )

            return results

        finally:
            # Cleanup
            if hasattr(self, "env"):
                self.logger.info("Cleaning up environment...")
                try:
                    self.env.cleanup()
                except Exception as e:
                    self.logger.warning("Error during cleanup: %s", e)

    def _restore_snapshot_safe(self, worker: Worker) -> None:
        """Attempt snapshot restoration with error handling."""
        self.logger.info(
            "Restoring database snapshot for worker %d...", worker.worker_id
        )
        try:
            restored = self.env.restore_snapshot(worker.worker_id)
            if not restored:
                self.logger.error(
                    "Snapshot restore failed for worker %d", worker.worker_id
                )
            else:
                self.logger.info("✓ Database snapshot restored successfully")
        except Exception as exc:
            self.logger.error("Failed to restore database from snapshot: %s", exc)

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
        "--resource-division",
        type=int,
        default=None,
        help="Divides host capacity by this number to determine instance resources. If a PBT session is provided, this automatically takes the PBT session's parallel worker count.",
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
