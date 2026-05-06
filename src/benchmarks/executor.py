"""
External Benchmark Executors
=============================

Provides interfaces for executing industry-standard database benchmarks
(Sysbench, TPC-H) via their native C-binaries rather than Python-level
query execution. This eliminates interpreter overhead and produces
results directly comparable to published academic baselines.
"""

from abc import ABC, abstractmethod
from typing import Optional

from psycopg2 import sql

from src.config.database import DatabaseConfig
from src.utils.metrics import PerformanceMetrics


class BenchmarkExecutor(ABC):
    """
    Abstract interface for external benchmarking tools.

    Subclasses wrap standard benchmark drivers (sysbench, dbgen, etc.)
    and parse their output into PerformanceMetrics.

    Used to provide rigorous academic-standard evaluations for automated tuning
    frameworks, as well as circumvent Python execution overhead.
    """

    @abstractmethod
    def prepare(self, db_config: DatabaseConfig) -> None:
        """Create required schema and data on the target database."""

    @abstractmethod
    def validate(self, db_config: DatabaseConfig) -> bool:
        """Return True if the required schema already exists."""

    @abstractmethod
    def execute(
        self, db_config: DatabaseConfig, worker_id: Optional[int] = None, **kwargs
    ) -> PerformanceMetrics:
        """
        Execute the benchmark workload and collect metrics.
        Implementation details (warmup, duration, query loops, etc.)
        are handled via **kwargs inside the executor implementations.

        Parameters
        ----------
        db_config : DatabaseConfig
            Database connection parameters
        worker_id : Optional[int]
            Worker ID for logging differentiation
        random_seed : Optional[int]
            Random seed for workload generation (fairness)
        **kwargs
            Arbitrary execution constraints (e.g., duration, warmup,
            warmup_passes, random_seed, etc.)

        Returns
        -------
        PerformanceMetrics
            Collected metrics from the execution. Must include core metrics (throughput,
            latency percentiles) as well as derived scoring attributes (latency_variance,
            throughput_variance, and tail_amplification).

        Notes
        -----
        Executing TPC-H Power Test doesn't require a fixed duration,
        but instead runs a fixed set of queries. It also doesn't need
        a random seed since the queries are static. Sysbench, on the other
        hand, typically runs for a fixed duration and can benefit from a
        random seed for reproducibility.
        """

    def _drop_existing_public_tables(
        self,
        cursor,
        logger,
        log_prefix: str = "",
    ) -> None:
        """Drop all existing tables in PostgreSQL public schema.

        Args:
            cursor: Active psycopg2 cursor bound to target database.
            logger: Logger used for debug diagnostics.
            log_prefix: Optional benchmark-specific prefix for log messages.
        """
        cursor.execute("SELECT tablename FROM pg_tables WHERE schemaname = 'public'")
        existing_tables = [str(row[0]) for row in cursor.fetchall()]
        if not existing_tables:
            return

        if log_prefix:
            logger.debug(
                f"{log_prefix} Dropping existing public tables (%d)...",
                len(existing_tables),
            )
        else:
            logger.debug(
                "Dropping existing public tables (%d)...",
                len(existing_tables),
            )

        for table_name in sorted(existing_tables):
            cursor.execute(
                sql.SQL("DROP TABLE IF EXISTS {} CASCADE").format(
                    sql.Identifier(table_name)
                )
            )
