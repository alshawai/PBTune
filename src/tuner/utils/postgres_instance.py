"""
PostgreSQL Instance Manager for Per-Worker Isolation
====================================================

Manages individual PostgreSQL instances for each worker, ensuring complete
independence and true parallel evaluation.

Each worker gets:
- Dedicated PostgreSQL instance on unique port
- Isolated data directory
- Independent configuration state
- Separate connection

Architecture:
- Worker-0 → Instance on port 5432 (default)
- Worker-1 → Instance on port 5433
- Worker-2 → Instance on port 5434
- etc.

This ensures each worker's score reflects ONLY its own configuration,
with no interference from other workers.
"""

import logging
import time
import subprocess
from pathlib import Path
from typing import Dict, Any, Optional, Set
from enum import Enum

import psycopg2
from psycopg2.extensions import connection as PostgresConnection

from src.database.connection import get_connection
from src.config.database import DatabaseConfig
from src.tuner.utils.applicator import KnobApplicator, ApplicatorConfig
from src.tuner.utils.restart_manager import PostgresRestartManager, RestartConfig

logger = logging.getLogger(__name__)


class KnobCategory(Enum):
    """Knob categorization based on PostgreSQL context."""
    RESTART_REQUIRED = "restart_required"  # postmaster context
    RUNTIME_MODIFIABLE = "runtime_modifiable"  # All other contexts


