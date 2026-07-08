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
import tempfile
from pathlib import Path
from typing import List, Dict, Optional, TYPE_CHECKING
import psycopg2
import psutil

from src.utils.environments.base import DatabaseEnvironment, InstanceConfig
from src.benchmarks.executor import BenchmarkExecutor
from src.config.database import DatabaseConfig
from src.database.connection import get_connection
from src.utils.logger import get_logger

if TYPE_CHECKING:
    from src.utils.hardware_info import WorkerResources

logger = get_logger("BareMetalEnvironment")


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
        base_dir: Path = Path("./.instances"),
        ram_bytes: int = 0,
        worker_resources: Optional["WorkerResources"] = None,
        force_recreate_baseline: bool = False,
    ):
        """Initialize bare metal environment with configuration."""
        logger.info(
            "Initializing BareMetalEnvironment with base_port=%d, base_dir=%s...",
            base_port,
            base_dir,
        )
        super().__init__(
            run_id,
            db_config,
            schema_provider,
            force_recreate_baseline=force_recreate_baseline,
        )
        self.base_port = base_port
        self.base_dir = base_dir
        self.ram_bytes = ram_bytes
        self.worker_resources = worker_resources
        self.instances: Dict[int, InstanceConfig] = {}
        # Bare-metal does not use Docker; declared explicitly for symmetry
        # with ``DockerEnvironment`` so SessionEnvironment can serialize
        # both backends through the same code path.
        self.docker_version = None
        # Number of parallel workers, populated in ``setup_instances``.
        # Default of 1 keeps resource queries safe before setup runs.
        self._num_parallel_workers = 1

        logger.debug(
            "➤ Initialized BareMetalEnvironment with base_port=%d, base_dir=%s",
            self.base_port,
            self.base_dir,
        )

    def _worker_root_dir(self, worker_id: int) -> Path:
        """Return the root directory that groups all files for a worker."""
        return self.base_dir / self._get_instance_subpath() / f"worker_{worker_id}"

    def _resolve_worker_data_dir(self, worker_id: int, force_recreate: bool) -> Path:
        """Choose the PGDATA directory for a worker.

        Bare-metal historically stored clusters directly under the worker root.
        New runs prefer a dedicated ``pgdata`` subdirectory so the layout matches
        the Docker backend and stale root-level files do not block initialization.
        """
        worker_root = self._worker_root_dir(worker_id)
        nested_pgdata = worker_root / "pgdata"

        if not force_recreate:
            if (nested_pgdata / "PG_VERSION").exists():
                return nested_pgdata
            if (worker_root / "PG_VERSION").exists():
                return worker_root

        return nested_pgdata

    def _prepare_worker_data_dir(self, worker_id: int, force_recreate: bool) -> Path:
        """Ensure the worker PGDATA directory exists and is writable."""
        worker_root = self._worker_root_dir(worker_id)
        data_dir = self._resolve_worker_data_dir(worker_id, force_recreate)
        worker_root.mkdir(parents=True, exist_ok=True)

        if force_recreate and data_dir.exists():
            try:
                shutil.rmtree(data_dir)
            except OSError as exc:
                logger.warning(
                    "Unable to remove existing PGDATA directory for worker %d at %s: %s. "
                    "Using a fresh directory instead.",
                    worker_id,
                    data_dir,
                    exc,
                )
                data_dir = Path(
                    tempfile.mkdtemp(
                        prefix=f"worker_{worker_id}_pgdata_", dir=str(worker_root)
                    )
                )
            else:
                data_dir.mkdir(parents=True, exist_ok=True)
        else:
            data_dir.mkdir(parents=True, exist_ok=True)

        return data_dir

    def setup_instances(
        self,
        num_workers: int,
        force_recreate: bool = False,
        num_parallel_workers: int = 1,
    ) -> List[InstanceConfig]:
        """Set up N database instances on the bare metal host."""
        if num_workers <= 0:
            raise ValueError("Must specify at least 1 worker")

        # Track parallelism on the instance so SessionEnvironment / resource
        # allocation queries can read it back later.
        self._num_parallel_workers = num_parallel_workers

        logger.info(
            "Setting up %d BareMetal PostgreSQL instances (force_recreate=%s)",
            num_workers,
            force_recreate,
        )

        if self.force_recreate_baseline:
            self._remove_baseline_snapshot()

        for worker_id in range(num_workers):
            port = self.base_port + worker_id
            data_dir = self._prepare_worker_data_dir(worker_id, force_recreate)

            # --- Phase 1: Clean up any pre-existing state ---
            # Do NOT use `pg_ctl stop -w` here — it blocks indefinitely if the
            # postmaster.pid references a process started by a different pg_ctl
            # invocation or one that was killed uncleanly (Ctrl+C / SIGKILL).
            # Instead, read the PID directly, signal it, and remove the file.
            pid_file = data_dir / "postmaster.pid"
            if pid_file.exists():
                try:
                    stale_pid = int(pid_file.read_text().splitlines()[0].strip())
                    logger.info(
                        "  Stopping pre-existing instance for worker %d (PID %d)...",
                        worker_id,
                        stale_pid,
                    )
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
                        logger.warning(
                            "  Process %d did not exit gracefully, sending SIGKILL",
                            stale_pid,
                        )
                        os.kill(stale_pid, signal.SIGKILL)
                        time.sleep(0.5)
                except (ValueError, OSError, IndexError):
                    pass  # PID file corrupt or process already dead
                pid_file.unlink(missing_ok=True)

            # Fallback: kill anything still holding the port (e.g. orphaned children)
            self._kill_stale_port_holder(port)

            # --- Phase 2: Ensure data directory is ready ---
            if not (data_dir / "PG_VERSION").exists():
                logger.info(
                    "Initializing new database cluster for worker %d at %s...",
                    worker_id,
                    data_dir,
                )
                # Use --username to create the superuser role matching our config
                # (initdb defaults to the OS user which may differ from base_config.user).
                # --auth=trust allows TCP connections without password for local instances.
                subprocess.run(
                    [
                        "initdb",
                        "-D",
                        str(data_dir),
                        f"--username={self.base_config.user}",
                        "--auth=trust",
                    ],
                    check=True,
                    capture_output=True,
                    text=True,
                )

                # Overwrite postgresql.conf to ensure the correct port is bound natively
                conf_path = data_dir / "postgresql.conf"
                with open(conf_path, "a", encoding="utf-8") as f:
                    f.write(f"\nport = {port}\n")
                    f.write("unix_socket_directories = '/tmp'\n")
                    f.write("listen_addresses = '*'\n")

            # --- Phase 3: Start and verify ---
            instance = InstanceConfig(
                worker_id=worker_id, port=port, data_dir=data_dir, running=False
            )
            self.instances[worker_id] = instance

            self.start_instance(worker_id)
            self._wait_for_ready(worker_id)

            # Auto-initialize schema natively and leverage snapshots to accelerate parallel workers
            if self.schema_provider:
                logger.info("  Initializing schema for worker %d...", worker_id)
                self.initialize_schema(worker_id)
                if worker_id == 0:
                    baseline_snapshot = self._resolve_snapshot_path()
                    if baseline_snapshot.exists():
                        logger.debug(
                            "  Baseline snapshot already exists at %s; skipping recreation",
                            baseline_snapshot,
                        )
                    else:
                        logger.debug(
                            "  Caching worker 0 baseline snapshot for fast-path initialization..."
                        )
                        self.create_snapshot(worker_id=0)

        return list(self.instances.values())

    def start_instance(self, worker_id: int) -> bool:
        """Start a specific worker instance using pg_ctl."""
        data_dir = self.instances[worker_id].data_dir
        log_file = data_dir / "postgresql.log"
        try:
            subprocess.run(
                [
                    "pg_ctl",
                    "start",
                    "-D",
                    str(data_dir),
                    "-w",
                    "-t",
                    "30",
                    "-l",
                    str(log_file),
                ],
                check=True,
                stdout=subprocess.DEVNULL,
                stderr=subprocess.PIPE,
                timeout=30,
            )
            self.instances[worker_id].running = True
            return True
        except subprocess.TimeoutExpired:
            logger.error("Timed out starting instance %d", worker_id)
            return False
        except subprocess.CalledProcessError as e:
            logger.error("Failed to start instance %d: %s", worker_id, e.stderr)
            return False

    def stop_instance(self, worker_id: int, mode: str = "fast") -> bool:
        """Stop a specific worker instance using pg_ctl."""
        data_dir = self.instances[worker_id].data_dir
        try:
            subprocess.run(
                ["pg_ctl", "stop", "-D", str(data_dir), "-m", mode, "-w"],
                check=True,
                capture_output=True,
            )
            self.instances[worker_id].running = False
            return True
        except subprocess.CalledProcessError:
            return False

    def stop_all(self, mode: str = "fast") -> bool:
        """Stop all worker instances."""
        for worker_id in list(self.instances.keys()):
            self.stop_instance(worker_id, mode)
        return True

    def recover_instance(self, worker_id: int) -> bool:
        """Recover a worker instance by stopping and restarting it."""
        self.stop_instance(worker_id, mode="immediate")
        return self.start_instance(worker_id)

    def restart_instance(self, worker_id: int, quiet: bool = False) -> bool:
        """Restart a specific worker's PostgreSQL instance via pg_ctl."""
        if not quiet:
            logger.info("Restarting bare-metal worker %d...", worker_id)
        if not self.stop_instance(worker_id):
            logger.warning(
                "Stop failed for worker %d, attempting start anyway", worker_id
            )
        success = self.start_instance(worker_id)
        if success:
            if not self.reset_statistics(worker_id):
                logger.warning(
                    "Worker %d restarted but statistics reset failed.",
                    worker_id,
                )

            if not quiet:
                logger.info("Worker %d restarted successfully.", worker_id)
        else:
            logger.error("Worker %d restart failed.", worker_id)
        return success

    def verify_instances(self) -> None:
        """Verify the status of all worker instances."""
        for worker_id, inst in self.instances.items():
            try:
                subprocess.run(
                    ["pg_ctl", "status", "-D", str(inst.data_dir)],
                    check=True,
                    capture_output=True,
                )
            except subprocess.CalledProcessError:
                raise RuntimeError(
                    f"Failed to verify instance for worker {worker_id}"
                ) from None

    def cleanup(self, remove_data: bool = False) -> None:
        self.stop_all(mode="immediate")
        if remove_data:
            for inst in self.instances.values():
                shutil.rmtree(inst.data_dir, ignore_errors=True)
        self.instances.clear()

    def _resolve_snapshot_path(self, snapshot_id: str = "") -> Path:
        """Resolve a snapshot identifier to an absolute snapshot directory path."""
        if snapshot_id:
            return Path(snapshot_id)
        return self.base_dir / "pg_snapshots" / self.run_id

    def _remove_baseline_snapshot(self) -> None:
        """Remove the baseline snapshot directory if it exists."""
        baseline_snapshot = self._resolve_snapshot_path()
        if baseline_snapshot.exists():
            logger.info(
                "Removing baseline snapshot at %s (force_recreate_baseline=True)",
                baseline_snapshot,
            )
            shutil.rmtree(baseline_snapshot, ignore_errors=True)

    def create_snapshot(self, worker_id: int = 0) -> str:
        """Create a baseline snapshot from the specified worker instance using Rsync."""
        baseline_path = self._resolve_snapshot_path()

        self.stop_instance(worker_id)

        # Rsync the data
        baseline_path.parent.mkdir(parents=True, exist_ok=True)
        shutil.rmtree(baseline_path, ignore_errors=True)
        subprocess.run(
            [
                "rsync",
                "-a",
                "--delete",
                "--exclude",
                "postgresql.conf",
                "--exclude",
                "postmaster.pid",
                "--exclude",
                "postgresql.auto.conf",
                str(self.instances[worker_id].data_dir) + "/",
                str(baseline_path) + "/",
            ],
            check=True,
        )

        self.start_instance(worker_id)
        return str(baseline_path)

    def restore_snapshot(
        self, worker_id: int, snapshot_id: str = "", quiet: bool = False
    ) -> bool:
        """Restore a targeted worker's data directory/volume from the baseline snapshot."""
        snapshot_path = self._resolve_snapshot_path(snapshot_id)
        if not snapshot_path.exists():
            logger.debug("  No snapshot found at %s, skipping restore", snapshot_path)
            return False

        data_dir = self.instances[worker_id].data_dir
        self.stop_instance(worker_id, mode="immediate")

        try:
            subprocess.run(
                [
                    "rsync",
                    "-a",
                    "--delete",
                    "--exclude",
                    "postgresql.conf",
                    "--exclude",
                    "postgresql.auto.conf",
                    str(snapshot_path) + "/",
                    str(data_dir) + "/",
                ],
                check=True,
            )
        except subprocess.CalledProcessError as e:
            logger.error("Failed to restore snapshot for worker %d: %s", worker_id, e)
            self.start_instance(worker_id)
            self._wait_for_ready(worker_id)
            return False

        self.start_instance(worker_id)
        self._wait_for_ready(worker_id)
        return True

    def clone_instances(
        self, source_worker_id: int, target_worker_ids: List[int]
    ) -> bool:
        """Clone the physical database state from a source worker to multiple target workers using Rsync."""
        if not target_worker_ids:
            return True

        source_data_dir = self.instances[source_worker_id].data_dir

        # Stop source to ensure consistency
        self.stop_instance(source_worker_id, mode="immediate")

        # Stop all targets
        for target_id in target_worker_ids:
            self.stop_instance(target_id, mode="immediate")

        success = True
        try:
            for target_id in target_worker_ids:
                target_data_dir = self.instances[target_id].data_dir
                try:
                    subprocess.run(
                        [
                            "rsync",
                            "-a",
                            "--delete",
                            "--exclude",
                            "postgresql.conf",
                            "--exclude",
                            "postgresql.auto.conf",
                            str(source_data_dir) + "/",
                            str(target_data_dir) + "/",
                        ],
                        check=True,
                    )

                except subprocess.CalledProcessError as e:
                    logger.error(
                        "Failed to clone instance %d to %d: %s",
                        source_worker_id,
                        target_id,
                        e,
                    )
                    success = False
        finally:
            # Always restart source
            self.start_instance(source_worker_id)
            self._wait_for_ready(source_worker_id)

            # Restart all targets
            for target_id in target_worker_ids:
                self.start_instance(target_id)
                try:
                    self._wait_for_ready(target_id)
                except RuntimeError as e:
                    logger.error(
                        "Target %d failed to restart after clone: %s", target_id, e
                    )
                    success = False

        return success

    def rebuild_worker_instance(self, worker_id: int) -> bool:
        """Rebuild a worker instance from scratch."""
        self.stop_instance(worker_id)
        self.cleanup(remove_data=True)
        return self.setup_instances(1, force_recreate=True)[0].worker_id == worker_id

    def get_db_config(self, worker_id: int) -> DatabaseConfig:
        port = self.base_port + worker_id
        return DatabaseConfig(
            host="127.0.0.1",
            port=port,
            dbname=self.base_config.dbname,
            user=self.base_config.user,
            password=self.base_config.password,
        )

    def get_resource_allocations(self):
        """Return per-worker resource allocations.

        Bare-metal does not enforce cgroup-style isolation, so ``cpuset_cpus``
        and ``docker_memory_limit_bytes`` are always ``None``. CPU/RAM
        figures come from the ``worker_resources`` passed at construction
        when available; otherwise reasonable host-derived fallbacks are
        used so the JSON record is still populated.
        """
        from src.utils.types import WorkerResourceAllocation

        worker_ids = sorted(self.instances.keys()) or [0]
        if self.worker_resources is not None:
            per_worker_ram = int(self.worker_resources.ram_bytes)
            per_worker_cpu = int(self.worker_resources.cpu_cores)
        else:
            num_workers = max(1, len(worker_ids))
            per_worker_ram = (
                int(self.ram_bytes) if self.ram_bytes > 0
                else int(psutil.virtual_memory().total / num_workers)
            )
            per_worker_cpu = max(1, (psutil.cpu_count(logical=True) or 1) // num_workers)
        return [
            WorkerResourceAllocation(
                worker_id=worker_id,
                cpu_cores=per_worker_cpu,
                cpuset_cpus=None,
                ram_bytes=per_worker_ram,
                docker_memory_limit_bytes=None,
            )
            for worker_id in worker_ids
        ]

    def collect_memory_utilization(self, worker_id: int) -> float:
        """Collect PostgreSQL RSS utilization ratio against worker memory budget."""
        db_config = self.get_db_config(worker_id)

        try:
            conn = get_connection(db_config, connect_timeout=5)
            try:
                cur = conn.cursor()
                cur.execute("SELECT pg_backend_pid()")
                row = cur.fetchone()
                if row is None or row[0] is None:
                    return 0.0
                backend_pid = int(row[0])
                cur.close()
            finally:
                conn.close()
        except psycopg2.Error as exc:
            logger.debug(
                "Memory collection connect/query failed for worker %d: %s",
                worker_id,
                exc,
            )
            return 0.0

        try:
            backend_process = psutil.Process(backend_pid)
            postmaster_process = backend_process.parent()
            if postmaster_process is None:
                return 0.0

            total_rss = float(postmaster_process.memory_info().rss)
            for proc in postmaster_process.children(recursive=True):
                try:
                    total_rss += float(proc.memory_info().rss)
                except (
                    psutil.NoSuchProcess,
                    psutil.AccessDenied,
                    psutil.ZombieProcess,
                ):
                    continue

            denominator = (
                float(self.ram_bytes)
                if self.ram_bytes > 0
                else float(psutil.virtual_memory().total)
            )
            if denominator <= 0.0:
                return 0.0
            return max(0.0, min(1.0, total_rss / denominator))
        except (psutil.NoSuchProcess, psutil.AccessDenied, ValueError) as exc:
            logger.debug(
                "Memory RSS aggregation failed for worker %d: %s", worker_id, exc
            )
            return 0.0

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
                    connect_timeout=2,
                )
                conn.close()
                logger.debug(
                    "  Worker %d is ready (took %.1fs)",
                    worker_id,
                    time.time() - start_time,
                )
                return
            except psycopg2.OperationalError:
                time.sleep(0.5)
        raise RuntimeError(
            f"Database for worker {worker_id} failed to become ready within {timeout}s."
        )

    def _kill_stale_port_holder(self, port: int) -> None:
        """Kill any host process listening on the target port."""
        try:
            lsof_output = subprocess.check_output(
                ["lsof", "-t", f"-i:{port}"], text=True, stderr=subprocess.DEVNULL
            ).strip()

            if not lsof_output:
                return

            pids = lsof_output.split("\n")
            for pid in pids:
                pid = pid.strip()
                if pid:
                    logger.warning(
                        "Killing rogue process %s holding port %d", pid, port
                    )
                    subprocess.run(["kill", "-9", pid], check=False)

            time.sleep(0.5)
        except (FileNotFoundError, subprocess.CalledProcessError):
            pass
