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
from typing import Dict, Any, Optional
from abc import ABC, abstractmethod
import logging
import time
import random
import psycopg2
from psycopg2.extensions import connection as PostgresConnection
import psutil

from src.database.connection import get_connection
from src.config.database import DatabaseConfig
from src.tuner.config import get_knob_space
from src.tuner.evaluator.metrics import (
    PerformanceMetrics,
    WorkloadType,
    MetricConfig,
)
from src.tuner.core.worker import Worker

logger = logging.getLogger(__name__)


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
    connection_params : Dict[str, Any]
        PostgreSQL connection parameters (host, port, dbname, user, password)
    warmup_queries : int
        Number of warmup queries before measurement (default: 100)
    measurement_duration : float
        Duration of measurement phase in seconds (default: 60.0)
    cooldown_duration : float
        Duration to wait after config change before evaluation (default: 5.0)
    """
    workload_type: WorkloadType
    metric_config: MetricConfig
    connection_params: Dict[str, Any]
    warmup_queries: int = 100
    measurement_duration: float = 60.0
    cooldown_duration: float = 5.0


class WorkloadExecutor(ABC):
    """
    Abstract base class for workload executors.
    
    Different workload types (SYSBENCH, TPC-H, custom) implement this interface
    to provide workload-specific execution logic.
    """

    @abstractmethod
    def execute(
        self,
        connection: PostgresConnection,
        duration: float,
        warmup: int = 0
    ) -> PerformanceMetrics:
        """
        Execute the workload and collect metrics.
        
        Parameters
        ----------
        connection : PostgresConnection
            Active PostgreSQL connection
        duration : float
            Measurement duration in seconds
        warmup : int
            Number of warmup queries
            
        Returns
        -------
        PerformanceMetrics
            Collected performance measurements
        """


class SysbenchOLTPExecutor(WorkloadExecutor):
    """
    SYSBENCH OLTP workload executor.
    
    Executes SYSBENCH's OLTP workload using simple queries:
    - Point selects
    - Range selects
    - Updates
    - Deletes
    - Inserts
    
    This is suitable for OLTP performance testing.
    """

    def __init__(
        self,
        table_size: int = 10000,
        num_threads: int = 4,
        read_write_ratio: float = 0.8,
    ):
        """
        Initialize SYSBENCH OLTP executor.
        
        Parameters
        ----------
        table_size : int
            Number of rows in test table
        num_threads : int
            Number of concurrent threads
        read_write_ratio : float
            Fraction of read operations (0.0 to 1.0)
        """
        self.table_size = table_size
        self.num_threads = num_threads
        self.read_write_ratio = read_write_ratio

    def execute(
        self,
        connection: PostgresConnection,
        duration: float,
        warmup: int = 0
    ) -> PerformanceMetrics:
        """
        Execute SYSBENCH OLTP workload.
        
        Implementation uses simple queries that mimic SYSBENCH patterns:
        - SELECT c FROM sbtest WHERE id=?
        - SELECT SUM(k) FROM sbtest WHERE id BETWEEN ? AND ?
        - UPDATE sbtest SET k=k+1 WHERE id=?
        - INSERT INTO sbtest VALUES (...)
        - DELETE FROM sbtest WHERE id=?
        """
        cursor = connection.cursor()

        logger.info("SYSBENCH warmup: %s queries", warmup)
        for _ in range(warmup):
            query_id = random.randint(1, self.table_size)
            try:
                if random.random() < self.read_write_ratio:
                    cursor.execute("SELECT * FROM sbtest1 WHERE id = %s", (query_id,))
                    cursor.fetchall()
                else:
                    cursor.execute("UPDATE sbtest1 SET k = k + 1 WHERE id = %s", (query_id,))
                connection.commit()
            except Exception as e:
                logger.warning("Warmup query failed: %s", e)
                connection.rollback()

        logger.info("SYSBENCH measurement: %ss", duration)
        start_time = time.time()
        latencies = []
        query_count = 0
        error_count = 0

        while (time.time() - start_time) < duration:
            query_start = time.time()
            query_id = random.randint(1, self.table_size)

            try:
                if random.random() < self.read_write_ratio:
                    cursor.execute("SELECT * FROM sbtest1 WHERE id = %s", (query_id,))
                    cursor.fetchall()
                else:
                    cursor.execute("UPDATE sbtest1 SET k = k + 1 WHERE id = %s", (query_id,))
                connection.commit()

                query_end = time.time()
                latencies.append((query_end - query_start) * 1000)  # Convert to ms
                query_count += 1

            except Exception as e:
                logger.warning("Query failed: %s", e)
                connection.rollback()
                error_count += 1
                query_count += 1

        total_time = time.time() - start_time
        cursor.close()

        if latencies:
            latencies_sorted = sorted(latencies)
            p50 = latencies_sorted[len(latencies_sorted) // 2]
            p95 = latencies_sorted[int(len(latencies_sorted) * 0.95)]
            p99 = latencies_sorted[int(len(latencies_sorted) * 0.99)]
        else:
            p50 = p95 = p99 = 0.0

        throughput = query_count / total_time if total_time > 0 else 0.0
        error_rate = error_count / query_count if query_count > 0 else 0.0

        return PerformanceMetrics(
            latency_p50=p50,
            latency_p95=p95,
            latency_p99=p99,
            throughput=throughput,
            total_queries=query_count,
            total_time=total_time,
            error_rate=error_rate,
        )


class CustomQueryExecutor(WorkloadExecutor):
    """
    Custom SQL query executor.
    
    Executes user-provided SQL queries for workload testing.
    Useful for application-specific workloads.
    """

    def __init__(self, queries: list[str], weights: Optional[list[float]] = None):
        """
        Initialize custom query executor.
        
        Parameters
        ----------
        queries : list[str]
            List of SQL queries to execute
        weights : Optional[list[float]]
            Execution frequency weights (default: uniform)
        """
        self.queries = queries
        self.weights = weights or [1.0] * len(queries)
        total = sum(self.weights)  # Normalize weights
        self.weights = [w / total for w in self.weights]

    def execute(
        self,
        connection: PostgresConnection,
        duration: float,
        warmup: int = 0
    ) -> PerformanceMetrics:
        """Execute custom queries and collect metrics."""
        cursor = connection.cursor()

        logger.info("Custom query warmup: %s queries", warmup)
        for _ in range(warmup):
            query = random.choices(self.queries, weights=self.weights)[0]
            try:
                cursor.execute(query)
                cursor.fetchall()
                connection.commit()
            except Exception as e:
                logger.warning("Warmup query failed: %s", e)
                connection.rollback()

        logger.info("Custom query measurement: %ss", duration)
        start_time = time.time()
        latencies = []
        query_count = 0
        error_count = 0

        while (time.time() - start_time) < duration:
            query = random.choices(self.queries, weights=self.weights)[0]
            query_start = time.time()

            try:
                cursor.execute(query)
                cursor.fetchall()
                connection.commit()

                query_end = time.time()
                latencies.append((query_end - query_start) * 1000)
                query_count += 1

            except Exception as e:
                logger.warning("Query failed: %s", e)
                connection.rollback()
                error_count += 1
                query_count += 1

        total_time = time.time() - start_time
        cursor.close()

        if latencies:
            latencies_sorted = sorted(latencies)
            p50 = latencies_sorted[len(latencies_sorted) // 2]
            p95 = latencies_sorted[int(len(latencies_sorted) * 0.95)]
            p99 = latencies_sorted[int(len(latencies_sorted) * 0.99)]
        else:
            p50 = p95 = p99 = 0.0

        throughput = query_count / total_time if total_time > 0 else 0.0
        error_rate = error_count / query_count if query_count > 0 else 0.0

        return PerformanceMetrics(
            latency_p50=p50,
            latency_p95=p95,
            latency_p99=p99,
            throughput=throughput,
            total_queries=query_count,
            total_time=total_time,
            error_rate=error_rate,
        )


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
    >>> from src.tuner.evaluator.metrics import WorkloadType, MetricConfig
    >>> 
    >>> config = EvaluatorConfig(
    ...     workload_type=WorkloadType.OLTP,
    ...     metric_config=MetricConfig.for_oltp(),
    ...     connection_params={
    ...         'host': 'localhost',
    ...         'port': 5432,
    ...         'dbname': 'testdb',
    ...         'user': 'postgres',
    ...         'password': 'password'
    ...     }
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
        workload_executor: WorkloadExecutor,
    ):
        """
        Initialize Evaluator.
        
        Parameters
        ----------
        config : EvaluatorConfig
            Evaluation configuration
        workload_executor : WorkloadExecutor
            Workload execution strategy
        """
        self.config = config
        self.workload_executor = workload_executor
        self.connection: Optional[PostgresConnection] = None

        logger.info(
            "Created Evaluator: workload=%s, duration=%ss",
            config.workload_type.value,
            config.measurement_duration
        )

    def connect(self) -> None:
        """
        Establish connection to PostgreSQL.
        
        Raises
        ------
        psycopg2.Error
            If connection fails
        """
        try:
            db_config = DatabaseConfig(**self.config.connection_params)
            self.connection = get_connection(config=db_config)
            self.connection.autocommit = False
            logger.info("Connected to PostgreSQL")
        except psycopg2.Error as e:
            logger.error("Failed to connect to PostgreSQL: %s", e)
            raise

    def disconnect(self) -> None:
        """Close PostgreSQL connection."""
        if self.connection:
            self.connection.close()
            self.connection = None
            logger.info("Disconnected from PostgreSQL")

    def apply_configuration(self, knob_config: Dict[str, Any]) -> None:
        """
        Apply knob configuration to PostgreSQL.
        
        This method applies configuration changes to the running PostgreSQL instance.
        Some parameters can be changed dynamically (via ALTER SYSTEM or SET),
        while others require a restart.
        
        Parameters
        ----------
        knob_config : Dict[str, Any]
            Configuration parameters to apply
        
        Note
        ----
        Currently implements dynamic parameter changes. For parameters requiring
        restart, consider using the KnobApplicator utility.
        """
        if not self.connection:
            raise RuntimeError("Not connected to PostgreSQL")

        cursor = self.connection.cursor()
        applied = []
        failed = []

        for knob, value in knob_config.items():
            try:
                cursor.execute(f"SET {knob} = %s", (value,))
                applied.append(knob)
            except psycopg2.Error as e:
                logger.warning("Failed to set %s=%s: %s", knob, value, e)
                failed.append(knob)

        self.connection.commit()
        cursor.close()
        logger.info("Applied %s parameters, %s failed", len(applied), len(failed))

        if self.config.cooldown_duration > 0:
            logger.debug("Cooldown: %ss", self.config.cooldown_duration)
            time.sleep(self.config.cooldown_duration)

    def _get_postgres_pid(self) -> Optional[int]:
        """
        Get the PostgreSQL backend process ID.
        
        Returns
        -------
        Optional[int]
            PostgreSQL backend PID, or None if unavailable
        """
        if not self.connection:
            return None

        try:
            cursor = self.connection.cursor()
            cursor.execute("SELECT pg_backend_pid()")
            result = cursor.fetchone()
            cursor.close()
            return int(result[0]) if result else None  # type: ignore
        except psycopg2.Error as e:
            logger.warning("Failed to get PostgreSQL PID: %s", e)
            return None

    def collect_system_metrics(self) -> Dict[str, float]:
        """
        Collect system-level metrics using psutil.
        
        Uses psutil to monitor PostgreSQL process for accurate CPU, memory,
        and I/O metrics. Also queries PostgreSQL for cache hit ratio.
        
        Returns
        -------
        Dict[str, float]
            System metrics:
            - cpu_utilization: CPU usage (0.0 to 1.0)
            - memory_utilization: Memory usage (0.0 to 1.0)
            - io_read_mb: Cumulative MB read from disk
            - io_write_mb: Cumulative MB written to disk
            - cache_hit_ratio: Buffer cache hit ratio (0.0 to 1.0)
        """
        if not self.connection:
            return {}

        metrics = {}

        # Get PostgreSQL backend PID for psutil monitoring
        postgres_pid = self._get_postgres_pid()

        if postgres_pid:
            try:
                postgres_process = psutil.Process(postgres_pid)

                cpu_percent = postgres_process.cpu_percent(interval=0.5)
                metrics['cpu_utilization'] = min(cpu_percent / 100.0, 1.0)  # Normalize to 0-1

                memory_percent = postgres_process.memory_percent()
                metrics['memory_utilization'] = min(memory_percent / 100.0, 1.0)

                try:
                    io_counters = postgres_process.io_counters()
                    metrics['io_read_mb'] = io_counters.read_bytes / (1024 * 1024)
                    metrics['io_write_mb'] = io_counters.write_bytes / (1024 * 1024)
                except (AttributeError, psutil.AccessDenied):
                    # I/O counters not available on all platforms (e.g., macOS)
                    logger.debug("I/O counters not available on this platform")
                    metrics['io_read_mb'] = 0.0
                    metrics['io_write_mb'] = 0.0

                logger.debug(
                    "System metrics: CPU=%.1f%%, Memory=%.1f%%, I/O Read=%.2fMB, I/O Write=%.2fMB",
                    cpu_percent, memory_percent,
                    metrics['io_read_mb'], metrics['io_write_mb']
                )

            except (psutil.NoSuchProcess, psutil.AccessDenied, psutil.ZombieProcess) as e:
                logger.warning("Failed to collect psutil metrics: %s", e)
                metrics['cpu_utilization'] = 0.0
                metrics['memory_utilization'] = 0.0
                metrics['io_read_mb'] = 0.0
                metrics['io_write_mb'] = 0.0
        else:
            logger.warning("PostgreSQL PID not available, using zero metrics")
            metrics['cpu_utilization'] = 0.0
            metrics['memory_utilization'] = 0.0
            metrics['io_read_mb'] = 0.0
            metrics['io_write_mb'] = 0.0

        # Cache hit ratio from PostgreSQL statistics
        cursor = self.connection.cursor()
        try:
            cursor.execute("""
                SELECT 
                    sum(blks_hit)::float / nullif(sum(blks_hit + blks_read), 0) as cache_hit_ratio
                FROM pg_stat_database
                WHERE datname = current_database()
            """)
            result = cursor.fetchone()
            metrics['cache_hit_ratio'] = float(result[0] or 0.0)  # type: ignore
        except psycopg2.Error as e:
            logger.warning("Failed to query cache hit ratio: %s", e)
            metrics['cache_hit_ratio'] = 0.0
        finally:
            cursor.close()

        return metrics

    def evaluate_worker(
        self,
        worker: Worker,
        apply_config: bool = True
    ) -> tuple[PerformanceMetrics, float]:
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
        
        Returns
        -------
        tuple[PerformanceMetrics, float]
            (metrics, score) tuple where score is the composite performance score
        
        Example
        -------
        >>> metrics, score = evaluator.evaluate_worker(worker)
        >>> worker.update_metrics(metrics, score)
        """
        logger.info("Evaluating Worker-%s", worker.worker_id)

        if not self.connection or self.connection.closed:
            self.connect()

        if apply_config:
            self.apply_configuration(worker.knob_config)  # type: ignore

        try:
            metrics = self.workload_executor.execute(
                connection=self.connection,  # type: ignore
                duration=self.config.measurement_duration,
                warmup=self.config.warmup_queries
            )
        except Exception as e:
            logger.error("Workload execution failed for Worker-%s: %s", worker.worker_id, e)
            metrics = PerformanceMetrics()

        system_metrics = self.collect_system_metrics()

        if 'cache_hit_ratio' in system_metrics:
            metrics.cache_hit_ratio = system_metrics['cache_hit_ratio']
        if 'cpu_utilization' in system_metrics:
            metrics.cpu_utilization = system_metrics['cpu_utilization']
        if 'memory_utilization' in system_metrics:
            metrics.memory_utilization = system_metrics['memory_utilization']
        if 'io_read_mb' in system_metrics:
            metrics.io_read_mb = system_metrics['io_read_mb']
        if 'io_write_mb' in system_metrics:
            metrics.io_write_mb = system_metrics['io_write_mb']

        score = self.config.metric_config.compute_score(metrics)
        logger.info(
            "Worker-%s: score=%.4f, throughput=%.2f, latency_p95=%.2fms",
            worker.worker_id, score, metrics.throughput, metrics.latency_p95
        )

        return metrics, score

    def evaluate_configuration(
        self,
        knob_config: Dict[str, Any]
    ) -> tuple[PerformanceMetrics, float]:
        """
        Evaluate a raw configuration (without Worker instance).
        
        Useful for one-off configuration testing.
        
        Parameters
        ----------
        knob_config : Dict[str, Any]
            Configuration to evaluate
        
        Returns
        -------
        tuple[PerformanceMetrics, float]
            (metrics, score) tuple
        """
        knob_space = get_knob_space('minimal')

        temp_worker = Worker(
            worker_id=-1,
            knob_space=knob_space,
            knob_config=knob_config
        )

        return self.evaluate_worker(temp_worker, apply_config=True)

    def __enter__(self):
        """Context manager entry - establish connection."""
        self.connect()
        return self

    def __exit__(self, exc_type, exc_val, exc_tb):
        """Context manager exit - close connection."""
        self.disconnect()

    def __repr__(self) -> str:
        """String representation."""
        return (
            f"Evaluator(workload={self.config.workload_type.value}, "
            f"duration={self.config.measurement_duration}s, "
            f"connected={self.connection is not None})"
        )