class PostgresInstance:
    """
    Manages a single PostgreSQL instance for one worker.
    
    Provides complete isolation between workers by running separate
    PostgreSQL instances on different ports with independent configurations.
    
    Attributes
    ----------
    worker_id : int
        Worker identifier (0, 1, 2, ...)
    port : int
        PostgreSQL port for this instance
    db_config : DatabaseConfig
        Database configuration
    connection : Optional[PostgresConnection]
        Active connection to this instance
    restart_manager : PostgresRestartManager
        Manages instance start/stop/restart
    applicator : KnobApplicator
        Applies configuration changes
    restart_required_knobs : Set[str]
        Knobs that require restart (postmaster context)
    runtime_knobs : Set[str]
        Knobs that can be applied at runtime
    
    Example
    -------
    >>> # Create instance for Worker-0
    >>> instance = PostgresInstance(
    ...     worker_id=0,
    ...     base_port=5432,
    ...     db_config=DatabaseConfig.from_env()
    ... )
    >>> 
    >>> # Categorize knobs once
    >>> instance.categorize_knobs(knob_space)
    >>> 
    >>> # Initial setup with full config
    >>> instance.apply_full_config(worker.knob_config)
    >>> instance.start()
    >>> instance.connect()
    >>> 
    >>> # Runtime-only updates
    >>> instance.apply_runtime_config(worker.knob_config)
    >>> 
    >>> # Restart interval
    >>> instance.stop()
    >>> instance.apply_full_config(worker.knob_config)
    >>> instance.start()
    >>> instance.connect()
    """

    def __init__(
        self,
        worker_id: int,
        base_port: int,
        db_config: DatabaseConfig,
        restart_config: Optional[RestartConfig] = None
    ):
        """
        Initialize PostgreSQL instance manager for a worker.
        
        Parameters
        ----------
        worker_id : int
            Worker identifier (0-indexed)
        base_port : int
            Base PostgreSQL port (worker gets base_port + worker_id)
        db_config : DatabaseConfig
            Base database configuration
        restart_config : Optional[RestartConfig]
            Restart manager configuration
        """
        self.worker_id = worker_id
        self.port = base_port + worker_id
        
        # Create worker-specific database config
        self.db_config = DatabaseConfig(
            host=db_config.host,
            port=self.port,
            dbname=db_config.dbname,
            user=db_config.user,
            password=db_config.password
        )
        
        self.connection: Optional[PostgresConnection] = None
        self.restart_required_knobs: Set[str] = set()
        self.runtime_knobs: Set[str] = set()
        self._knobs_categorized = False
        
        # Initialize restart manager
        self.restart_config = restart_config or RestartConfig()
        self.restart_manager = PostgresRestartManager(
            db_config=self.db_config,
            restart_config=self.restart_config
        )
        
        # Initialize applicator (will be created after connection)
        self.applicator: Optional[KnobApplicator] = None
        
        logger.info(
            "[Worker-%d] Initialized PostgresInstance on port %d",
            self.worker_id, self.port
        )

    def categorize_knobs(self, knob_config: Dict[str, Any]) -> None:
        """
        Categorize knobs into restart-required vs runtime-modifiable.
        
        This should be called ONCE at initialization after the first connection.
        Queries pg_settings to determine each knob's context.
        
        Parameters
        ----------
        knob_config : Dict[str, Any]
            Sample knob configuration (to get knob names)
        """
        if self._knobs_categorized:
            logger.debug("[Worker-%d] Knobs already categorized", self.worker_id)
            return
        
        if not self.connection:
            raise RuntimeError("Must be connected to categorize knobs")
        
        knob_names = list(knob_config.keys())
        if not knob_names:
            logger.warning("[Worker-%d] Empty knob config for categorization", self.worker_id)
            return
        
        logger.info("[Worker-%d] Categorizing %d knobs...", self.worker_id, len(knob_names))
        
        try:
            cursor = self.connection.cursor()
            placeholders = ','.join(['%s'] * len(knob_names))
            query = f"""
                SELECT name, context
                FROM pg_settings
                WHERE name IN ({placeholders})
            """
            cursor.execute(query, knob_names)
            rows = cursor.fetchall()
            cursor.close()
            
            for name, context in rows:
                if context == 'postmaster':
                    self.restart_required_knobs.add(name)
                else:
                    # sighup, user, superuser, backend, superuser-backend
                    self.runtime_knobs.add(name)
            
            self._knobs_categorized = True
            
            logger.info(
                "[Worker-%d] Categorized: %d restart-required, %d runtime-modifiable",
                self.worker_id,
                len(self.restart_required_knobs),
                len(self.runtime_knobs)
            )
            
            if self.restart_required_knobs:
                logger.debug(
                    "[Worker-%d] Restart-required knobs: %s",
                    self.worker_id,
                    sorted(self.restart_required_knobs)
                )
            
        except psycopg2.Error as e:
            logger.error("[Worker-%d] Failed to categorize knobs: %s", self.worker_id, e)
            raise

    def connect(self) -> None:
        """
        Establish connection to this instance.
        
        Raises
        ------
        psycopg2.Error
            If connection fails
        """
        try:
            self.connection = get_connection(config=self.db_config)
            self.connection.autocommit = False
            logger.info("[Worker-%d] Connected to PostgreSQL on port %d", 
                       self.worker_id, self.port)
            
            # Initialize applicator after connection
            if self.applicator is None:
                applicator_config = ApplicatorConfig(
                    persist=True,
                    auto_reload=True,
                    validate=True,
                    dry_run=False,
                    rollback_on_error=False,  # Partial success allowed
                    allow_restart_params=True,
                    auto_restart=False  # We manage restarts manually
                )
                self.applicator = KnobApplicator(
                    connection_params=self.db_config.to_dict(),
                    config=applicator_config
                )
                # Reuse our connection
                self.applicator.connection = self.connection
            
        except psycopg2.Error as e:
            logger.error("[Worker-%d] Failed to connect: %s", self.worker_id, e)
            raise

    def disconnect(self) -> None:
        """Close connection to this instance."""
        if self.connection:
            self.connection.close()
            self.connection = None
            logger.info("[Worker-%d] Disconnected from PostgreSQL", self.worker_id)

    def apply_full_config(self, knob_config: Dict[str, Any]) -> None:
        """
        Apply FULL configuration (both restart-required and runtime knobs).
        
        Used during:
        - Initial setup (generation 0)
        - Restart interval generations (10, 20, 30, ...)
        
        Parameters
        ----------
        knob_config : Dict[str, Any]
            Complete knob configuration
        """
        if not self.applicator:
            raise RuntimeError("Applicator not initialized - call connect() first")
        
        logger.info("[Worker-%d] Applying FULL configuration (%d knobs)", 
                   self.worker_id, len(knob_config))
        
        result = self.applicator.apply(knob_config)
        
        logger.info(
            "[Worker-%d] Configuration applied: %d success, %d failed",
            self.worker_id, result.applied_count, result.failed_count
        )
        
        if result.failed:
            logger.warning(
                "[Worker-%d] Failed knobs: %s",
                self.worker_id,
                list(result.failed.keys())
            )

    def apply_runtime_config(self, knob_config: Dict[str, Any]) -> None:
        """
        Apply ONLY runtime-modifiable configuration.
        
        Used during runtime generations (1-9, 11-19, 21-29, ...).
        Filters out restart-required knobs to avoid unnecessary restart warnings.
        
        Parameters
        ----------
        knob_config : Dict[str, Any]
            Complete knob configuration (will be filtered)
        """
        if not self._knobs_categorized:
            raise RuntimeError("Knobs not categorized - call categorize_knobs() first")
        
        if not self.applicator:
            raise RuntimeError("Applicator not initialized - call connect() first")
        
        # Filter to only runtime-modifiable knobs
        runtime_config = {
            k: v for k, v in knob_config.items()
            if k in self.runtime_knobs
        }
        
        if not runtime_config:
            logger.debug("[Worker-%d] No runtime knobs to apply", self.worker_id)
            return
        
        logger.info("[Worker-%d] Applying RUNTIME configuration (%d knobs)", 
                   self.worker_id, len(runtime_config))
        
        result = self.applicator.apply(runtime_config)
        
        logger.info(
            "[Worker-%d] Runtime config applied: %d success, %d failed",
            self.worker_id, result.applied_count, result.failed_count
        )
        
        if result.failed:
            logger.warning(
                "[Worker-%d] Failed runtime knobs: %s",
                self.worker_id,
                list(result.failed.keys())
            )

    def start(self) -> bool:
        """
        Start this PostgreSQL instance.
        
        Returns
        -------
        bool
            True if start successful, False otherwise
        """
        logger.info("[Worker-%d] Starting PostgreSQL on port %d...", 
                   self.worker_id, self.port)
        
        success = self.restart_manager.restart()
        
        if success:
            logger.info("[Worker-%d] PostgreSQL started successfully", self.worker_id)
            # Give it a moment to initialize
            time.sleep(1)
        else:
            logger.error("[Worker-%d] Failed to start PostgreSQL", self.worker_id)
        
        return success

    def stop(self) -> bool:
        """
        Stop this PostgreSQL instance.
        
        Returns
        -------
        bool
            True if stop successful, False otherwise
        """
        logger.info("[Worker-%d] Stopping PostgreSQL on port %d...", 
                   self.worker_id, self.port)
        
        # Disconnect first
        self.disconnect()
        
        # Use restart manager's stop logic
        # (restart_manager.restart() includes stop, so we'll use it)
        # For now, we'll implement a simple stop
        try:
            if self.restart_config.data_dir:
                pg_ctl = self.restart_config.pg_ctl_path or 'pg_ctl'
                cmd = [pg_ctl, 'stop', '-D', self.restart_config.data_dir, '-m', 'fast']
                result = subprocess.run(
                    cmd,
                    capture_output=True,
                    text=True,
                    timeout=10
                )
                if result.returncode == 0:
                    logger.info("[Worker-%d] PostgreSQL stopped successfully", self.worker_id)
                    return True
                else:
                    logger.warning("[Worker-%d] pg_ctl stop returned: %s", 
                                 self.worker_id, result.returncode)
                    return False
            else:
                logger.warning("[Worker-%d] No data_dir configured, cannot stop", self.worker_id)
                return False
                
        except Exception as e:
            logger.error("[Worker-%d] Failed to stop PostgreSQL: %s", self.worker_id, e)
            return False

    def restart(self) -> bool:
        """
        Restart this PostgreSQL instance.
        
        Convenience method that calls stop() then start().
        
        Returns
        -------
        bool
            True if restart successful, False otherwise
        """
        logger.info("[Worker-%d] Restarting PostgreSQL...", self.worker_id)
        return self.restart_manager.restart()

    def is_running(self) -> bool:
        """
        Check if this instance is currently running.
        
        Returns
        -------
        bool
            True if instance is running and connectable
        """
        try:
            temp_conn = get_connection(config=self.db_config)
            temp_conn.close()
            return True
        except psycopg2.Error:
            return False

    def __repr__(self) -> str:
        """String representation."""
        status = "connected" if self.connection else "disconnected"
        return f"PostgresInstance(worker={self.worker_id}, port={self.port}, {status})"
