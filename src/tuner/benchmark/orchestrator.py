"""
Workload Orchestrator for Database Tuning
==========================================

The WorkloadOrchestrator class orchestrates workload execution and collects performance metrics.
It serves as the bridge between PBT's Population and the actual PostgreSQL database.

Key Responsibilities:
- Execute workload benchmarks (SYSBENCH, TPC-H, custom queries)
- Apply knob configurations to PostgreSQL
- Collect performance metrics (latency, throughput, resource utilization)
- Compute composite performance scores
- Handle workload-specific behavior (OLTP vs OLAP)

Architecture:
------------
    Population -> WorkloadOrchestrator -> PostgreSQL
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
from typing import Dict, Any, Optional, Union, List
import logging
import threading
import time
import numpy as np
import psycopg2
from psycopg2 import sql
from psycopg2.extensions import connection as PostgresConnection, register_adapter, AsIs

from src.database.connection import get_connection
from src.config.database import DatabaseConfig
from src.utils.environments.base import DatabaseEnvironment
from src.utils.logger.helpers import log_section_header
from src.utils.metrics import (
    PerformanceMetrics,
    WorkloadType,
    MetricConfig,
)
from src.utils.metric_instrumentation import MetricInstrumentationEngine
from src.utils.scoring import create_scoring_engine
from src.benchmarks.executor import BenchmarkExecutor
from src.tuner.benchmark.workload import WorkloadExecutor
from src.tuner.core.worker import Worker
from src.utils.types import TuningMode
from src.tuner.benchmark.restart_policy import should_restart
from src.tuner.core.barriers import GenerationBarrier
from src.utils.applicator import KnobApplicator, ApplicatorConfig
from src.utils.logger import get_logger, get_color_context

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
    workload_executor : WorkloadExecutor
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
    >>> metrics, score = orchestrator.evaluate_worker(worker)
    >>> print(f"Score: {score:.4f}, Throughput: {metrics.throughput:.2f} TPS")
    """

    def __init__(
        self,
        config: WorkloadOrchestratorConfig,
        workload_executor: Union[WorkloadExecutor, BenchmarkExecutor],
        env: DatabaseEnvironment,
    ):
        """
        Initialize WorkloadOrchestrator.

        Parameters
        ----------
        config : WorkloadOrchestratorConfig
            Orchestration configuration
        workload_executor : Union[WorkloadExecutor, BenchmarkExecutor]
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

    @property
    def scorer(self):
        return self._get_scoring_engine()

    def connect(
        self,
        db_config: Optional[DatabaseConfig] = None,
        max_retries: int = 1,
        retry_delay: float = 2.0,
    ) -> PostgresConnection:
        """
        Establish connection to PostgreSQL with retry logic.

        Parameters
        ----------
        db_config : Optional[DatabaseConfig]
            Database configuration. If None, uses self.config.db_config
        max_retries : int
            Maximum number of connection attempts (default: 1, no retry)
        retry_delay : float
            Delay in seconds between retries (default: 2.0)

        Returns
        -------
        PostgresConnection
            Active PostgreSQL connection

        Raises
        ------
        psycopg2.Error
            If connection fails after all retries
        """
        last_error = None

        for attempt in range(1, max_retries + 1):
            try:
                connection = get_connection(config=db_config or self.config.db_config)
                connection.autocommit = False
                if attempt > 1:
                    LOGGER.debug(" ➤ Connection established after %d attempts", attempt)
                return connection
            except psycopg2.Error as e:
                last_error = e
                error_msg = str(e).lower()

                # Check if it's a recoverable error (instance still recovering)
                if (
                    "starting up" in error_msg
                    or "not yet accepting connections" in error_msg
                    or "consistent recovery state" in error_msg
                    or (
                        "connection refused" in error_msg
                        and "is the server running" in error_msg
                    )
                ):
                    if attempt < max_retries:
                        LOGGER.warning(
                            " Database recovering, retry %d/%d in %.1fs...",
                            attempt,
                            max_retries,
                            retry_delay,
                        )
                        time.sleep(retry_delay)

        LOGGER.error(
            " ➤ Failed to connect after %d attempts: %s", max_retries, last_error
        )
        raise last_error  # type: ignore

    def disconnect(
        self, connection: Optional[PostgresConnection], worker_id: Optional[int] = None
    ) -> None:
        """
        Close PostgreSQL connection.

        Parameters
        ----------
        connection : Optional[PostgresConnection]
            Connection to close
        worker_id : Optional[int]
            Worker ID for logging context
        """
        if connection:
            worker_logger = (
                get_logger("BenchmarkWorker", worker_id=worker_id)
                if worker_id is not None
                else LOGGER
            )
            try:
                connection.close()
                worker_logger.debug(
                    "  %sDisconnected from PostgreSQL%s", COLORS.italic, COLORS.reset
                )
            except Exception as e:
                worker_logger.warning("Error closing connection: %s", e)

    def apply_configuration(
        self,
        connection: PostgresConnection,
        worker: Worker,
        knob_applicator: KnobApplicator,
        force_restart: bool = False,
        generation: Optional[int] = None,
    ) -> bool:
        """
        Apply knob configuration and optionally restart via policy.

        This method applies knobs directly through KnobApplicator,
        then uses RestartPolicy (should_restart) for restart decisions,
        with env.restart_instance() for the actual restart mechanism.

        Parameters
        ----------
        connection : PostgresConnection
            Active connection to worker's instance
        worker : Worker
            Worker instance for which to apply configuration
        knob_applicator : KnobApplicator
            Applicator for this worker's instance (legacy compat)
        force_restart : bool
            Force immediate restart regardless of mode/interval
        generation : Optional[int]
            Current generation number

        Returns
        -------
        bool
            True if restart occurred during this application
        """
        try:
            result = knob_applicator.apply(worker.knob_config)  # type: ignore

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

            do_restart = should_restart(
                mode=self.config.tuning_mode,
                restart_required=restart_required,
                generation=generation,
                adaptive_restart_interval=self.config.adaptive_restart_interval,
                force=force_restart,
            )

            if do_restart:
                worker.logger.debug(" Restarting PostgreSQL instance...")
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

            return False

        except Exception as e:
            worker.logger.error("Failed to apply configuration: %s", e)
            raise

    def _perform_restart(
        self,
        connection: PostgresConnection,
        worker: Worker,
    ) -> bool:
        """Restart PostgreSQL via the injected environment.

        Parameters
        ----------
        connection : PostgresConnection
            Connection to close before restart
        worker : Worker
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
        metrics: Dict[str, float] = {
            "memory_utilization": 0.0,
            "cache_hit_ratio": 0.0,
        }

        wid = worker_id if worker_id is not None else 0

        # Retry once because worker restarts and transient reconnect windows can
        # briefly make environment-backed metrics unavailable.
        for attempt in range(2):
            try:
                metrics["memory_utilization"] = self.env.collect_memory_utilization(wid)
                metrics["cache_hit_ratio"] = self.env.collect_cache_hit_ratio(wid)
                break
            except (RuntimeError, OSError, ValueError, TypeError, AttributeError) as exc:
                LOGGER.debug(
                    "System metric collection attempt %d failed for worker %d: %s",
                    attempt + 1,
                    wid,
                    exc,
                )
                if attempt == 0:
                    time.sleep(0.1)
        return metrics

    def _fetch_pg_stat_database_snapshot(
        self,
        db_config: DatabaseConfig,
        *,
        connection: Any | None = None,
        worker_logger: Optional[logging.Logger] = None,
    ) -> tuple[int, int, int, int, int, int, int] | None:
        """Read pg_stat_database counters with a retry for transient failures."""
        query = """
            SELECT
                blks_read,
                blks_hit,
                tup_returned,
                tup_fetched,
                tup_inserted,
                tup_updated,
                tup_deleted
            FROM pg_stat_database
            WHERE datname = current_database()
        """

        logger = worker_logger or LOGGER
        last_error: Exception | None = None

        for attempt in range(2):
            active_connection = connection
            owns_connection = False
            try:
                if active_connection is None or getattr(active_connection, "closed", 1):
                    active_connection = self.connect(
                        db_config, max_retries=2, retry_delay=1.0
                    )
                    owns_connection = True

                if not active_connection:
                    continue

                cursor = active_connection.cursor()
                cursor.execute(query)
                row = cursor.fetchone()
                cursor.close()

                if owns_connection:
                    self.disconnect(active_connection)

                if row is None:
                    return None

                return (
                    int(row[0] or 0),
                    int(row[1] or 0),
                    int(row[2] or 0),
                    int(row[3] or 0),
                    int(row[4] or 0),
                    int(row[5] or 0),
                    int(row[6] or 0),
                )
            except Exception as exc:
                last_error = exc
                logger.debug(
                    " ➤ Failed to capture pg_stat_database snapshot (attempt %d): %s",
                    attempt + 1,
                    exc,
                )
                if owns_connection and active_connection is not None:
                    self.disconnect(active_connection)
                if attempt == 0:
                    time.sleep(0.2)

        if last_error is not None:
            logger.debug(" ➤ Last pg_stat_database snapshot error: %s", last_error)
        return None

    def _vacuum_after_dml(
        self, db_config: DatabaseConfig, worker_logger: Optional[logging.Logger] = None
    ) -> None:
        """
        Run bounded post-workload maintenance after DML-heavy workloads.

        Full-database VACUUM ANALYZE is too expensive for short sysbench-style
        generations and frequently times out while scanning toast/system tables.
        Instead, analyze only user tables that were actually modified.
        """
        # Skip for read-only workloads (OLAP, TPC-H)
        if self.config.workload_type.value in ("olap", "tpch"):
            return
        worker_logger = worker_logger or LOGGER

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
        """Validate benchmark state before execution and repair it if needed."""
        if not isinstance(self.workload_executor, BenchmarkExecutor):
            return

        worker_logger = worker_logger or LOGGER

        try:
            if self.workload_executor.validate(db_config):
                worker_logger.debug(
                    " ➤ Benchmark validation successful, ready to execute"
                )
                return
        except Exception as e:
            worker_logger.warning(
                "Benchmark validation raised %s; attempting prepare()", e
            )

        self.workload_executor.prepare(db_config)

        if not self.workload_executor.validate(db_config):
            raise RuntimeError("Benchmark validation still failing after prepare()")

        worker_logger.debug(" ➤ Benchmark state re-prepared successfully")

    def evaluate_worker(
        self,
        worker: Worker,
        apply_config: bool = True,
        generation: Optional[int] = None,
        barriers: Optional[GenerationBarrier] = None,
    ) -> tuple[PerformanceMetrics, float, bool, Dict[str, Any]]:
        """
        Evaluate a Worker's configuration.

        This is the main evaluation method called by Population.train_generation().

        Process:
        1. Apply worker's knob configuration (if apply_config=True)
        2. Execute workload with warmup and measurement phases
        3. Collect performance metrics
        4. Collect system metrics
        5. Compute composite performance score

        All sub-steps are gated by optional ``GenerationBarrier`` synchronization
        points (B1–B17) so that workers advance in lockstep when barriers are
        enabled.

        Parameters
        ----------
        worker : Worker
            Worker instance to evaluate
        apply_config : bool, default=True
            Whether to apply the worker's configuration
        generation : Optional[int]
            Current generation number (for restart cost calculation)
        barriers : GenerationBarrier | None
            Optional lockstep barriers.  When provided and enabled, this
            method will ``wait()`` at each barrier so all workers stay
            in phase.

        Returns
        -------
        tuple[PerformanceMetrics, float, bool, Dict[str, Any]]
            (metrics, score, restart_occurred, actual_db_config) tuple.
            ``actual_db_config`` contains the true values currently active
            in PostgreSQL after apply + optional restart, as read back
            from ``pg_settings``.

        Example
        -------
        >>> metrics, score, restarted, db_cfg = evaluator.evaluate_worker(worker)
        >>> worker.update_metrics(metrics, score)
        """
        if not worker.db_config:
            raise ValueError(
                f"[Worker-{worker.worker_id}] Missing db_config for evaluation"
            )

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
                )
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
                worker.logger.debug(" Verifying knob configuration...")
                verification = knob_applicator.verify(worker.knob_config)
                if verification.failed_params:
                    worker.logger.warning(
                        " ➤ Configuration verification failed for %d parameters: %s",
                        len(verification.failed_params),
                        verification.failed_params,
                    )
                else:
                    worker.logger.debug(" ➤ All parameters verified.")

                # Save the true applied DB config back to the worker so
                # downstream consumers (scoring, result writers) see the
                # actual quantized values PostgreSQL is using.
                if verification.db_config:
                    actual_db_config = verification.db_config
                    worker.knob_config.update(actual_db_config)
                    worker.logger.debug(
                        " ➤ Updated worker.knob_config with %d actual DB values",
                        len(actual_db_config),
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
                if isinstance(self.workload_executor, BenchmarkExecutor):
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
                if isinstance(self.workload_executor, BenchmarkExecutor):
                    # External benchmarks (sysbench, tpch) handle warmup
                    # internally; we still gate with barriers around the
                    # combined call.
                    _barrier("warmup_done")
                    last_completed_barrier = "warmup_done"

                    metrics = self.workload_executor.execute(
                        db_config=worker.db_config,
                        worker_id=worker.worker_id,
                        random_seed=self.config.random_seed,
                        duration=self.config.measurement_duration,
                        warmup=self.config.warmup_duration,
                        warmup_passes=self.config.warmup_passes,
                    )
                else:
                    # Internal workload executor: warmup then measurement.
                    # Warmup is run first, barrier, then measurement.
                    metrics = self.workload_executor.execute(
                        connection=connection,
                        duration=self.config.measurement_duration,
                        warmup=self.config.warmup_duration,
                        worker_id=worker.worker_id,
                        random_seed=self.config.random_seed,
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
                if stats_after:
                    try:
                        (
                            blks_read_after,
                            blks_hit_after,
                            tup_returned_after,
                            tup_fetched_after,
                            tup_inserted_after,
                            tup_updated_after,
                            tup_deleted_after,
                        ) = stats_after

                        (
                            blks_read_before,
                            blks_hit_before,
                            tup_returned_before,
                            tup_fetched_before,
                            tup_inserted_before,
                            tup_updated_before,
                            tup_deleted_before,
                        ) = stats_before  # type: ignore

                        blocks_read_delta = blks_read_after - blks_read_before
                        blocks_hit_delta = blks_hit_after - blks_hit_before
                        tup_returned_delta = tup_returned_after - tup_returned_before
                        tup_fetched_delta = tup_fetched_after - tup_fetched_before

                        # Convert to MB (8KB blocks)
                        io_read_mb = (blocks_read_delta * 8) / 1024.0
                        metrics.io_read_mb = max(0, io_read_mb)

                        # Estimate write MB from row modification counts when
                        # filesystem-level write counters aren't directly
                        # available. This is a conservative heuristic: assume
                        # an average row size (bytes) and multiply by the number
                        # of inserted/updated/deleted rows observed.
                        try:
                            tup_inserted_delta = tup_inserted_after - tup_inserted_before
                            tup_updated_delta = tup_updated_after - tup_updated_before
                            tup_deleted_delta = tup_deleted_after - tup_deleted_before
                        except Exception:
                            tup_inserted_delta = tup_updated_delta = tup_deleted_delta = 0

                        total_rows_written = max(
                            0, tup_inserted_delta + tup_updated_delta + tup_deleted_delta
                        )
                        # Conservative average row size in bytes; adjustable later
                        avg_row_bytes = 128
                        io_write_bytes = total_rows_written * avg_row_bytes
                        metrics.io_write_mb = max(0.0, io_write_bytes / (1024.0 * 1024.0))

                        # Diagnostic: if we observed no written rows but the workload
                        # appears to have read/fetched rows or the DB changed, emit a
                        # debug message to help trace why writes are zero.
                        if total_rows_written == 0 and (
                            tup_fetched_delta > 0 or blocks_read_delta > 0
                        ):
                            worker.logger.debug(
                                "IO write estimation produced 0 bytes (inserted=%s updated=%s deleted=%s). "
                                "Blocks read delta=%s, tup_returned_delta=%s, tup_fetched_delta=%s",
                                tup_inserted_delta,
                                tup_updated_delta,
                                tup_deleted_delta,
                                blocks_read_delta,
                                tup_returned_delta,
                                tup_fetched_delta,
                            )

                        # Populate DB counters
                        metrics.rows_returned = max(0, tup_returned_delta)
                        metrics.rows_examined = max(0, tup_fetched_delta)

                        # Compute buffer miss rate
                        total_blocks = blocks_read_delta + blocks_hit_delta
                        if total_blocks > 0:
                            metrics.buffer_miss_rate = max(
                                0.0, min(1.0, blocks_read_delta / total_blocks)
                            )
                        else:
                            metrics.buffer_miss_rate = 0.0

                    except Exception as e:
                        worker.logger.debug("Failed to calculate IO stats: %s", e)

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
                return metrics, score, restart_occurred, actual_db_config

            # ── B12: Collect system metrics ──────────────────────────
            system_metrics = self.collect_system_metrics(worker_id=worker.worker_id)

            if "cache_hit_ratio" in system_metrics:
                metrics.cache_hit_ratio = system_metrics["cache_hit_ratio"]
            if "memory_utilization" in system_metrics:
                metrics.memory_utilization = system_metrics["memory_utilization"]

            metrics.scan_efficiency = MetricInstrumentationEngine.calculate_scan_efficiency(
                metrics.cache_hit_ratio,
                rows_examined=metrics.rows_examined if metrics.rows_examined > 0 else None,
                rows_returned=metrics.rows_returned if metrics.rows_returned > 0 else None,
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
            self._vacuum_after_dml(worker.db_config, worker_logger=worker.logger)
            _barrier("vacuum_done")
            last_completed_barrier = "vacuum_done"

            worker.logger.info(" Computing performance score...")
            engine = self._get_scoring_engine()
            score_breakdown = engine.compute_breakdown(metrics, worker_logger=worker.logger)
            worker.score_breakdown = score_breakdown
            score = score_breakdown.final_score
            _barrier("score_computed")
            last_completed_barrier = "score_computed"

            worker.logger.info("➤ Evaluated successfully.")

            return metrics, score, restart_occurred, actual_db_config

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
        """
        if metrics.failure_type is not None:
            # Already classified (e.g. EXECUTION_CRASH from the outer handler)
            return

        if metrics.error_rate >= self._HIGH_ERROR_RATE_THRESHOLD:
            metrics.failure_type = "HIGH_ERROR_RATE"
            worker_logger.warning(
                " ➤ Reliability gate: error_rate=%.2f exceeds threshold %.2f — "
                "marking as HIGH_ERROR_RATE",
                metrics.error_rate,
                self._HIGH_ERROR_RATE_THRESHOLD,
            )
            return

        if metrics.throughput <= self._NEAR_ZERO_THROUGHPUT_THRESHOLD:
            metrics.failure_type = "NEAR_ZERO_THROUGHPUT"
            worker_logger.warning(
                " ➤ Reliability gate: throughput=%.4f at or below threshold %.4f — "
                "marking as NEAR_ZERO_THROUGHPUT",
                metrics.throughput,
                self._NEAR_ZERO_THROUGHPUT_THRESHOLD,
            )
            return

        if metrics.error_rate >= self._DEGRADED_ERROR_RATE_THRESHOLD:
            metrics.failure_type = "DEGRADED"
            worker_logger.warning(
                " ➤ Reliability gate: error_rate=%.2f exceeds degraded threshold "
                "%.2f — marking as DEGRADED",
                metrics.error_rate,
                self._DEGRADED_ERROR_RATE_THRESHOLD,
            )
            return

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
                floor = 0.15 * self._static_feature_priors.get("concurrency_pressure", 0.0)
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
                floor = 0.15 * self._static_feature_priors.get("tail_latency_sensitivity", 0.0)
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
        workers : List[Worker]
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
        aggregated_metrics.throughput = sum(
            m.throughput for m in health_metrics
        ) / len(health_metrics)
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
