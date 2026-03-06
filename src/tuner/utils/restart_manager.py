"""
PostgreSQL Restart Manager with Cost Modeling

Implements safe, scripted database restarts with:
1. Cross-platform restart support (pg_ctl, systemctl, Windows)
2. Restart cost modeling based on literature (CDBTune, OtterTune)
3. Backup/rollback safety mechanisms
4. Connection retry and validation

Research basis:
- CDBTune (SIGMOD 2019): Batched restart strategy
- OtterTune (SIGMOD 2017): Automated restart with cost acceptance
- QTune (arXiv 2019): RL reward function with restart penalty
"""

import logging
import subprocess
import time
import shutil
import platform
from pathlib import Path
from glob import glob
from typing import Optional
from dataclasses import dataclass
import psycopg2

from src.config.database import DatabaseConfig
from src.database.connection import get_connection
from src.tuner.utils.logger_config import WorkerLoggerAdapter

# Base logger - will be wrapped with WorkerLoggerAdapter in __init__
base_logger = logging.getLogger(__name__)


@dataclass
class RestartConfig:
    """Configuration for PostgreSQL restart behavior"""

    method: str = 'auto' # 'pg_ctl', 'systemctl', 'auto' (detect)
    timeout: int = 5
    max_retries: int = 10
    retry_delay: float = 3.0
    pg_ctl_path: Optional[str] = None # Path to pg_ctl binary (auto-detect if None)
    data_dir: Optional[str] = None # PostgreSQL data directory (needed for pg_ctl)
    service_name: str = 'postgresql'  # Service name for systemctl (Linux)
    backup_enabled: bool = True
    rollback_on_failure: bool = True


class RestartCostModel:
    """
    Models restart cost based on database tuning research
    
    Cost model from literature:
    - Base restart time: ~7 seconds (PostgreSQL startup median)
    - Cache warmup: ~10% of measurement duration
    - Batching: Amortize cost over N generations
    
    References:
    - CDBTune: Batch restarts every 10 iterations (3% amortized cost)
    - OtterTune: Accept ~25% cost per restart for 100x+ gains
    - QTune: Learned restart penalty converges to 5-15% of throughput
    """

    def __init__(
        self,
        base_restart_time: float = 7.0,
        cache_warmup_ratio: float = 0.1,
        restart_interval: int = 10
    ):
        """
        Initialize restart cost model
        
        Args:
            base_restart_time: Base PostgreSQL restart time (seconds)
                Literature value: 7s median (5-10s range)
            cache_warmup_ratio: Cache warmup as fraction of measurement time
                Literature value: 0.1 (10% of measurement duration)
            restart_interval: Batch restarts every N generations
                Literature value: 10 generations (CDBTune)
        """
        self.base_restart_time = base_restart_time
        self.cache_warmup_ratio = cache_warmup_ratio
        self.restart_interval = restart_interval

        base_logger.debug(
            "✓ Initialized RestartCostModel: base=%.1fs, warmup_ratio=%.1f, interval=%d",
            base_restart_time, cache_warmup_ratio, restart_interval
        )

    def calculate_raw_cost(self, measurement_duration: float) -> float:
        """
        Calculate raw restart cost (seconds)
        
        Args:
            measurement_duration: Duration of performance measurement (seconds)
        
        Returns:
            Total restart cost in seconds
        """
        cache_warmup = measurement_duration * self.cache_warmup_ratio
        return self.base_restart_time + cache_warmup

    def calculate_amortized_cost(
        self,
        measurement_duration: float,
        generation: int
    ) -> float:
        """
        Calculate amortized restart cost per generation
        
        With batching, cost is distributed across multiple generations
        
        Args:
            measurement_duration: Duration of measurement (seconds)
            generation: Current generation number
        
        Returns:
            Amortized cost per generation (seconds)
        """
        raw_cost = self.calculate_raw_cost(measurement_duration)

        if generation % self.restart_interval == 0:
            return raw_cost / self.restart_interval

        return 0.0

    def calculate_penalty_factor(
        self,
        measurement_duration: float,
        restart_occurred: bool,
        generation: Optional[int] = None
    ) -> float:
        """
        Calculate score penalty factor
        
        Args:
            measurement_duration: Duration of measurement (seconds)
            restart_occurred: Whether restart happened this generation
            generation: Generation number (for amortization calculation)
        
        Returns:
            Penalty factor to multiply score by (0.0-1.0)
            - 1.0 = no penalty
            - 0.75 = 25% penalty
            - 0.97 = 3% penalty (typical with batching)
        """
        if not restart_occurred:
            return 1.0

        if generation is not None:
            effective_cost = self.calculate_amortized_cost(measurement_duration, generation)
        else:
            effective_cost = self.calculate_raw_cost(measurement_duration)

        total_time = measurement_duration + effective_cost
        penalty_factor = measurement_duration / total_time

        return penalty_factor

    def apply_penalty(
        self,
        score: float,
        measurement_duration: float,
        restart_occurred: bool,
        generation: Optional[int] = None,
        logger: Optional[logging.Logger] = None
    ) -> float:
        """
        Apply restart penalty to score
        
        Args:
            score: Raw performance score
            measurement_duration: Duration of measurement (seconds)
            restart_occurred: Whether restart happened
            generation: Generation number (for batching)
            logger: Optional logger for worker-contextualized logging
        
        Returns:
            Adjusted score with restart penalty applied
        """
        penalty_factor = self.calculate_penalty_factor(
            measurement_duration, restart_occurred, generation
        )

        adjusted_score = score * penalty_factor

        if restart_occurred:
            penalty_pct = (1 - penalty_factor) * 100
            # Use provided logger (with worker context) or fallback to base_logger
            log = logger or base_logger
            log.debug(
                "Applied restart penalty: %.1f%% (score: %.4f -> %.4f)",
                penalty_pct, score, adjusted_score
            )

        return adjusted_score


