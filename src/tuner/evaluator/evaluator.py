"""
Workload Evaluator for Database Tuning
======================================

The Evaluator class executes workloads and collects performance metrics.
It serves as the bridge between PBT's Population and the actual PostgreSQL database.

Key Responsibilities:
- Execute workload benchmarks (SYSBENCH, TPC-H, custom queries)
- Apply knob configurations to PostgreSQL
- Collect performance metrics (latency, throughput, resource utilization)
- Compute composite performance scores
- Handle workload-specific behavior (OLTP vs OLAP)

Architecture:
------------
    Population → Evaluator → PostgreSQL
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
from typing import Dict, Any, Optional, Union
import logging
import time
import numpy as np
import psycopg2
from psycopg2 import sql
from psycopg2.extensions import connection as PostgresConnection, register_adapter, AsIs

from src.database.connection import get_connection
from src.config.database import DatabaseConfig
from src.utils.environments.base import DatabaseEnvironment
from src.utils.metrics import (
    PerformanceMetrics,
    WorkloadType,
    MetricConfig,
)
from src.benchmarks.executor import BenchmarkExecutor
from src.tuner.evaluator.workload import WorkloadExecutor
from src.tuner.core.worker import Worker
from src.tuner.evaluator.restart_policy import TuningMode, should_restart
from src.utils.applicator import KnobApplicator, ApplicatorConfig
from src.utils.logger import get_logger

logger = get_logger(__name__)

# Register numpy type adapters for psycopg2
register_adapter(np.int64, lambda x: AsIs(int(x)))
register_adapter(np.int32, lambda x: AsIs(int(x)))
register_adapter(np.float64, lambda x: AsIs(float(x)))
register_adapter(np.float32, lambda x: AsIs(float(x)))


@dataclass
class EvaluatorConfig:
    """
    Configuration for Evaluator behavior.

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


