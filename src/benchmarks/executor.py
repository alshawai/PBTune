"""
Benchmark Executor ABC
======================

Defines the unified interface for all workload drivers: external C-binary
benchmarks (Sysbench, TPC-H) and internal template-based SQL executors.
Every executor implements ``prepare → validate → execute`` against a single
``ExecutionContext`` that carries everything the driver needs.
"""

from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from typing import Callable, Optional, TYPE_CHECKING

from psycopg2 import sql

from src.config.database import DatabaseConfig
from src.utils.metrics import PerformanceMetrics
from src.utils.logger import get_logger, get_color_context

if TYPE_CHECKING:
    from psycopg2.extensions import connection as PostgresConnection

LOGGER = get_logger("BenchmarkExecutor")
COLORS = get_color_context()


@dataclass
class ExecutionContext:
    """
    All inputs a benchmark driver might need for a single execution.

    External drivers (Sysbench, TPC-H) use ``db_config`` and manage their own
    connections. Internal template drivers use the caller-supplied ``connection``
    and fire the ``pre_measurement_callback`` at the warmup→measurement boundary.
    """

    db_config: DatabaseConfig
    duration: float
    warmup: float = 30.0
    worker_id: Optional[int] = None
    random_seed: Optional[int] = None
    warmup_passes: int = 0
    connection: Optional["PostgresConnection"] = None
    pre_measurement_callback: Optional[Callable[[], None]] = None


class BenchmarkExecutor(ABC):
    """
    Abstract interface for all workload drivers.

    Subclasses wrap standard benchmark binaries (sysbench, dbgen) or internal
    SQL template execution and parse output into PerformanceMetrics.

    Attributes
    ----------
    manages_own_connection : bool
        True for external drivers that open/close their own database connections
        (Sysbench, TPC-H). False for internal template drivers that require a
        caller-supplied connection via ``ExecutionContext.connection``.
    """

    manages_own_connection: bool = True

    @abstractmethod
    def prepare(self, db_config: DatabaseConfig) -> None:
        """Create required schema and data on the target database."""

    @abstractmethod
    def validate(self, db_config: DatabaseConfig) -> bool:
        """Return True if the required schema already exists."""

    @abstractmethod
    def execute(self, ctx: ExecutionContext) -> PerformanceMetrics:
        """
        Execute the benchmark workload and collect metrics.

        Parameters
        ----------
        ctx : ExecutionContext
            Unified execution context carrying db_config, timing params,
            and (for internal drivers) the caller-supplied connection and
            barrier callback.

        Returns
        -------
        PerformanceMetrics
            Collected metrics including core metrics (throughput, latency
            percentiles) and derived scoring attributes (latency_variance,
            throughput_variance, tail_amplification).
        """

    def _drop_existing_public_tables(
        self,
        cursor,
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
            LOGGER.debug(
                "    %s Dropping existing public tables (%d)...",
                log_prefix,
                len(existing_tables),
            )
        else:
            LOGGER.debug(
                "    Dropping existing public tables (%d)...",
                len(existing_tables),
            )

        for table_name in sorted(existing_tables):
            cursor.execute(
                sql.SQL("DROP TABLE IF EXISTS {} CASCADE").format(
                    sql.Identifier(table_name)
                )
            )

        LOGGER.debug(
            "    %s➤ Dropped all existing tables.%s", COLORS.italic, COLORS.reset
        )
