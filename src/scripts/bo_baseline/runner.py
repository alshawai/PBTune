"""Main Bayesian Optimization baseline runner orchestrator."""

from src.utils.metrics import PerformanceMetrics
import json
import shutil
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Dict, Any, Tuple, Optional

from ConfigSpace import Configuration, ConfigurationSpace
from smac import BlackBoxFacade, HyperparameterOptimizationFacade
from smac.initial_design import SobolInitialDesign
from smac.random_design import ProbabilityRandomDesign
from smac.scenario import Scenario
from smac.runhistory.dataclasses import TrialInfo, TrialValue
from smac.runhistory.enumerations import StatusType

from src.knobs import get_knob_space
from src.tuners.engine.worker import BaseWorker
from src.tuners.engine.orchestrator import (
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
from src.utils.logger import (
    setup_logging,
    get_logger,
    log_section_header,
    log_worker_metrics_table,
)
from src.utils.session_clock import format_session_id
from src.utils.timing import TimingRecorder
from src.utils.types import build_session_environment
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
from src.scripts.bo_baseline.cotenant import CoTenantLoadController
from src.scripts.bo_baseline.result_writer import (
    write_bo_results,
    resolve_bo_output_root,
)

LOGGER = get_logger("Runner")


@dataclass
class EvalRecord:
    """Parallel history entry used for dynamic SMAC cost relabeling.

    Every evaluated configuration is stored here so that when the
    normalization bounds expand, all past costs can be recomputed and
    overwritten in SMAC's RunHistory via ``force_update=True``.
    """

    config: Configuration  # resolved (DB-quantized) ConfigSpace config
    raw_metrics: "PerformanceMetrics | None"  # raw metrics object (None on crash)
    trial_info: TrialInfo  # TrialInfo used in the matching tell() call
    eval_time: float  # wall-clock seconds
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
            # PBT inheritance wins for fairness, but make the precedence
            # explicit: if manual --worker-ram/--worker-cpus/--worker-disk-*
            # flags were ALSO supplied they are intentionally ignored here.
            # Surfacing this avoids the silent-override footgun where a caller
            # believes their flags took effect.
            manual_flags_present = config.worker_ram is not None or (
                config.worker_cpus is not None
            ) or any(
                v is not None
                for v in (
                    config.worker_disk_read_bps,
                    config.worker_disk_write_bps,
                    config.worker_disk_read_iops,
                    config.worker_disk_write_iops,
                )
            )
            if manual_flags_present:
                self.logger.warning(
                    "Manual --worker-ram/--worker-cpus/--worker-disk-* flags are "
                    "IGNORED because a PBT session was supplied; per-worker "
                    "resources are inherited from the PBT session to preserve the "
                    "fair-comparison invariant."
                )
            self.worker_resources = WorkerResources(
                ram_bytes=int(config.pbt_worker_resources.get("ram_bytes", 0)),
                cpu_cores=int(config.pbt_worker_resources.get("cpu_cores", 1)),
                disk_type=str(config.pbt_worker_resources.get("disk_type", "unknown")),
                disk_read_bps=int(
                    config.pbt_worker_resources.get("disk_read_bps", 0) or 0
                ),
                disk_write_bps=int(
                    config.pbt_worker_resources.get("disk_write_bps", 0) or 0
                ),
                disk_read_iops=int(
                    config.pbt_worker_resources.get("disk_read_iops", 0) or 0
                ),
                disk_write_iops=int(
                    config.pbt_worker_resources.get("disk_write_iops", 0) or 0
                ),
                disk_class=str(
                    config.pbt_worker_resources.get("disk_class", "unknown")
                    or "unknown"
                ),
            )
            self.logger.info(
                "Using PBT-derived per-worker resources: %d cores, %.1f GB RAM",
                self.worker_resources.cpu_cores,
                self.worker_resources.ram_bytes / (1024**3),
            )
            self.logger.debug(
                "PBT-derived worker resources object: %s", self.worker_resources
            )
        elif config.worker_ram is not None or config.worker_cpus is not None or any(
            v is not None
            for v in (
                config.worker_disk_read_bps,
                config.worker_disk_write_bps,
                config.worker_disk_read_iops,
                config.worker_disk_write_iops,
            )
        ):
            self.worker_resources = resolve_manual_worker_resources(
                worker_ram=config.worker_ram,
                worker_cpus=config.worker_cpus,
                num_workers=config.resource_division,
                data_path=self.data_root,
                worker_disk_read_bps=config.worker_disk_read_bps,
                worker_disk_write_bps=config.worker_disk_write_bps,
                worker_disk_read_iops=config.worker_disk_read_iops,
                worker_disk_write_iops=config.worker_disk_write_iops,
                probe_disk=config.probe_disk,
            )
            self.logger.info(
                "Using manual per-worker resources: "
                f"{self.worker_resources.cpu_cores} cores, "
                f"{self.worker_resources.ram_bytes / (1024**3):.1f} GB RAM"
            )
        else:
            self.worker_resources = detect_worker_resources(
                max_parallel_workers=config.resource_division,
                data_path=self.data_root,
                probe_disk=config.probe_disk,
            )
            self.logger.info(
                "Dividing host resources by %s: %d cores, %.1f GB RAM per instance",
                config.resource_division,
                self.worker_resources.cpu_cores,
                self.worker_resources.ram_bytes / (1024**3),
            )
            self.logger.debug(
                "Host-divided worker resources object: %s", self.worker_resources
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
        metric_kwargs: Dict[str, Any] = {}
        if config.scoring_policy is not None:
            metric_kwargs["scoring_policy"] = config.scoring_policy
        self.metric_config = create_metric_config(
            workload_type.value, **metric_kwargs
        )

        self.logger.info(
            "BO Baseline Runner initialized for tier: %s", config.knob_tier
        )

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
        # Co-tenant load controller; replaced with a real one in run() once the
        # environment/orchestrator exist. Defaults to a disabled no-op so any
        # early cleanup path is safe.
        self.cotenant: Optional[CoTenantLoadController] = None

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
            self.logger.error(
                "Failed to query pg_settings from worker %d "
                "(DB may be unreachable or not yet started): %s",
                worker_id,
                e,
                exc_info=True,
            )
            raise

    def _relabel_smac_history(
        self,
        facade,
        orchestrator,
        eval_history: list,
        worker,
    ) -> int:
        """Rescore every past evaluation and overwrite SMAC costs.

        Called immediately after ``metric_config.expand_ranges_for_metrics()``
        returns True so the surrogate model retrains on a consistent landscape.

        Returns
        -------
        int
            Number of entries relabeled.
        """
        engine = orchestrator._get_scoring_engine()
        relabeled = 0
        for record in eval_history:
            if record.status != StatusType.SUCCESS or record.raw_metrics is None:
                continue
            breakdown = engine.compute_breakdown(
                record.raw_metrics, worker_logger=worker.logger
            )
            new_cost = max(0.0, min(100.0, 100.0 - breakdown.final_score))
            self.logger.debug(
                "  Relabeling entry %d: new_cost=%.4f (score=%.4f)",
                relabeled + 1,
                new_cost,
                breakdown.final_score,
            )
            facade.runhistory.add(
                config=record.config,
                cost=new_cost,
                time=record.eval_time,
                status=record.status,
                instance=record.trial_info.instance,
                seed=record.trial_info.seed,
                force_update=True,
            )
            relabeled += 1
        skipped = len(eval_history) - relabeled
        self.logger.debug(
            "Relabeling complete: %d updated, %d skipped (CRASHED/None metrics)",
            relabeled,
            skipped,
        )
        return relabeled

    def _log_disk_usage(self, label: str) -> None:
        """Log disk usage of PGDATA directories and filesystem for diagnostics."""
        try:
            total, used, free = shutil.disk_usage("/")
            pct = used / total * 100
            self.logger.info(
                "[disk] %s — filesystem: %.1f%% used (%.1f GB free)",
                label,
                pct,
                free / (1024**3),
            )
            if not hasattr(self, "env") or self.env is None:
                return
            base = getattr(self.env, "base_dir", None)
            if base is None:
                return
            base = Path(base)
            for child in sorted(base.rglob("pgdata")):
                if child.is_dir():
                    try:
                        size = sum(
                            f.stat().st_blocks * 512
                            for f in child.rglob("*")
                            if f.is_file()
                        )
                        self.logger.info(
                            "[disk]   %s: %.1f MB",
                            child.relative_to(base),
                            size / (1024**2),
                        )
                    except OSError:
                        pass
        except Exception as exc:
            self.logger.debug("[disk] usage probe failed: %s", exc)

    def _evaluate_with_cotenancy(
        self,
        config,
        worker: BaseWorker,
        orchestrator: WorkloadOrchestrator,
        previous_engine_config,
        seed=None,
        restore_due: bool = False,
        next_eval_will_restore: bool = False,
    ):
        """Evaluate one foreground BO trial under matched co-tenant load.

        Creates a fresh per-trial barrier shared by the foreground worker and
        the ``degree - 1`` background loaders, launches the loaders, runs the
        foreground evaluation in lockstep (its workload contends with the
        background load during B8–B9), then joins the loaders. When co-tenancy
        is disabled the barrier is ``None`` and this reduces to a plain
        ``evaluate_config`` call (zero overhead).
        """
        barriers = self.cotenant.make_barrier() if self.cotenant else None
        futures = self.cotenant.start_round(barriers) if self.cotenant else []
        try:
            return evaluate_config(
                config,
                worker,
                orchestrator,
                self.knob_space,
                previous_engine_config,
                seed=seed,
                restore_due=restore_due,
                next_eval_will_restore=next_eval_will_restore,
                barriers=barriers,
            )
        finally:
            if self.cotenant:
                self.cotenant.finish_round(futures)

    def _run_sequential_optimization(
        self,
        facade,
        orchestrator: WorkloadOrchestrator,
        worker: BaseWorker,
        iteration_log: list,
        pilot_size: int,
        sobol_configs: list,
    ) -> tuple[bool, int]:
        """
        Run sequential BO optimization with Dynamic Relabeling.

        Phase 1 — Bootstrap:
            Evaluate the pre-generated Sobol configs using fallback normalizer
            anchors (QuantileUtilityNormalizer already ships sensible defaults).
            Each evaluation is immediately injected into SMAC via ``tell()``
            (unsolicited observations — no prior ``ask()`` needed) so the
            surrogate is primed from the first iteration.

            After all bootstrap configs are evaluated, the normalizer is
            calibrated from those observations and all bootstrap entries in
            SMAC's RunHistory are rewritten via ``force_update=True``.

        Phase 2 — Adaptive BO Loop:
            Standard ``facade.ask()`` / ``evaluate_config()`` / ``facade.tell()``
            for the remaining iterations.  After every evaluation, the normalizer
            checks whether the new metrics exceed the current bounds.  If so,
            the scoring engine is reloaded and the entire history is relabeled
            before the next ``ask()`` so the surrogate retrains on a consistent
            landscape.

            Early stopping: if the incumbent score does not improve for
            ``early_stopping_patience`` consecutive iterations, the loop exits.

        Parameters
        ----------
        facade : BlackBoxFacade or HyperparameterOptimizationFacade
            SMAC facade in ask-tell mode (created with empty initial design)
        orchestrator : WorkloadOrchestrator
            Workload orchestrator
        worker : BaseWorker
            Worker instance (single — strictly sequential)
        iteration_log : list
            Mutable iteration log shared with the caller
        pilot_size : int
            Number of bootstrap configurations (== len(sobol_configs))
        sobol_configs : list[Configuration]
            Pre-generated Sobol configurations from
            ``SobolInitialDesign.select_configurations()``

        Returns
        -------
        tuple[bool, int]
            (early_stopped, stale_counter) — whether early stopping fired and
            how many consecutive non-improving iterations were recorded.
        """

        previous_engine_config = None
        # Parallel history for dynamic relabeling
        eval_history: list[EvalRecord] = []

        # ── Phase 1: Bootstrap ────────────────────────────────────────────────
        self.logger.info(
            "=== Phase 1: Bootstrap (%d iterations, fallback anchors) ===", pilot_size
        )
        self._log_disk_usage("bootstrap start")

        for pilot_idx, sobol_config in enumerate(sobol_configs):
            restore_due = (
                self.config.enable_snapshots
                and pilot_idx > 0
                and pilot_idx % self.config.snapshot_restore_interval == 0
            )
            # Symmetric predicate for the next iteration. Same shape as
            # restore_due, shifted by one step. The post-workload VACUUM
            # would otherwise run only to have its on-disk effects wiped
            # by the next snapshot restore.
            next_idx = pilot_idx + 1
            next_eval_will_restore = (
                self.config.enable_snapshots
                and next_idx > 0
                and next_idx % self.config.snapshot_restore_interval == 0
            )

            self.logger.info(
                "Bootstrap %d/%d: starting evaluation...",
                pilot_idx + 1,
                pilot_size,
            )

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
                ) = self._evaluate_with_cotenancy(
                    sobol_config,
                    worker,
                    orchestrator,
                    previous_engine_config,
                    restore_due=restore_due,
                    next_eval_will_restore=next_eval_will_restore,
                    seed=None,
                )
                previous_engine_config = dict(
                    configspace_to_knobs(sobol_config, self.knob_space)
                )

                if metrics is not None:
                    resolved_cs_config = knobs_to_configspace(
                        knob_config,
                        self.knob_space,
                        facade.scenario.configspace,
                    )
                    status = StatusType.SUCCESS
                else:
                    resolved_cs_config = sobol_config
                    status = StatusType.CRASHED
                    cost = 100.0

            except Exception as exc:
                self.logger.error(
                    "Bootstrap iteration %d failed: %s", pilot_idx, exc, exc_info=True
                )
                resolved_cs_config = sobol_config
                metrics = None
                cost = 100.0
                score = 0.0
                score_breakdown = None
                wall_time = 0.0
                restarted = False
                status = StatusType.CRASHED
                eval_timing = TimingRecorder()

            trial_info = TrialInfo(
                config=resolved_cs_config, seed=self.config.random_seed
            )
            t_tell = time.time()
            facade.tell(
                trial_info,
                TrialValue(cost=cost, time=wall_time, status=status),
            )
            tell_overhead = time.time() - t_tell
            self.bo_timing.add(
                "bo_overhead_tell", tell_overhead, phase="bootstrap"
            )

            eval_history.append(
                EvalRecord(
                    config=resolved_cs_config,
                    raw_metrics=metrics,
                    trial_info=trial_info,
                    eval_time=wall_time,
                    status=status,
                )
            )

            iteration_score = score if score is not None else 0.0
            iteration_log.append(
                {
                    "iteration": pilot_idx,
                    "config": configspace_to_knobs(resolved_cs_config, self.knob_space),
                    "metrics": metrics.to_dict() if metrics is not None else {},
                    "score": iteration_score,
                    "score_breakdown": score_breakdown,
                    "cost": cost,
                    "bo_overhead_seconds": tell_overhead,
                    "wall_clock_seconds": wall_time,
                    "restarted": restarted,
                    "timestamp": time.time(),
                    "timing": eval_timing.to_dict(include_summary=False),
                    "phase": "bootstrap",
                }
            )

            self.logger.info(
                "Bootstrap %d/%d: status=%s, score=%.2f, wall_time=%.2fs",
                pilot_idx + 1,
                pilot_size,
                status.name,
                iteration_score,
                wall_time,
            )

            # ── Per-iteration metrics table (Bootstrap) ───────────────────────
            if metrics is not None:
                metrics_with_score = metrics.to_dict()
                metrics_with_score["score"] = iteration_score
                log_worker_metrics_table(
                    self.logger,
                    [metrics_with_score],
                    worker_labels=[f"Bootstrap-{pilot_idx + 1}"],
                    title=f"\n🔷 Bootstrap {pilot_idx + 1}/{pilot_size} Metrics 🔷",
                )

        # ── Calibrate normalizer from all successful bootstrap observations ────
        # Wrapped in a single ``bootstrap_calibration`` span so the cost of
        # range-update + scoring-engine reload + history relabel + iteration_log
        # rescore is visible to timing_breakdown analysis. The whole block fires
        # once per session, between the bootstrap and BO phases — we tag it
        # ``phase="bootstrap_calibration"`` to keep it distinct from per-eval
        # work.
        t_calibration = time.monotonic()
        self.logger.info("=== Bootstrap Calibration ===")
        successful_metrics = [
            r.raw_metrics
            for r in eval_history
            if r.status == StatusType.SUCCESS and r.raw_metrics is not None
        ]
        crash_count = sum(1 for r in eval_history if r.status == StatusType.CRASHED)
        if crash_count > 0:
            self.logger.warning(
                "Bootstrap phase had %d/%d CRASHED iteration(s) — "
                "calibration quality is reduced. Check DB/benchmark logs above.",
                crash_count,
                pilot_size,
            )

        if len(successful_metrics) == 0:
            raise RuntimeError(
                "Zero bootstrap evaluations succeeded. Cannot calibrate normalization ranges. "
                "Check database connectivity and benchmark configuration."
            )
        elif len(successful_metrics) < 3:
            self.logger.warning(
                "Only %d successful bootstrap evaluation(s) (minimum 3 recommended). "
                "Continuing with degraded calibration.",
                len(successful_metrics),
            )

        self.metric_config.update_ranges(successful_metrics)
        self.logger.info(
            "Normalizer calibrated from %d bootstrap observations",
            len(successful_metrics),
        )
        self.logger.debug(
            "Calibrated metric ranges: %s",
            getattr(self.metric_config, "ranges", "(not exposed by metric_config)"),
        )
        try:
            orchestrator.reload_scoring_engine()
        except Exception:
            self.logger.error(
                "Failed to rebuild scoring engine after calibration", exc_info=True
            )
            raise

        # Relabel all bootstrap entries with calibrated scores
        n_relabeled = self._relabel_smac_history(
            facade, orchestrator, eval_history, worker
        )
        self.logger.info(
            "Bootstrap relabeling: %d/%d entries updated in SMAC RunHistory",
            n_relabeled,
            len(eval_history),
        )

        # Update iteration_log bootstrap entries to reflect calibrated scores
        engine = orchestrator._get_scoring_engine()
        log_bootstrap = [e for e in iteration_log if e["phase"] == "bootstrap"]
        for log_entry, record in zip(log_bootstrap, eval_history, strict=False):
            if record.status == StatusType.SUCCESS and record.raw_metrics is not None:
                bd = engine.compute_breakdown(
                    record.raw_metrics, worker_logger=worker.logger
                )
                log_entry["score"] = bd.final_score
                log_entry["score_breakdown"] = bd
                log_entry["cost"] = max(0.0, min(100.0, 100.0 - bd.final_score))

        calibration_elapsed = time.monotonic() - t_calibration
        self.bo_timing.add(
            "bootstrap_calibration",
            calibration_elapsed,
            phase="bootstrap_calibration",
            n_observations=len(successful_metrics),
        )

        incumbents = facade.intensifier.get_incumbents()
        assert len(incumbents) > 0, (
            "No incumbent found after bootstrap injection. "
            "Verify that StatusType and Configuration identity are correct."
        )
        self.logger.info(
            "Bootstrap complete: %d incumbent(s), %d observations injected",
            len(incumbents),
            len(eval_history),
        )

        # ── Phase 2: Adaptive BO Loop ─────────────────────────────────────────
        remaining = self.config.n_iterations - pilot_size
        self.logger.info(
            "=== Phase 2: Adaptive BO Loop (%d iterations, early_stopping=%s, patience=%d) ===",
            remaining,
            self.config.early_stopping_enabled,
            self.config.early_stopping_patience,
        )

        early_stopped = False
        stale_counter = 0
        best_score_so_far = max(
            (entry.get("score", 0.0) or 0.0 for entry in iteration_log),
            default=0.0,
        )

        for bo_idx in range(remaining):
            iteration_count = pilot_size + bo_idx

            if iteration_count % 5 == 0:
                self._log_disk_usage(f"iter {iteration_count}")

            restore_due = (
                self.config.enable_snapshots
                and iteration_count > 0
                and iteration_count % self.config.snapshot_restore_interval == 0
            )
            # Symmetric predicate for the next iteration (see pilot loop).
            next_iter = iteration_count + 1
            next_eval_will_restore = (
                self.config.enable_snapshots
                and next_iter > 0
                and next_iter % self.config.snapshot_restore_interval == 0
            )

            try:
                self.logger.debug(
                    "Calling facade.ask() for iteration %d/%d...",
                    iteration_count + 1,
                    self.config.n_iterations,
                )
                t_ask = time.time()
                trial_info = facade.ask()
                ask_overhead = time.time() - t_ask
                self.bo_timing.add(
                    "bo_overhead_ask", ask_overhead, phase="optimize"
                )
                self.logger.debug(
                    "facade.ask() returned in %.3fs (seed=%s)",
                    ask_overhead,
                    trial_info.seed,
                )
            except StopIteration:
                self.logger.warning(
                    "SMAC exhausted its n_trials budget at iteration %d/%d "
                    "(n_trials=%d, budget_multiplier=3x). "
                    "Consider increasing n_iterations or the 3x multiplier in the scenario.",
                    iteration_count + 1,
                    self.config.n_iterations,
                    self.config.n_iterations * 3,
                )
                break

            # Per-iteration BO overhead accumulator. Starts with the ask
            # cost, picks up the drift+repair+tell+relabel costs as we go.
            # This is what gets stored in the iteration_log so each entry
            # has an honest "total BO-attributable overhead I incurred"
            # number — not just facade.ask/facade.tell.
            iteration_bo_overhead = ask_overhead

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
                ) = self._evaluate_with_cotenancy(
                    trial_info.config,
                    worker,
                    orchestrator,
                    previous_engine_config,
                    seed=trial_info.seed,
                    restore_due=restore_due,
                    next_eval_will_restore=next_eval_will_restore,
                )
                previous_engine_config = dict(
                    configspace_to_knobs(trial_info.config, self.knob_space)
                )
            except Exception as exc:
                self.logger.error("Error evaluating config: %s", exc, exc_info=True)
                (
                    cost,
                    knob_config,
                    metrics,
                    score,
                    score_breakdown,
                    restarted,
                    wall_time,
                ) = (100.0, {}, None, 0.0, None, False, 0.0)
                eval_timing = TimingRecorder()

            # Drift check: bracket the comparison so timing_breakdown can
            # see how much per-iteration cost the dedup/repair detection adds.
            t_drift = time.monotonic()
            original_knob_config = configspace_to_knobs(
                trial_info.config, self.knob_space
            )
            from src.scripts.bo_baseline.search_space import get_config_drift

            # Guard: if evaluation crashed with empty knob_config, skip drift/repair
            if not knob_config and metrics is None:
                self.logger.warning(
                    "Iteration %d: evaluation crashed with empty knob_config — "
                    "skipping repaired config injection to avoid corrupting SMAC surrogate",
                    iteration_count + 1,
                )
                configs_differ = False
            else:
                configs_differ = bool(
                    get_config_drift(original_knob_config, knob_config)
                )
            drift_elapsed = time.monotonic() - t_drift
            self.bo_timing.add(
                "bo_drift_check", drift_elapsed, phase="optimize"
            )
            iteration_bo_overhead += drift_elapsed

            repaired_cs_config = None
            if configs_differ:
                t_repair = time.monotonic()
                try:
                    repaired_cs_config = knobs_to_configspace(
                        knob_config, self.knob_space, facade.scenario.configspace
                    )
                except Exception as exc:
                    knob_def_repr = {
                        k: str(self.knob_space.knobs.get(k)) for k in knob_config
                    }
                    self.logger.warning(
                        "Failed to build repaired CS config at iteration %d: %s. "
                        "Knob definitions involved: %s",
                        iteration_count + 1,
                        exc,
                        knob_def_repr,
                        exc_info=True,
                    )
                repair_elapsed = time.monotonic() - t_repair
                self.bo_timing.add(
                    "bo_repair_inject", repair_elapsed, phase="optimize"
                )
                iteration_bo_overhead += repair_elapsed

            bo_status = StatusType.SUCCESS
            effective_config = (
                repaired_cs_config
                if repaired_cs_config is not None
                else trial_info.config
            )
            effective_trial_info = TrialInfo(
                config=effective_config, seed=trial_info.seed
            )

            t_tell = time.time()
            if repaired_cs_config is not None:
                facade.tell(
                    effective_trial_info,
                    TrialValue(cost=cost, time=wall_time, status=bo_status),
                )
            facade.tell(
                trial_info,
                TrialValue(cost=cost, time=wall_time, status=bo_status),
            )
            tell_overhead = time.time() - t_tell
            self.bo_timing.add(
                "bo_overhead_tell", tell_overhead, phase="optimize"
            )
            iteration_bo_overhead += tell_overhead

            # Record in parallel history for potential future relabeling
            eval_history.append(
                EvalRecord(
                    config=effective_config,
                    raw_metrics=metrics,
                    trial_info=effective_trial_info,
                    eval_time=wall_time,
                    status=bo_status if metrics is not None else StatusType.CRASHED,
                )
            )

            # ── Dynamic Range Expansion & Relabeling ─────────────────────────
            # Wrapped in a single ``bo_relabel`` span: covers the conditional
            # reload + SMAC RunHistory relabel + iteration_log retroactive
            # update. These always co-occur; one span keeps the
            # timing_summary readable.
            if metrics is not None:
                ranges_expanded = self.metric_config.expand_ranges_for_metrics(
                    [metrics]
                )
                if ranges_expanded:
                    t_relabel = time.monotonic()
                    orchestrator.reload_scoring_engine()
                    n_relabeled = self._relabel_smac_history(
                        facade, orchestrator, eval_history, worker
                    )
                    # Recompute current iteration cost/score with updated engine
                    new_engine = orchestrator._get_scoring_engine()
                    new_bd = new_engine.compute_breakdown(
                        metrics, worker_logger=worker.logger
                    )
                    cost = max(0.0, min(100.0, 100.0 - new_bd.final_score))
                    score = new_bd.final_score
                    score_breakdown = new_bd
                    self.logger.info(
                        "🔄 Normalization ranges expanded — %d/%d history entries relabeled "
                        "(scores recalibrated on updated bounds)",
                        n_relabeled,
                        len(eval_history),
                    )

                    # ── Retroactively update iteration_log entries ────────────
                    # SMAC RunHistory was updated by _relabel_smac_history() above.
                    # Mirror the same fix to iteration_log so the result JSON
                    # reflects the rescaled scores (mirrors bootstrap pattern on
                    # lines 500-508).  eval_history and iteration_log are kept
                    # in lock-step: eval_history[i] corresponds to
                    # iteration_log[i] for every successfully recorded entry.
                    log_relabeled = 0
                    for log_entry, record in zip(
                        iteration_log, eval_history, strict=False
                    ):
                        if (
                            record.status == StatusType.SUCCESS
                            and record.raw_metrics is not None
                        ):
                            bd = new_engine.compute_breakdown(
                                record.raw_metrics, worker_logger=worker.logger
                            )
                            log_entry["score"] = bd.final_score
                            log_entry["score_breakdown"] = bd
                            log_entry["cost"] = max(
                                0.0, min(100.0, 100.0 - bd.final_score)
                            )
                            log_relabeled += 1
                    self.logger.debug(
                        "iteration_log retroactive update: %d/%d entries rescored",
                        log_relabeled,
                        len(iteration_log),
                    )

                    # Recompute best_score_so_far from the updated log so that
                    # early stopping comparisons use the rescaled landscape.
                    best_score_so_far = max(
                        (e.get("score", 0.0) or 0.0 for e in iteration_log),
                        default=0.0,
                    )
                    self.logger.debug(
                        "best_score_so_far recalculated after relabeling: %.4f",
                        best_score_so_far,
                    )
                    relabel_elapsed = time.monotonic() - t_relabel
                    self.bo_timing.add(
                        "bo_relabel",
                        relabel_elapsed,
                        phase="optimize",
                        n_relabeled=n_relabeled,
                    )
                    iteration_bo_overhead += relabel_elapsed

            iteration_score = score if score is not None else 0.0
            iteration_log.append(
                {
                    "iteration": iteration_count,
                    "config": knob_config,
                    "metrics": metrics.to_dict() if metrics is not None else {},
                    "score": iteration_score,
                    "score_breakdown": score_breakdown,
                    "cost": cost,
                    "bo_overhead_seconds": iteration_bo_overhead,
                    "wall_clock_seconds": wall_time,
                    "restarted": restarted,
                    "timestamp": time.time(),
                    "timing": eval_timing.to_dict(include_summary=False),
                    "phase": "bo",
                }
            )

            self.logger.info(
                "Iteration %d/%d [BO]: score=%.2f, cost=%.2f, wall_time=%.2fs",
                iteration_count + 1,
                self.config.n_iterations,
                iteration_score,
                cost,
                wall_time,
            )

            # ── Per-iteration metrics table (BO) ──────────────────────────────
            if metrics is not None:
                metrics_with_score = metrics.to_dict()
                metrics_with_score["score"] = iteration_score
                log_worker_metrics_table(
                    self.logger,
                    [metrics_with_score],
                    worker_labels=[f"Iter-{iteration_count + 1}"],
                    title=f"\n🔷 BO Iteration {iteration_count + 1}/{self.config.n_iterations} Metrics 🔷",
                )

            # ── Early Stopping Check + Best/Stale Status ──────────────────────
            is_new_best = iteration_score > best_score_so_far
            if is_new_best:
                best_score_so_far = iteration_score
                stale_counter = 0
                self.logger.info(
                    "✅ New best score: %.4f  (iteration %d/%d)",
                    best_score_so_far,
                    iteration_count + 1,
                    self.config.n_iterations,
                )
            else:
                stale_counter += 1 if self.config.early_stopping_enabled else 0
                self.logger.info(
                    "⏸  No improvement — best stays %.4f  (stale=%d/%s)",
                    best_score_so_far,
                    stale_counter,
                    self.config.early_stopping_patience
                    if self.config.early_stopping_enabled
                    else "∞",
                )

            if (
                self.config.early_stopping_enabled
                and stale_counter >= self.config.early_stopping_patience
            ):
                self.logger.warning(
                    "Early stopping triggered: no improvement for %d consecutive "
                    "iterations (patience=%d). Best score=%.4f.",
                    stale_counter,
                    self.config.early_stopping_patience,
                    best_score_so_far,
                )
                early_stopped = True
                break

        return early_stopped, stale_counter

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
            knob_source=self.config.knob_source,
        )
        smac_root = bo_root / "smac_output"
        smac_root.mkdir(parents=True, exist_ok=True)
        return smac_root

    def _generate_pilot_configs(
        self,
        configspace: ConfigurationSpace,
        pilot_size: int,
    ) -> list[Configuration]:
        """Sample exactly ``pilot_size`` unique, constraint-valid configurations.

        ConfigSpace's ``NotEqualsCondition`` and ``Forbidden*`` clauses
        (declared in :func:`KnobSpace.configspace_constraints`) reject most
        Sobol points in high-dimensional spaces; SMAC's internal dedup then
        collapses survivors further. With the 179-knob extensive tier, a
        single ``SobolInitialDesign(n_configs=pilot_size * 5)`` pass has
        been observed to return as few as 7 configs when 10 were requested.
        That silently truncates the user's iteration budget and creates
        gaps in the iteration log.

        Strategy:
            1. Sobol pass with ``n_configs = pilot_size * 5``.
            2. If short, up to 3 more Sobol passes, each doubling the
               request and using a derived seed, deduped against everything
               already accepted via a canonical dict key.
            3. If still short, fall back to
               ``ConfigurationSpace.sample_configuration(size=remaining)``
               which loops internally until it has the requested number of
               valid configurations
               (see :file:`ConfigSpace/configuration_space.py:531-623`).

        The fallback path emits an INFO-level log so it's visible whenever
        the search space's constraint density forces it.
        """
        if pilot_size <= 0:
            return []

        accepted: list[Configuration] = []
        seen: set[str] = set()

        def _key(cfg: Configuration) -> str:
            # Canonical JSON encoding of the resolved dict makes a stable
            # hashable identity that survives ConfigSpace value reordering.
            return json.dumps(dict(cfg), sort_keys=True, default=str)

        max_sobol_passes = 4
        for attempt in range(max_sobol_passes):
            if len(accepted) >= pilot_size:
                break
            n_request = pilot_size * 5 * (2**attempt)
            pass_seed = self.config.random_seed + attempt
            scenario = Scenario(
                configspace=configspace,
                n_trials=n_request,
                seed=pass_seed,
                n_workers=1,
                output_directory=(
                    self._build_smac_output_root()
                    / f"_sobol_gen_attempt_{attempt}"
                ),
            )
            design = SobolInitialDesign(
                scenario=scenario,
                n_configs=n_request,
                max_ratio=1.0,
            )
            for cfg in design.select_configurations():
                k = _key(cfg)
                if k in seen:
                    continue
                seen.add(k)
                accepted.append(cfg)
                if len(accepted) >= pilot_size:
                    break

        if len(accepted) < pilot_size:
            shortfall = pilot_size - len(accepted)
            self.logger.info(
                "Sobol exhausted after %d passes with %d/%d unique-valid "
                "configs; falling back to ConfigurationSpace.sample_configuration() "
                "for the remaining %d. The search space is constraint-dense.",
                max_sobol_passes,
                len(accepted),
                pilot_size,
                shortfall,
            )
            extras = configspace.sample_configuration(size=shortfall)
            if isinstance(extras, Configuration):
                # ConfigSpace returns a single Configuration when size=1.
                extras = [extras]
            for cfg in extras:
                k = _key(cfg)
                if k in seen:
                    # Rare in a 179-D space, but defend against it: keep
                    # sampling one-at-a-time until we have enough.
                    continue
                seen.add(k)
                accepted.append(cfg)
            # If sampling collided enough to still leave a deficit, top up
            # one configuration at a time (sample_configuration's internal
            # rejection sampling guarantees a valid Configuration each call).
            while len(accepted) < pilot_size:
                cfg = configspace.sample_configuration()
                k = _key(cfg)
                if k in seen:
                    continue
                seen.add(k)
                accepted.append(cfg)

        return accepted[:pilot_size]

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
            knob_source=self.config.knob_source,
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
            self.logger.debug(
                "Workload executor initialized: %s", type(workload_executor).__name__
            )

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
            self.logger.debug(
                "Database environment created: %s", type(self.env).__name__
            )

            # Foreground BO trial always runs on worker 0. When co-tenancy is
            # enabled (degree > 1, matched to the PBT session's parallel-worker
            # count) we additionally bring up ``degree - 1`` background load
            # instances (worker ids 1..degree-1) on their own disjoint cpusets,
            # so each BO measurement window experiences the same single-host
            # contention a PBT generation does. Worker 0 keeps its full
            # per-worker slice either way (not devalued).
            degree = max(1, int(getattr(self.config, "cotenancy_degree", 1)))
            num_instances = degree
            if degree > 1:
                self.logger.info(
                    "Setting up %d PostgreSQL instances (1 foreground BO + %d "
                    "co-tenant load) under matched co-tenancy degree %d...",
                    num_instances,
                    degree - 1,
                    degree,
                )
            else:
                self.logger.info("Setting up 1 PostgreSQL instance...")
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
            self.logger.debug("Orchestrator configuration: %s", orchestrator_config)

            # Single (foreground) worker
            worker = BaseWorker(worker_id=0, knob_space=self.knob_space)
            worker.db_config = self.env.get_db_config(0)

            # Co-tenant load controller: drives the background load instances
            # in lockstep with worker 0's measurement window. A no-op when
            # degree <= 1. Stored on self so the sequential-optimization loop
            # and the finally-cleanup can reach it.
            self.cotenant = CoTenantLoadController(
                degree=degree,
                env=self.env,
                orchestrator=orchestrator,
                knob_space=self.knob_space,
                base_db_config=self.db_config,
                seed=self.config.random_seed,
            )

            # Build ConfigSpace
            self.logger.info("Building ConfigSpace...")
            with self.bootstrap_timing.span("configspace_build"):
                configspace = build_configspace(
                    self.knob_space, seed=self.config.random_seed
                )
            self.logger.debug(
                "ConfigSpace initialized with %d dimensions",
                len(configspace.get_hyperparameters()),
            )

            # Create iteration log
            iteration_log: list = []

            # Pilot phase size
            requested_pilot_size = min(
                self.config.range_update_interval, self.config.n_iterations
            )
            pilot_size = requested_pilot_size

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
            with self.bootstrap_timing.span(
                "pilot_generation", requested=pilot_size
            ):
                sobol_configs = self._generate_pilot_configs(
                    configspace, pilot_size
                )

            # Inject the default configuration as the first pilot observation.
            # Wrapped in a bootstrap span so the cost of querying live DB defaults
            # is visible to timing_breakdown analysis.
            with self.bootstrap_timing.span("default_config_seed"):
                base_default_config = configspace.get_default_configuration()
                base_knobs = configspace_to_knobs(base_default_config, self.knob_space)

                # Fetch real defaults from the active DB using Applicator
                from src.utils.applicator import KnobApplicator
                from src.scripts.bo_baseline.search_space import get_config_drift

                applicator = KnobApplicator(
                    db_config=self.env.get_db_config(0), worker_id=0
                )
                try:
                    verify_result = applicator.verify(expected_config=base_knobs)
                except Exception as exc:
                    self.logger.warning(
                        "KnobApplicator.verify() failed (%s); "
                        "using static ConfigSpace defaults as pilot seed",
                        exc,
                    )
                    verify_result = type("_FakeVerify", (), {"db_config": {}})()  # type: ignore[assignment]

                # Log drift between static ConfigSpace defaults and live DB values
                default_drift = get_config_drift(base_knobs, verify_result.db_config)
                if default_drift:
                    drift_preview = dict(list(default_drift.items())[:10])
                    self.logger.info(
                        "Live DB defaults differ from ConfigSpace defaults in %d knob(s): %s%s",
                        len(default_drift),
                        drift_preview,
                        " ..." if len(default_drift) > 10 else "",
                    )
                else:
                    self.logger.debug(
                        "Live DB defaults match ConfigSpace defaults exactly."
                    )

                # Update base_knobs with the true active database values
                base_knobs.update(verify_result.db_config)

                try:
                    real_default_config = knobs_to_configspace(
                        base_knobs,
                        self.knob_space,
                        configspace,
                    )
                except Exception as e:
                    self.logger.warning(
                        "Could not build real default config, falling back to static defaults: %s",
                        e,
                    )
                    real_default_config = base_default_config

                # Prepend default, remove any exact duplicates, and slice to requested pilot size
                sobol_configs = [real_default_config] + [
                    c for c in sobol_configs if c != real_default_config
                ]
                sobol_configs = sobol_configs[:pilot_size]

            # Rebind ``pilot_size`` to the actually-generated count so the
            # Phase 2 ``remaining`` calculation honors the user's full
            # iteration budget. With the backfill sampler above this should
            # equal ``requested_pilot_size``; the rebind is defensive against
            # future regressions.
            actual_pilot_size = len(sobol_configs)
            pilot_size = actual_pilot_size

            self.logger.info(
                "Generated %d pilot configs (including real DB default configuration)",
                len(sobol_configs),
            )

            # Since we use ask-tell loops, provide a dummy objective to satisfy
            # the Facade constructor (it should never be called directly).
            def objective(config, seed=0):
                raise NotImplementedError(
                    "Objective should not be called directly in ask-tell mode"
                )

            # Create SMAC scenario (generous budget; n_trials covers pilot + BO + multi-seed validation)
            self.logger.info(
                "Creating SMAC scenario with generous budget for %d iterations...",
                self.config.n_iterations,
            )
            with self.bootstrap_timing.span("smac_scenario_build"):
                scenario = Scenario(
                    configspace=configspace,
                    n_trials=self.config.n_iterations * 3,
                    seed=self.config.random_seed,
                    deterministic=False,
                    n_workers=1,
                    output_directory=(
                        self._build_smac_output_root()
                        / f"run_{self.run_timestamp}_{self.config.random_seed}"
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

            # ── Pre-run session summary ───────────────────────────────────────
            self.logger.info(
                "=== BO Session Summary ===\n"
                "  Tier:              %s (%d knobs)\n"
                "  Surrogate:         %s\n"
                "  Total iterations:  %d  (pilot=%d, BO=%d)\n"
                "  Early stopping:    %s  (patience=%d)\n"
                "  Snapshots:         %s  (interval=%d)\n"
                "  Tuning mode:       %s\n"
                "  Benchmark:         %s / %s\n"
                "  Warmup / Eval:     %.0fs / %.0fs\n"
                "  Resource division: %d\n"
                "  Seed:              %d",
                self.config.knob_tier,
                len(self.knob_space.knobs),
                bo_surrogate,
                self.config.n_iterations,
                pilot_size,
                self.config.n_iterations - pilot_size,
                self.config.early_stopping_enabled,
                self.config.early_stopping_patience,
                self.config.enable_snapshots,
                self.config.snapshot_restore_interval,
                self.config.benchmark_config.tuning_mode,
                self.config.benchmark_config.benchmark,
                (
                    self.config.benchmark_config.sysbench_workload
                    or self.config.benchmark_config.workload_type
                ),
                self.config.benchmark_config.warmup_duration,
                self.config.benchmark_config.evaluation_duration,
                self.config.resource_division,
                self.config.random_seed,
            )

            # Run optimization
            self.logger.info("Starting Bayesian Optimization...")
            early_stopped = False
            stale_counter = 0
            # Bootstrap is done; capture the post-bootstrap clock so
            # ``tuning_time_seconds`` excludes setup overhead.
            self.tuning_start_time = time.time()
            try:
                early_stopped, stale_counter = self._run_sequential_optimization(
                    facade,
                    orchestrator,
                    worker,
                    iteration_log,
                    pilot_size,
                    sobol_configs,
                )
            except KeyboardInterrupt:
                self.logger.warning("Optimization interrupted by user")

            # Write results
            self.logger.info("Writing results...")
            tuning_time = time.time() - self.tuning_start_time
            total_time = time.time() - start_time

            # ``population_size`` is borrowed from PBT semantics where it
            # equals the parallel worker count. BO is strictly sequential
            # with a single worker, so the only honest value is 1. Using
            # ``config.n_iterations`` here (the prior behavior) conflated
            # iterations with workers and broke any consumer that treats
            # population_size as parallelism.
            session_environment = build_session_environment(
                env=self.env,
                num_parallel_workers=self.config.max_workers,
                population_size=1,
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
                early_stopped=early_stopped,
                stale_counter=stale_counter,
                session_environment=session_environment,
                tuning_time_seconds=tuning_time,
                bootstrap_timing=self.bootstrap_timing,
                bo_timing=self.bo_timing,
                run_timestamp=self.run_timestamp,
                requested_iterations=self.config.n_iterations,
                requested_pilot_size=requested_pilot_size,
                actual_pilot_size=actual_pilot_size,
                cotenancy=(
                    self.cotenant.to_metadata() if self.cotenant is not None else None
                ),
            )

            self.logger.info("BO tuning completed in %.2f seconds", total_time)
            self.logger.info("Best score: %.4f", results["best_configuration"]["score"])

            return results

        finally:
            # Cleanup
            _cotenant = getattr(self, "cotenant", None)
            if _cotenant is not None:
                try:
                    _cotenant.shutdown()
                except Exception as e:  # noqa: BLE001
                    self.logger.warning("Error shutting down co-tenant pool: %s", e)
            if hasattr(self, "env"):
                self.logger.info("Cleaning up environment...")
                try:
                    self.env.cleanup()
                except Exception as e:
                    self.logger.warning("Error during cleanup: %s", e)


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
        "--worker-disk-read-bps",
        type=int,
        default=None,
        help=(
            "Per-worker disk read bandwidth in bytes/sec (cgroup blkio / io.max). "
            "When unset, auto-detected via fio probe (when available) or heuristic."
        ),
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
        help="Scoring policy to use (default: feature_driven_v2 via per-workload config)",
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
    parser.add_argument(
        "--early-stopping-patience",
        type=int,
        default=None,
        help=(
            "Number of consecutive non-improving BO iterations before stopping early. "
            "Defaults to ~20%% of n_iterations per preset "
            "(Rapid=8, Standard=20, Thorough=60, Research=200, Extreme=400). "
            "Auto-scaled when --pbt-session sets the budget."
        ),
    )
    parser.add_argument(
        "--disable-early-stopping",
        action="store_true",
        help="Disable early stopping and always run all BO iterations.",
    )

    args = parser.parse_args()

    # Create config and run
    config = BOConfig.from_args(args)
    runner = BOBaselineRunner(config)
    results = runner.run()

    return results


if __name__ == "__main__":
    main()
