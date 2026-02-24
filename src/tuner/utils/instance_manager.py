"""
PostgreSQL Instance Manager for Parallel PBT

Manages multiple PostgreSQL instances for true parallel worker execution.
Each worker gets its own isolated PostgreSQL instance with unique port and data directory.

Architecture:
- Base port: 5432 (configurable)
- Worker N uses port: base_port + N
- Data directory: {base_dir}/worker_{N}
- Reuses existing instances when possible
"""

from __future__ import annotations
import subprocess
import shutil
import psutil
import time
import logging
from pathlib import Path
from dataclasses import dataclass
from typing import Optional, List, Dict
from psycopg2.extensions import ISOLATION_LEVEL_AUTOCOMMIT
import getpass

from src.config.database import DatabaseConfig
from src.database.connection import get_connection

logger = logging.getLogger(__name__)


@dataclass
class InstanceConfig:
    """Configuration for a single PostgreSQL instance."""
    worker_id: int
    port: int
    data_dir: Path
    running: bool = False
    pid: Optional[int] = None


class PostgresInstanceManager:
    """
    Manages multiple PostgreSQL instances for parallel worker execution.
    
    Responsibilities:
    - Create/initialize new PostgreSQL instances
    - Start/stop instances
    - Monitor instance health
    - Reuse existing instances from previous runs
    - Clean up resources
    """

    def __init__(
        self,
        base_dir: Path,
        base_port: int = 5432,
        template_db_config: Optional[DatabaseConfig] = None,
        table_size: int = 5000000,
        pg_ctl_path: Optional[str] = None,
        initdb_path: Optional[str] = None
    ):
        """
        Initialize the instance manager.
        
        Parameters
        ----------
        base_dir : Path
            Base directory for all worker instances
        base_port : int
            Base port number (worker N uses base_port + N)
        template_db_config : Optional[DatabaseConfig]
            Template database config (for schema/data)
        table_size : int
            Number of rows to insert into sbtest1 table (default: 5M)
        pg_ctl_path : Optional[str]
            Path to pg_ctl executable (auto-detected if None)
        initdb_path : Optional[str]
            Path to initdb executable (auto-detected if None)
        """
        self.base_dir = Path(base_dir)
        self.base_port = base_port
        self.template_db_config = template_db_config
        self.table_size = table_size
        self.instances: Dict[int, InstanceConfig] = {}

        # Auto-detect PostgreSQL binaries
        self.pg_ctl = pg_ctl_path or self._find_executable('pg_ctl')
        self.initdb = initdb_path or self._find_executable('initdb')
        self.pg_dump = self._find_executable('pg_dump')
        self.psql = self._find_executable('psql')

        if not self.pg_ctl:
            raise RuntimeError("pg_ctl not found. Please install PostgreSQL or specify path.")
        if not self.initdb:
            raise RuntimeError("initdb not found. Please install PostgreSQL or specify path.")

        logger.debug("✓ Initialized InstanceManager: base_dir=%s, base_port=%d\n", base_dir, base_port)

    def _find_executable(self, name: str) -> Optional[str]:
        """Find PostgreSQL executable in PATH or common locations."""
        path = shutil.which(name)  # Trying PATH first
        if path:
            return path

        # Try common PostgreSQL installation paths
        common_paths = [
            f"C:/Program Files/PostgreSQL/18/bin/{name}.exe",
            f"C:/Program Files/PostgreSQL/17/bin/{name}.exe",
            f"C:/Program Files/PostgreSQL/16/bin/{name}.exe",
            f"/usr/local/pgsql/bin/{name}",
            f"/usr/lib/postgresql/*/bin/{name}",
        ]

        for path_pattern in common_paths:
            if '*' in path_pattern:
                # Handle wildcard paths
                from glob import glob
                matches = glob(path_pattern)
                if matches:
                    return matches[0]
            elif Path(path_pattern).exists():
                return path_pattern

        logger.warning("Could not find %s in PATH or common locations", name)
        return None

    def setup_instances(self, num_workers: int, force_recreate: bool = False) -> List[InstanceConfig]:
        """
        Set up PostgreSQL instances for all workers.
        Reuses existing instances when possible unless force_recreate=True.
        
        Parameters
        ----------
        num_workers : int
            Number of worker instances needed
        force_recreate : bool
            If True, recreate all instances from scratch
        
        Returns
        -------
        List[InstanceConfig]
            List of configured instances
        """
        logger.info("Setting up %d PostgreSQL instances (force_recreate=%s)", num_workers, force_recreate)
        
        self.base_dir.mkdir(parents=True, exist_ok=True)
        
        for worker_id in range(num_workers):
            port = self.base_port + worker_id
            data_dir = self.base_dir / f"worker_{worker_id}"
            
            if not force_recreate and self._is_valid_instance(data_dir, port):
                logger.info("Reusing existing instance for worker-%d at %s (port %d)", worker_id, data_dir, port)

                if self._is_instance_running(data_dir):
                    logger.debug("Instance already running, skipping start")
                else:
                    self._start_instance_internal(data_dir)
                    time.sleep(2)  # Give PostgreSQL time to start

                if self.template_db_config:
                    self._ensure_postgres_user_exists(port)

                instance = InstanceConfig(
                    worker_id=worker_id,
                    port=port,
                    data_dir=data_dir,
                    running=True
                )
            else:
                # Create new instance
                if data_dir.exists():
                    logger.info("Removing old instance at %s", data_dir)
                    shutil.rmtree(data_dir)
                
                logger.info("Creating new instance for worker-%d at %s (port %d)", worker_id, data_dir, port)
                instance = self._create_instance(worker_id, port, data_dir)
            
            self.instances[worker_id] = instance
        
        return list(self.instances.values())
    
    def _is_valid_instance(self, data_dir: Path, expected_port: int) -> bool:
        """
        Check if a data directory contains a valid PostgreSQL instance with correct port.
        
        Parameters
        ----------
        data_dir : Path
            Instance data directory
        expected_port : int
            Port the instance should be configured for
            
        Returns
        -------
        bool
            True if valid and has correct port configuration
        """
        if not data_dir.exists():
            return False

        required_files = [
            'PG_VERSION',
            'postgresql.conf',
            'pg_hba.conf',
            'base',  # Database directory
        ]

        for file_name in required_files:
            if not (data_dir / file_name).exists():
                logger.debug("Missing required file: %s", file_name)
                return False

        conf_file = data_dir / 'postgresql.conf'
        try:
            with open(conf_file, 'r', encoding='utf-8') as f:
                for line in f:
                    if line.strip().startswith('port'):
                        parts = line.split('=')
                        if len(parts) == 2:
                            configured_port = int(parts[1].strip().split('#')[0])
                            if configured_port != expected_port:
                                logger.info(
                                    "Instance at %s configured for port %d, "
                                    "expected %d - needs recreation",
                                    data_dir, configured_port, expected_port
                                )
                                return False
                            break
        except (IOError, ValueError) as e:
            logger.warning("Could not verify port configuration in %s: %s", conf_file, e)
            return False

        return True

    def _is_instance_running(self, data_dir: Path) -> bool:
        """
        Check if a PostgreSQL instance is already running.
        
        Parameters
        ----------
        data_dir : Path
            Instance data directory
            
        Returns
        -------
        bool
            True if instance is running
        """
        pid_file = data_dir / 'postmaster.pid'
        if not pid_file.exists():
            return False

        try:
            with open(pid_file, 'r', encoding='utf-8') as f:
                pid = int(f.readline().strip())

            if psutil.pid_exists(pid):
                try:
                    proc = psutil.Process(pid)
                    if 'postgres' in proc.name().lower():
                        return True
                except (psutil.NoSuchProcess, psutil.AccessDenied):
                    pass

            return False

        except (IOError, ValueError, ImportError) as e:
            logger.debug("Could not verify if instance is running: %s", e)
            # If psutil not available, assume not running and try to start
            return False

    def _create_instance(self, worker_id: int, port: int, data_dir: Path) -> InstanceConfig:
        """Create and initialize a new PostgreSQL instance."""
        data_dir.mkdir(parents=True, exist_ok=True)

        # Step 1: Initialize data directory with initdb
        logger.debug("Running initdb for worker-%d...", worker_id)
        try:
            result = subprocess.run(
                [self.initdb, '-D', str(data_dir), '--encoding=UTF8', '--locale=C'],
                capture_output=True,
                text=True,
                timeout=60
            )
            if result.returncode != 0:
                raise RuntimeError(f"initdb failed: {result.stderr}")
        except subprocess.TimeoutExpired:
            raise RuntimeError("initdb timed out after 60 seconds")
        
        # Step 2: Configure postgresql.conf
        self._configure_instance(data_dir, port)
        
        # Step 3: Start the instance
        self._start_instance_internal(data_dir)
        
        # Step 4: Verify instance is running and create postgres user
        logger.debug("Verifying instance startup on port %d...", port)
        
        # initdb creates a user with current Windows username, not 'postgres'
        # We need to connect with that user first, then create 'postgres' user
        import getpass
        current_user = getpass.getuser()
        
        max_attempts = 15  # 15 attempts = 30 seconds max wait
        instance_conn = None
        for attempt in range(max_attempts):
            try:
                # Try to connect with Windows username (what initdb creates)
                test_config = DatabaseConfig(
                    host='localhost',
                    port=str(port),
                    dbname='postgres',
                    user=current_user,
                    password=''  # No password for local initdb user
                )
                instance_conn = get_connection(config=test_config)
                logger.debug("Instance verified running on port %d (attempt %d)", port, attempt + 1)
                break
            except Exception as e:
                if attempt == max_attempts - 1:
                    # Last attempt failed
                    logger.error("Instance failed to start on port %d after %d attempts", port, max_attempts)
                    logfile = data_dir / 'logfile'
                    if logfile.exists():
                        try:
                            with open(logfile, 'r', encoding='utf-8') as f:
                                logger.error("PostgreSQL log:\n%s", f.read())
                        except Exception:
                            pass
                    raise RuntimeError(f"Instance failed to start: {e}")
                time.sleep(2)  # Wait before retry
        
        # Step 4b: Create postgres superuser if template db config requires it
        if instance_conn and self.template_db_config and self.template_db_config.user != current_user:
            self._create_user_if_not_exists(instance_conn, self.template_db_config.user, self.template_db_config.password)
            
            # Step 4c: Create the application database (test_dataset)
            self._create_application_database(instance_conn, self.template_db_config.dbname)
            
            instance_conn.close()
            
            # Step 5: Initialize schema/data (create sbtest1 table)
            self._initialize_schema(port)
        elif instance_conn:
            instance_conn.close()
        
        logger.info("Successfully created instance for worker-%d on port %d", worker_id, port)
        
        return InstanceConfig(
            worker_id=worker_id,
            port=port,
            data_dir=data_dir,
            running=True
        )

    def _ensure_postgres_user_exists(self, port: int) -> None:
        """
        Ensure postgres user and application database exist on a 
        running instance (for reused instances).
        
        Parameters
        ----------
        port : int
            Port of the running instance
        """
        if not self.template_db_config:
            return

        current_user = getpass.getuser()

        # If template requires same user as Windows user, no need to create
        if self.template_db_config.user == current_user:
            return

        try:
            # Connect with Windows username
            test_config = DatabaseConfig(
                host='localhost',
                port=str(port),
                dbname='postgres',
                user=current_user,
                password=''
            )
            conn = get_connection(config=test_config)

            self._create_user_if_not_exists(
                conn,
                self.template_db_config.user,
                self.template_db_config.password
            )

            self._create_application_database(conn, self.template_db_config.dbname)

            conn.close()
            logger.debug("Ensured user '%s' exists on port %d", self.template_db_config.user, port)

        except Exception as e:
            logger.warning("Could not ensure user exists on port %d: %s", port, e)

    def _create_application_database(self, conn, dbname: str) -> None:
        """
        Create the application database if it doesn't exist.
        
        Parameters
        ----------
        conn : psycopg2.connection
            Active database connection (connected to postgres database)
        dbname : str
            Name of the database to create
        """
        try:

            old_isolation = conn.isolation_level
            conn.set_isolation_level(ISOLATION_LEVEL_AUTOCOMMIT)

            cursor = conn.cursor()

            cursor.execute(
                "SELECT 1 FROM pg_database WHERE datname = %s",
                (dbname,)
            )
            exists = cursor.fetchone() is not None

            if not exists:
                logger.debug("Creating database '%s'...", dbname)
                cursor.execute(f'CREATE DATABASE "{dbname}"')
                logger.debug("Created database '%s'", dbname)
            else:
                logger.debug("Database '%s' already exists", dbname)

            cursor.close()

            conn.set_isolation_level(old_isolation)

        except Exception as e:
            logger.warning("Could not create database '%s': %s", dbname, e)
            try:
                conn.set_isolation_level(old_isolation)
            except:
                pass

    def _create_user_if_not_exists(self, conn, username: str, password: str) -> None:
        """
        Create a PostgreSQL user if it doesn't already exist.
        
        Parameters
        ----------
        conn : psycopg2.connection
            Active database connection
        username : str
            Username to create
        password : str
            Password for the user
        """
        try:
            cursor = conn.cursor()

            cursor.execute(
                "SELECT 1 FROM pg_roles WHERE rolname = %s",
                (username,)
            )
            exists = cursor.fetchone() is not None

            if not exists:
                logger.debug("Creating user '%s'...", username)
                cursor.execute(
                    f"CREATE USER {username} WITH SUPERUSER PASSWORD %s",
                    (password,)
                )
                conn.commit()
                logger.debug("Created user '%s'", username)
            else:
                logger.debug("User '%s' already exists", username)

            cursor.close()

        except Exception as e:
            logger.warning("Could not create user '%s': %s", username, e)
            conn.rollback()

    def _configure_instance(self, data_dir: Path, port: int) -> None:
        """Configure PostgreSQL instance with appropriate settings."""
        conf_path = data_dir / 'postgresql.conf'

        # Read existing config
        with open(conf_path, 'r') as f:
            config_lines = f.readlines()

        custom_config = f"""
# Custom configuration for worker instance
port = {port}
logging_collector = off
log_destination = 'stderr'
# Use /tmp for Unix domain sockets to avoid path length issues
unix_socket_directories = '/tmp'
"""
        
        with open(conf_path, 'a') as f:
            f.write(custom_config)
        
        logger.debug("Configured instance at %s with port %d", data_dir, port)
    
    def _start_instance_internal(self, data_dir: Path) -> None:
        """Start a PostgreSQL instance."""
        logger.debug("Starting PostgreSQL instance at %s", data_dir)

        logfile = data_dir / 'logfile'

        try:
            startupinfo = None
            if hasattr(subprocess, 'STARTUPINFO'):
                startupinfo = subprocess.STARTUPINFO()
                startupinfo.dwFlags |= subprocess.STARTF_USESHOWWINDOW

            subprocess.Popen(
                [self.pg_ctl, '-D', str(data_dir), '-l', str(logfile), '-W', 'start'],
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
                startupinfo=startupinfo,
                creationflags=(
                    subprocess.CREATE_NEW_PROCESS_GROUP 
                    if hasattr(subprocess, 'CREATE_NEW_PROCESS_GROUP') else 0
                )
            )

            logger.debug("Issued start command for instance at %s", data_dir)

        except Exception as e:
            logger.warning("Error issuing pg_ctl start command: %s", e)
            # Continue anyway - connection test will tell us if it worked

    def _initialize_schema(self, port: int) -> None:
        """
        Initialize schema by creating required tables directly.
        
        Creates sbtest1 table (for OLTP workload) with sample data.
        This is more reliable than pg_dump/psql approach.
        """
        if not self.template_db_config:
            logger.debug("No template database configured, skipping schema init")
            return
        
        logger.debug("Initializing schema for instance on port %d", port)
        
        try:
            # Connect to the test_dataset database on this instance
            instance_config = DatabaseConfig(
                host='localhost',
                port=str(port),
                dbname=self.template_db_config.dbname,  # test_dataset
                user=self.template_db_config.user,  # postgres
                password=self.template_db_config.password or ''
            )
            
            conn = get_connection(config=instance_config)
            cursor = conn.cursor()
            
            # Check if sbtest1 table already exists
            cursor.execute(
                "SELECT EXISTS (SELECT FROM information_schema.tables "
                "WHERE table_schema = 'public' AND table_name = 'sbtest1')"
            )
            exists = cursor.fetchone()[0]
            
            if exists:
                logger.debug("Table 'sbtest1' already exists on port %d", port)
            else:
                logger.info("Creating sbtest1 table on port %d...", port)
                
                # Create sbtest1 table (SYSBENCH-compatible schema)
                cursor.execute("""
                    CREATE TABLE sbtest1 (
                        id SERIAL PRIMARY KEY,
                        k INTEGER NOT NULL DEFAULT 0,
                        c CHAR(120) NOT NULL DEFAULT '',
                        pad CHAR(60) NOT NULL DEFAULT ''
                    )
                """)
                
                # Insert sample data in batches
                import random
                logger.debug("Inserting sample data into sbtest1...")
                batch_size = 1000
                total_rows = self.table_size

                for batch_start in range(0, total_rows, batch_size):
                    values = []
                    for _ in range(batch_start, min(batch_start + batch_size, total_rows)):
                        k = random.randint(1, 100000)
                        c = ('x' * random.randint(50, 120))[:120].ljust(120)
                        pad = ('y' * random.randint(30, 60))[:60].ljust(60)
                        # Escape single quotes in strings
                        c_escaped = c.replace("'", "''")
                        pad_escaped = pad.replace("'", "''")
                        values.append(f"({k}, '{c_escaped}', '{pad_escaped}')")
                    
                    cursor.execute(f"INSERT INTO sbtest1 (k, c, pad) VALUES {','.join(values)}")
                
                # Create index
                logger.debug("Creating indexes on sbtest1...")
                cursor.execute("CREATE INDEX k_1 ON sbtest1(k)")
                
                # Analyze table
                cursor.execute("ANALYZE sbtest1")
                
                conn.commit()
                logger.info("Schema initialized successfully on port %d (sbtest1: %d rows)", port, total_rows)
            
            cursor.close()
            conn.close()
            
        except Exception as e:
            logger.error("Failed to initialize schema on port %d: %s", port, e)
            # Don't raise - allow instance to be used even without schema
    
    def start_instance(self, worker_id: int) -> bool:
        """
        Start a specific worker's PostgreSQL instance.
        
        Parameters
        ----------
        worker_id : int
            Worker ID
        
        Returns
        -------
        bool
            True if started successfully
        """
        if worker_id not in self.instances:
            logger.error("No instance configured for worker-%d", worker_id)
            return False
        
        instance = self.instances[worker_id]
        
        if instance.running:
            logger.debug("Instance for worker-%d already running", worker_id)
            return True
        
        try:
            self._start_instance_internal(instance.data_dir)
            instance.running = True
            logger.info("Started instance for worker-%d on port %d", worker_id, instance.port)
            return True
        except Exception as e:
            logger.error("Failed to start instance for worker-%d: %s", worker_id, e)
            return False
    
    def stop_instance(self, worker_id: int, mode: str = 'fast') -> bool:
        """
        Stop a specific worker's PostgreSQL instance.
        
        Parameters
        ----------
        worker_id : int
            Worker ID
        mode : str
            Shutdown mode: 'smart', 'fast', or 'immediate'
        
        Returns
        -------
        bool
            True if stopped successfully
        """
        if worker_id not in self.instances:
            logger.error("No instance configured for worker-%d", worker_id)
            return False
        
        instance = self.instances[worker_id]
        
        if not instance.running:
            logger.debug("Instance for worker-%d already stopped", worker_id)
            return True
        
        logger.info("Stopping instance for worker-%d (mode=%s)", worker_id, mode)
        
        try:
            result = subprocess.run(
                [self.pg_ctl, '-D', str(instance.data_dir), 'stop', '-m', mode],
                capture_output=True,
                text=True,
                timeout=30
            )
            if result.returncode not in [0, 1]:  # 1 = not running
                logger.warning("pg_ctl stop returned %d: %s", result.returncode, result.stderr)
            
            instance.running = False
            logger.info("Stopped instance for worker-%d", worker_id)
            return True
        except subprocess.TimeoutExpired:
            logger.error("pg_ctl stop timed out for worker-%d", worker_id)
            return False
        except Exception as e:
            logger.error("Failed to stop instance for worker-%d: %s", worker_id, e)
            return False
    
    def start_all(self) -> bool:
        """Start all configured instances."""
        logger.info("Starting all %d instances...", len(self.instances))
        success = True
        
        for worker_id in self.instances:
            if not self.start_instance(worker_id):
                success = False
        
        return success
    
    def stop_all(self, mode: str = 'fast') -> bool:
        """Stop all running instances."""
        logger.info("Stopping all %d instances...", len(self.instances))
        success = True
        
        for worker_id in self.instances:
            if not self.stop_instance(worker_id, mode=mode):
                success = False
        
        return success
    
    def get_instance_config(self, worker_id: int) -> Optional[InstanceConfig]:
        """Get configuration for a specific worker instance."""
        return self.instances.get(worker_id)
    
    def verify_instances(self) -> Dict[int, bool]:
        """
        Verify all instances are accessible.
        
        Returns
        -------
        Dict[int, bool]
            Map of worker_id -> connection_successful
        """
        logger.info("Verifying %d instances...", len(self.instances))
        results = {}
        
        for worker_id, instance in self.instances.items():
            try:
                test_config = DatabaseConfig(
                    host='localhost',
                    port=instance.port,
                    dbname='postgres',
                    user=self.template_db_config.user if self.template_db_config else 'postgres',
                    password=self.template_db_config.password if self.template_db_config else ''
                )
                
                conn = get_connection(test_config)
                cursor = conn.cursor()
                cursor.execute("SELECT 1")
                cursor.fetchone()
                cursor.close()
                conn.close()
                
                results[worker_id] = True
                logger.debug("✓ Worker-%d instance accessible on port %d", worker_id, instance.port)
            except Exception as e:
                results[worker_id] = False
                logger.error("✗ Worker-%d instance NOT accessible: %s", worker_id, e)
        
        success_count = sum(results.values())
        logger.info("Verification complete: %d/%d instances accessible", success_count, len(results))
        
        return results
    
    def cleanup(self, remove_data: bool = False) -> None:
        """
        Clean up resources.
        
        Parameters
        ----------
        remove_data : bool
            If True, also remove data directories
        """
        logger.info("Cleaning up instance manager (remove_data=%s)", remove_data)
        
        # Stop all instances
        self.stop_all(mode='immediate')
        
        # Remove data directories if requested
        if remove_data:
            for instance in self.instances.values():
                if instance.data_dir.exists():
                    logger.info("Removing data directory: %s", instance.data_dir)
                    shutil.rmtree(instance.data_dir)
        
        self.instances.clear()
