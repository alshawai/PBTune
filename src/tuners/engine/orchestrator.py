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
import time
import numpy as np
import psycopg2
from psycopg2 import sql
from psycopg2.extensions import connection as PostgresConnection, register_adapter, AsIs

from src.database.connection import get_connection, connect_with_retry, safe_disconnect
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
from src.tuners.engine.restart_policy import should_restart
from src.tuners.engine.barriers import GenerationBarrier
from src.tuners.engine.reliability_gate import apply_reliability_gate
from src.tuners.engine.worker_metrics import (
    collect_system_metrics as _collect_system_metrics,
    fetch_pg_stat_database_snapshot as _fetch_pg_stat_snapshot,
    compute_io_metrics as _compute_io_metrics_impl,
)
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
        self._pending_feature_deltas: Dict[str, float] = {}

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
        """
        Apply knob configuration and optionally restart via policy.

        This method writes knobs via apply_only (ALTER SYSTEM only), then
        decides activation strategy (reload/restart/none) via RestartPolicy.
        When ``restore_due`` is True, activation is skipped because the
        caller will perform a snapshot restore that serves as the restart.

        Parameters
        ----------
        connection : PostgresConnection
            Active connection to worker's instance
        worker : BaseWorker
            Worker instance for which to apply configuration
        knob_applicator : KnobApplicator
            Applicator for this worker's instance
        force_restart : bool
            Force immediate restart regardless of mode/interval
        generation : Optional[int]
            Current generation number
        restore_due : bool
            When True, skip activation — snapshot restore will serve as
            the restart. The caller is responsible for calling
            env.restore_snapshot() after this method returns.

        Returns
        -------
        bool
            True if restart occurred during this application
        """
        try:
            if recorder is not None:
                with recorder.span("apply_only"):
                    result = knob_applicator.apply_only(worker.knob_config)  # type: ignore
            else:
                result = knob_applicator.apply_only(worker.knob_config)  # type: ignore

            restart_required = bool(
                result.restart_required and len(result.restart_required) > 0
            )

            if restart_required:
                restart_required_params = list(result.restart_required)
                first_three = (
                    restart_required_params[:3] + ["..."]
                    if len(restart_required_params) > 3
                    else restart_required_params
                )

                worker.logger.info(
                    " %s➤ Restart required for %d parameter(s): %s%s",
                    COLORS.bold,
                    len(restart_required_params),
                    ", ".join(first_three),
                    COLORS.reset,
                )

            # When snapshot restore is due, the restore IS the restart.
            # Skip activation here; the orchestrator handles it.
            if restore_due:
                worker.logger.debug(
                    " Snapshot restore due — skipping activation (restore IS the restart)"
                )
                return False

            do_restart = should_restart(
                mode=self.config.tuning_mode,
                restart_required=restart_required,
                generation=generation,
                adaptive_restart_interval=self.config.adaptive_restart_interval,
                force=force_restart,
            )

            if do_restart:
                worker.logger.debug(" Restarting PostgreSQL instance...")
                if recorder is not None:
                    with recorder.span("activate_restart", strategy="restart"):
                        return self._perform_restart(connection, worker=worker)
                return self._perform_restart(connection, worker=worker)

            if restart_required and not do_restart:
                if self.config.tuning_mode == TuningMode.ADAPTIVE:
                    interval = self.config.adaptive_restart_interval
                    next_restart = (
                        ((generation // interval) + 1) * interval
                        if generation is not None
                        else interval
                    )
                    worker.logger.info(
                        " ➤ Deferring restart (will restart at generation %s%d%s)",
                        COLORS.bold,
                        next_restart,
                        COLORS.reset,
                    )
                elif self.config.tuning_mode == TuningMode.ONLINE:
                    worker.logger.info(
                        " %s➤ ONLINE mode: restart-required knobs written but restart skipped%s",
                        COLORS.bold,
                        COLORS.reset,
                    )

            # Non-restart activation: reload for sighup params
            if not do_restart and result.applied_count > 0 and not restart_required:
                # Reload to pick up sighup/user params without restart
                if recorder is not None:
                    with recorder.span("activate_reload", strategy="reload"):
                        activation = knob_applicator.activate(
                            restart_required=False,
                            env=self.env,
                            worker_id=worker.worker_id,
                        )
                else:
                    activation = knob_applicator.activate(
                        restart_required=False,
                        env=self.env,
                        worker_id=worker.worker_id,
                    )
                if not activation.success:
                    worker.logger.warning(
                        " ➤ Configuration reload failed: %s", activation.message
                    )

            return False

        except Exception as e:
            worker.logger.error("Failed to apply configuration: %s", e)
            raise

    def _perform_restart(
        self,
        connection: PostgresConnection,
        worker: BaseWorker,
    ) -> bool:
        """Restart PostgreSQL via the injected environment.

        Parameters
        ----------
        connection : PostgresConnection
            Connection to close before restart
        worker : BaseWorker
            Worker instance for which to perform restart

        Returns
        -------
        bool
            True if restart succeeded
        """
        try:
            # Close connection before restart
            try:
                if connection and not connection.closed:
                    connection.close()
            except (psycopg2.Error, AttributeError):
                pass

            if self.env.restart_instance(worker.worker_id, quiet=True):
                worker.logger.info(" ➤ Restart successful")

                return True
            else:
                worker.logger.error(" ➤ Restart failed")
                return False

        except Exception as e:
            worker.logger.error("➤ Restart failed with exception: %s", e)
            return False

    def collect_system_metrics(
        self,
        worker_id: Optional[int] = None,
    ) -> Dict[str, float]:
        """Collect system-level metrics by delegating to the environment.

        Memory utilization and cache hit ratio are collected via the
        DatabaseEnvironment abstraction, eliminating the need for
        process-scanning via psutil.

        Parameters
        ----------
        worker_id : Optional[int]
            Worker ID for environment delegation

        Returns
        -------
        Dict[str, float]
            System metrics including memory and cache hit ratio
        """
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
        """Populate I/O and row-count metrics from pg_stat_database deltas (B11).

        Derives read MB from block deltas, estimates write MB from row
        modification counts (no filesystem-level write counters available),
        and computes the buffer miss rate. All failures are swallowed at debug
        level so a stats hiccup never fails an otherwise-healthy evaluation.
        """
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
        """
        Run bounded post-workload maintenance after DML-heavy workloads.

        Full-database VACUUM ANALYZE is too expensive for short sysbench-style
        generations and frequently times out while scanning toast/system tables.
        Instead, analyze only user tables that were actually modified.

        When ``next_eval_will_restore`` is True, the caller has guaranteed the
        next evaluation begins with a baseline snapshot restore (PGDATA copied
        over from the post-prepare baseline, which already contains a clean
        VACUUM ANALYZE). Any per-eval VACUUM we run now is:
          1. Too late to influence the just-collected metrics — those are
             captured at B12 before this method is reached.
          2. About to be discarded by the next restore.
        Skipping eliminates 20–60s of dead wall-clock per generation on
        sysbench RW/WO with high table/row counts.
        """
        # Skip for read-only workloads (OLAP, TPC-H)
        if self.config.workload_type.value in ("olap", "tpch"):
            return
        worker_logger = worker_logger or LOGGER

        if next_eval_will_restore:
            worker_logger.debug(
                " ➤ Skipping post-workload VACUUM ANALYZE %s(next eval restores"
                " baseline snapshot)%s",
                COLORS.italic,
                COLORS.reset,
            )
            return

        timeout_seconds = max(0.0, float(self.config.vacuum_analyze_timeout_seconds))
        if timeout_seconds <= 0:
            worker_logger.debug(
                " ➤ Skipping post-workload maintenance %s(timeout disabled)%s",
                COLORS.italic,
                COLORS.reset,
            )
            return

        try:
            conn = get_connection(config=db_config)
            conn.autocommit = True  # VACUUM cannot run inside a transaction
            cursor = conn.cursor()

            statement_timeout_ms = int(timeout_seconds * 1000)
            lock_timeout_ms = max(1000, statement_timeout_ms // 4)
            cursor.execute("SET statement_timeout = %s", (statement_timeout_ms,))
            cursor.execute("SET lock_timeout = %s", (lock_timeout_ms,))

            cursor.execute(
                """
                SELECT schemaname, relname
                FROM pg_stat_user_tables
                WHERE n_mod_since_analyze > 0 OR n_dead_tup > 0
                ORDER BY n_mod_since_analyze DESC, n_dead_tup DESC
                """
            )
            tables = cursor.fetchall() or []

            if not tables:
                worker_logger.debug(
                    " ➤ Skipping post-workload maintenance %s(no modified user tables)%s",
                    COLORS.italic,
                    COLORS.reset,
                )
                cursor.close()
                conn.close()
                return

            worker_logger.debug(
                "  Running post-workload VACUUM ANALYZE on %d modified tables...",
                len(tables),
            )

            start = time.time()
            for schema_name, table_name in tables:
                try:
                    cursor.execute(
                        sql.SQL("VACUUM ANALYZE {}.{}").format(
                            sql.Identifier(schema_name),
                            sql.Identifier(table_name),
                        )
                    )
                except Exception as table_error:
                    worker_logger.warning(
                        " ➤ Post-workload maintenance failed for %s.%s: %s",
                        schema_name,
                        table_name,
                        table_error,
                    )

            elapsed = time.time() - start

            worker_logger.debug(
                " ➤ Post-workload VACUUM ANALYZE completed in %.2fs", elapsed
            )
            cursor.close()
            conn.close()

        except Exception as e:
            worker_logger.warning(" ➤ Post-workload VACUUM ANALYZE failed: %s", e)

    def _ensure_benchmark_ready(
        self,
        db_config: DatabaseConfig,
        worker_logger: Optional[logging.Logger] = None,
    ) -> None:
        """Validate benchmark state before execution and repair it if needed.

        Retries validation up to 3 times with a short delay before falling
        back to ``prepare()``, which recreates the full benchmark schema
        (~4.5 GB for large sysbench configs) and generates significant WAL.
        Transient connection errors under co-tenant load would otherwise
        trigger needless ``prepare()`` calls on every iteration.
        """
        if not self.workload_executor.manages_own_connection:
            return

        worker_logger = worker_logger or LOGGER

        max_retries = 3
        for attempt in range(1, max_retries + 1):
            try:
                if self.workload_executor.validate(db_config):
                    worker_logger.debug(
                        " ➤ Benchmark validation successful, ready to execute"
                    )
                    return
            except Exception as e:
                worker_logger.warning(
                    "Benchmark validation attempt %d/%d raised %s",
                    attempt,
                    max_retries,
                    e,
                )
            if attempt < max_retries:
                worker_logger.debug(
                    " ➤ Validation failed (attempt %d/%d); retrying in 2s...",
                    attempt,
                    max_retries,
                )
                time.sleep(2)

        worker_logger.warning(
            "Benchmark validation failed after %d attempts; running prepare()",
            max_retries,
        )
        self.workload_executor.prepare(db_config)

        if not self.workload_executor.validate(db_config):
            raise RuntimeError("Benchmark validation still failing after prepare()")

        worker_logger.debug(" ➤ Benchmark state re-prepared successfully")

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

    def _refine_workload_features(
        self,
        metrics: PerformanceMetrics,
    ) -> Dict[str, tuple[float, float]]:
        """Refine static workload features with runtime observations using EMA blending.

        Blends observed runtime metrics into the static feature vector to capture
        dynamic workload characteristics. Uses exponential moving average with
        alpha=0.7 to keep static features dominant while allowing runtime correction.
        Refined features are damped with a 15% soft minimum retention floor of the
        original static prior to prevent prior erasure.

        Refinement rules (bounded to [0, 1]):
        - High throughput CV -> increase concurrency_pressure (concurrency pressure signal = CV / 0.20)
        - High tail amplification (p99/p50) -> increase tail_latency_sensitivity (sensitivity signal = tail_amp / 10.0)
        """
        if not self.config.metric_config.workload_features:
            LOGGER.debug(" ➤ No workload features to refine")
            return {}

        features = self.config.metric_config.workload_features

        # Cache static feature priors on first call to establish the damping baseline
        if not hasattr(self, "_static_feature_priors"):
            self._static_feature_priors = dict(features)

        alpha = 0.7  # EMA blending factor: keep static features dominant
        refinements = {}

        # 1. Throughput Coefficient of Variation (CV) -> concurrency pressure
        if (
            hasattr(metrics, "throughput_variance")
            and metrics.throughput_variance is not None
            and hasattr(metrics, "throughput")
            and metrics.throughput is not None
        ):
            if metrics.throughput > 0:
                # metrics.throughput_variance holds stddev (np.std)
                throughput_cv = metrics.throughput_variance / metrics.throughput
                throughput_variance_signal = min(1.0, throughput_cv / 0.20)
            else:
                throughput_variance_signal = 0.0

            if "concurrency_pressure" in features:
                old_val = features["concurrency_pressure"]
                refined_val = (
                    alpha * features["concurrency_pressure"]
                    + (1 - alpha) * throughput_variance_signal
                )
                # Apply 15% soft minimum floor based on the original static prior
                floor = 0.15 * self._static_feature_priors.get(
                    "concurrency_pressure", 0.0
                )
                features["concurrency_pressure"] = max(floor, min(1.0, refined_val))

                refinements["concurrency_pressure"] = (
                    old_val,
                    features["concurrency_pressure"],
                )

        # 2. Tail Latency Amplification (p99/p50) -> tail latency sensitivity
        if (
            hasattr(metrics, "latency_p99")
            and metrics.latency_p99 is not None
            and hasattr(metrics, "latency_p50")
            and metrics.latency_p50 is not None
        ):
            if metrics.latency_p50 > 0:
                tail_amp = metrics.latency_p99 / metrics.latency_p50
                tail_sensitivity_signal = min(1.0, tail_amp / 10.0)
            else:
                tail_sensitivity_signal = 0.0

            if "tail_latency_sensitivity" in features:
                old_val = features["tail_latency_sensitivity"]
                refined_val = (
                    alpha * features["tail_latency_sensitivity"]
                    + (1 - alpha) * tail_sensitivity_signal
                )
                # Apply 15% soft minimum floor based on the original static prior
                floor = 0.15 * self._static_feature_priors.get(
                    "tail_latency_sensitivity", 0.0
                )
                features["tail_latency_sensitivity"] = max(floor, min(1.0, refined_val))

                refinements["tail_latency_sensitivity"] = (
                    old_val,
                    features["tail_latency_sensitivity"],
                )

        return refinements

    def refine_workload_features_from_generation(self, workers: List[Any]) -> bool:
        """Refine workload features using aggregated metrics from all workers in a generation.

        This generation-level refinement aggregates metrics from all workers before
        refining features once, ensuring that all workers in a generation use the same
        features and thus the same weights. This prevents race conditions that occur
        when feature refinement is performed per-worker during parallel evaluation.

        Parameters
        ----------
        workers : List[BaseWorker]
            List of all workers in the current generation
        """
        logger = get_logger("BenchmarkExecutor")

        if not workers:
            logger.debug(" No workers to aggregate for feature refinement")
            return False

        # Aggregate metrics from all healthy workers
        health_metrics = [
            w.metrics
            for w in workers
            if w.metrics is not None and w.metrics.failure_type is None
        ]
        if not health_metrics:
            logger.debug(" No valid metrics to aggregate for feature refinement")
            return False

        LOGGER.debug(
            " Aggregating metrics from %s%d%s healthy workers...",
            COLORS.bold,
            len(health_metrics),
            COLORS.reset,
        )
        aggregated_metrics = PerformanceMetrics()

        # Average numeric metrics
        aggregated_metrics.latency_p50 = sum(
            m.latency_p50 for m in health_metrics
        ) / len(health_metrics)
        aggregated_metrics.latency_p95 = sum(
            m.latency_p95 for m in health_metrics
        ) / len(health_metrics)
        aggregated_metrics.latency_p99 = sum(
            m.latency_p99 for m in health_metrics
        ) / len(health_metrics)
        aggregated_metrics.latency_variance = sum(
            m.latency_variance for m in health_metrics
        ) / len(health_metrics)
        aggregated_metrics.throughput = sum(m.throughput for m in health_metrics) / len(
            health_metrics
        )
        aggregated_metrics.throughput_variance = sum(
            m.throughput_variance for m in health_metrics
        ) / len(health_metrics)
        aggregated_metrics.buffer_miss_rate = sum(
            m.buffer_miss_rate for m in health_metrics
        ) / len(health_metrics)
        aggregated_metrics.scan_efficiency = sum(
            m.scan_efficiency for m in health_metrics
        ) / len(health_metrics)

        logger.debug(
            " ➤ Aggregated metrics from %s%d%s workers for generation-level feature refinement.",
            COLORS.bold,
            len(health_metrics),
            COLORS.reset,
        )

        logger.debug(" Refining features using aggregated metrics...")
        refinements = self._refine_workload_features(aggregated_metrics)
        if refinements:
            for feature, (old, new) in refinements.items():
                self._pending_feature_deltas[feature] = (
                    self._pending_feature_deltas.get(feature, 0.0) + (new - old)
                )
        return bool(refinements)

    def maybe_update_feature_weights(
        self,
        generation: int,
        *,
        force: bool = False,
        log_every: int = 5,
    ) -> bool:
        if not self._pending_feature_deltas and not force:
            return False

        should_update = force or (log_every > 0 and (generation + 1) % log_every == 0)
        if not should_update:
            return False

        if self._pending_feature_deltas:
            delta_line = ", ".join(
                f"{feature} {delta:+.4f}"
                for feature, delta in self._pending_feature_deltas.items()
            )
            LOGGER.info(
                "%sΔ features (accumulated):%s %s%s%s",
                COLORS.bold,
                COLORS.reset,
                COLORS.italic,
                delta_line,
                COLORS.reset,
            )

        updated = self.scorer.update_context(
            features=self.config.metric_config.workload_features,
            update_reason="feature_refinement",
        )
        if updated or force:
            self.scorer.schedule_log_next_generation()

        self._pending_feature_deltas.clear()
        return updated

    def __repr__(self) -> str:
        """String representation."""
        return (
            f"WorkloadOrchestrator(workload={self.config.workload_type.value}, "
            f"duration={self.config.measurement_duration}s)"
        )
