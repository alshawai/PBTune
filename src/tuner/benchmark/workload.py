"""
Template-Based Workload Executor
=================================

Provides a SQL template executor for custom workloads and a file loader
for JSON/YAML workload definitions. These are used by the evaluator as
an alternative to native BenchmarkExecutor drivers (Sysbench, TPC-H)
when the user supplies custom SQL query templates.
"""

from __future__ import annotations
from typing import Callable, Optional
from pathlib import Path
import json
import subprocess
import time
import threading
from queue import Queue

import numpy as np
import yaml
import psycopg2
from psycopg2.extensions import connection as PostgresConnection

from src.database.connection import get_connection
from src.config.database import DatabaseConfig
from src.utils.metrics import PerformanceMetrics
from src.utils.logger import get_logger
from src.utils.scoring.workload_features import TemplateWorkloadMetadata

LOGGER = get_logger("WorkloadExecutor")


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
        schema: Optional[dict[str, int]] = None,
        query_definitions: Optional[list[dict[str, str | float]]] = None,
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
        schema : Optional[dict[str, int]]
            Optional workload schema metadata loaded from workload file.
        query_definitions : Optional[list[dict[str, str | float]]]
            Optional original query definitions (sql, weight, description).
        """
        self.queries = queries
        self.weights = weights or [1.0] * len(queries)
        total = sum(self.weights)  # Normalize weights
        self.weights = [w / total for w in self.weights]
        self.table_size = table_size
        self.num_tables = num_tables
        self.num_threads = num_threads
        self.schema = schema or {
            "tables": num_tables,
            "table_size": table_size,
        }
        self.query_definitions = query_definitions or []
        # Placeholder, will be seeded in execute() if random_seed is provided
        self.rng = np.random.default_rng()

    def _instantiate_query(self, template: str) -> str:
        """Instantiate query template with random parameters."""
        table_idx = self.rng.integers(1, self.num_tables)
        table2_idx = self.rng.integers(1, self.num_tables)
        params = {
            "table": f"sbtest{table_idx}",
            "table2": f"sbtest{table2_idx}",
            "id": self.rng.integers(1, self.table_size + 1),
            "k_val": self.rng.integers(1, self.table_size + 1),
            "threshold": self.rng.integers(
                self.table_size // 4, 3 * self.table_size // 4
            ),
            "low": self.rng.integers(1, self.table_size // 2),
            "high": self.rng.integers(self.table_size // 2, self.table_size),
            "low_k": self.rng.integers(1, self.table_size // 2),
            "high_k": self.rng.integers(self.table_size // 2, self.table_size),
            "offset": self.rng.integers(0, self.table_size),
        }
        try:
            return template.format(**params)
        except KeyError:
            return template  # A template that doesn't need parameters

    def prepare(self, db_config: DatabaseConfig) -> None:
        """Create required sbtest tables using native sysbench C-binary."""
        LOGGER.info(
            "Preparing %d sbtest tables (%d rows each) on %s:%s...",
            self.num_tables,
            self.table_size,
            db_config.host,
            db_config.port,
        )
        cmd = [
            "sysbench",
            "oltp_read_write",
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
            check=False,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
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
            LOGGER.debug(
                "Successfully executed post-prepare VACUUM ANALYZE on all sbtest tables."
            )
        except Exception as e:
            LOGGER.warning("Failed to post-vacuum workload tables: %s", e)

        LOGGER.info("Schema preparation complete.")

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
        random_seed: Optional[int] = None,
        pre_measurement_callback: Optional[Callable] = None,
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
        pre_measurement_callback : Callable | None
            Optional callback invoked after warmup completes but before
            the timed measurement begins.  Used by the barrier system
            to synchronize workers at the warmup→measurement boundary.

        Returns
        -------
            PerformanceMetrics
                Collected metrics from the execution
        """
        work_logger = (
            get_logger(__name__, worker_id=worker_id)
            if worker_id is not None
            else LOGGER
        )

        # Use a dedicated random instance for reproducibility without affecting global RNG
        self.rng = (
            np.random.default_rng(random_seed) if random_seed is not None else np.random
        )

        if random_seed is not None:
            work_logger.debug(
                "Seeded random with %d for reproducible workload", random_seed
            )

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

        # Invoke barrier callback between warmup and measurement phases
        if pre_measurement_callback is not None:
            pre_measurement_callback()

        if self.num_threads > 1:
            work_logger.debug(
                "Template query measurement: %ss with %d threads",
                duration,
                self.num_threads,
            )
            return self._execute_concurrent(connection, duration, work_logger)

        work_logger.debug("Template query measurement: %ss (sequential)", duration)
        return self._execute_sequential(connection, duration, work_logger)

    def _execute_concurrent(
        self, connection: PostgresConnection, duration: float, work_logger
    ) -> PerformanceMetrics:
        """Execute queries with multiple concurrent threads."""
        db_params = {
            "host": connection.info.host,
            "port": connection.info.port,
            "dbname": connection.info.dbname,
            "user": connection.info.user,
            "password": connection.info.password
            if hasattr(connection.info, "password")
            else None,
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
            stddev = float(np.std(all_latencies))
        else:
            p50 = p95 = p99 = 0.0
            stddev = 0.0

        throughput = total_queries / total_time if total_time > 0 else 0.0
        error_rate = total_errors / total_queries if total_queries > 0 else 0.0

        return PerformanceMetrics(
            latency_p50=p50,
            latency_p95=p95,
            latency_p99=p99,
            latency_stddev=stddev,
            throughput=throughput,
            total_queries=total_queries,
            total_time=total_time,
            error_rate=error_rate,
        )

    def _execute_sequential(
        self, connection: PostgresConnection, duration: float, work_logger
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
            stddev = float(np.std(latencies))
        else:
            p50 = p95 = p99 = 0.0
            stddev = 0.0

        throughput = query_count / total_time if total_time > 0 else 0.0
        error_rate = error_count / query_count if query_count > 0 else 0.0

        return PerformanceMetrics(
            latency_p50=p50,
            latency_p95=p95,
            latency_p99=p99,
            latency_stddev=stddev,
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

        if filepath.suffix == ".json":
            with open(filepath, "r", encoding="utf-8") as f:
                data = json.load(f)
        elif filepath.suffix in [".yaml", ".yml"]:
            try:
                with open(filepath, "r", encoding="utf-8") as f:
                    data = yaml.safe_load(f)
            except ImportError as exc:
                raise ImportError(
                    "PyYAML is required for YAML workload files. "
                    "Install with: pip install pyyaml"
                ) from exc
        else:
            raise ValueError(
                f"Unsupported file format: {filepath.suffix}. Use .json, .yaml, or .yml"
            )

        if not isinstance(data, dict):
            raise ValueError("Workload file must contain a JSON object/YAML dict")

        if "queries" not in data:
            raise ValueError("Workload file must contain 'queries' field")

        queries = data["queries"]
        if not isinstance(queries, list) or len(queries) == 0:
            raise ValueError("'queries' must be a non-empty list")

        query_list = []
        weight_list = []
        query_definitions: list[dict[str, str | float]] = []

        for i, query_def in enumerate(queries):
            if isinstance(query_def, str):
                query_list.append(query_def)
                weight_list.append(1.0)
                query_definitions.append(
                    {
                        "sql": query_def,
                        "weight": 1.0,
                        "description": "",
                    }
                )
            elif isinstance(query_def, dict):
                if "sql" not in query_def:
                    raise ValueError(f"Query {i} missing 'sql' field")

                query_list.append(query_def["sql"])
                weight = float(query_def.get("weight", 1.0))
                weight_list.append(weight)
                query_definitions.append(
                    {
                        "sql": str(query_def["sql"]),
                        "weight": weight,
                        "description": str(query_def.get("description", "")),
                    }
                )
            else:
                raise ValueError(f"Query {i} must be a string or dict with 'sql' field")

        name = data.get("name", filepath.stem)
        description = data.get("description", "Custom workload")

        schema = data.get("schema", {})
        num_tables = schema.get("tables", 1)
        table_size = schema.get("table_size", 100000)

        if not schema:
            LOGGER.warning(
                "Workload '%s' has no 'schema' section — defaulting to 1 table "
                "with 100K rows. Add a 'schema' section for multi-table support.",
                name,
            )

        LOGGER.debug(
            "-> Loaded workload '%s': %s (%d queries, %d tables × %d rows)\n",
            name,
            description,
            len(query_list),
            num_tables,
            table_size,
        )

        return WorkloadExecutor(
            queries=query_list,
            weights=weight_list,
            num_tables=num_tables,
            table_size=table_size,
            schema={
                "tables": num_tables,
                "table_size": table_size,
            },
            query_definitions=query_definitions,
        )


def extract_workload_template_metadata(
    executor: WorkloadExecutor,
) -> TemplateWorkloadMetadata:
    """Build normalized feature-extraction metadata from a template executor."""
    return TemplateWorkloadMetadata(
        queries=list(executor.queries),
        weights=list(executor.weights),
        num_threads=int(executor.num_threads),
        schema=dict(executor.schema),
    )
