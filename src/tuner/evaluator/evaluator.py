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
from typing import Dict, Any, Optional, List
from abc import ABC, abstractmethod
from pathlib import Path
import json
import logging
import time
import random
import yaml
import numpy as np
import psycopg2
from psycopg2.extensions import connection as PostgresConnection, register_adapter, AsIs
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
from src.tuner.utils.restart_manager import (
    RestartCostModel,
    PostgresRestartManager,
    RestartConfig,
)
from src.tuner.utils.applicator import KnobApplicator, ApplicatorConfig
from src.tuner.utils.logger_config import get_logger

logger = logging.getLogger(__name__)

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
    warmup_queries : int
        Number of warmup queries before measurement (default: 100)
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
    """
    workload_type: WorkloadType
    metric_config: MetricConfig
    db_config: DatabaseConfig
    warmup_queries: int = 100
    measurement_duration: float = 60.0
    cooldown_duration: float = 5.0
    enable_restart: bool = False
    restart_interval: int = 10
    restart_config: Optional[RestartConfig] = None


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
        warmup: int = 0,
        worker_id: Optional[int] = None
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
        worker_id : Optional[int]
            Worker ID for logging context
            
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
        warmup: int = 0,
        worker_id: Optional[int] = None
    ) -> PerformanceMetrics:
        """
        Execute SYSBENCH OLTP workload with concurrent threads.
        
        Implementation uses simple queries that mimic SYSBENCH patterns:
        - SELECT c FROM sbtest WHERE id=?
        - SELECT SUM(k) FROM sbtest WHERE id BETWEEN ? AND ?
        - UPDATE sbtest SET k=k+1 WHERE id=?
        - INSERT INTO sbtest VALUES (...)
        - DELETE FROM sbtest WHERE id=?
        """
        work_logger = get_logger(__name__, worker_id=worker_id) if worker_id is not None else logger

        db_params = {
            'host': connection.info.host,
            'port': connection.info.port,
            'dbname': connection.info.dbname,
            'user': connection.info.user,
            'password': connection.info.password if hasattr(connection.info, 'password') else None
        }

        cursor = connection.cursor()
        work_logger.debug("SYSBENCH warmup: %s queries", warmup)
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
                work_logger.warning("Warmup query failed: %s", e)
                connection.rollback()
        cursor.close()

        # Multi-threaded measurement phase
        work_logger.debug("SYSBENCH measurement: %ss with %d threads", duration, self.num_threads)
        
        import threading
        from queue import Queue
        
        # Shared results queue
        results_queue = Queue()
        start_time = time.time()
        stop_event = threading.Event()
        
        def worker_thread():
            """Worker thread executing queries concurrently."""
            thread_latencies = []
            thread_errors = 0
            thread_queries = 0
            
            try:
                # Each thread creates its own connection
                import psycopg2
                thread_conn = psycopg2.connect(**{k: v for k, v in db_params.items() if v is not None})
                thread_cursor = thread_conn.cursor()
                
                while not stop_event.is_set():
                    query_start = time.time()
                    query_id = random.randint(1, self.table_size)
                    
                    try:
                        if random.random() < self.read_write_ratio:
                            # Mix of point queries (70%) and range queries (30%) for reads
                            if random.random() < 0.7:
                                thread_cursor.execute("SELECT * FROM sbtest1 WHERE id = %s", (query_id,))
                            else:
                                # Range query: scan 100 rows
                                range_end = min(query_id + 100, self.table_size)
                                thread_cursor.execute("SELECT * FROM sbtest1 WHERE id BETWEEN %s AND %s", (query_id, range_end))
                            thread_cursor.fetchall()
                        else:
                            thread_cursor.execute("UPDATE sbtest1 SET k = k + 1 WHERE id = %s", (query_id,))
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
                work_logger.warning("Thread worker failed: %s", e)
            
            # Return results
            results_queue.put((thread_latencies, thread_queries, thread_errors))
        
        # Start worker threads
        threads = []
        for _ in range(self.num_threads):
            t = threading.Thread(target=worker_thread, daemon=True)
            t.start()
            threads.append(t)
        
        # Run for specified duration
        time.sleep(duration)
        stop_event.set()
        
        # Wait for threads to finish
        for t in threads:
            t.join(timeout=5.0)
        
        total_time = time.time() - start_time
        
        # Aggregate results from all threads
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


class TPCHOLAPExecutor(WorkloadExecutor):
    """
    TPC-H inspired OLAP workload executor.
    
    Executes analytical queries similar to TPC-H benchmark:
    - Aggregations (COUNT, SUM, AVG, MIN, MAX)
    - GROUP BY operations
    - Range scans
    - Sorting and limiting
    - Complex analytical patterns
    
    This is suitable for OLAP (Online Analytical Processing) testing.
    """

    def __init__(
        self,
        table_size: int = 10000,
        complexity_mix: str = "balanced",  # 'simple', 'balanced', 'complex'
    ):
        """
        Initialize TPC-H OLAP executor.
        
        Parameters
        ----------
        table_size : int
            Number of rows in test table
        complexity_mix : str
            Query complexity distribution:
            - 'simple': Mostly aggregations
            - 'balanced': Mix of simple and complex
            - 'complex': Heavy on joins and grouping
        """
        self.table_size = table_size
        self.complexity_mix = complexity_mix

        self.query_templates = self._create_query_templates()
        self.query_weights = self._get_query_weights()

    def _create_query_templates(self) -> List[str]:
        """Create TPC-H inspired analytical query templates."""
        return [
            # Q1: Simple aggregations
            "SELECT COUNT(*), AVG(k), SUM(k), MIN(k), MAX(k) FROM sbtest1",
            # Q2: Conditional aggregation
            "SELECT COUNT(*), AVG(k), SUM(k) FROM sbtest1 WHERE k > {threshold}",
            # Q3: Range scan with aggregation
            "SELECT COUNT(*), AVG(k) FROM sbtest1 WHERE k BETWEEN {low} AND {high}",
            # Q4: Grouping with aggregation
            "SELECT (k % 100) as bucket, COUNT(*), AVG(k) FROM sbtest1 GROUP BY bucket",
            # Q5: More complex grouping
            "SELECT (k % 1000) as bucket, COUNT(*), AVG(k), SUM(k) FROM sbtest1 GROUP BY bucket HAVING COUNT(*) > 5",
            # Q6: Statistical aggregations
            "SELECT MIN(k), MAX(k), AVG(k), STDDEV(k) FROM sbtest1",
            # Q7: Range scan with ordering
            "SELECT id, k FROM sbtest1 WHERE k > {threshold} ORDER BY k DESC LIMIT 100",
            # Q8: Percentile-style query
            "SELECT k FROM sbtest1 ORDER BY k LIMIT 1 OFFSET {offset}",
            # Q9: Multiple aggregations with filtering
            "SELECT (k / 1000) as range, COUNT(*), MIN(k), MAX(k), AVG(k) FROM sbtest1 WHERE k > 10000 GROUP BY range",
            # Q10: Complex analytical with HAVING
            "SELECT (k % 500) as bucket, COUNT(*) as cnt, AVG(k) as avg_k FROM sbtest1 GROUP BY bucket HAVING COUNT(*) > 10 ORDER BY cnt DESC LIMIT 50",
        ]

    def _get_query_weights(self) -> List[float]:
        """Get query execution weights based on complexity mix."""
        if self.complexity_mix == "simple":
            return [0.3, 0.25, 0.2, 0.1, 0.05, 0.05, 0.03, 0.01, 0.005, 0.005]
        elif self.complexity_mix == "complex":
            return [0.05, 0.05, 0.05, 0.1, 0.1, 0.05, 0.1, 0.15, 0.2, 0.15]
        else:  # balanced
            return [0.15, 0.15, 0.12, 0.12, 0.12, 0.1, 0.08, 0.06, 0.06, 0.04]

    def _instantiate_query(self, template: str) -> str:
        """Instantiate query template with random parameters."""
        params = {
            'threshold': random.randint(self.table_size // 4, 3 * self.table_size // 4),
            'low': random.randint(1, self.table_size // 2),
            'high': random.randint(self.table_size // 2, self.table_size),
            'offset': random.randint(0, self.table_size - 1),
        }
        try:
            return template.format(**params)
        except KeyError:
            # Template doesn't need parameters
            return template

    def execute(
        self,
        connection: PostgresConnection,
        duration: float,
        warmup: int = 0,
        worker_id: Optional[int] = None
    ) -> PerformanceMetrics:
        """
        Execute TPC-H OLAP workload.
        
        Runs analytical queries with random parameter instantiation
        to simulate realistic OLAP workload patterns.
        """
        work_logger = get_logger(__name__, worker_id=worker_id) if worker_id is not None else logger

        cursor = connection.cursor()

        work_logger.debug("TPC-H OLAP warmup: %s queries", warmup)
        for _ in range(warmup):
            template = random.choices(self.query_templates, weights=self.query_weights)[0]
            query = self._instantiate_query(template)
            try:
                cursor.execute(query)
                cursor.fetchall()
                connection.commit()
            except Exception as e:
                work_logger.warning("Warmup query failed: %s", e)
                connection.rollback()

        work_logger.debug("TPC-H OLAP measurement: %ss", duration)
        start_time = time.time()
        latencies = []
        query_count = 0
        error_count = 0

        while (time.time() - start_time) < duration:
            template = random.choices(self.query_templates, weights=self.query_weights)[0]
            query = self._instantiate_query(template)
            query_start = time.time()

            try:
                cursor.execute(query)
                cursor.fetchall()
                connection.commit()

                query_end = time.time()
                latencies.append((query_end - query_start) * 1000)  # Convert to ms
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
        warmup: int = 0,
        worker_id: Optional[int] = None
    ) -> PerformanceMetrics:
        """Execute custom queries and collect metrics."""
        work_logger = get_logger(__name__, worker_id=worker_id) if worker_id is not None else logger
        cursor = connection.cursor()

        work_logger.debug("Custom query warmup: %s queries", warmup)
        for _ in range(warmup):
            query = random.choices(self.queries, weights=self.weights)[0]
            try:
                cursor.execute(query)
                cursor.fetchall()
                connection.commit()
            except Exception as e:
                work_logger.warning("Warmup query failed: %s", e)
                connection.rollback()

        work_logger.debug("Custom query measurement: %ss", duration)
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
    def load_from_file(filepath: str) -> CustomQueryExecutor:
        """
        Load workload from JSON or YAML file.
        
        Parameters
        ----------
        filepath : str
            Path to workload definition file (.json or .yaml/.yml)
        
        Returns
        -------
        CustomQueryExecutor
            Configured executor with loaded queries
        
        Raises
        ------
        FileNotFoundError
            If file doesn't exist
        ValueError
            If file format is invalid or queries are malformed
        """
        filepath = Path(filepath)  # type: ignore

        if not filepath.exists():  # type: ignore
            raise FileNotFoundError(f"Workload file not found: {filepath}")

        if filepath.suffix == '.json':  #type: ignore
            with open(filepath, 'r', encoding='utf-8') as f:
                data = json.load(f)
        elif filepath.suffix in ['.yaml', '.yml']:  #type: ignore
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
                f"Unsupported file format: {filepath.suffix}. "  #type: ignore
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

        name = data.get('name', filepath.stem)  # type: ignore
        description = data.get('description', 'Custom workload')
        logger.info(
            "Loaded workload '%s': %s (%d queries)",
            name, description, len(query_list)
        )

        return CustomQueryExecutor(queries=query_list, weights=weight_list)

    @staticmethod
    def validate_queries(
        queries: List[str],
        connection: PostgresConnection,
        dry_run: bool = True
    ) -> Dict[str, Any]:
        """
        Validate queries against database schema.
        
        Parameters
        ----------
        queries : List[str]
            SQL queries to validate
        connection : PostgresConnection
            Database connection for validation
        dry_run : bool
            If True, use EXPLAIN instead of executing
        
        Returns
        -------
        Dict[str, Any]
            Validation results with errors/warnings
        """
        results = {
            'total': len(queries),
            'valid': 0,
            'invalid': 0,
            'errors': [],
            'warnings': []
        }

        cursor = connection.cursor()

        for i, query in enumerate(queries):
            try:
                if dry_run:
                    cursor.execute(f"EXPLAIN {query}")
                    cursor.fetchall()
                else:
                    cursor.execute(query)
                    cursor.fetchall()

                results['valid'] += 1
            except Exception as e:
                results['invalid'] += 1
                results['errors'].append({
                    'query_index': i,
                    'query': query[:100] + '...' if len(query) > 100 else query,
                    'error': str(e)
                })
            finally:
                connection.rollback()  # Don't commit validation queries

        cursor.close()
        return results


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
        workload_executor: WorkloadExecutor,
        worker_id: Optional[str] = None,
    ):
        """
        Initialize Evaluator.
        
        Parameters
        ----------
        config : EvaluatorConfig
            Evaluation configuration
        workload_executor : WorkloadExecutor
            Workload execution strategy
        worker_id : Optional[str]
            Worker identifier for logging
        """
        self.config = config
        self.workload_executor = workload_executor
        self.worker_id = worker_id or "Evaluator"

        # Use generic logger without worker_id since Evaluator is shared across workers
        self.logger = logging.getLogger(__name__)

        self.restart_cost_model = RestartCostModel(
            base_restart_time=7.0,
            cache_warmup_ratio=0.1,
            restart_interval=config.restart_interval
        )

        self.applicator_config = ApplicatorConfig(
            auto_restart=False,  # We handle restart manually per instance
            rollback_on_error=False
        )

        self.logger.debug(
            "Created Evaluator: workload=%s, duration=%ss",
            config.workload_type.value,
            config.measurement_duration
        )

    def connect(self, db_config: Optional[DatabaseConfig] = None) -> PostgresConnection:
        """
        Establish connection to PostgreSQL.
        
        Parameters
        ----------
        db_config : Optional[DatabaseConfig]
            Database configuration. If None, uses self.config.db_config
        
        Returns
        -------
        PostgresConnection
            Active PostgreSQL connection
        
        Raises
        ------
        psycopg2.Error
            If connection fails
        """
        try:
            connection = get_connection(config=db_config or self.config.db_config)
            connection.autocommit = False
            return connection
        except psycopg2.Error as e:
            self.logger.error("Failed to connect to PostgreSQL: %s", e)
            raise

    def disconnect(self, connection: Optional[PostgresConnection], worker_id: Optional[int] = None) -> None:
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
                    self.logger.debug("Disconnected from PostgreSQL")
            except Exception as e:
                if worker_id is not None:
                    worker_logger = get_logger(__name__, worker_id=worker_id)
                    worker_logger.warning("Error closing connection: %s", e)
                else:
                    self.logger.warning("Error closing connection: %s", e)

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
        logger = get_logger(__name__, worker_id=worker_id) if worker_id is not None else self.logger

        try:
            result = knob_applicator.apply(knob_config)

            # Don't log here - we'll log after verification instead

            # If restart-required params changed, check if we should restart this generation
            if result.restart_required and len(result.restart_required) > 0:
                logger.info(
                    "Restart required for %d parameters: %s",
                    len(result.restart_required),
                    list(result.restart_required)
                )
                
                # Only restart every restart_interval generations (batching strategy)
                should_restart = generation is not None and (generation % restart_interval == 0)
                
                if should_restart:
                    if restart_manager:
                        logger.info(
                            "Restarting (generation %d is restart interval)",
                            generation
                        )
                        restart_occurred = self._perform_restart(
                            connection, restart_manager, worker_log_id, worker_id=worker_id
                        )
                    else:
                        logger.warning(
                            "Restart needed but restart_manager not configured"
                        )
                else:
                    logger.info(
                        "Deferring restart (will restart at generation %d)",
                        ((generation // restart_interval) + 1) * restart_interval if generation is not None else restart_interval
                    )
            elif force_restart and restart_manager:
                restart_occurred = self._perform_restart(
                    connection, restart_manager, worker_log_id, worker_id=worker_id
                )

        except Exception as e:
            logger.error("Failed to apply configuration: %s", e)
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
        logger = get_logger(__name__, worker_id=worker_id) if worker_id is not None else self.logger
        
        logger.info("Restarting PostgreSQL instance...")
        
        try:
            # Close connection before restart
            try:
                if connection and not connection.closed:
                    connection.close()
            except (psycopg2.Error, AttributeError):
                pass

            # Restart the instance
            if restart_manager.restart():
                logger.info("Restart successful")
                
                # Note: Connection will be reopened by evaluate_worker
                # Reset statistics after restart
                try:
                    temp_conn = get_connection(config=restart_manager.db_config)
                    cursor = temp_conn.cursor()
                    cursor.execute("SELECT pg_stat_reset()")
                    cursor.fetchone()
                    cursor.close()
                    temp_conn.commit()
                    temp_conn.close()
                    logger.debug("Reset PostgreSQL statistics")
                except Exception as e:
                    logger.warning("Failed to reset statistics: %s", e)
                
                return True
            else:
                logger.error("Restart failed")
                return False
                
        except Exception as e:
            logger.error("Restart failed with exception: %s", e)
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
        logger = get_logger(__name__, worker_id=worker_id) if worker_id is not None else self.logger
        
        verification = {}
        mismatches = []
        
        try:
            cursor = connection.cursor()
            
            for param_name, expected_value in expected_config.items():
                try:
                    # Query current value
                    cursor.execute(
                        "SELECT setting, unit FROM pg_settings WHERE name = %s",
                        (param_name,)
                    )
                    result = cursor.fetchone()
                    
                    if not result:
                        self.logger.warning(
                            "[%s] Parameter '%s' not found in pg_settings",
                            worker_log_id, param_name
                        )
                        verification[param_name] = False
                        continue
                    
                    current_value_str, unit = result
                    
                    # Convert current value to comparable type
                    if isinstance(expected_value, bool):
                        current_value = current_value_str.lower() in ('on', 'true', '1')
                        match = current_value == expected_value
                    elif isinstance(expected_value, (int, float)):
                        # Convert both to float for consistent comparison
                        current_value = float(current_value_str)
                        expected_float = float(expected_value)
                        # Use tolerance for floating-point comparison
                        match = abs(current_value - expected_float) < 0.01
                    else:
                        current_value = current_value_str
                        match = str(current_value) == str(expected_value)
                    
                    verification[param_name] = match
                    
                    if not match:
                        mismatches.append(
                            f"{param_name}: expected={expected_value}, actual={current_value}"
                        )
                
                except Exception as e:
                    logger.warning(
                        "Failed to verify parameter '%s': %s",
                        param_name, e
                    )
                    verification[param_name] = False
            
            cursor.close()
            
            # Log results
            verified_count = sum(verification.values())
            total_count = len(verification)
            
            if verified_count == total_count:
                logger.debug(
                    "Configuration verified: %d/%d parameters correct",
                    verified_count, total_count
                )
            else:
                logger.warning(
                    "Configuration mismatch: %d/%d parameters verified",
                    verified_count, total_count
                )
                for mismatch in mismatches:
                    logger.warning("  %s", mismatch)
        
        except Exception as e:
            logger.error(
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
            self.logger.warning("Failed to get PostgreSQL PID: %s", e)
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
                                self.logger.debug(
                                    "Found postmaster PID %d for port %d", 
                                    proc.info['pid'], port
                                )
                            return proc.info['pid']
                            
                except (psutil.NoSuchProcess, psutil.AccessDenied, psutil.ZombieProcess):
                    continue
            
            self.logger.warning("Could not find PostgreSQL postmaster for port %d", port)
        except Exception as e:
            self.logger.warning("Error finding postmaster PID: %s", e)
        
        return None
    
    def _get_all_postgres_processes(self, postmaster_pid: int, worker_id: Optional[int] = None) -> List[psutil.Process]:
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
                self.logger.debug(
                    "Found %d PostgreSQL processes (1 postmaster + %d backends) for PID %d",
                    len(processes), len(children), postmaster_pid
                )
            
        except (psutil.NoSuchProcess, psutil.AccessDenied) as e:
            self.logger.warning("Failed to get PostgreSQL processes: %s", e)
        
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
            self.logger.warning("Connection not available for system metrics collection")
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
                    postgres_processes = self._get_all_postgres_processes(postgres_pid, worker_id=worker_id)
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
                        metrics['memory_utilization'] = (total_memory_rss / system_memory) * 100
                        memory_mb = total_memory_rss / (1024 * 1024)
                        if worker_id is not None:
                            worker_logger = get_logger(__name__, worker_id=worker_id)
                            worker_logger.debug(
                                "Memory: %.1fMB (%.1f%%) across %d processes",
                                memory_mb, metrics['memory_utilization'], len(postgres_processes)
                            )
                        else:
                            self.logger.debug(
                                "Memory: %.1fMB (%.1f%%) across %d processes",
                                memory_mb, metrics['memory_utilization'], len(postgres_processes)
                            )
                    except:
                        metrics['memory_utilization'] = 0.0
                        
                except Exception as e:
                    self.logger.warning("Failed to collect memory metrics: %s", e)
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
                self.logger.warning("Failed to query cache hit ratio: %s", e)
                metrics['cache_hit_ratio'] = 0.0
            finally:
                cursor.close()
        else:
            metrics['cache_hit_ratio'] = 0.0

        return metrics

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
            connection = self.connect(worker.db_config)

            if apply_config and worker.knob_config:
                knob_applicator = KnobApplicator(
                    connection_params=worker.db_config.to_dict(),
                    config=self.applicator_config,
                    worker_id=worker.worker_id
                )

                restart_manager = None
                if self.config.enable_restart:
                    restart_manager = PostgresRestartManager(
                        db_config=worker.db_config,
                        restart_config=self.config.restart_config,
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
                    connection = self.connect(worker.db_config)
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

                metrics = self.workload_executor.execute(
                    connection=connection,
                    duration=self.config.measurement_duration,
                    warmup=self.config.warmup_queries,
                    worker_id=worker.worker_id
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
                metrics = PerformanceMetrics()

            system_metrics = self.collect_system_metrics(
                connection,
                port=worker.port,
                worker_id=worker.worker_id
            )

            if 'cache_hit_ratio' in system_metrics:
                metrics.cache_hit_ratio = system_metrics['cache_hit_ratio']
            if 'memory_utilization' in system_metrics:
                metrics.memory_utilization = system_metrics['memory_utilization']

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