class Evaluator:
    """
    Main Evaluator class for workload execution and performance measurement.

    The Evaluator orchestrates the evaluation process:
    1. Apply knob configuration to PostgreSQL
    2. Wait for cooldown period
    3. Execute workload (with warmup)
    4. Collect metrics
    5. Compute performance score

    Attributes
    ----------
    config : EvaluatorConfig
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
    >>> config = EvaluatorConfig(
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
    >>> evaluator = Evaluator(config, executor)
    >>>
    >>> # Evaluate a worker
    >>> metrics, score = evaluator.evaluate_worker(worker)
    >>> print(f"Score: {score:.4f}, Throughput: {metrics.throughput:.2f} TPS")
    """

    def __init__(
        self,
        config: EvaluatorConfig,
        workload_executor: Union[WorkloadExecutor, BenchmarkExecutor],
        env: DatabaseEnvironment,
    ):
        """
        Initialize Evaluator.

        Parameters
        ----------
        config : EvaluatorConfig
            Evaluation configuration
        workload_executor : Union[WorkloadExecutor, BenchmarkExecutor]
            Workload execution strategy
        worker_id : Optional[str]
            Worker identifier for logging
        """
        self.config = config
        self.workload_executor = workload_executor
        self.env = env

        logger.debug(
            "✓ Created Evaluator: workload=%s, mode=%s, duration=%ss",
            config.workload_type.value.upper(),
            config.tuning_mode.value,
            config.measurement_duration,
        )

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
                    logger.info("Connection established after %d attempts", attempt)
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
                        logger.warning(
                            "Database recovering, retry %d/%d in %.1fs...",
                            attempt,
                            max_retries,
                            retry_delay,
                        )
                        time.sleep(retry_delay)
                        continue

                # Non-recoverable error or last attempt
                logger.error("Failed to connect to PostgreSQL: %s", e)
                raise

        logger.error("Failed to connect after %d attempts: %s", max_retries, last_error)
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
            try:
                connection.close()
                if worker_id is not None:
                    worker_logger = get_logger(__name__, worker_id=worker_id)
                    worker_logger.debug("Disconnected from PostgreSQL")
                else:
                    logger.debug("Disconnected from PostgreSQL")
            except Exception as e:
                if worker_id is not None:
                    worker_logger = get_logger(__name__, worker_id=worker_id)
                    worker_logger.warning("Error closing connection: %s", e)
                else:
                    logger.warning("Error closing connection: %s", e)

    def apply_configuration(
        self,
        connection: PostgresConnection,
        knob_config: Dict[str, Any],
        knob_applicator: KnobApplicator,
        force_restart: bool = False,
        generation: Optional[int] = None,
        worker_id: Optional[int] = None,
    ) -> bool:
        """Apply knob configuration and optionally restart via policy.

        This method applies knobs directly through KnobApplicator,
        then uses RestartPolicy (should_restart) for restart decisions,
        with env.restart_instance() for the actual restart mechanism.

        Parameters
        ----------
        connection : PostgresConnection
            Active connection to worker's instance
        knob_config : Dict[str, Any]
            Configuration parameters to apply
        knob_applicator : KnobApplicator
            Applicator for this worker's instance (legacy compat)
        force_restart : bool
            Force immediate restart regardless of mode/interval
        generation : Optional[int]
            Current generation number
        worker_id : Optional[int]
            Numeric worker ID

        Returns
        -------
        bool
            True if restart occurred during this application
        """
        worker_logger = (
            get_logger(__name__, worker_id=worker_id)
            if worker_id is not None
            else logger
        )

        try:
            result = knob_applicator.apply(knob_config)

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

                worker_logger.info(
                    "Restart required for %d parameters: %s",
                    len(restart_required_params),
                    first_three,
                )

            do_restart = should_restart(
                mode=self.config.tuning_mode,
                restart_required=restart_required,
                generation=generation,
                adaptive_restart_interval=self.config.adaptive_restart_interval,
                force=force_restart,
            )

            if do_restart:
                return self._perform_restart(connection, worker_id=worker_id)

            if restart_required and not do_restart:
                if self.config.tuning_mode == TuningMode.ADAPTIVE:
                    interval = self.config.adaptive_restart_interval
                    next_restart = (
                        ((generation // interval) + 1) * interval
                        if generation is not None
                        else interval
                    )
                    worker_logger.info(
                        "Deferring restart (will restart at generation %d)",
                        next_restart,
                    )
                elif self.config.tuning_mode == TuningMode.ONLINE:
                    worker_logger.debug(
                        "ONLINE mode: restart-required knobs written but restart skipped"
                    )

            return False

        except Exception as e:
            worker_logger.error("Failed to apply configuration: %s", e)
            raise

    def _perform_restart(
        self,
        connection: PostgresConnection,
        worker_id: Optional[int] = None,
    ) -> bool:
        """Restart PostgreSQL via the injected environment.

        Parameters
        ----------
        connection : PostgresConnection
            Connection to close before restart
        worker_id : Optional[int]
            Worker ID for environment restart and logging

        Returns
        -------
        bool
            True if restart succeeded
        """
        worker_logger = (
            get_logger(__name__, worker_id=worker_id)
            if worker_id is not None
            else logger
        )

        worker_logger.info("Restarting PostgreSQL instance...")

        try:
            # Close connection before restart
            try:
                if connection and not connection.closed:
                    connection.close()
            except (psycopg2.Error, AttributeError):
                pass

            wid = worker_id if worker_id is not None else 0
            if self.env.restart_instance(wid):
                worker_logger.info("Restart successful")

                return True
            else:
                worker_logger.error("Restart failed")
                return False

        except Exception as e:
            worker_logger.error("Restart failed with exception: %s", e)
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

        metrics["memory_utilization"] = self.env.collect_memory_utilization(wid)
        metrics["cache_hit_ratio"] = self.env.collect_cache_hit_ratio(wid)
        return metrics

    def _vacuum_after_dml(
        self, db_config: DatabaseConfig, worker_id: Optional[int] = None
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

        worker_logger = (
            get_logger(__name__, worker_id=worker_id)
            if worker_id is not None
            else logger
        )

        timeout_seconds = max(0.0, float(self.config.vacuum_analyze_timeout_seconds))
        if timeout_seconds <= 0:
            worker_logger.debug(
                "Skipping post-workload VACUUM ANALYZE (timeout disabled)"
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
                    "Skipping post-workload maintenance (no modified user tables)"
                )
                cursor.close()
                conn.close()
                return

            worker_logger.debug(
                "Running post-workload VACUUM ANALYZE on %d modified tables (statement_timeout=%sms, lock_timeout=%sms)",
                len(tables),
                statement_timeout_ms,
                lock_timeout_ms,
            )

            start = time.time()
            for schema_name, table_name in tables:
                table_start = time.time()
                try:
                    cursor.execute(
                        sql.SQL("VACUUM ANALYZE {}.{}").format(
                            sql.Identifier(schema_name),
                            sql.Identifier(table_name),
                        )
                    )
                    worker_logger.debug(
                        "VACUUM ANALYZE completed for %s.%s in %.2fs",
                        schema_name,
                        table_name,
                        time.time() - table_start,
                    )
                except Exception as table_error:
                    worker_logger.warning(
                        "Post-workload maintenance failed for %s.%s: %s",
                        schema_name,
                        table_name,
                        table_error,
                    )

            elapsed = time.time() - start

            worker_logger.debug(
                "Post-workload VACUUM ANALYZE completed in %.2fs", elapsed
            )
            cursor.close()
            conn.close()

        except Exception as e:
            worker_logger.warning("Post-workload VACUUM ANALYZE failed: %s", e)

    def _ensure_benchmark_ready(
        self,
        db_config: DatabaseConfig,
        worker_logger: Optional[logging.Logger] = None,
    ) -> None:
        """Validate benchmark state before execution and repair it if needed."""
        if not isinstance(self.workload_executor, BenchmarkExecutor):
            return

        worker_logger = worker_logger or logger

        try:
            benchmark_ready = self.workload_executor.validate(db_config)
        except Exception as e:
            worker_logger.warning(
                "Benchmark validation raised %s; attempting prepare()", e
            )
            benchmark_ready = False

        if benchmark_ready:
            return

        worker_logger.warning(
            "Benchmark state invalid; running prepare() before workload execution"
        )
        self.workload_executor.prepare(db_config)

        if not self.workload_executor.validate(db_config):
            raise RuntimeError("Benchmark validation still failing after prepare()")

        worker_logger.info("Benchmark state re-prepared successfully")

    def evaluate_worker(
        self,
        worker: Worker,
        apply_config: bool = True,
        generation: Optional[int] = None,
    ) -> tuple[PerformanceMetrics, float, bool]:
        """
        Evaluate a Worker's configuration.

        This is the main evaluation method called by Population.train_generation().

        Process:
        1. Apply worker's knob configuration (if apply_config=True)
        2. Execute workload with warmup and measurement phases
        3. Collect performance metrics
        4. Collect system metrics
        5. Compute composite performance score

        Parameters
        ----------
        worker : Worker
            Worker instance to evaluate
        apply_config : bool, default=True
            Whether to apply the worker's configuration
        generation : Optional[int]
            Current generation number (for restart cost calculation)

        Returns
        -------
        tuple[PerformanceMetrics, float, bool]
            (metrics, score, restart_occurred) tuple

        Example
        -------
        >>> metrics, score, restarted = evaluator.evaluate_worker(worker)
        >>> worker.update_metrics(metrics, score)
        """
        if not worker.db_config:
            raise ValueError(
                f"Worker {worker.worker_id} has no db_config set. "
                "Initialize workers with PostgresInstanceManager first."
            )

        worker_logger = get_logger(__name__, worker_id=worker.worker_id)
        worker_logger.info(
            "Evaluating configuration on instance port %d...", worker.port or 0
        )

        connection = None
        restart_occurred = False

        try:
            # Retry connection with backoff (handles instances in recovery mode)
            connection = self.connect(worker.db_config, max_retries=5, retry_delay=3.0)

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

                restart_occurred = self.apply_configuration(
                    connection=connection,
                    knob_config=worker.knob_config,
                    knob_applicator=knob_applicator,
                    force_restart=force_restart,
                    generation=generation,
                    worker_id=worker.worker_id,
                )

                if force_restart and restart_occurred:
                    worker.force_restart_next_eval = False
                    worker_logger.debug(
                        "Cleared forced-restart marker after successful restart"
                    )

                if restart_occurred:
                    self.disconnect(connection, worker_id=worker.worker_id)
                    # Retry connection after restart (instance may be in recovery)
                    connection = self.connect(
                        worker.db_config, max_retries=5, retry_delay=3.0
                    )
                    worker_logger.debug("Reconnected after restart")

                    if worker.knob_config:
                        verification = knob_applicator.verify(worker.knob_config)

                        failed_params = [k for k, v in verification.items() if not v]
                        if failed_params:
                            worker_logger.warning(
                                "Configuration verification failed for %d parameters: %s",
                                len(failed_params),
                                failed_params,
                            )

            try:
                stats_before = None
                if connection and not connection.closed:
                    try:
                        cursor = connection.cursor()
                        cursor.execute("""
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
                        """)
                        stats_before = cursor.fetchone()
                        cursor.close()
                    except Exception as e:
                        worker_logger.debug("Failed to capture initial stats: %s", e)

                if isinstance(self.workload_executor, BenchmarkExecutor):
                    self._ensure_benchmark_ready(
                        worker.db_config, worker_logger=worker_logger
                    )
                    metrics = self.workload_executor.execute(
                        db_config=worker.db_config,
                        worker_id=worker.worker_id,
                        random_seed=self.config.random_seed,
                        duration=self.config.measurement_duration,
                        warmup=self.config.warmup_duration,
                        warmup_passes=self.config.warmup_passes,
                    )
                else:
                    metrics = self.workload_executor.execute(
                        connection=connection,
                        duration=self.config.measurement_duration,
                        warmup=self.config.warmup_duration,
                        worker_id=worker.worker_id,
                        random_seed=self.config.random_seed,
                    )

                stats_after = None
                if connection and not connection.closed and stats_before:
                    try:
                        cursor = connection.cursor()
                        cursor.execute("""
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
                        """)
                        stats_after = cursor.fetchone()
                        cursor.close()

                        # Calculate I/O from database statistics (8KB blocks)
                        if stats_after:
                            blocks_read_delta = stats_after[0] - stats_before[0]

                            # Convert to MB (8KB blocks)
                            io_read_mb = (blocks_read_delta * 8) / 1024.0

                            # Store in metrics
                            metrics.io_read_mb = max(0, io_read_mb)

                    except Exception as e:
                        worker_logger.debug("Failed to capture final stats: %s", e)

            except Exception as e:
                worker_logger.error("Workload execution failed: %s", e)
                raise RuntimeError(f"Workload execution failed: {e}") from e

            system_metrics = self.collect_system_metrics(worker_id=worker.worker_id)

            if "cache_hit_ratio" in system_metrics:
                metrics.cache_hit_ratio = system_metrics["cache_hit_ratio"]
            if "memory_utilization" in system_metrics:
                metrics.memory_utilization = system_metrics["memory_utilization"]

            # Clean up dead tuples from DML operations to prevent bloat between generations
            self._vacuum_after_dml(worker.db_config, worker_id=worker.worker_id)

            score = self.config.metric_config.compute_score(metrics)

            return metrics, score, restart_occurred

        finally:
            self.disconnect(
                connection,
                worker_id=worker.worker_id if hasattr(worker, "worker_id") else None,
            )

    def __repr__(self) -> str:
        """String representation."""
        return (
            f"Evaluator(workload={self.config.workload_type.value}, "
            f"duration={self.config.measurement_duration}s)"
        )
