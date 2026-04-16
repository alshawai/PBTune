"""
Bare-Metal Environment Implementation
======================================

Provides `BareMetalEnvironment` which implements `DatabaseEnvironment`
for direct execution on the host machine using `pg_ctl`.

Note: Bare-metal environments lack containerized resource isolation.
"""

import os
import signal
import time
import subprocess
import shutil
from pathlib import Path
from typing import List, Dict, Any
import psycopg2

from src.utils.environments.base import DatabaseEnvironment, InstanceConfig
from src.tuner.evaluator.executor import BenchmarkExecutor
from src.config.database import DatabaseConfig
from src.utils.logger import get_logger

logger = get_logger(__name__)


class BareMetalEnvironment(DatabaseEnvironment):
    """
    Bare-metal PostgreSQL environment for multi-worker parallel operations.
    
    Controls local PostgreSQL instances via `pg_ctl`. Lacks cgroup-level
    resource isolation. Relies on the host's existing PostgreSQL binaries.
    """

    def __init__(
        self,
        run_id: str,
        db_config: DatabaseConfig,
        schema_provider: BenchmarkExecutor,
        base_port: int = 5440,
        base_dir: Path = Path("./pg_instances"),
    ):
        super().__init__(run_id, db_config, schema_provider)
        self.base_port = base_port
        self.base_dir = base_dir
        self.instances: Dict[int, InstanceConfig] = {}

    def setup_instances(
            self,
            num_workers: int,
            force_recreate: bool = False
        ) -> List[InstanceConfig]:
        """Set up N database instances on the bare metal host."""
        if num_workers <= 0:
            raise ValueError("Must specify at least 1 worker")

        logger.info(
            "Setting up %d BareMetal PostgreSQL instances (force_recreate=%s)",
            num_workers,
            force_recreate
        )

        for worker_id in range(num_workers):
            port = self.base_port + worker_id
            data_dir = self.base_dir / f"worker_{worker_id}"

            # --- Phase 1: Clean up any pre-existing state ---
            # Do NOT use `pg_ctl stop -w` here — it blocks indefinitely if the
            # postmaster.pid references a process started by a different pg_ctl
            # invocation or one that was killed uncleanly (Ctrl+C / SIGKILL).
            # Instead, read the PID directly, signal it, and remove the file.
            pid_file = data_dir / "postmaster.pid"
            if pid_file.exists():
                try:
                    stale_pid = int(pid_file.read_text().splitlines()[0].strip())
                    logger.info("  Stopping pre-existing instance for worker %d (PID %d)...", worker_id, stale_pid)
                    os.kill(stale_pid, signal.SIGTERM)
                    # Wait briefly for graceful shutdown
                    for _ in range(10):
                        time.sleep(0.5)
                        try:
                            os.kill(stale_pid, 0)  # Check if still alive
                        except OSError:
                            break
                    else:
                        # Still alive after 5s — force kill
                        logger.warning("  Process %d did not exit gracefully, sending SIGKILL", stale_pid)
                        os.kill(stale_pid, signal.SIGKILL)
                        time.sleep(0.5)
                except (ValueError, OSError, IndexError):
                    pass  # PID file corrupt or process already dead
                pid_file.unlink(missing_ok=True)

            # Fallback: kill anything still holding the port (e.g. orphaned children)
            self._kill_stale_port_holder(port)

            # --- Phase 2: Ensure data directory is ready ---
            if force_recreate and data_dir.exists():
                shutil.rmtree(data_dir, ignore_errors=True)

            if not (data_dir / "PG_VERSION").exists():
                data_dir.parent.mkdir(parents=True, exist_ok=True)
                logger.info("Initializing new database cluster for worker %d at %s...", worker_id, data_dir)
                # Use --username to create the superuser role matching our config
                # (initdb defaults to the OS user which may differ from base_config.user).
                # --auth=trust allows TCP connections without password for local instances.
                subprocess.run(
                    ["initdb", "-D", str(data_dir),
                     f"--username={self.base_config.user}",
                     "--auth=trust"],
                    check=True,
                    capture_output=True
                )
                
                # Overwrite postgresql.conf to ensure the correct port is bound natively
                conf_path = data_dir / "postgresql.conf"
                with open(conf_path, "a") as f:
                    f.write(f"\nport = {port}\n")
                    f.write("unix_socket_directories = '/tmp'\n")
                    f.write("listen_addresses = '*'\n")

            # --- Phase 3: Start and verify ---
            instance = InstanceConfig(
                worker_id=worker_id,
                port=port,
                data_dir=data_dir,
                running=False
            )
            self.instances[worker_id] = instance
            
            self.start_instance(worker_id)
            self._wait_for_ready(worker_id)

            # Auto-initialize schema natively and leverage snapshots to accelerate parallel workers
            if self.schema_provider:
                logger.info("  Initializing schema for worker %d...", worker_id)
                self.initialize_schema(worker_id)
                if worker_id == 0:
                    logger.debug("  Caching worker 0 baseline snapshot for fast-path initialization...")
                    self.create_snapshot(worker_id=0)
            
        return list(self.instances.values())

    def start_instance(self, worker_id: int) -> bool:
        """Start a specific worker instance using pg_ctl."""
        data_dir = self.instances[worker_id].data_dir
        log_file = data_dir / "postgresql.log"
        try:
            subprocess.run(
                ["pg_ctl", "start", "-D", str(data_dir), "-w", "-t", "30",
                 "-l", str(log_file)],
                check=True,
                stdout=subprocess.DEVNULL,
                stderr=subprocess.PIPE,
                timeout=30
            )
            self.instances[worker_id].running = True
            return True
        except subprocess.TimeoutExpired:
            logger.error("Timed out starting instance %d", worker_id)
            return False
        except subprocess.CalledProcessError as e:
            logger.error("Failed to start instance %d: %s", worker_id, e.stderr)
            return False

    def stop_instance(self, worker_id: int, mode: str = 'fast') -> bool:
        """Stop a specific worker instance using pg_ctl."""
        data_dir = self.instances[worker_id].data_dir
        try:
            subprocess.run(
                ["pg_ctl", "stop", "-D", str(data_dir), "-m", mode, "-w"],
                check=True,
                capture_output=True
            )
            self.instances[worker_id].running = False
            return True
        except subprocess.CalledProcessError:
            return False

    def stop_all(self, mode: str = 'fast') -> bool:
        for worker_id in list(self.instances.keys()):
            self.stop_instance(worker_id, mode)
        return True

    def recover_instance(self, worker_id: int) -> bool:
        self.stop_instance(worker_id, mode='immediate')
        return self.start_instance(worker_id)

    def verify_instances(self) -> dict[int, bool]:
        res = {}
        for worker_id, inst in self.instances.items():
            try:
                subprocess.run(
                    ["pg_ctl", "status", "-D", str(inst.data_dir)],
                    check=True,
                    capture_output=True
                )
                res[worker_id] = True
            except subprocess.CalledProcessError:
                res[worker_id] = False
            inst.running = res[worker_id]
        return res

    def cleanup(self, remove_data: bool = False) -> None:
        self.stop_all(mode='immediate')
        if remove_data:
            for inst in self.instances.values():
                shutil.rmtree(inst.data_dir, ignore_errors=True)
        self.instances.clear()

    def apply_knobs(self, worker_id: int, knobs: Dict[str, Any]) -> None:
        """Apply a knob configuration."""
        from src.utils.applicator import KnobApplicator, ApplicatorConfig
        db_config = self.get_db_config(worker_id)
        config = ApplicatorConfig(
            persist=True,
            auto_reload=True,
            validate=True,
            rollback_on_error=False,
            allow_restart_params=True,
            auto_restart=False
        )
        applicator = KnobApplicator(db_config, config)
        result = applicator.apply(knobs)
        if result.restart_required:
            self.stop_instance(worker_id)
            self.start_instance(worker_id)
            self._wait_for_ready(worker_id)

    def create_snapshot(self, worker_id: int = 0) -> str:
        """Create a baseline snapshot from the specified worker instance using Rsync."""
        baseline_path = self.base_dir / "pbt-snapshot-baseline"

        self.stop_instance(worker_id)

        # Rsync the data
        shutil.rmtree(baseline_path, ignore_errors=True)
        subprocess.run([
            "rsync", "-a", "--delete",
            "--exclude", "postgresql.conf",
            "--exclude", "postmaster.pid",
            str(self.instances[worker_id].data_dir) + "/",
            str(baseline_path) + "/"
        ], check=True)

        self.start_instance(worker_id)
        return str(baseline_path)

    def restore_snapshot(self, worker_id: int, snapshot_id: str = "") -> bool:
        """Restore a targeted worker's data directory/volume from the baseline snapshot."""
        if not snapshot_id:
            snapshot_id = str(self.base_dir / "pbt-snapshot-baseline")

        snapshot_path = Path(snapshot_id)
        if not snapshot_path.exists():
            logger.debug("  No snapshot found at %s, skipping restore", snapshot_id)
            return False

        data_dir = self.instances[worker_id].data_dir
        self.stop_instance(worker_id, mode='immediate')
        
        try:
            subprocess.run([
                "rsync", "-a", "--delete",
                "--exclude", "postgresql.conf",
                snapshot_id + "/",
                str(data_dir) + "/"
            ], check=True)
        except subprocess.CalledProcessError as e:
            logger.error("Failed to restore snapshot for worker %d: %s", worker_id, e)
            self.start_instance(worker_id)
            self._wait_for_ready(worker_id)
            return False

        # Make sure persist configuration logic stays clean! (removes any postgresql.auto.conf)
        auto_conf = data_dir / "postgresql.auto.conf"
        if auto_conf.exists():
            auto_conf.unlink()

        self.start_instance(worker_id)
        self._wait_for_ready(worker_id)
        return True

    def get_db_config(self, worker_id: int) -> DatabaseConfig:
        port = self.base_port + worker_id
        return DatabaseConfig(
            host="127.0.0.1",
            port=port,
            dbname=self.base_config.dbname,
            user=self.base_config.user,
            password=self.base_config.password
        )

    def _wait_for_ready(self, worker_id: int, timeout=30) -> None:
        """Wait until PostgreSQL is accepting connections.
        
        Connects to the 'postgres' database (always exists after initdb)
        rather than the application database which may not exist yet.
        """
        port = self.base_port + worker_id

        start_time = time.time()
        while time.time() - start_time < timeout:
            try:
                conn = psycopg2.connect(
                    host="127.0.0.1",
                    port=port,
                    dbname="postgres",
                    user=self.base_config.user,
                    password=self.base_config.password,
                    connect_timeout=2
                )
                conn.close()
                logger.debug("  Worker %d is ready (took %.1fs)", worker_id, time.time() - start_time)
                return
            except psycopg2.OperationalError:
                time.sleep(0.5)
        raise RuntimeError(f"Database for worker {worker_id} failed to become ready within {timeout}s.")

    def _kill_stale_port_holder(self, port: int) -> None:
        """Kill any host process listening on the target port."""
        try:
            lsof_output = subprocess.check_output(
                ["lsof", "-t", f"-i:{port}"],
                text=True,
                stderr=subprocess.DEVNULL
            ).strip()

            if not lsof_output:
                return

            pids = lsof_output.split('\n')
            for pid in pids:
                pid = pid.strip()
                if pid:
                    logger.warning("Killing rogue process %s holding port %d", pid, port)
                    subprocess.run(["kill", "-9", pid], check=False)

            time.sleep(0.5)
        except (FileNotFoundError, subprocess.CalledProcessError):
            pass
