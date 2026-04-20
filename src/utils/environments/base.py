"""
Base Environment Interface
===========================

Provides the polymorphic `DatabaseEnvironment` abstract base class
that standardizes the lifecycle and interface of a Postgres instance,
abstracting away whether it runs in Docker or on Bare-Metal.
"""

from abc import ABC, abstractmethod
from typing import Any, Dict, List
from pathlib import Path
from dataclasses import dataclass
import time

import psycopg2
from psycopg2 import sql

from src.config.database import DatabaseConfig
from src.database.connection import get_connection
from src.utils.logger import get_logger, ColorCode
from src.tuner.evaluator.executor import BenchmarkExecutor


LOGGER = get_logger(__name__)

@dataclass
class InstanceConfig:
    """Configuration for a single PostgreSQL instance."""
    worker_id: int
    port: int
    data_dir: Path
    running: bool = False


class DatabaseEnvironment(ABC):
    """
    Abstract Base Class for managing isolated database environments.
    
    Provides a standardized interface for lifecycle (setup/teardown)
    and configuration management. This abstraction natively manages multiple 
    workers simultaneously, unifying multi-worker tuning with single-worker evaluation.
    """

    def __init__(
        self, run_id: str,
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

        LOGGER.debug("    Validating schema for worker %d...", worker_id)
        if self.schema_provider.validate(config):
            LOGGER.debug(
                "%s    ➤ Schema already exists and valid for worker %d%s",
                ColorCode.OKGREEN,
                worker_id,
                ColorCode.RESET
            )
            return

        LOGGER.debug(
            "%s    Invalid schema detected. Attempting to restore from snapshot for worker %d...%s",
            ColorCode.WARNING,
            worker_id,
            ColorCode.RESET
        )
        if self.restore_snapshot(worker_id):
            if self.schema_provider.validate(config):
                self._reset_persisted_configuration(worker_id, config)
                LOGGER.debug(
                    "%s    ➤ Schema restored from snapshot for worker %d%s",
                    ColorCode.OKGREEN,
                    worker_id,
                    ColorCode.RESET
                )
                return

        LOGGER.debug(
            "%s    Snapshot restoration failed or schema still invalid."
            " Preparing schema for worker %d...%s",
            ColorCode.WARNING,
            worker_id,
            ColorCode.RESET
        )
        self.schema_provider.prepare(config)
        LOGGER.debug(
            "%s    ➤ Schema prepared for worker %d%s",
            ColorCode.OKGREEN,
            worker_id,
            ColorCode.RESET
        )

    def _ensure_database_exists(self, config: DatabaseConfig) -> None:
        """Create the application database if it doesn't exist.
        
        After initdb, only the 'postgres' database exists. This method
        connects to 'postgres' and creates the application database
        (e.g. 'test_dataset') which a schema provider expects.
        """
        try:
            conn = get_connection(
                config=config,
                dbname="postgres",
                connect_timeout=5
            )
            conn.autocommit = True
            cursor = conn.cursor()

            cursor.execute(
                "SELECT 1 FROM pg_database WHERE datname = %s",
                (config.dbname,)
            )
            if not cursor.fetchone():
                LOGGER.debug("    Creating database '%s'...", config.dbname)
                cursor.execute(
                    sql.SQL("CREATE DATABASE {}").format(
                        sql.Identifier(config.dbname)
                    )
                )

            cursor.close()
            conn.close()
        except psycopg2.Error as e:
            LOGGER.error("Failed to ensure database '%s' exists: %s", config.dbname, e)

    def _wait_until_connectable(self, config: DatabaseConfig, timeout_seconds: int = 30) -> bool:
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

    def _reset_persisted_configuration(self, worker_id: int, config: DatabaseConfig) -> None:
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
        force_recreate: bool = False
    ) -> List[InstanceConfig]:

        """Set up infrastructure for N database instances."""

    @abstractmethod
    def start_instance(self, worker_id: int) -> bool:
        """Start a specific worker instance."""

    @abstractmethod
    def stop_instance(self, worker_id: int, mode: str = 'fast') -> bool:
        """Stop a specific worker instance."""

    @abstractmethod
    def stop_all(self, mode: str = 'fast') -> bool:
        """Stop all managed worker instances."""

    @abstractmethod
    def recover_instance(self, worker_id: int) -> bool:
        """Attempt to recover/restart a failed worker instance."""

    @abstractmethod
    def verify_instances(self) -> dict[int, bool]:
        """Verify heartbeat/connectivity of all managed instances."""

    @abstractmethod
    def cleanup(self, remove_data: bool = False) -> None:
        """Clean up the environment and release any resources."""

    @abstractmethod
    def apply_knobs(self, worker_id: int, knobs: Dict[str, Any]) -> None:
        """Apply a knob configuration mapped to the specific worker using KnobApplicator."""

    @abstractmethod
    def create_snapshot(self, worker_id: int = 0) -> str:
        """Create a baseline snapshot from the specified worker instance."""

    @abstractmethod
    def restore_snapshot(self, worker_id: int) -> bool:
        """Restore a targeted worker's data directory/volume from the baseline snapshot."""

    @abstractmethod
    def get_db_config(self, worker_id: int) -> DatabaseConfig:
        """Get the runtime connection configuration for a defined worker."""

    @abstractmethod
    def collect_memory_utilization(self, worker_id: int) -> float:
        """Collect per-worker PostgreSQL memory utilization as a [0, 1] ratio."""
