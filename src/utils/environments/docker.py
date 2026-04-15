"""
Docker Environment Implementation
===================================

Provides `DockerEnvironment` which implements `DatabaseEnvironment`
for containerized database execution.

Responsibilities:
- Container lifecycle management via Docker SDK.
- Applying resource constraints via cgroups.
- Setup of multiple independent worker containers.
- Mapping container ports to dynamic host ports.
- Snapshot handling using docker commit.
"""

import time
from contextlib import contextmanager
from typing import Optional, List, Dict, Any
from pathlib import Path

import psycopg2

from src.utils.environments.base import DatabaseEnvironment, InstanceConfig
from src.utils.applicator import KnobApplicator, ApplicatorConfig
from src.tuner.evaluator.executor import BenchmarkExecutor
from src.utils.logger import get_logger, ColorCode
from src.database.connection import get_connection
from src.config.database import DatabaseConfig

try:
    import docker
except ImportError as exc:
    raise ImportError(
        "The 'docker' Python package is not installed. "
        "Install it with: pip install docker>=7.0.0"
    ) from exc

LOGGER = get_logger(__name__)


class DockerEnvironment(DatabaseEnvironment):
    """
    Docker-backed PostgreSQL environment supporting multi-worker parallelism.
    
    Creates isolated containers for each worker, ensuring clean state
    and strict resource isolation (CPU/RAM).
    """

    def __init__(
        self,
        run_id: str,
        db_config: DatabaseConfig,
        schema_provider: BenchmarkExecutor,
        cpu_cores: float = 0.0,
        ram_bytes: int = 0,
        image_name: str = "postgres:17",
        base_port: int = 5440,
        base_dir: Path = Path("./pg_instances"),
    ):
        super().__init__(run_id, db_config, schema_provider)
        self.cpu_cores = cpu_cores
        self.ram_bytes = ram_bytes
        self.image_name = image_name
        self.base_port = base_port
        self.base_dir = base_dir

        self.client = docker.from_env(timeout=30)
        self.instances: Dict[int, InstanceConfig] = {}
        self._snapshot_timeout = self._derive_snapshot_timeout()

        # Ensure network exists
        self.network_name = "pbt-network"
        try:
            self.client.networks.get(self.network_name)
        except docker.errors.NotFound:
            LOGGER.info("Creating Docker network '%s'", self.network_name)
            self.client.networks.create(self.network_name, driver="bridge")
            LOGGER.debug("➤ Network '%s' created successfully", self.network_name)

    def setup_instances(
            self,
            num_workers: int,
            force_recreate: bool = False
        ) -> List[InstanceConfig]:
        """Create and start the Docker containers for N workers."""
        if num_workers <= 0:
            raise ValueError("Must specify at least 1 worker")

        LOGGER.info(
            "%sSetting up %d PostgreSQL containers (force_recreate=%s)%s",
            ColorCode.BOLD,
            num_workers,
            force_recreate,
            ColorCode.RESET
        )

        for worker_id in range(num_workers):
            port = self.base_port + worker_id
            container_name = f"pbt-worker-{worker_id}"
            LOGGER.info("  Setting up container '%s' on port %d:", container_name, port)

            if force_recreate:
                try:
                    LOGGER.debug("    Removing existing container '%s'...", container_name)
                    c = self.client.containers.get(container_name)
                    c.remove(force=True)

                    LOGGER.debug(
                        "%s    ➤ Container '%s' removed successfully%s",
                        ColorCode.OKGREEN,
                        container_name,
                        ColorCode.RESET
                    )
                except docker.errors.NotFound:
                    pass

            try:  # Exists already
                container = self.client.containers.get(container_name)
                if container.status != 'running':
                    LOGGER.debug("    Starting stopped container '%s'...", container_name)
                    container.start()
                running = True

                LOGGER.debug(
                    "%s  ➤ Container '%s' already exists, reusing it.%s",
                    ColorCode.OKGREEN,
                    container_name,
                    ColorCode.RESET
                )
            except docker.errors.NotFound:  # Create it
                volumes = {}
                environment = {
                    'POSTGRES_USER': self.base_config.user,
                    'POSTGRES_PASSWORD': self.base_config.password,
                    'POSTGRES_DB': self.base_config.dbname,
                    'PGDATA': '/pgdata/data',
                }
                ports = {'5432/tcp': port}

                nano_cpus = int(self.cpu_cores * 1e9) if self.cpu_cores > 0 else None

                LOGGER.debug("    Creating container '%s'...", container_name)
                try:
                    self.client.containers.run(
                        self.image_name,
                        name=container_name,
                        environment=environment,
                        ports=ports,
                        volumes=volumes,
                        mem_limit=self.ram_bytes if self.ram_bytes > 0 else None,
                        nano_cpus=nano_cpus,
                        network=self.network_name,
                        detach=True
                    )
                except docker.errors.APIError as e:
                    raise RuntimeError(f"Failed to create container '{container_name}'") from e

                running = True
                LOGGER.debug(
                    "%s  ➤ Container '%s' created successfully.%s",
                    ColorCode.OKGREEN,
                    container_name,
                    ColorCode.RESET
                )

            self._wait_for_ready(container_name, port)
            self.instances[worker_id] = InstanceConfig(
                worker_id=worker_id,
                port=port,
                data_dir=self.base_dir / f"worker_{worker_id}",
                running=running
            )

            # Auto-initialize schema natively and leverage snapshots to accelerate parallel workers
            if self.schema_provider:
                LOGGER.debug("  Initializing schema for worker %d...", worker_id)
                self.initialize_schema(worker_id)

                if worker_id == 0:
                    LOGGER.debug(
                        "    Caching worker 0 baseline snapshot for fast-path initialization...",
                    )
                    self.create_snapshot(worker_id=0)

            LOGGER.debug(
                "%s  ➤ Container '%s' set up successfully.%s",
                ColorCode.OKGREEN,
                container_name,
                ColorCode.RESET
            )

        LOGGER.info(
            "%s%s➤ All %d containers set up successfully.%s",
            ColorCode.BOLD,
            ColorCode.OKGREEN,
            num_workers,
            ColorCode.RESET
        )

        return list(self.instances.values())

    def start_instance(self, worker_id: int) -> bool:
        """Start a stopped container."""
        container_name = f"pbt-worker-{worker_id}"
        try:
            container = self.client.containers.get(container_name)
            container.start()
            self.instances[worker_id].running = True
            return True
        except docker.errors.APIError:
            return False

    def stop_instance(self, worker_id: int, mode: str = 'fast') -> bool:
        """Stop a running container."""
        container_name = f"pbt-worker-{worker_id}"
        try:
            container = self.client.containers.get(container_name)
            container.stop(timeout=5)
            self.instances[worker_id].running = False
            return True
        except docker.errors.APIError:
            return False

    def stop_all(self, mode: str = 'fast') -> bool:
        """Stop all known worker containers."""
        for worker_id in self.instances:
            self.stop_instance(worker_id, mode)
        return True

    def recover_instance(self, worker_id: int) -> bool:
        """Restart a failed container."""
        return self.start_instance(worker_id)

    def verify_instances(self) -> dict[int, bool]:
        """Check status of containers."""
        res = {}
        for worker_id, instance_config in self.instances.items():
            container_name = f"pbt-worker-{worker_id}"
            try:
                container = self.client.containers.get(container_name)
                res[worker_id] = container.status == 'running'
                instance_config.running = res[worker_id]
            except docker.errors.NotFound:
                res[worker_id] = False
        return res

    def cleanup(self, remove_data: bool = False) -> None:
        """Remove containers."""
        for worker_id in self.instances:
            container_name = f"pbt-worker-{worker_id}"
            try:
                container = self.client.containers.get(container_name)
                container.remove(force=True)
            except docker.errors.NotFound:
                pass
        self.instances.clear()

    def apply_knobs(self, worker_id: int, knobs: Dict[str, Any]) -> None:
        """Apply a knob configuration."""

        db_config = self.get_db_config(worker_id)
        applicator_config = ApplicatorConfig(
            persist=True,
            auto_reload=True,
            validate=True,
            rollback_on_error=False,
            allow_restart_params=True,
            auto_restart=False
        )
        applicator = KnobApplicator(db_config, applicator_config)
        result = applicator.apply(knobs)

        if result.restart_required:
            self.stop_instance(worker_id)
            self.start_instance(worker_id)
            self._wait_for_ready(f"pbt-worker-{worker_id}", db_config.port)

    @contextmanager
    def _with_timeout(self, seconds: Optional[int]):
        """Temporarily override the Docker API timeout for a heavy operation.

        Parameters
        ----------
        seconds : Optional[int]
            Timeout in seconds. ``None`` disables the timeout entirely
            (suitable for TPC-H snapshots where PostgreSQL's own
            ``statement_timeout`` provides the safety net).
        """
        original = self.client.api.timeout
        self.client.api.timeout = seconds
        try:
            yield
        finally:
            self.client.api.timeout = original

    def _derive_snapshot_timeout(self) -> Optional[int]:
        """Derive an appropriate Docker commit timeout from the benchmark type.

        Returns
        -------
        Optional[int]
            Timeout in seconds for ``docker commit``. Returns ``None``
            (unlimited) for TPC-H, where the dataset size scales with
            ``scale_factor`` and PostgreSQL's own ``statement_timeout``
            safeguards execution. Returns 120s for Sysbench, whose
            dataset size is bounded and predictable.
        """
        from src.benchmarks.tpch.executor import TPCHExecutor
        if isinstance(self.schema_provider, TPCHExecutor):
            return None
        return 120

    def create_snapshot(self, worker_id: int = 0) -> str:
        """Create a baseline snapshot from the specified worker instance using Docker Commit."""
        container_name = f"pbt-worker-{worker_id}"
        snapshot_id = f"pbt-snapshot-{self.run_id}"
        try:
            container = self.client.containers.get(container_name)
            with self._with_timeout(self._snapshot_timeout):
                container.commit(repository=snapshot_id)
            return snapshot_id
        except docker.errors.APIError as e:
            LOGGER.error("Failed to create snapshot from %s: %s", container_name, e)
            return ""

    def restore_snapshot(self, worker_id: int, snapshot_id: str = "") -> bool:
        """Restore a targeted worker's data directory/volume from the baseline snapshot."""
        if not snapshot_id:
            snapshot_id = f"pbt-snapshot-{self.run_id}"

        try:
            self.client.images.get(snapshot_id)
        except docker.errors.ImageNotFound:
            LOGGER.debug("  No snapshot image '%s' found, skipping restore", snapshot_id)
            return False

        container_name = f"pbt-worker-{worker_id}"
        port = self.base_port + worker_id

        # Stop and remove current container
        try:
            old_c = self.client.containers.get(container_name)
            old_c.remove(force=True)
        except docker.errors.NotFound:
            pass

        environment = {
            'POSTGRES_USER': self.base_config.user,
            'POSTGRES_PASSWORD': self.base_config.password,
            'POSTGRES_DB': self.base_config.dbname,
            'PGDATA': '/pgdata/data',
        }
        ports = {'5432/tcp': port}
        nano_cpus = int(self.cpu_cores * 1e9) if self.cpu_cores > 0 else None

        try:
            self.client.containers.run(
                snapshot_id,
                name=container_name,
                environment=environment,
                ports=ports,
                mem_limit=self.ram_bytes if self.ram_bytes > 0 else None,
                nano_cpus=nano_cpus,
                network=self.network_name,
                detach=True
            )
            self._wait_for_ready(container_name, port)
            if worker_id in self.instances:
                self.instances[worker_id].running = True
            return True
        except Exception as e:
            LOGGER.error("Failed to restore snapshot to %s: %s", container_name, e)
            return False

    def get_db_config(self, worker_id: int) -> DatabaseConfig:
        """Get the runtime connection configuration for a defined worker."""
        port = self.base_port + worker_id
        return DatabaseConfig(
            host="127.0.0.1",
            port=port,
            dbname=self.base_config.dbname,
            user=self.base_config.user,
            password=self.base_config.password
        )

    def _wait_for_ready(self, container_name: str, port: int, timeout=60) -> None:
        """Wait until PostgreSQL is accepting connections."""
        active_config = DatabaseConfig(
            host="127.0.0.1",
            port=port,
            dbname=self.base_config.dbname,
            user=self.base_config.user,
            password=self.base_config.password
        )

        LOGGER.debug("  Waiting for container '%s' to be ready...", container_name)
        start_time = time.time()
        while time.time() - start_time < timeout:
            try:
                conn = get_connection(
                    config=active_config,
                    connect_timeout=2
                )
                conn.close()
                LOGGER.debug(
                    "%s  ➤ Container '%s' is ready after %.2f seconds.%s",
                    ColorCode.OKGREEN,
                    container_name,
                    time.time() - start_time,
                    ColorCode.RESET
                )
                return
            except psycopg2.OperationalError:
                time.sleep(1)

        raise RuntimeError(
            f"Database in container {container_name} failed to become ready within {timeout}s."
        )
