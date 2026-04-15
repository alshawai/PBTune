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
from typing import Dict, Any, Optional, List, Union
from pathlib import Path
import json
import logging
import math
import time
import subprocess
import threading
from queue import Queue
import yaml
import numpy as np
import psycopg2
from psycopg2 import sql
from psycopg2.extensions import connection as PostgresConnection, register_adapter, AsIs
import psutil

from src.database.connection import get_connection
from src.config.database import DatabaseConfig
from src.tuner.config import get_knob_space
from src.utils.metrics import (
    PerformanceMetrics,
    WorkloadType,
    MetricConfig,
)
from src.tuner.evaluator.executor import BenchmarkExecutor
from src.tuner.core.worker import Worker
from src.utils.restart_manager import (
    RestartCostModel,
    PostgresRestartManager,
    RestartConfig,
)
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
    enable_restart : bool
        Enable automatic database restart for restart-required params (default: False)
    restart_interval : int
        Batch restarts every N generations (default: 10)
    restart_config : Optional[RestartConfig]
        Configuration for restart manager (default: None = auto-detect)
    random_seed : Optional[int]
        Optional random seed for reproducibility (default: None)
    vacuum_analyze_timeout_seconds : float
        Per-worker timeout for post-workload VACUUM ANALYZE safety maintenance.
        Prevents generation stalls when maintenance blocks or runs too long.
    """
    workload_type: WorkloadType
    metric_config: MetricConfig
    db_config: DatabaseConfig
    warmup_duration: float = 30.0
    measurement_duration: float = 60.0
    cooldown_duration: float = 5.0
    enable_restart: bool = False
    restart_interval: int = 10
    restart_config: Optional[RestartConfig] = None
    random_seed: Optional[int] = None
    warmup_passes: int = 0
    vacuum_analyze_timeout_seconds: float = 45.0


class WorkloadExecutor:
    """
    Template-based SQL query executor.
    
    Executes user-provided SQL queries for workload testing with optional
    parameterization and concurrent execution.
    Standard OLTP, OLAP, and MIXED workloads also use this executor via internal templates.
    """

    def __init__(
        self,
        queries: list[str],
        weights: Optional[list[float]] = None,
        table_size: int = 100000,
        num_tables: int = 10,
        num_threads: int = 8,
    ):
        """
        Initialize template workload executor.
        
        Parameters
        ----------
        queries : list[str]
            List of SQL queries to execute (can contain placeholders like {id}, {table}, etc.)
        weights : Optional[list[float]]
            Execution frequency weights (default: uniform)
        table_size : int
            Rows per table for parameter instantiation (default: 100K, academic standard)
        num_tables : int
            Number of sbtest tables to use (default: 10, academic standard)
        num_threads : int
            Number of concurrent threads (default: 8)
        """
        self.queries = queries
        self.weights = weights or [1.0] * len(queries)
        total = sum(self.weights)  # Normalize weights
        self.weights = [w / total for w in self.weights]
        self.table_size = table_size
        self.num_tables = num_tables
        self.num_threads = num_threads
        # Placeholder, will be seeded in execute() if random_seed is provided
        self.rng = np.random.default_rng()

    def _instantiate_query(self, template: str) -> str:
        """Instantiate query template with random parameters."""
        table_idx = self.rng.integers(1, self.num_tables)
        table2_idx = self.rng.integers(1, self.num_tables)
        params = {
            'table': f'sbtest{table_idx}',
            'table2': f'sbtest{table2_idx}',
            'id': self.rng.integers(1, self.table_size + 1),
            'k_val': self.rng.integers(1, self.table_size + 1),
            'threshold': self.rng.integers(self.table_size // 4, 3 * self.table_size // 4),
            'low': self.rng.integers(1, self.table_size // 2),
            'high': self.rng.integers(self.table_size // 2, self.table_size),
            'low_k': self.rng.integers(1, self.table_size // 2),
            'high_k': self.rng.integers(self.table_size // 2, self.table_size),
            'offset': self.rng.integers(0, self.table_size),
        }
        try:
            return template.format(**params)
        except KeyError:
            return template  # A template that doesn't need parameters

    def prepare(self, db_config: DatabaseConfig) -> None:
        """Create required sbtest tables using native sysbench C-binary."""
        logger.info(
            "Preparing %d sbtest tables (%d rows each) on %s:%s...",
            self.num_tables, self.table_size, db_config.host, db_config.port,
        )
        cmd = [
            "sysbench", "oltp_read_write",
            "--db-driver=pgsql",
            f"--pgsql-host={db_config.host}",
            f"--pgsql-port={db_config.port}",
            f"--pgsql-user={db_config.user}",
            f"--pgsql-password={db_config.password}",
            f"--pgsql-db={db_config.dbname}",
            f"--tables={self.num_tables}",
            f"--table-size={self.table_size}",
        ]
        subprocess.run(
            cmd + ["cleanup"],
            check=False, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
        )
        subprocess.run(cmd + ["prepare"], check=True, stdout=subprocess.DEVNULL)

        # Run VACUUM ANALYZE to stabilize statistics and prevent 'autoanalyze'
        # from randomly triggering during benchmark
        try:
            conn = get_connection(config=db_config)
            conn.autocommit = True
            cursor = conn.cursor()
            for i in range(1, self.num_tables + 1):
                cursor.execute(f"VACUUM ANALYZE sbtest{i}")
            cursor.close()
            conn.close()
            logger.debug("Successfully executed post-prepare VACUUM ANALYZE on all sbtest tables.")
        except Exception as e:
            logger.warning("Failed to post-vacuum workload tables: %s", e)

        logger.info("Schema preparation complete.")

    def validate(self, db_config: DatabaseConfig) -> bool:
        """Check if all required sbtest tables exist."""
        conn = get_connection(config=db_config)
        cursor = conn.cursor()
        cursor.execute(
            "SELECT count(*) FROM information_schema.tables "
            "WHERE table_schema = 'public' AND table_name LIKE 'sbtest%'"
        )
        count = cursor.fetchone()[0]  # type: ignore
        cursor.close()
        conn.close()
        return count >= self.num_tables

    def execute(
        self,
        connection: PostgresConnection,
        duration: float,
        warmup: float = 30.0,
        worker_id: Optional[int] = None,
        random_seed: Optional[int] = None
    ) -> PerformanceMetrics:
        """
        Execute template queries with optional concurrent execution.

        Parameters
        ----------
        connection : PostgresConnection
            Active database connection to execute queries on
        duration : float
            Duration of the measurement period
        warmup : float
            Warmup period duration
        worker_id : Optional[int]
            Worker ID for logging differentiation
        random_seed : Optional[int]
            Optional random seed for reproducibility

        Returns
        -------
            PerformanceMetrics
                Collected metrics from the execution
        """
        work_logger = (
            get_logger(__name__, worker_id=worker_id)
            if worker_id is not None else logger
        )

        # Use a dedicated random instance for reproducibility without affecting global RNG
        self.rng = np.random.default_rng(random_seed) if random_seed is not None else np.random

        if random_seed is not None:
            work_logger.debug("Seeded random with %d for reproducible workload", random_seed)

        cursor = connection.cursor()

        if warmup > 0:
            work_logger.debug("Template query warmup: %.1fs", warmup)
            warmup_end = time.time() + warmup
            while time.time() < warmup_end:
                template = self.rng.choice(self.queries, p=self.weights)
                query = self._instantiate_query(template)
                try:
                    cursor.execute(query)
                    if cursor.description is not None:
                        cursor.fetchall()
                    connection.commit()
                except Exception as e:
                    work_logger.warning("Warmup query failed: %s", e)
                    connection.rollback()
        cursor.close()

        if self.num_threads > 1:
            work_logger.debug(
                "Template query measurement: %ss with %d threads",
                duration,
                self.num_threads
            )
            return self._execute_concurrent(connection, duration, work_logger)

        work_logger.debug("Template query measurement: %ss (sequential)", duration)
        return self._execute_sequential(connection, duration, work_logger)

    def _execute_concurrent(
        self,
        connection: PostgresConnection,
        duration: float,
        work_logger
    ) -> PerformanceMetrics:
        """Execute queries with multiple concurrent threads."""
        db_params = {
            'host': connection.info.host,
            'port': connection.info.port,
            'dbname': connection.info.dbname,
            'user': connection.info.user,
            'password': connection.info.password if hasattr(connection.info, 'password') else None
        }

        results_queue = Queue()
        start_time = time.time()
        stop_event = threading.Event()

        def worker_thread():
            """Worker thread for concurrent query execution."""
            thread_latencies = []
            thread_errors = 0
            thread_queries = 0

            try:
                thread_conn = psycopg2.connect(
                    **{k: v for k, v in db_params.items() if v is not None}
                )
                thread_cursor = thread_conn.cursor()

                while not stop_event.is_set():
                    template = self.rng.choice(self.queries, p=self.weights)  # type: ignore
                    query = self._instantiate_query(template)
                    query_start = time.time()

                    try:
                        thread_cursor.execute(query)
                        if thread_cursor.description is not None:
                            thread_cursor.fetchall()
                        thread_conn.commit()

                        query_end = time.time()
                        thread_latencies.append((query_end - query_start) * 1000)
                        thread_queries += 1
                    except Exception:
                        thread_conn.rollback()
                        thread_errors += 1
                        thread_queries += 1

                thread_cursor.close()
                thread_conn.close()
            except Exception as e:
                work_logger.warning("Worker thread failed: %s", e)

            results_queue.put((thread_latencies, thread_queries, thread_errors))

        # Start concurrent threads
        threads = []
        for _ in range(self.num_threads):
            t = threading.Thread(target=worker_thread, daemon=True)
            t.start()
            threads.append(t)

        # Run for specified duration
        time.sleep(duration)
        stop_event.set()

        # Wait for threads
        for t in threads:
            t.join(timeout=5.0)

        total_time = time.time() - start_time

        # Aggregate results
        all_latencies = []
        total_queries = 0
        total_errors = 0

        while not results_queue.empty():
            thread_latencies, thread_queries, thread_errors = results_queue.get()
            all_latencies.extend(thread_latencies)
            total_queries += thread_queries
            total_errors += thread_errors

        if all_latencies:
            latencies_sorted = sorted(all_latencies)
            p50 = latencies_sorted[len(latencies_sorted) // 2]
            p95 = latencies_sorted[int(len(latencies_sorted) * 0.95)]
            p99 = latencies_sorted[int(len(latencies_sorted) * 0.99)]
        else:
            p50 = p95 = p99 = 0.0

        throughput = total_queries / total_time if total_time > 0 else 0.0
        error_rate = total_errors / total_queries if total_queries > 0 else 0.0

        return PerformanceMetrics(
            latency_p50=p50,
            latency_p95=p95,
            latency_p99=p99,
            throughput=throughput,
            total_queries=total_queries,
            total_time=total_time,
            error_rate=error_rate,
        )

    def _execute_sequential(
        self,
        connection: PostgresConnection,
        duration: float,
        work_logger
    ) -> PerformanceMetrics:
        """Execute queries sequentially."""
        cursor = connection.cursor()
        start_time = time.time()
        latencies = []
        query_count = 0
        error_count = 0

        while (time.time() - start_time) < duration:
            template = self.rng.choice(self.queries, p=self.weights)  # type: ignore
            query = self._instantiate_query(template)
            query_start = time.time()

            try:
                cursor.execute(query)
                if cursor.description is not None:
                    cursor.fetchall()
                connection.commit()

                query_end = time.time()
                latencies.append((query_end - query_start) * 1000)
                query_count += 1

            except Exception as e:
                work_logger.warning("Query failed: %s", e)
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


class WorkloadFileLoader:
    """
    Utility to load workload definitions from files.
    
    Supports JSON and YAML formats for defining custom workloads.
    Validates queries and provides helpful error messages.
    
    File Format (JSON):
    ------------------
    {
        "name": "My Custom Workload",
        "description": "Application-specific queries",
        "queries": [
            {
                "sql": "SELECT * FROM users WHERE id = {id}",
                "weight": 0.5,
                "description": "Point select"
            },
            {
                "sql": "SELECT COUNT(*) FROM orders WHERE status = 'pending'",
                "weight": 0.3
            }
        ]
    }
    
    File Format (YAML):
    ------------------
    name: My Custom Workload
    description: Application-specific queries
    queries:
      - sql: "SELECT * FROM users WHERE id = {id}"
        weight: 0.5
        description: "Point select"
      - sql: "SELECT COUNT(*) FROM orders WHERE status = 'pending'"
        weight: 0.3
    """

    @staticmethod
    def load_from_file(filepath: str) -> WorkloadExecutor:
        """
        Load workload from JSON or YAML file.
        
        Parameters
        ----------
        filepath : str
            Path to workload definition file (.json or .yaml/.yml)
        
        Returns
        -------
        WorkloadExecutor
            Configured executor with loaded queries
        
        Raises
        ------
        FileNotFoundError
            If file doesn't exist
        ValueError
            If file format is invalid or queries are malformed
        """
        filepath = Path(filepath)

        if not filepath.exists():
            raise FileNotFoundError(f"Workload file not found: {filepath}")

        if filepath.suffix == '.json':
            with open(filepath, 'r', encoding='utf-8') as f:
                data = json.load(f)
        elif filepath.suffix in ['.yaml', '.yml']:
            try:
                with open(filepath, 'r', encoding='utf-8') as f:
                    data = yaml.safe_load(f)
            except ImportError as exc:
                raise ImportError(
                    "PyYAML is required for YAML workload files. "
                    "Install with: pip install pyyaml"
                ) from exc
        else:
            raise ValueError(
                f"Unsupported file format: {filepath.suffix}. "
                f"Use .json, .yaml, or .yml"
            )

        if not isinstance(data, dict):
            raise ValueError("Workload file must contain a JSON object/YAML dict")

        if 'queries' not in data:
            raise ValueError("Workload file must contain 'queries' field")

        queries = data['queries']
        if not isinstance(queries, list) or len(queries) == 0:
            raise ValueError("'queries' must be a non-empty list")

        query_list = []
        weight_list = []

        for i, query_def in enumerate(queries):
            if isinstance(query_def, str):
                query_list.append(query_def)
                weight_list.append(1.0)
            elif isinstance(query_def, dict):
                if 'sql' not in query_def:
                    raise ValueError(f"Query {i} missing 'sql' field")

                query_list.append(query_def['sql'])
                weight_list.append(query_def.get('weight', 1.0))
            else:
                raise ValueError(
                    f"Query {i} must be a string or dict with 'sql' field"
                )

        name = data.get('name', filepath.stem)
        description = data.get('description', 'Custom workload')

        schema = data.get('schema', {})
        num_tables = schema.get('tables', 1)
        table_size = schema.get('table_size', 100000)

        if not schema:
            logger.warning(
                "Workload '%s' has no 'schema' section — defaulting to 1 table "
                "with 100K rows. Add a 'schema' section for multi-table support.",
                name,
            )

        logger.debug(
            "-> Loaded workload '%s': %s (%d queries, %d tables × %d rows)\n",
            name, description, len(query_list), num_tables, table_size,
        )

        return WorkloadExecutor(
            queries=query_list,
            weights=weight_list,
            num_tables=num_tables,
            table_size=table_size,
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
        worker_id: Optional[str] = None,
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
        self.worker_id = worker_id or "Evaluator"

        self.restart_cost_model = RestartCostModel(
            base_restart_time=7.0,
            cache_warmup_ratio=0.1,
            restart_interval=config.restart_interval
        )

        self.applicator_config = ApplicatorConfig(
            auto_restart=False,  # We handle restart manually per instance
            rollback_on_error=False
        )

        logger.debug(
            "✓ Created Evaluator: workload=%s, duration=%ss",
            config.workload_type.value.upper(),
            config.measurement_duration
        )

    def connect(
            self,
            db_config: Optional[DatabaseConfig] = None,
            max_retries: int = 1,
            retry_delay: float = 2.0
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
                    "starting up" in error_msg or
                    "not yet accepting connections" in error_msg or
                    "consistent recovery state" in error_msg or
                    ("connection refused" in error_msg and "is the server running" in error_msg)
                ):
                    if attempt < max_retries:
                        logger.warning(
                            "Database recovering, retry %d/%d in %.1fs...",
                            attempt,
                            max_retries,
                            retry_delay
                        )
                        time.sleep(retry_delay)
                        continue

                # Non-recoverable error or last attempt
                logger.error("Failed to connect to PostgreSQL: %s", e)
                raise

        logger.error("Failed to connect after %d attempts: %s", max_retries, last_error)
        raise last_error  # type: ignore

    def disconnect(
        self,
        connection: Optional[PostgresConnection],
        worker_id: Optional[int] = None
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
        restart_manager: Optional[PostgresRestartManager],
        worker_log_id: str,
        force_restart: bool = False,
        generation: Optional[int] = None,
        restart_interval: int = 10,
        worker_id: Optional[int] = None,
    ) -> bool:
        """
        Apply knob configuration to worker's PostgreSQL instance.
        
        This method separates parameters into:
        1. Runtime params: Applied immediately via SET/ALTER SYSTEM
        2. Restart params: Batched and applied only on restart intervals
        
        Parameters
        ----------
        connection : PostgresConnection
            Active connection to worker's instance
        knob_config : Dict[str, Any]
            Configuration parameters to apply
        knob_applicator : KnobApplicator
            Applicator for this worker's instance
        restart_manager : Optional[PostgresRestartManager]
            Restart manager for this worker's instance
        worker_log_id : str
            Worker identifier for logging
        force_restart : bool
            Force immediate restart regardless of params
        generation : Optional[int]
            Current generation number (for restart interval checking)
        restart_interval : int, default=10
            Only restart every N generations (to batch restart cost)
        
        Returns
        -------
        bool
            True if restart occurred during this application
        """
        restart_occurred = False

        # Create worker logger for consistent [Worker-X] formatting and colors
        worker_logger = (
            get_logger(__name__, worker_id=worker_id)
            if worker_id is not None else logger
        )

        try:
            result = knob_applicator.apply(knob_config)

            # Don't log here - we'll log after verification instead

            # If restart-required params changed, check if we should restart this generation
            if result.restart_required and len(result.restart_required) > 0:
                worker_logger.info(
                    "Restart required for %d parameters: %s",
                    len(result.restart_required),
                    list(result.restart_required)
                )

                # Only restart every restart_interval generations (batching strategy)
                should_restart = generation is not None and (generation % restart_interval == 0)

                if should_restart:
                    if restart_manager:
                        worker_logger.info(
                            "Restarting (generation %d is restart interval)",
                            generation
                        )
                        restart_occurred = self._perform_restart(
                            connection, restart_manager, worker_log_id, worker_id=worker_id
                        )
                        if not restart_occurred:
                            raise ConnectionError(
                                "PostgreSQL restart failed; worker marked as dead-config candidate"
                            )
                    else:
                        worker_logger.warning(
                            "Restart needed but restart_manager not configured"
                        )
                else:
                    worker_logger.info(
                        "Deferring restart (will restart at generation %d)",
                        ((generation // restart_interval) + 1) * restart_interval
                        if generation is not None else restart_interval
                    )
            elif force_restart and restart_manager:
                restart_occurred = self._perform_restart(
                    connection, restart_manager, worker_log_id, worker_id=worker_id
                )
                if not restart_occurred:
                    raise ConnectionError(
                        "Forced PostgreSQL restart failed; worker marked as dead-config candidate"
                    )

        except Exception as e:
            worker_logger.error("Failed to apply configuration: %s", e)
            raise

        return restart_occurred

    def _perform_restart(
        self,
        connection: PostgresConnection,
        restart_manager: PostgresRestartManager,
        worker_log_id: str,
        worker_id: Optional[int] = None
    ) -> bool:
        """
        Restart the PostgreSQL instance for this worker.
        
        Parameters
        ----------
        connection : PostgresConnection
            Connection to close/reconnect after restart
        restart_manager : PostgresRestartManager
            Manager for this instance
        worker_log_id : str
            Worker ID for logging
        worker_id : Optional[int]
            Numeric worker ID for logger context
        
        Returns
        -------
        bool
            True if restart succeeded
        """
        # Create worker logger for consistent [Worker-X] formatting and colors
        worker_logger = (
            get_logger(__name__, worker_id=worker_id)
            if worker_id is not None else logger
        )

        worker_logger.info("Restarting PostgreSQL instance...")

        try:
            # Close connection before restart
            try:
                if connection and not connection.closed:
                    connection.close()
            except (psycopg2.Error, AttributeError):
                pass

            # Restart the instance
            if restart_manager.restart():
                worker_logger.info("Restart successful")

                # Reset statistics after restart
                try:
                    temp_conn = get_connection(config=restart_manager.db_config)
                    cursor = temp_conn.cursor()
                    cursor.execute("SELECT pg_stat_reset()")
                    cursor.fetchone()
                    cursor.close()
                    temp_conn.commit()
                    temp_conn.close()
                    worker_logger.debug("Reset PostgreSQL statistics")
                except Exception as e:
                    worker_logger.warning("Failed to reset statistics: %s", e)

                return True
            else:
                worker_logger.error("Restart failed")
                return False

        except Exception as e:
            worker_logger.error("Restart failed with exception: %s", e)
            return False

    def _verify_configuration(
        self,
        connection: PostgresConnection,
        expected_config: Dict[str, Any],
        worker_log_id: str,
        worker_id: Optional[int] = None
    ) -> Dict[str, bool]:
        """
        Verify that configuration parameters were actually applied.
        
        Parameters
        ----------
        connection : PostgresConnection
            Active connection to query current settings
        expected_config : Dict[str, Any]
            Configuration that should be applied
        worker_log_id : str
            Worker identifier for logging
        worker_id : Optional[int]
            Numeric worker ID for logger context
        
        Returns
        -------
        Dict[str, bool]
            Parameter name -> verification status
        """
        # Create worker logger for consistent [Worker-X] formatting and colors
        worker_logger = (
            get_logger(__name__, worker_id=worker_id)
            if worker_id is not None else logger
        )
        verification = {}
        mismatches = []

        try:
            cursor = connection.cursor()

            for param_name, expected_value in expected_config.items():
                try:
                    # Query current value
                    cursor.execute(
                        "SELECT setting, unit, vartype FROM pg_settings WHERE name = %s",
                        (param_name,)
                    )
                    result = cursor.fetchone()

                    if not result:
                        worker_logger.warning(
                            "[%s] Parameter '%s' not found in pg_settings",
                            worker_log_id, param_name
                        )
                        verification[param_name] = False
                        continue

                    current_value_str, unit, vartype = result

                    # Convert current value to comparable type
                    if isinstance(expected_value, bool):
                        current_value = current_value_str.lower() in ('on', 'true', '1')
                        match = current_value == expected_value
                    elif isinstance(expected_value, (int, float)):
                        current_value = float(current_value_str)
                        expected_float = float(expected_value)

                        # pg_settings may coerce or quantize numeric settings on apply.
                        # Compare by PostgreSQL vartype semantics, not a fixed tiny delta.
                        if vartype == 'integer':
                            current_int = int(round(current_value))
                            expected_int = int(round(expected_float))
                            current_value = current_int
                            match = current_int == expected_int
                        else:
                            abs_tolerance = max(0.01, abs(expected_float) * 1e-6)
                            match = math.isclose(
                                current_value,
                                expected_float,
                                rel_tol=1e-6,
                                abs_tol=abs_tolerance,
                            )
                    else:
                        current_value = current_value_str
                        # `wal_compression` became an enum on new versions of postgres (on -> pglz)
                        if (
                            param_name == "wal_compression" and
                            str(expected_value).lower() in ("on", "true")
                            and str(current_value).lower() in ("on", "pglz")
                        ):
                            match = True
                        else:
                            match = str(current_value) == str(expected_value)

                    verification[param_name] = match

                    if not match:
                        mismatches.append(
                            f"{param_name}: expected={expected_value}, actual={current_value}"
                        )

                except Exception as e:
                    worker_logger.warning(
                        "Failed to verify parameter '%s': %s",
                        param_name, e
                    )
                    verification[param_name] = False

            cursor.close()

            # Log results
            verified_count = sum(verification.values())
            total_count = len(verification)

            if verified_count == total_count:
                worker_logger.debug(
                    "Configuration verified: %d/%d parameters correct",
                    verified_count, total_count
                )
            else:
                worker_logger.warning(
                    "Configuration mismatch: %d/%d parameters verified",
                    verified_count, total_count
                )
                for mismatch in mismatches:
                    worker_logger.warning("  %s", mismatch)

        except Exception as e:
            worker_logger.error(
                "Configuration verification failed: %s",
                e
            )

        return verification

    def _get_postgres_pid(self, connection: PostgresConnection) -> Optional[int]:
        """
        Get the PostgreSQL backend process ID.
        
        Parameters
        ----------
        connection : PostgresConnection
            Active connection to query
        
        Returns
        -------
        Optional[int]
            PostgreSQL backend PID, or None if unavailable
        """
        if not connection or connection.closed:
            return None

        try:
            cursor = connection.cursor()
            cursor.execute("SELECT pg_backend_pid()")
            result = cursor.fetchone()
            cursor.close()
            return int(result[0]) if result else None  # type: ignore
        except psycopg2.Error as e:
            logger.warning("Failed to get PostgreSQL PID: %s", e)
            return None

    def _get_postmaster_pid(self, port: int, worker_id: Optional[int] = None) -> Optional[int]:
        """
        Find the PostgreSQL postmaster (main server) PID by port.
        
        The postmaster is the parent PostgreSQL process that manages all backends.
        This is the correct process to monitor for CPU/Memory usage.
        
        Parameters
        ----------
        port : int
            PostgreSQL port number
        
        Returns
        -------
        Optional[int]
            Postmaster PID, or None if not found
        """
        try:
            # Find all postgres processes
            for proc in psutil.process_iter(['pid', 'name']):
                try:
                    proc_name = proc.info.get('name', '')
                    if not proc_name or 'postgres' not in proc_name.lower():
                        continue

                    # Check if this process is listening on the target port
                    connections = proc.connections(kind='inet')
                    for conn in connections:
                        if (conn.status == psutil.CONN_LISTEN and 
                            conn.laddr.port == port):
                            if worker_id is not None:
                                worker_logger = get_logger(__name__, worker_id=worker_id)
                                worker_logger.debug(
                                    "Found postmaster PID %d for port %d", 
                                    proc.info['pid'], port
                                )
                            else:
                                logger.debug(
                                    "Found postmaster PID %d for port %d", 
                                    proc.info['pid'], port
                                )
                            return proc.info['pid']

                except (psutil.NoSuchProcess, psutil.AccessDenied, psutil.ZombieProcess):
                    continue

        except Exception as e:
            logger.debug("Error finding postmaster PID via psutil: %s", e)

        # Fallback for Docker instances: find container mapping this port and get its State.Pid
        try:
            import docker
            client = docker.from_env()
            for container in client.containers.list():
                ports = container.attrs.get('NetworkSettings', {}).get('Ports', {})
                for container_port, host_bindings in ports.items():
                    if host_bindings:
                        for binding in host_bindings:
                            if str(binding.get('HostPort')) == str(port):
                                pid = container.attrs['State']['Pid']
                                if worker_id is not None:
                                    worker_logger = get_logger(__name__, worker_id=worker_id)
                                    worker_logger.debug(
                                        "Found docker container postmaster PID %d for port %d", 
                                        pid, port
                                    )
                                else:
                                    logger.debug(
                                        "Found docker container postmaster PID %d for port %d", 
                                        pid, port
                                    )
                                return pid
        except Exception as docker_exc:
            logger.debug("Error finding postmaster PID via Docker: %s", docker_exc)

        logger.warning("Could not find PostgreSQL postmaster for port %d", port)

        return None

    def _get_all_postgres_processes(
        self,
        postmaster_pid: int,
        worker_id: Optional[int] = None
    ) -> List[psutil.Process]:
        """
        Get all PostgreSQL processes (postmaster + backends) for a specific instance.
        
        On Windows, PostgreSQL spawns separate processes for each connection.
        We need to measure CPU across all of them, not just the postmaster.
        
        Parameters
        ----------
        postmaster_pid : int
            Postmaster (parent) process PID
        
        Returns
        -------
        List[psutil.Process]
            List of all postgres processes (parent + children)
        """
        processes = []

        try:
            postmaster = psutil.Process(postmaster_pid)
            processes.append(postmaster)

            # Get all child processes (backend workers)
            children = postmaster.children(recursive=True)
            processes.extend(children)

            if worker_id is not None:
                worker_logger = get_logger(__name__, worker_id=worker_id)
                worker_logger.debug(
                    "Found %d PostgreSQL processes (1 postmaster + %d backends) for PID %d",
                    len(processes), len(children), postmaster_pid
                )
            else:
                logger.debug(
                    "Found %d PostgreSQL processes (1 postmaster + %d backends) for PID %d",
                    len(processes), len(children), postmaster_pid
                )

        except (psutil.NoSuchProcess, psutil.AccessDenied) as e:
            logger.warning("Failed to get PostgreSQL processes: %s", e)

        return processes

    def collect_system_metrics(
        self,
        connection: PostgresConnection,
        port: Optional[int] = None,
        worker_id: Optional[int] = None
    ) -> Dict[str, float]:
        """
        Collect system-level metrics using PostgreSQL statistics and psutil for memory.
        
        Parameters
        ----------
        connection : PostgresConnection
            Active connection to the PostgreSQL instance
        port : Optional[int]
            PostgreSQL port number (for finding postmaster PID)
        worker_id : Optional[int]
            Worker ID for logging context
        
        Returns
        -------
        Dict[str, float]
            System metrics including memory and cache hit ratio
            
        Notes
        -----
        CPU and I/O metrics are collected separately during workload execution
        using PostgreSQL's built-in statistics (more accurate than psutil on Windows).
        """
        if not connection or connection.closed:
            logger.warning("Connection not available for system metrics collection")
            return {
                'memory_utilization': 0.0,
                'cache_hit_ratio': 0.0
            }

        metrics = {}

        # Measure memory usage via psutil (only reliable cross-process metric)
        if port:
            postgres_pid = self._get_postmaster_pid(port, worker_id=worker_id)
            if postgres_pid:
                try:
                    postgres_processes = self._get_all_postgres_processes(
                        postgres_pid,
                        worker_id=worker_id
                    )
                    total_memory_rss = 0

                    for proc in postgres_processes:
                        try:
                            mem_info = proc.memory_info()
                            total_memory_rss += mem_info.rss
                        except (psutil.NoSuchProcess, psutil.AccessDenied, psutil.ZombieProcess):
                            continue

                    # Memory percentage
                    try:
                        system_memory = psutil.virtual_memory().total
                        metrics['memory_utilization'] = total_memory_rss / system_memory
                        memory_mb = total_memory_rss / (1024 * 1024)
                        memory_pct = metrics['memory_utilization'] * 100
                        if worker_id is not None:
                            worker_logger = get_logger(__name__, worker_id=worker_id)
                            worker_logger.debug(
                                "Memory: %.1fMB (%.1f%%) across %d processes",
                                memory_mb, memory_pct, len(postgres_processes)
                            )
                        else:
                            logger.debug(
                                "Memory: %.1fMB (%.1f%%) across %d processes",
                                memory_mb, memory_pct, len(postgres_processes)
                            )
                    except:
                        metrics['memory_utilization'] = 0.0

                except Exception as e:
                    logger.warning("Failed to collect memory metrics: %s", e)
                    metrics['memory_utilization'] = 0.0
            else:
                metrics['memory_utilization'] = 0.0
        else:
            metrics['memory_utilization'] = 0.0

        # Get cache hit ratio from PostgreSQL statistics
        if connection and not connection.closed:
            cursor = connection.cursor()
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
        else:
            metrics['cache_hit_ratio'] = 0.0

        return metrics

    def _vacuum_after_dml(
        self,
        db_config: DatabaseConfig,
        worker_id: Optional[int] = None
    ) -> None:
        """
        Run bounded post-workload maintenance after DML-heavy workloads.
        
        Full-database VACUUM ANALYZE is too expensive for short sysbench-style
        generations and frequently times out while scanning toast/system tables.
        Instead, analyze only user tables that were actually modified.
        """
        # Skip for read-only workloads (OLAP, TPC-H)
        if self.config.workload_type.value in ('olap', 'tpch'):
            return
        
        worker_logger = (
            get_logger(__name__, worker_id=worker_id)
            if worker_id is not None else logger
        )

        timeout_seconds = max(0.0, float(self.config.vacuum_analyze_timeout_seconds))
        if timeout_seconds <= 0:
            worker_logger.debug("Skipping post-workload VACUUM ANALYZE (timeout disabled)")
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
                worker_logger.debug("Skipping post-workload maintenance (no modified user tables)")
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

            worker_logger.debug("Post-workload VACUUM ANALYZE completed in %.2fs", elapsed)
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
            worker_logger.warning("Benchmark validation raised %s; attempting prepare()", e)
            benchmark_ready = False

        if benchmark_ready:
            return

        worker_logger.warning("Benchmark state invalid; running prepare() before workload execution")
        self.workload_executor.prepare(db_config)

        if not self.workload_executor.validate(db_config):
            raise RuntimeError("Benchmark validation still failing after prepare()")

        worker_logger.info("Benchmark state re-prepared successfully")

    def evaluate_worker(
        self,
        worker: Worker,
        apply_config: bool = True,
        generation: Optional[int] = None
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
            raise ValueError(f"Worker {worker.worker_id} has no db_config set. "
                           "Initialize workers with PostgresInstanceManager first.")

        worker_log_id = f"Worker-{worker.worker_id}"
        worker_logger = get_logger(__name__, worker_id=worker.worker_id)
        worker_logger.info("Evaluating configuration on instance port %d...", worker.port or 0)

        connection = None
        restart_occurred = False

        try:
            # Retry connection with backoff (handles instances in recovery mode)
            connection = self.connect(worker.db_config, max_retries=5, retry_delay=3.0)

            if apply_config and worker.knob_config:
                knob_applicator = KnobApplicator(
                    db_config=worker.db_config,
                    config=self.applicator_config,
                    worker_id=worker.worker_id
                )

                restart_manager = None
                if self.config.enable_restart:
                    import copy
                    # Create a specific config for this worker
                    worker_restart_config = copy.deepcopy(self.config.restart_config or RestartConfig())
                    
                    # If in docker or auto-detect, ensure we use docker method with correct name
                    if worker_restart_config.method in ['docker', 'auto']:
                        worker_restart_config.container_name = f"pbt-worker-{worker.worker_id}"
                        # Disable local backup as worker files aren't mounted in tuner
                        worker_restart_config.backup_enabled = False
                        worker_restart_config.method = 'docker'

                    restart_manager = PostgresRestartManager(
                        db_config=worker.db_config,
                        restart_config=worker_restart_config,
                        worker_id=worker.worker_id
                    )

                restart_occurred = self.apply_configuration(
                    connection=connection,
                    knob_config=worker.knob_config,
                    knob_applicator=knob_applicator,
                    restart_manager=restart_manager,
                    worker_log_id=worker_log_id,
                    force_restart=False,
                    generation=generation,
                    restart_interval=self.config.restart_interval,
                    worker_id=worker.worker_id
                )

                if restart_occurred:
                    self.disconnect(connection, worker_id=worker.worker_id)
                    # Retry connection after restart (instance may be in recovery)
                    connection = self.connect(worker.db_config, max_retries=5, retry_delay=3.0)
                    worker_logger.debug("Reconnected after restart")

                    if worker.knob_config:
                        verification = self._verify_configuration(
                            connection=connection,
                            expected_config=worker.knob_config,
                            worker_log_id=worker_log_id,
                            worker_id=worker.worker_id
                        )

                        failed_params = [k for k, v in verification.items() if not v]
                        if failed_params:
                            worker_logger.warning(
                                "Configuration verification failed for %d parameters: %s",
                                len(failed_params), failed_params
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
                    self._ensure_benchmark_ready(worker.db_config, worker_logger=worker_logger)
                    metrics = self.workload_executor.execute(
                        db_config=worker.db_config,
                        worker_id=worker.worker_id,
                        random_seed=self.config.random_seed,
                        duration=self.config.measurement_duration,
                        warmup=self.config.warmup_duration,
                        warmup_passes=self.config.warmup_passes
                    )
                else:
                    metrics = self.workload_executor.execute(
                        connection=connection,
                        duration=self.config.measurement_duration,
                        warmup=self.config.warmup_duration,
                        worker_id=worker.worker_id,
                        random_seed=self.config.random_seed
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
                            blocks_hit_delta = stats_after[1] - stats_before[1]

                            # Convert to MB (8KB blocks)
                            io_read_mb = (blocks_read_delta * 8) / 1024.0
                            total_io_mb = ((blocks_read_delta + blocks_hit_delta) * 8) / 1024.0

                            # Store in metrics
                            metrics.io_read_mb = max(0, io_read_mb)

                    except Exception as e:
                        worker_logger.debug("Failed to capture final stats: %s", e)

            except Exception as e:
                worker_logger.error("Workload execution failed: %s", e)
                raise RuntimeError(f"Workload execution failed: {e}") from e

            system_metrics = self.collect_system_metrics(
                connection,
                port=worker.port,
                worker_id=worker.worker_id
            )

            if 'cache_hit_ratio' in system_metrics:
                metrics.cache_hit_ratio = system_metrics['cache_hit_ratio']
            if 'memory_utilization' in system_metrics:
                metrics.memory_utilization = system_metrics['memory_utilization']

            # Clean up dead tuples from DML operations to prevent bloat between generations
            self._vacuum_after_dml(worker.db_config, worker_id=worker.worker_id)

            base_score = self.config.metric_config.compute_score(metrics)

            adjusted_score = self.restart_cost_model.apply_penalty(
                score=base_score,
                measurement_duration=self.config.measurement_duration,
                restart_occurred=restart_occurred,
                generation=generation,
                logger=worker_logger
            )

            return metrics, adjusted_score, restart_occurred

        finally:
            self.disconnect(
                connection,
                worker_id=worker.worker_id
                if hasattr(worker, 'worker_id') else None
            )

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

        metrics, score, _ = self.evaluate_worker(temp_worker, apply_config=True)
        return metrics, score

    def __repr__(self) -> str:
        """String representation."""
        return (
            f"Evaluator(workload={self.config.workload_type.value}, "
            f"duration={self.config.measurement_duration}s)"
        )
