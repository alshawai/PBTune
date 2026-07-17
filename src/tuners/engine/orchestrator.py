"""
Workload Orchestrator for Database Tuning
==========================================

The WorkloadOrchestrator class orchestrates workload execution and collects
performance metrics. It serves as the bridge between any tuning strategy
(PBT, BO, LHS) and the actual PostgreSQL database.

Key Responsibilities:
- Execute workload benchmarks (SYSBENCH, TPC-H, custom queries)
- Apply knob configurations to PostgreSQL
- Collect performance metrics (latency, throughput, resource utilization)
- Compute composite performance scores
- Handle workload-specific behavior (OLTP vs OLAP)

Architecture:
------------
    TuningStrategy -> WorkloadOrchestrator -> PostgreSQL
                              ↓
                          Metrics
                              ↓
                      Performance Score

Design Patterns:
- Strategy Pattern: Different workload executors (SYSBENCH, TPC-H)
- Template Method: Common evaluation flow with workload-specific steps
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Dict, Any, Optional, List
import logging
import threading
import numpy as np
from psycopg2.extensions import connection as PostgresConnection, register_adapter, AsIs

from src.database.connection import connect_with_retry, safe_disconnect
from src.config.database import DatabaseConfig
from src.utils.environments.base import DatabaseEnvironment
from src.utils.metrics import (
    PerformanceMetrics,
    WorkloadType,
    MetricConfig,
)
from src.utils.metric_instrumentation import MetricInstrumentationEngine
from src.utils.scoring import create_scoring_engine
from src.benchmarks.executor import BenchmarkExecutor, ExecutionContext
from src.tuners.engine.worker import BaseWorker
from src.utils.types import TuningMode
from src.tuners.engine.barriers import GenerationBarrier
from src.tuners.engine.reliability_gate import apply_reliability_gate
from src.tuners.engine.worker_metrics import (
    collect_system_metrics as _collect_system_metrics,
    fetch_pg_stat_database_snapshot as _fetch_pg_stat_snapshot,
    compute_io_metrics as _compute_io_metrics_impl,
)
from src.tuners.engine.maintenance import (
    vacuum_after_dml as _vacuum_after_dml_impl,
    ensure_benchmark_ready as _ensure_benchmark_ready_impl,
)
from src.tuners.engine.activation import (
    apply_configuration as _apply_configuration_impl,
    perform_restart as _perform_restart_impl,
)
from src.tuners.engine.feature_refinement import WorkloadFeatureRefiner
from src.utils.applicator import KnobApplicator, ApplicatorConfig
from src.utils.logger import get_logger, get_color_context
from src.utils.timing import TimingRecorder

LOGGER = get_logger("WorkloadOrchestrator")
COLORS = get_color_context()

# Register numpy type adapters for psycopg2
register_adapter(np.int64, lambda x: AsIs(int(x)))
register_adapter(np.int32, lambda x: AsIs(int(x)))
register_adapter(np.float64, lambda x: AsIs(float(x)))
register_adapter(np.float32, lambda x: AsIs(float(x)))


@dataclass
class WorkloadOrchestratorConfig:
    """
    Configuration for WorkloadOrchestrator behavior.

    Parameters
    ----------
    workload_type : WorkloadType
        Type of workload (OLTP, OLAP, MIXED)
    metric_config : MetricConfig
        Metric weights and scoring configuration
    db_config : DatabaseConfig
        PostgreSQL database configuration
    warmup_duration : float
        Duration of warmup phase in seconds before measurement (default: 30.0)
    measurement_duration : float
        Duration of measurement phase in seconds (default: 60.0)
    cooldown_duration : float
        Duration to wait after config change before evaluation (default: 5.0)
    random_seed : Optional[int]
        Optional random seed for reproducibility (default: None)
    vacuum_analyze_timeout_seconds : float
        Per-worker timeout for post-workload VACUUM ANALYZE safety maintenance.
        Prevents generation stalls when maintenance blocks or runs too long.
    worker_memory_budget_bytes : Optional[int]
        Per-worker RAM budget used to normalize PostgreSQL RSS into
        memory_utilization [0, 1]. If None/invalid, falls back to host RAM.
    """

    workload_type: WorkloadType
    metric_config: MetricConfig
    db_config: DatabaseConfig
    warmup_duration: float = 30.0
    measurement_duration: float = 60.0
    cooldown_duration: float = 5.0
    tuning_mode: TuningMode = TuningMode.ONLINE
    adaptive_restart_interval: int = 10
    random_seed: Optional[int] = None
    warmup_passes: int = 0
    vacuum_analyze_timeout_seconds: float = 45.0
    worker_memory_budget_bytes: Optional[int] = None


class WorkloadOrchestrator:
    """
    Main WorkloadOrchestrator class for workload execution and performance measurement.

    The WorkloadOrchestrator orchestrates the evaluation process:
    1. Apply knob configuration to PostgreSQL
    2. Wait for cooldown period
    3. Execute workload (with warmup)
    4. Collect metrics
    5. Compute performance score

    Attributes
    ----------
    config : WorkloadOrchestratorConfig
        Configuration parameters
    workload_executor : BenchmarkExecutor
        Workload-specific execution logic
    connection : Optional[PostgresConnection]
        Active database connection

    Example
    -------
    >>> from src.utils.metrics import WorkloadType, MetricConfig
    >>> from src.config.database import DatabaseConfig
    >>>
    >>> config = WorkloadOrchestratorConfig(
    ...     workload_type=WorkloadType.OLTP,
    ...     metric_config=MetricConfig.for_oltp(),
    ...     db_config=DatabaseConfig(
    ...         host='localhost',
    ...         port=5432,
    ...         dbname='testdb',
    ...         user='postgres',
    ...         password='password'
    ...     )
    ... )
    >>>
    >>> executor = SysbenchOLTPExecutor(table_size=10000)
    >>> orchestrator = WorkloadOrchestrator(config, executor)
    >>>
    >>> # Evaluate a worker
    >>> metrics, score, _, _, timing = orchestrator.evaluate_worker(worker)
    >>> print(f"Score: {score:.4f}, Throughput: {metrics.throughput:.2f} TPS")
    """

    def __init__(
        self,
        config: WorkloadOrchestratorConfig,
        workload_executor: BenchmarkExecutor,
        env: DatabaseEnvironment,
    ):
        """
        Initialize WorkloadOrchestrator.

        Parameters
        ----------
        config : WorkloadOrchestratorConfig
            Orchestration configuration
        workload_executor : BenchmarkExecutor
            Workload execution strategy
        env : DatabaseEnvironment
            Database environment for instance management
        """
        self.config = config
        self.workload_executor = workload_executor
        self.env = env
        self._scoring_engine = None
        self._scoring_engine_lock = threading.Lock()
        self._feature_refiner = WorkloadFeatureRefiner(
            config.metric_config,
            scorer_provider=lambda: self.scorer,
        )

        LOGGER.info(
            "➤ Created WorkloadOrchestrator: workload=%s, mode=%s, duration=%ss",
            config.workload_type.value.upper(),
            config.tuning_mode.value.capitalize(),
            config.measurement_duration,
        )

    def _get_scoring_engine(self):
        if self._scoring_engine is not None:
            return self._scoring_engine

        with self._scoring_engine_lock:
            if self._scoring_engine is None:
                self._scoring_engine = create_scoring_engine(self.config.metric_config)

        return self._scoring_engine

    def reload_scoring_engine(self) -> None:
        """
        Invalidate the cached scoring engine and rebuild it.
        This is typically called after the metric configuration ranges
        have been recalibrated (e.g. after a pilot phase).
        """
        with self._scoring_engine_lock:
            self._scoring_engine = None
        self._get_scoring_engine()
        LOGGER.info("Rebuilt scoring engine with calibrated normalizer")

    @property
    def scorer(self):
        return self._get_scoring_engine()

    def connect(
        self,
        db_config: Optional[DatabaseConfig] = None,
        max_retries: int = 1,
        retry_delay: float = 2.0,
    ) -> PostgresConnection:
        """Establish connection to PostgreSQL with retry logic.

        Delegates to :func:`src.database.connection.connect_with_retry`.
        """
        return connect_with_retry(
            db_config or self.config.db_config,
            max_retries=max_retries,
            retry_delay=retry_delay,
        )

    def disconnect(
        self, connection: Optional[PostgresConnection], worker_id: Optional[int] = None
    ) -> None:
        """Close PostgreSQL connection safely.

        Delegates to :func:`src.database.connection.safe_disconnect`.
        """
        worker_logger = (
            get_logger("BenchmarkWorker", worker_id=worker_id)
            if worker_id is not None
            else LOGGER
        )
        safe_disconnect(connection, worker_id=worker_id, logger=worker_logger)

    def apply_configuration(
        self,
        connection: PostgresConnection,
        worker: BaseWorker,
        knob_applicator: KnobApplicator,
        force_restart: bool = False,
        generation: Optional[int] = None,
        restore_due: bool = False,
        recorder: Optional[TimingRecorder] = None,
    ) -> bool:
        """Apply knob configuration and optionally restart; see :func:`apply_configuration`."""
        return _apply_configuration_impl(
            self.config,
            self.env,
            connection,
            worker,
            knob_applicator,
            force_restart=force_restart,
            generation=generation,
            restore_due=restore_due,
            recorder=recorder,
            restart_fn=lambda conn, wkr: self._perform_restart(conn, worker=wkr),
        )

    def _perform_restart(
        self,
        connection: PostgresConnection,
        worker: BaseWorker,
    ) -> bool:
        """Restart PostgreSQL via the injected environment; see :func:`perform_restart`."""
        return _perform_restart_impl(self.env, connection, worker=worker)

    def collect_system_metrics(
        self,
        worker_id: Optional[int] = None,
    ) -> Dict[str, float]:
        """Collect system-level metrics via the environment; see :func:`collect_system_metrics`."""
        return _collect_system_metrics(self.env, worker_id)

    def _fetch_pg_stat_database_snapshot(
        self,
        db_config: DatabaseConfig,
        *,
        connection: Any | None = None,
        worker_logger: Optional[logging.Logger] = None,
    ) -> tuple[int, int, int, int, int, int, int] | None:
        """Read pg_stat_database counters with a retry for transient failures."""
        return _fetch_pg_stat_snapshot(
            db_config,
            connect=self.connect,
            disconnect=self.disconnect,
            connection=connection,
            worker_logger=worker_logger,
        )

    def _verify_and_capture_config(
        self,
        knob_applicator: KnobApplicator,
        worker: BaseWorker,
        recorder: TimingRecorder,
    ) -> Dict[str, Any]:
        """Verify applied knobs and capture the true active DB config (B5).

        Reads back the values PostgreSQL actually accepted so downstream
        consumers (scoring, result writers) see the real quantized settings
        rather than the requested ones.

        Returns
        -------
        Dict[str, Any]
            The actual DB config read back from ``pg_settings`` (empty when
            verification produced no db_config).
        """
        worker.logger.debug(" Verifying knob configuration...")
        with recorder.span("knob_verify"):
            verification = knob_applicator.verify(worker.knob_config)  # type: ignore
        if verification.failed_params:
            worker.logger.warning(
                " ➤ Configuration verification failed for %d parameters: %s",
                len(verification.failed_params),
                verification.failed_params,
            )
        else:
            worker.logger.debug(" ➤ All parameters verified.")

        # Save the true applied DB config back to the worker so downstream
        # consumers (scoring, result writers) see the actual quantized values
        # PostgreSQL is using.
        if verification.db_config:
            worker.knob_config.update(verification.db_config)  # type: ignore
            worker.logger.debug(
                " ➤ Updated worker.knob_config with %d actual DB values",
                len(verification.db_config),
            )
            return verification.db_config

        return {}

    def _run_workload(
        self,
        worker: BaseWorker,
        *,
        connection: Optional[PostgresConnection],
        effective_seed: Optional[int],
        recorder: TimingRecorder,
        pre_measurement_callback: Optional[Any] = None,
    ) -> PerformanceMetrics:
        """Build the ExecutionContext and run warmup + measurement (B8/B9).

        Chooses the context shape from the executor's connection model:
        externally-managed benchmarks (sysbench, tpch) run warmup internally
        and take a warmup-pass count; the internal executor reuses the caller's
        connection and fires ``pre_measurement_callback`` between warmup and
        measurement.

        Barrier waits stay in the caller so the lockstep drain invariant is
        untouched — this helper only builds the context and executes.
        """
        if worker.db_config is None:
            raise ValueError(
                f"Worker {worker.worker_id} has no db_config; instance must be "
                "brought up before running a workload"
            )

        if self.workload_executor.manages_own_connection:
            ctx = ExecutionContext(
                db_config=worker.db_config,
                duration=self.config.measurement_duration,
                warmup=self.config.warmup_duration,
                worker_id=worker.worker_id,
                random_seed=effective_seed,
                warmup_passes=self.config.warmup_passes,
            )
            with recorder.span("workload", executor="benchmark"):
                return self.workload_executor.execute(ctx)

        ctx = ExecutionContext(
            db_config=worker.db_config,
            duration=self.config.measurement_duration,
            warmup=self.config.warmup_duration,
            worker_id=worker.worker_id,
            random_seed=effective_seed,
            connection=connection,
            pre_measurement_callback=pre_measurement_callback,
        )
        with recorder.span("workload", executor="internal"):
            return self.workload_executor.execute(ctx)

    def _compute_io_metrics(
        self,
        metrics: PerformanceMetrics,
        *,
        stats_before: tuple[int, int, int, int, int, int, int],
        stats_after: tuple[int, int, int, int, int, int, int],
        worker_logger: logging.Logger,
    ) -> None:
        """Populate I/O and row-count metrics from pg_stat_database deltas (B11); see :func:`compute_io_metrics`."""
        _compute_io_metrics_impl(
            metrics,
            stats_before=stats_before,
            stats_after=stats_after,
            worker_logger=worker_logger,
        )

    def _vacuum_after_dml(
        self,
        db_config: DatabaseConfig,
        worker_logger: Optional[logging.Logger] = None,
        next_eval_will_restore: bool = False,
    ) -> None:
        """Run bounded post-workload maintenance after DML-heavy workloads; see :func:`vacuum_after_dml`."""
        _vacuum_after_dml_impl(
            self.config.workload_type,
            self.config.vacuum_analyze_timeout_seconds,
            db_config,
            worker_logger=worker_logger,
            next_eval_will_restore=next_eval_will_restore,
        )

    def _ensure_benchmark_ready(
        self,
        db_config: DatabaseConfig,
        worker_logger: Optional[logging.Logger] = None,
    ) -> None:
        """Validate benchmark state and repair it if needed; see :func:`ensure_benchmark_ready`."""
        _ensure_benchmark_ready_impl(
            self.workload_executor,
            db_config,
            worker_logger=worker_logger,
        )

    def evaluate_worker(
        self,
        worker: BaseWorker,
        apply_config: bool = True,
        generation: Optional[int] = None,
        barriers: Optional[GenerationBarrier] = None,
        random_seed: Optional[int] = None,
        restore_due: bool = False,
        next_eval_will_restore: bool = False,
    ) -> tuple[PerformanceMetrics, float, bool, Dict[str, Any], TimingRecorder]:
        """
        Evaluate a Worker's configuration.

        This is the main evaluation method called by Population.train_generation().

        Process:
        1. Apply worker's knob configuration (if apply_config=True)
        2. If restore_due: snapshot restore (which IS the restart)
        3. Otherwise: activate (reload or restart per policy)
        4. Execute workload with warmup and measurement phases
        5. Collect performance metrics
        6. Collect system metrics
        7. Compute composite performance score

        All sub-steps are gated by optional ``GenerationBarrier`` synchronization
        points (B1–B17) so that workers advance in lockstep when barriers are
        enabled.

        Parameters
        ----------
        worker : BaseWorker
            Worker instance to evaluate
        apply_config : bool, default=True
            Whether to apply the worker's configuration
        generation : Optional[int]
            Current generation number (for restart cost calculation)
        barriers : GenerationBarrier | None
            Optional lockstep barriers.  When provided and enabled, this
            method will ``wait()`` at each barrier so all workers stay
            in phase.
        restore_due : bool, default=False
            When True, perform snapshot restore after apply_only instead of
            the normal activate step. The snapshot restore serves as the
            restart (instance stops, PGDATA restored preserving auto.conf,
            instance starts with new knobs).
        next_eval_will_restore : bool, default=False
            When True, the *next* eval on this worker is guaranteed to begin
            with a baseline snapshot restore. Skips the post-workload VACUUM
            ANALYZE because its on-disk effects would be wiped by the next
            restore (and it cannot influence the metrics we just collected).
            For PBT with ``snapshot_restore_interval=1`` this is always True
            after generation 0; for BO with the same interval it is always
            True after the first iteration.

        Returns
        -------
        tuple[PerformanceMetrics, float, bool, Dict[str, Any], TimingRecorder]
            (metrics, score, restart_occurred, actual_db_config, timing) tuple.
            ``actual_db_config`` contains the true values currently active
            in PostgreSQL after apply + optional restart, as read back
            from ``pg_settings``.
            ``timing`` contains per-component wall-clock durations for this
            evaluation.

        Example
        -------
        >>> metrics, score, restarted, db_cfg, timing = evaluator.evaluate_worker(worker)
        >>> worker.update_metrics(metrics, score)
        """
        if not worker.db_config:
            raise ValueError(
                f"[Worker-{worker.worker_id}] Missing db_config for evaluation"
            )

        recorder = TimingRecorder()

        # Helper: wait at a named barrier (no-op when barriers is None/disabled).
        def _barrier(name: str) -> None:
            if barriers is not None:
                barriers.wait(name, worker_id=worker.worker_id)

        # Track which barrier we've completed so that on failure we can
        # drain all remaining barriers to prevent deadlocks.
        last_completed_barrier: Optional[str] = None

        _barriers_drained = False

        connection = None
        restart_occurred = False
        actual_db_config: Dict[str, Any] = {}

        try:
            worker.logger.debug(" Establishing connection to PostgreSQL...")
            # ── B1: Connect ──────────────────────────────────────────
            connection = self.connect(worker.db_config, max_retries=5, retry_delay=3.0)
            _barrier("connected")
            last_completed_barrier = "connected"

            # ── B2: Apply knob configuration ─────────────────────────
            if apply_config and worker.knob_config:
                applicator_config = ApplicatorConfig(
                    rollback_on_error=False,
                )
                knob_applicator = KnobApplicator(
                    db_config=worker.db_config,
                    config=applicator_config,
                    worker_id=worker.worker_id,
                )

                force_restart = worker.force_restart_next_eval

                worker.logger.info(" Applying knob configuration...")
                restart_occurred = self.apply_configuration(
                    connection=connection,
                    worker=worker,
                    knob_applicator=knob_applicator,
                    force_restart=force_restart,
                    generation=generation,
                    restore_due=restore_due,
                    recorder=recorder,
                )

                # ── B2a: Snapshot restore (when due) ────────────────────
                if restore_due:
                    worker.logger.debug(
                        " Performing snapshot restore (serves as restart)..."
                    )
                    # Close connection before restore (instance will stop)
                    self.disconnect(connection, worker_id=worker.worker_id)
                    connection = None

                    with recorder.span("snapshot_restore"):
                        restored = self.env.restore_snapshot(worker.worker_id, quiet=True)
                    if restored:
                        restart_occurred = True
                        worker.logger.info(
                            " %s➤ Snapshot restore successful (restart via restore)%s",
                            COLORS.italic,
                            COLORS.reset,
                        )
                    else:
                        # Attempt rebuild on restore failure
                        worker.logger.error(
                            "Snapshot restore failed for [Worker-%d]; attempting rebuild",
                            worker.worker_id,
                        )
                        rebuilt = self.env.rebuild_worker_instance(worker.worker_id)
                        if not rebuilt:
                            raise RuntimeError(
                                f"Snapshot restore and rebuild both failed for worker {worker.worker_id}"
                            )
                        restart_occurred = True

                _barrier("config_applied")
                last_completed_barrier = "config_applied"

                if force_restart and restart_occurred:
                    worker.force_restart_next_eval = False
                    worker.logger.debug(
                        " %sCleared forced-restart marker after successful restart%s",
                        COLORS.italic,
                        COLORS.reset,
                    )

            if restart_occurred:
                self.disconnect(connection, worker_id=worker.worker_id)
                connection = None  # Will reconnect in B4
            _barrier("restarted")
            last_completed_barrier = "restarted"

            # ── B4: Reconnect after restart ──────────────────────────
            if restart_occurred or connection is None or connection.closed:
                # Retry connection after restart (instance may be in recovery)
                connection = self.connect(
                    worker.db_config, max_retries=5, retry_delay=3.0
                )
                if restart_occurred:
                    worker.logger.debug(" ➤ Reconnected after restart")
            _barrier("reconnected")
            last_completed_barrier = "reconnected"

            # ── B5: Verify configuration ─────────────────────────────
            if apply_config and worker.knob_config:
                actual_db_config = self._verify_and_capture_config(
                    knob_applicator, worker, recorder
                )

            _barrier("config_verified")
            last_completed_barrier = "config_verified"

            # ── B6: Capture pg_stat_database BEFORE ──────────────────
            try:
                worker.logger.debug(
                    " Capturing pre-workload database stats for I/O metrics..."
                )
                stats_before = self._fetch_pg_stat_database_snapshot(
                    worker.db_config,
                    connection=connection,
                    worker_logger=worker.logger,
                )

                _barrier("pre_stats_captured")
                last_completed_barrier = "pre_stats_captured"

                # ── B7: Ensure benchmark ready ───────────────────────
                if self.workload_executor.manages_own_connection:
                    worker.logger.debug(
                        " %sEnsuring benchmark is ready...%s",
                        COLORS.italic,
                        COLORS.reset,
                    )
                    self._ensure_benchmark_ready(
                        worker.db_config, worker_logger=worker.logger
                    )

                _barrier("benchmark_ready")
                last_completed_barrier = "benchmark_ready"

                # ── B8: Warmup + B9: Measurement ─────────────────────
                effective_seed = (
                    random_seed
                    if random_seed is not None
                    else self.config.random_seed
                )

                if self.workload_executor.manages_own_connection:
                    # External benchmarks (sysbench, tpch) handle warmup
                    # internally; we still gate with barriers around the
                    # combined call.
                    _barrier("warmup_done")
                    last_completed_barrier = "warmup_done"

                    metrics = self._run_workload(
                        worker,
                        connection=connection,
                        effective_seed=effective_seed,
                        recorder=recorder,
                    )
                else:
                    # Internal workload executor: warmup then measurement.
                    # Warmup is run first, barrier fires via callback, then measurement.
                    metrics = self._run_workload(
                        worker,
                        connection=connection,
                        effective_seed=effective_seed,
                        recorder=recorder,
                        pre_measurement_callback=lambda: _barrier("warmup_done"),
                    )
                    last_completed_barrier = "warmup_done"

                worker.logger.debug(
                    " ➤ Workload execution completed, collecting "
                    "post-workload stats for I/O metrics..."
                )
                _barrier("measurement_done")
                last_completed_barrier = "measurement_done"

                # ── B10: Capture pg_stat_database AFTER ──────────────
                stats_after = None
                if stats_before:
                    stats_after = self._fetch_pg_stat_database_snapshot(
                        worker.db_config,
                        worker_logger=worker.logger,
                    )

                _barrier("post_stats_captured")
                last_completed_barrier = "post_stats_captured"

                # ── B11: Compute I/O delta ───────────────────────────
                if stats_before and stats_after:
                    self._compute_io_metrics(
                        metrics,
                        stats_before=stats_before,
                        stats_after=stats_after,
                        worker_logger=worker.logger,
                    )

                _barrier("io_computed")
                last_completed_barrier = "io_computed"

            except Exception as e:
                worker.logger.error(" ➤ Workload execution failed: %s", e)
                metrics = PerformanceMetrics(failure_type="EXECUTION_CRASH")
                engine = self._get_scoring_engine()
                score_breakdown = engine.compute_breakdown(
                    metrics, worker_logger=worker.logger
                )
                worker.score_breakdown = score_breakdown
                score = score_breakdown.final_score

                # Drain remaining barriers so other threads don't deadlock
                if barriers is not None and last_completed_barrier is not None:
                    next_name = barriers.next_barrier_name(last_completed_barrier)
                    if next_name:
                        barriers.drain_remaining(next_name, worker_id=worker.worker_id)
                _barriers_drained = True
                return metrics, score, restart_occurred, actual_db_config, recorder

            # ── B12: Collect system metrics ──────────────────────────
            system_metrics = self.collect_system_metrics(worker_id=worker.worker_id)

            if "cache_hit_ratio" in system_metrics:
                metrics.cache_hit_ratio = system_metrics["cache_hit_ratio"]
            if "memory_utilization" in system_metrics:
                metrics.memory_utilization = system_metrics["memory_utilization"]

            metrics.scan_efficiency = (
                MetricInstrumentationEngine.calculate_scan_efficiency(
                    metrics.cache_hit_ratio,
                    rows_examined=metrics.rows_examined
                    if metrics.rows_examined > 0
                    else None,
                    rows_returned=metrics.rows_returned
                    if metrics.rows_returned > 0
                    else None,
                )
            )

            _barrier("system_metrics_collected")
            last_completed_barrier = "system_metrics_collected"

            # ── B13: Compute memory pressure ─────────────────────────
            # Based on memory utilization and cache hit ratio.
            metrics.memory_pressure = metrics.memory_utilization * (
                1.0 - metrics.cache_hit_ratio
            )
            _barrier("memory_pressure_computed")
            last_completed_barrier = "memory_pressure_computed"

            worker.logger.debug(
                " ➤ Collected all metrics, applying reliability gate..."
            )
            self._apply_reliability_gate(metrics, worker.logger)
            _barrier("reliability_gated")
            last_completed_barrier = "reliability_gated"

            worker.logger.debug(
                " Running post-workload maintenance (VACUUM ANALYZE) if needed..."
            )
            self._vacuum_after_dml(
                worker.db_config,
                worker_logger=worker.logger,
                next_eval_will_restore=next_eval_will_restore,
            )
            _barrier("vacuum_done")
            last_completed_barrier = "vacuum_done"

            worker.logger.info(" Computing performance score...")
            with recorder.span("score"):
                engine = self._get_scoring_engine()
                score_breakdown = engine.compute_breakdown(
                    metrics, worker_logger=worker.logger
                )
            worker.score_breakdown = score_breakdown
            score = score_breakdown.final_score
            _barrier("score_computed")
            last_completed_barrier = "score_computed"

            worker.logger.info("➤ Evaluated successfully.")

            return metrics, score, restart_occurred, actual_db_config, recorder

        except Exception:
            # Top-level safety net: drain all remaining barriers.
            if barriers is not None:
                if last_completed_barrier is not None:
                    next_name = barriers.next_barrier_name(last_completed_barrier)
                    if next_name:
                        barriers.drain_remaining(next_name, worker_id=worker.worker_id)
                else:
                    # Failed before hitting any barrier — drain from the first.
                    barriers.drain_remaining("connected", worker_id=worker.worker_id)
            _barriers_drained = True
            raise

        finally:
            # ── B17: Disconnect ──────────────────────────────────────
            self.disconnect(
                connection,
                worker_id=worker.worker_id if hasattr(worker, "worker_id") else None,
            )

            # Only call the disconnected barrier on the NORMAL path.
            # Exception handlers already drained all barriers (including
            # "disconnected") — calling it again would start a new barrier
            # cycle that can never complete (the other workers already left).
            if not _barriers_drained:
                _barrier("disconnected")

    # ------------------------------------------------------------------
    # Reliability gate
    # ------------------------------------------------------------------

    # Thresholds for failure classification.  Kept as class-level constants
    # so they are easy to override in tests or subclasses.
    _HIGH_ERROR_RATE_THRESHOLD: float = 0.50
    _NEAR_ZERO_THROUGHPUT_THRESHOLD: float = 0.1
    _DEGRADED_ERROR_RATE_THRESHOLD: float = 0.10

    def _apply_reliability_gate(
        self,
        metrics: PerformanceMetrics,
        worker_logger: logging.Logger,
    ) -> None:
        """
        Classify the evaluation result and set ``failure_type`` if degraded.

        The gate runs *after* workload execution succeeds but *before* scoring.
        It inspects the raw metrics and assigns one of:

        * ``HIGH_ERROR_RATE`` — more than 50 % of queries failed.
        * ``NEAR_ZERO_THROUGHPUT`` — throughput is effectively zero, meaning
          the workload produced no useful work despite not crashing.
        * ``DEGRADED`` — error rate above 10 % but below the crash threshold,
          indicating partial failure that still produced some useful data.

        If the evaluation is healthy, ``failure_type`` remains ``None``.
        Only the first matching classification is applied (most severe first).

        Delegates to :func:`apply_reliability_gate`, forwarding the class-level
        thresholds so subclass/test overrides of the ``_*_THRESHOLD`` attributes
        continue to take effect.
        """
        apply_reliability_gate(
            metrics,
            worker_logger,
            high_error_rate_threshold=self._HIGH_ERROR_RATE_THRESHOLD,
            near_zero_throughput_threshold=self._NEAR_ZERO_THROUGHPUT_THRESHOLD,
            degraded_error_rate_threshold=self._DEGRADED_ERROR_RATE_THRESHOLD,
        )

    def refine_workload_features_from_generation(self, workers: List[Any]) -> bool:
        """Refine workload features from a generation's aggregated metrics; see :meth:`WorkloadFeatureRefiner.refine_from_generation`."""
        return self._feature_refiner.refine_from_generation(workers)

    def maybe_update_feature_weights(
        self,
        generation: int,
        *,
        force: bool = False,
        log_every: int = 5,
    ) -> bool:
        return self._feature_refiner.maybe_update_weights(
            generation, force=force, log_every=log_every
        )

    def __repr__(self) -> str:
        """String representation."""
        return (
            f"WorkloadOrchestrator(workload={self.config.workload_type.value}, "
            f"duration={self.config.measurement_duration}s)"
        )