class PostgresRestartManager:
    """
    Manages safe PostgreSQL restarts with backup/rollback
    
    Supports multiple restart methods:
    1. pg_ctl: Cross-platform, direct control
    2. systemctl: Linux service manager
    3. Windows Service Control (sc)
    
    Safety features:
    - Backup postgresql.auto.conf before restart
    - Validate connection after restart
    - Rollback on failure
    - Timeout protection
    """

    def __init__(
        self,
        db_config: DatabaseConfig,
        restart_config: Optional[RestartConfig] = None,
        worker_id: Optional[int] = None
    ):
        """
        Initialize restart manager
        
        Args:
            db_config: Database connection configuration
            restart_config: Restart behavior configuration
            worker_id: Worker ID for logging context (optional)
        """
        self.db_config = db_config
        self.config = restart_config or RestartConfig()
        self.worker_id = worker_id
        
        # Create worker-aware logger with proper coloring
        if worker_id is not None:
            self.logger = WorkerLoggerAdapter(base_logger, {'worker_id': worker_id})
        else:
            self.logger = base_logger

        # Detect data_dir first (needed by method detection)
        if not self.config.data_dir:
            self.config.data_dir = self._detect_data_dir()
        
        self.data_dir = self.config.data_dir

        if self.config.method == 'auto':
            self.config.method = self._detect_restart_method()

        self.logger.debug(
            "Initialized PostgresRestartManager with method: %s, data_dir: %s",
            self.config.method,
            self.config.data_dir
        )

    def _detect_restart_method(self) -> str:
        """Auto-detect best restart method for platform"""
        if self.data_dir:
            try:
                pg_ctl = self._find_pg_ctl()
                if pg_ctl:
                    return 'pg_ctl'
            except Exception:
                pass
        
        system = platform.system()

        if system == 'Linux':
            try:  # Check if systemctl available (for system-wide service only)
                subprocess.run(
                    ['systemctl', '--version'],
                    capture_output=True,
                    check=True,
                    timeout=5
                )
                return 'systemctl'
            except (subprocess.CalledProcessError, FileNotFoundError, subprocess.TimeoutExpired):
                pass

        try:  # Try pg_ctl as fallback
            pg_ctl = self._find_pg_ctl()
            if pg_ctl:
                return 'pg_ctl'
        except Exception:
            pass

        self.logger.warning("Could not auto-detect restart method, defaulting to pg_ctl")
        return 'pg_ctl'

    def _detect_data_dir(self) -> Optional[str]:
        """Auto-detect PostgreSQL data directory by querying the database"""
        try:
            conn = get_connection(self.db_config)
            try:
                with conn.cursor() as cur:
                    cur.execute("SHOW data_directory")
                    data_dir = cur.fetchone()[0]  # type: ignore
                    self.logger.debug("Auto-detected data_directory: %s", data_dir)
                    return data_dir
            finally:
                conn.close()
        except Exception as e:
            self.logger.warning("Could not auto-detect data_directory: %s", e)
            return None

    def _find_pg_ctl(self) -> Optional[str]:
        """Find pg_ctl binary path"""
        if self.config.pg_ctl_path:
            return self.config.pg_ctl_path

        common_paths = [  # Try common locations
            'pg_ctl',  # In PATH
            '/usr/bin/pg_ctl',
            '/usr/local/bin/pg_ctl',
            r'C:\Program Files\PostgreSQL\*\bin\pg_ctl.exe',
        ]

        for path in common_paths:
            if '*' in path:
                matches = glob(path)
                if matches:
                    return matches[0]
            else:
                try:
                    result = subprocess.run(
                        [path, '--version'],
                        capture_output=True,
                        timeout=5
                    )
                    if result.returncode == 0:
                        return path
                except (FileNotFoundError, subprocess.TimeoutExpired):
                    continue

        return None

    def backup_config(self) -> Optional[Path]:
        """
        Backup postgresql.auto.conf before restart
        
        Returns:
            Path to backup file, or None if backup disabled/failed
        """
        if not self.config.backup_enabled:
            return None

        try:
            conn = get_connection(self.db_config)
            try:
                with conn.cursor() as cur:
                    cur.execute("SHOW data_directory")
                    data_dir = cur.fetchone()[0]  # type: ignore
                    auto_conf = Path(data_dir) / 'postgresql.auto.conf'
            finally:
                conn.close()

            if auto_conf.exists():
                # Use parent directory to avoid .auto.auto.conf.backup
                backup_path = auto_conf.parent / 'postgresql.auto.conf.backup'
                shutil.copy2(auto_conf, backup_path)
                self.logger.info("Backed up postgresql.auto.conf to %s", backup_path)
                return backup_path
            else:
                self.logger.warning("postgresql.auto.conf not found at %s", auto_conf)
                return None

        except Exception as e:
            self.logger.error("Failed to backup config: %s", e)
            return None

    def restore_config(self, backup_path: Path) -> bool:
        """
        Restore configuration from backup and restart PostgreSQL
        
        Args:
            backup_path: Path to backup file
        
        Returns:
            True if restored successfully
        """
        try:
            auto_conf = backup_path.with_suffix('')
            shutil.copy2(backup_path, auto_conf)
            self.logger.debug("Restored postgresql.auto.conf from %s", backup_path)
            self.logger.debug("Attempting to start PostgreSQL with restored configuration...")

            if self.config.method == 'pg_ctl' and self.config.data_dir:
                pg_ctl = self._find_pg_ctl()
                if not pg_ctl:
                    self.logger.error("Cannot restart after rollback: pg_ctl not found")
                    return False

                self.logger.debug("Ensuring PostgreSQL is stopped before rollback restart...")
                try:
                    _ = subprocess.run(
                        [pg_ctl, 'stop', '-D', self.config.data_dir, '-m', 'immediate'],
                        capture_output=True,
                        timeout=5,
                        text=True
                    )
                    time.sleep(2)
                except subprocess.TimeoutExpired:
                    pass

                try:
                    process = subprocess.Popen(
                        [pg_ctl, 'start', '-D', self.config.data_dir],
                        stdout=subprocess.PIPE,
                        stderr=subprocess.PIPE,
                        text=True
                    )

                    try:
                        _, stderr = process.communicate(timeout=20)
                        returncode = process.returncode
                    except subprocess.TimeoutExpired:
                        self.logger.error(
                            "Timeout starting PostgreSQL with restored config, killing process"
                        )
                        process.kill()
                        try:
                            process.communicate(timeout=2)
                        except subprocess.TimeoutExpired:
                            pass
                        return False

                    if returncode == 0:
                        self.logger.info("PostgreSQL start command completed with restored config")
                        time.sleep(2)
                        return True
                    else:
                        self.logger.error(
                            "Failed to start PostgreSQL with restored config: %s",
                            stderr[:200] if stderr else "no error"
                        )
                        return False

                except Exception as e:
                    self.logger.error("Exception starting PostgreSQL with restored config: %s", e)
                    return False
            else:
                self.logger.warning(
                    "Cannot restart after rollback: method=%s, data_dir=%s",
                    self.config.method,
                    self.config.data_dir
                )
                return True  # Return True for config restoration even if can't restart

        except Exception as e:
            self.logger.error("Failed to restore config: %s", e)
            return False

    def restart(self) -> bool:
        """
        Perform database restart with safety checks
        
        Returns:
            True if restart successful, False otherwise
        """
        self.logger.info("Initiating PostgreSQL restart using %s", self.config.method)

        backup_path = self.backup_config()
        try:  # Execute restart
            if self.config.method == 'systemctl':
                success = self._restart_systemctl()
            elif self.config.method == 'pg_ctl':
                success = self._restart_pg_ctl()
            elif self.config.method == 'windows_service':
                success = self._restart_windows_service()
            else:
                self.logger.error("Unknown restart method: %s", self.config.method)
                return False

            if not success:
                self.logger.error("Restart command failed")
                if self.config.rollback_on_failure and backup_path:
                    self.restore_config(backup_path)
                return False

            if not self._wait_for_connection():
                self.logger.error("Database did not come back up after restart")
                if self.config.rollback_on_failure and backup_path:
                    self.restore_config(backup_path)
                return False

            if not self._validate_restart():
                self.logger.error("Restart validation failed")
                if self.config.rollback_on_failure and backup_path:
                    self.restore_config(backup_path)
                return False

            self.logger.info("PostgreSQL restart completed successfully")
            return True

        except Exception as e:
            self.logger.error("Restart failed with exception: %s", e)
            if self.config.rollback_on_failure and backup_path:
                self.restore_config(backup_path)
            return False

    def _restart_systemctl(self) -> bool:
        """Restart PostgreSQL using systemctl"""
        try:
            result = subprocess.run(
                ['sudo', 'systemctl', 'restart', self.config.service_name],
                capture_output=True,
                timeout=self.config.timeout,
                text=True
            )

            if result.returncode != 0:
                self.logger.error("systemctl restart failed: %s", result.stderr)
                return False

            return True

        except subprocess.TimeoutExpired:
            self.logger.error("systemctl restart timed out")
            return False
        except Exception as e:
            self.logger.error("systemctl restart error: %s", e)
            return False

    def _restart_pg_ctl(self) -> bool:
        """Restart PostgreSQL using pg_ctl"""
        pg_ctl = self._find_pg_ctl()
        if not pg_ctl:
            self.logger.error("pg_ctl not found")
            return False

        if not self.config.data_dir:
            self.logger.error("data_dir required for pg_ctl restart")
            return False

        try:
            self.logger.info("Stopping PostgreSQL...")
            # Use -w to wait for shutdown to complete, use fast mode instead of immediate
            # to avoid potential data corruption
            stop_result = subprocess.run(
                [pg_ctl, 'stop', '-D', self.config.data_dir, '-w', '-m', 'fast'],
                capture_output=True,
                timeout=30,  # Allow more time for clean shutdown
                text=True
            )

            if (
                stop_result.returncode != 0
                and "PG_CTL: no server running" not in stop_result.stderr
                and "no server running" not in stop_result.stderr.lower()
            ):
                self.logger.warning("pg_ctl stop warning: %s", stop_result.stderr)
            else:
                self.logger.info("PostgreSQL stopped successfully")

            # Wait longer after stop to ensure clean shutdown and PID file cleanup
            time.sleep(5)
            self.logger.debug("Waited 5 seconds after stop for cleanup")

            # Start PostgreSQL without waiting for pg_ctl to complete
            # On Windows, pg_ctl with -l flag blocks until server is ready, which takes too long
            # We'll issue the start command and immediately proceed to connection validation
            self.logger.info("Starting PostgreSQL...")
            
            try:
                # Start in background - don't wait for pg_ctl to complete
                subprocess.Popen(
                    [pg_ctl, 'start', '-D', self.config.data_dir, '-l', str(Path(self.config.data_dir) / 'logfile')],
                    stdout=subprocess.DEVNULL,
                    stderr=subprocess.DEVNULL,
                    creationflags=subprocess.CREATE_NEW_PROCESS_GROUP if platform.system() == 'Windows' else 0
                )
                self.logger.debug("Issued pg_ctl start command")
                
                # Give PostgreSQL a moment to begin startup
                time.sleep(2)
                
            except Exception as e:
                self.logger.warning("Error issuing pg_ctl start command: %s", e)
            
            # Proceed directly to connection validation
            # This is the reliable way to verify PostgreSQL is actually running
            return True

        except subprocess.TimeoutExpired:
            self.logger.error("pg_ctl restart timed out")
            return False
        except Exception as e:
            self.logger.error("pg_ctl restart error: %s", e)
            return False

    def _restart_windows_service(self) -> bool:
        """Restart PostgreSQL using Windows Service Control"""
        try:
            stop_result = subprocess.run(
                ['sc', 'stop', self.config.service_name],
                capture_output=True,
                timeout=15,
                text=True
            )

            start_result = subprocess.run(
                ['sc', 'start', self.config.service_name],
                capture_output=True,
                timeout=self.config.timeout,
                text=True
            )

            if start_result.returncode != 0:
                self.logger.error("Windows service restart failed: %s", start_result.stderr)
                return False

            return True

        except subprocess.TimeoutExpired:
            self.logger.error("Windows service restart timed out")
            return False
        except Exception as e:
            self.logger.error("Windows service restart error: %s", e)
            return False

    def _wait_for_connection(self) -> bool:
        """
        Wait for database to accept connections after restart
        
        Returns:
            True if connection established within timeout
        """
        self.logger.info("Waiting for database connection...")

        for attempt in range(self.config.max_retries):
            try:
                self.logger.debug("Connection attempt %d/%d...", attempt + 1, self.config.max_retries)
                conn = get_connection(self.db_config, connect_timeout=3)
                conn.close()
                self.logger.debug("Connection established after %d attempts", attempt + 1)
                return True
            except psycopg2.OperationalError as e:
                self.logger.debug("Connection attempt %d failed: %s", attempt + 1, str(e)[:100])
                if attempt < self.config.max_retries - 1:
                    time.sleep(self.config.retry_delay)
                continue
            except Exception as e:
                self.logger.error(
                    "Unexpected error during connection attempt %d: %s",
                    attempt + 1,
                    str(e)
                )
                return False

        self.logger.error("Could not connect after %d attempts", self.config.max_retries)
        return False

    def _validate_restart(self) -> bool:
        """
        Validate that database is functioning correctly after restart
        
        Returns:
            True if validation passes
        """
        try:
            conn = get_connection(self.db_config)
            try:
                with conn.cursor() as cur:
                    cur.execute("SELECT 1")
                    result = cur.fetchone()

                    if result[0] != 1:  # type: ignore
                        self.logger.error("Validation query returned unexpected result")
                        return False

                    cur.execute("SELECT pg_is_in_recovery()")
                    in_recovery = cur.fetchone()[0]  # type: ignore

                    if in_recovery:  # Not necessarily an error, but worth noting
                        self.logger.warning("Database is in recovery mode")

                    self.logger.info("Restart validation passed")
                    return True
            finally:
                conn.close()

        except Exception as e:
            self.logger.error("Restart validation failed: %s", e)
            return False
