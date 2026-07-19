"""
Base Environment Interface
===========================

Provides the polymorphic `DatabaseEnvironment` abstract base class
that standardizes the lifecycle and interface of a Postgres instance,
abstracting away whether it runs in Docker or on Bare-Metal.
"""

from abc import ABC, abstractmethod
from typing import List, Optional, TYPE_CHECKING
from pathlib import Path
from dataclasses import dataclass
import time

import psycopg2
from psycopg2 import sql

from src.config.database import DatabaseConfig
from src.database.connection import get_connection
from src.utils.logger import get_logger, get_color_context
from src.benchmarks.executor import BenchmarkExecutor

if TYPE_CHECKING:
    from src.utils.types import WorkerResourceAllocation


LOGGER = get_logger("BaseEnvironment")
COLORS = get_color_context()


@dataclass
class InstanceConfig:
    """Configuration for a single PostgreSQL instance.

    ``host`` defaults to loopback so local (single-device) backends are
    unchanged. The distributed :class:`RemoteEnvironment` sets it to the
    owning device's address so workers bind to the right host.
    """

    worker_id: int
    port: int
    data_dir: Path
    running: bool = False
    host: str = "127.0.0.1"


class DatabaseEnvironment(ABC):
    """
    Abstract Base Class for managing isolated database environments.

    Provides a standardized interface for lifecycle (setup/teardown)
    and configuration management. This abstraction natively manages multiple
    workers simultaneously, unifying multi-worker tuning with single-worker evaluation.
    """

    def __init__(
        self,
        run_id: str,
        db_config: DatabaseConfig,
        schema_provider: BenchmarkExecutor,
        force_recreate_baseline: bool = False,
    ):
        """
        Initialize the environment manager.

        Parameters
        ----------
        run_id : str
            Unique identifier for the evaluation run/tuning session.
        db_config : DatabaseConfig
            Base configuration describing the database to manage.
        schema_provider : BenchmarkExecutor
            Provider to handle database schema initialization (e.g. SysbenchExecutor).
        force_recreate_baseline : bool
            If True, removes and recreates the baseline snapshot before setup.
        """
        self.run_id = run_id
        self.base_config = db_config
        self.schema_provider = schema_provider
        self.force_recreate_baseline = force_recreate_baseline
        # Lazily populated on first connection (see ``_capture_pg_server_version``).
        self.pg_server_version: Optional[str] = None
        # Backend-specific. Bare-metal sets this to ``None`` for symmetry;
        # Docker captures the daemon version on init.
        self.docker_version: Optional[str] = None

    def _capture_pg_server_version(self, connection) -> Optional[str]:
        """Capture the PostgreSQL ``server_version`` and store it on the env.

        Idempotent — once populated, subsequent calls become a no-op so that
        repeated callers (e.g. ``_prune_unsupported_runtime_knobs`` and the
        SessionEnvironment builder) don't re-query the server.

        Parameters
        ----------
        connection
            An active psycopg2 connection. Caller manages its lifetime.

        Returns
        -------
        Optional[str]
            The server version string, or ``None`` if the query failed.
        """
        if self.pg_server_version is not None:
            return self.pg_server_version
        try:
            cursor = connection.cursor()
            try:
                cursor.execute("SHOW server_version")
                row = cursor.fetchone()
            finally:
                cursor.close()
            if row and row[0]:
                self.pg_server_version = str(row[0]).strip()
        except (psycopg2.Error, OSError, ValueError, TypeError, AttributeError) as exc:
            LOGGER.debug("Failed to capture pg_server_version: %s", exc)
        return self.pg_server_version

    def _get_instance_subpath(self) -> str:
        """Determine the logical subpath for runtime data based on the schema."""
        if self.schema_provider is None:
            return "unknown_benchmark"

        provider_name = self.schema_provider.__class__.__name__.lower()
        if "sysbench" in provider_name:
            tables = getattr(self.schema_provider, "tables", 10)
            table_size = getattr(self.schema_provider, "table_size", 100000)
            return f"sysbench/t{tables}_s{table_size}"
        elif "tpch" in provider_name:
            scale_factor = getattr(self.schema_provider, "scale_factor", 1.0)
            return f"tpch/sf_{scale_factor}"

        return "unknown_benchmark"

    def initialize_schema(self, worker_id: int) -> None:
        """
        Initialize schema by delegating to the schema_provider.

        The provider's validate() checks if the schema already exists;
        if not, prepare() creates it.

        Fast path: if a baseline snapshot exists for the current scale
        factor, restores from snapshot instead of running the full
        prepare() pipeline.
        """
        config = self.get_db_config(worker_id)
        self._ensure_database_exists(config)
        self._reset_persisted_configuration(worker_id, config)

        LOGGER.debug("  Initializing schema for worker %d...", worker_id)
        if self.schema_provider.validate(config):
            LOGGER.debug(
                "%s  ➤ Schema already exists and valid for worker %d%s",
                COLORS.italic,
                worker_id,
                COLORS.reset,
            )
            return

        LOGGER.debug(
            "  Invalid schema detected. Attempting to restore from snapshot for worker %d...",
            worker_id,
        )
        if self.restore_snapshot(worker_id):
            if self.schema_provider.validate(config):
                self._reset_persisted_configuration(worker_id, config)
                LOGGER.debug(
                    "  %s➤ Schema restored from snapshot for worker %d%s",
                    COLORS.italic,
                    worker_id,
                    COLORS.reset,
                )
                return

        LOGGER.debug(
            "  Snapshot restoration failed or schema still invalid."
            " Preparing schema for worker %d...",
            worker_id,
        )
        self.schema_provider.prepare(config)
        LOGGER.debug(
            "%s  ➤ Schema prepared for worker %d%s",
            COLORS.italic,
            worker_id,
            COLORS.reset,
        )

    def _ensure_database_exists(self, config: DatabaseConfig) -> None:
        """Create the application database if it doesn't exist.

        After initdb, only the 'postgres' database exists. This method
        connects to 'postgres' and creates the application database
        (e.g. 'test_dataset') which a schema provider expects.
        """
        try:
            conn = get_connection(config=config, dbname="postgres", connect_timeout=5)
            conn.autocommit = True
            cursor = conn.cursor()

            cursor.execute(
                "SELECT 1 FROM pg_database WHERE datname = %s", (config.dbname,)
            )
            if not cursor.fetchone():
                LOGGER.debug("    Creating database '%s'...", config.dbname)
                cursor.execute(
                    sql.SQL("CREATE DATABASE {}").format(sql.Identifier(config.dbname))
                )

            cursor.close()
            conn.close()
        except psycopg2.Error as e:
            LOGGER.error("Failed to ensure database '%s' exists: %s", config.dbname, e)

    def _wait_until_connectable(
        self, config: DatabaseConfig, timeout_seconds: int = 30
    ) -> bool:
        """Wait for PostgreSQL to accept connections after restart operations."""
        deadline = time.time() + timeout_seconds
        while time.time() < deadline:
            try:
                conn = get_connection(config=config, connect_timeout=2)
                conn.close()
                return True
            except (RuntimeError, psycopg2.Error):
                time.sleep(0.5)
        return False

    def _reset_persisted_configuration(
        self, worker_id: int, config: DatabaseConfig
    ) -> None:
        """Clear persisted ALTER SYSTEM settings and restart if pending_restart remains."""
        conn = None
        cursor = None
        pending_restart_count = 0
        try:
            conn = get_connection(config=config, connect_timeout=5)
            conn.autocommit = True
            cursor = conn.cursor()
            cursor.execute("ALTER SYSTEM RESET ALL")
            cursor.execute("SELECT pg_reload_conf()")
            cursor.execute("SELECT count(*) FROM pg_settings WHERE pending_restart")
            row = cursor.fetchone()
            pending_restart_count = int(row[0]) if row and row[0] is not None else 0
        except (RuntimeError, psycopg2.Error, ValueError) as exc:
            LOGGER.warning(
                "Failed to reset persisted configuration for worker %d: %s",
                worker_id,
                exc,
            )
            return
        finally:
            if cursor is not None:
                cursor.close()
            if conn is not None:
                conn.close()

        if pending_restart_count <= 0:
            return

        if not self.stop_instance(worker_id):
            LOGGER.warning(
                "Failed to stop worker %d during persisted configuration reset",
                worker_id,
            )
            return

        if not self.start_instance(worker_id):
            LOGGER.warning(
                "Failed to restart worker %d during persisted configuration reset",
                worker_id,
            )
            return

        if not self._wait_until_connectable(config):
            LOGGER.warning(
                "Worker %d did not become connectable after persisted configuration reset",
                worker_id,
            )

    @abstractmethod
    def setup_instances(
        self,
        num_workers: int,
        force_recreate: bool = False,
        num_parallel_workers: int = 1,
    ) -> List[InstanceConfig]:
        """Set up infrastructure for N database instances."""

    @abstractmethod
    def start_instance(self, worker_id: int) -> bool:
        """Start a specific worker instance."""

    @abstractmethod
    def stop_instance(self, worker_id: int, mode: str = "fast") -> bool:
        """Stop a specific worker instance."""

    @abstractmethod
    def stop_all(self, mode: str = "fast") -> bool:
        """Stop all managed worker instances."""

    @abstractmethod
    def recover_instance(self, worker_id: int) -> bool:
        """Attempt to recover/restart a failed worker instance."""

    @abstractmethod
    def restart_instance(self, worker_id: int, quiet: bool = False) -> bool:
        """Restart a specific worker's database instance.

        Handles the full stop → start → wait-for-ready cycle.
        Used by OFFLINE tuning mode and forced restarts after config changes.

        Returns
        -------
        bool
            True if restart succeeded, False otherwise.
        """

    @abstractmethod
    def verify_instances(self) -> None:
        """Verify heartbeat/connectivity of all managed instances."""

    @abstractmethod
    def cleanup(self, remove_data: bool = False) -> None:
        """Clean up the environment and release any resources."""

    @abstractmethod
    def create_snapshot(self, worker_id: int = 0) -> str:
        """Create a baseline snapshot from the specified worker instance."""

    @abstractmethod
    def restore_snapshot(
        self, worker_id: int, snapshot_id: str = "", quiet: bool = False
    ) -> bool:
        """Restore a targeted worker's data directory/volume from the baseline snapshot."""

    @abstractmethod
    def clone_instances(
        self, source_worker_id: int, target_worker_ids: List[int]
    ) -> bool:
        """Clone the physical database state from a source worker to multiple target workers."""

    @abstractmethod
    def rebuild_worker_instance(self, worker_id: int) -> bool:
        """Rebuild a worker instance from scratch, in cases like failed snapshot restoration."""

    @abstractmethod
    def get_db_config(self, worker_id: int) -> DatabaseConfig:
        """Get the runtime connection configuration for a defined worker."""

    @abstractmethod
    def collect_memory_utilization(self, worker_id: int) -> float:
        """Collect per-worker PostgreSQL memory utilization as a [0, 1] ratio."""

    @abstractmethod
    def get_resource_allocations(self) -> List["WorkerResourceAllocation"]:
        """Return per-worker resource allocations.

        Backends that enforce resource limits (Docker via cgroups) report
        ``cpuset_cpus`` and ``docker_memory_limit_bytes`` from the
        configured container runtime kwargs. Backends without enforced
        isolation (bare-metal) return ``cpuset_cpus=None`` and
        ``docker_memory_limit_bytes=None``.
        """

    def collect_cache_hit_ratio(self, worker_id: int) -> float:
        """Query pg_stat_database for buffer cache hit ratio.

        Default implementation using SQL — works for both Docker and
        BareMetal since it only needs a database connection.

        Returns
        -------
        float
            Cache hit ratio in [0.0, 1.0], or 0.0 on failure.
        """
        try:
            db_config = self.get_db_config(worker_id)
            conn = get_connection(config=db_config, connect_timeout=5)
            cursor = conn.cursor()
            cursor.execute(
                "SELECT sum(blks_hit)::float / nullif(sum(blks_hit + blks_read), 0) "
                "FROM pg_stat_database WHERE datname = current_database()"
            )
            result = cursor.fetchone()
            cursor.close()
            conn.close()
            return float(result[0]) if result and result[0] is not None else 0.0
        except (psycopg2.Error, OSError, ValueError, TypeError, AttributeError) as exc:
            LOGGER.debug(
                "Failed to collect cache hit ratio for worker %d: %s",
                worker_id,
                exc,
            )
            return 0.0

    def reset_statistics(self, worker_id: int) -> bool:
        """Reset PostgreSQL statistics counters for a worker instance."""
        try:
            db_config = self.get_db_config(worker_id)
            conn = get_connection(config=db_config, connect_timeout=5)
            cursor = conn.cursor()
            cursor.execute("SELECT pg_stat_reset()")
            cursor.fetchone()
            cursor.close()
            conn.commit()
            conn.close()
            return True
        except (psycopg2.Error, OSError, ValueError, TypeError, AttributeError) as exc:
            LOGGER.debug(
                "Failed to reset statistics for worker %d: %s",
                worker_id,
                exc,
            )
            return False
