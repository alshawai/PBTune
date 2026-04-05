"""
PostgreSQL Instance Manager for Parallel PBT (Docker Version)

Manages multiple PostgreSQL instances for true parallel worker execution using Docker.
Each worker gets its own isolated PostgreSQL container and attached volume.

Architecture:
- Base port: 5432 (configurable)
- Worker N uses mapped port: base_port + N
- Container names: pbt-worker-N
- Network: pbt-network
- Data volume: bind-mounted to host's base_dir / worker_N
"""

from __future__ import annotations
import time
import logging
import os
import shutil
import getpass
import subprocess
from pathlib import Path
from dataclasses import dataclass
from typing import Optional, List, Dict
import docker

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


class PostgresInstanceManager:
    """
    Manages multiple PostgreSQL containers for parallel worker execution.
    """

    def __init__(
        self,
        base_dir: Path,
        base_port: int = 5432,
        template_db_config: Optional[DatabaseConfig] = None,
        schema_provider: Optional[object] = None,
        **kwargs
    ):
        """
        Initialize the Docker instance manager.
        """
        self.base_dir = Path(base_dir) # Kept for backward compatibility
        self.base_port = base_port
        self.template_db_config = template_db_config
        self.schema_provider = schema_provider
        self.instances: Dict[int, InstanceConfig] = {}
        
        # Detect if we are running inside a container (our tuner container)
        # to decide how to connect to workers for health checks.
        self.in_docker = os.path.exists('/.dockerenv') or os.environ.get('PBT_IN_DOCKER') == '1'
        
        # Connect to Docker Daemon
        try:
            self.docker_client = docker.from_env()
            self.docker_client.ping()
        except docker.errors.DockerException as e:
            raise RuntimeError(f"Could not connect to Docker daemon: {e}. Is Docker running?")

        # Ensure the pbt-network exists
        self.network_name = "pbt-network"
        try:
            self.docker_client.networks.get(self.network_name)
        except docker.errors.NotFound:
            logger.info("Creating Docker network '%s'", self.network_name)
            self.docker_client.networks.create(self.network_name, driver="bridge")

        logger.debug(
            "✓ Initialized Docker InstanceManager: base_port=%d\n",
            base_port
        )

    def setup_instances(
        self,
        num_workers: int,
        force_recreate: bool = False
    ) -> List[InstanceConfig]:
        """Set up PostgreSQL container instances for all workers."""
        if num_workers <= 0:
            raise ValueError("Must specify at least 1 worker")

        logger.info(
            "Setting up %d PostgreSQL containers (force_recreate=%s)",
            num_workers,
            force_recreate
        )

        for worker_id in range(num_workers):
            port = self.base_port + worker_id
            container_name = f"pbt-worker-{worker_id}"
            
            # Use host directory as bind mount to preserve data and allow host-level recovery/resets
            data_dir = self.base_dir / f"worker_{worker_id}"
            data_dir.mkdir(parents=True, exist_ok=True)
            
            exists, running = self._get_container_status(container_name)

            if exists and not force_recreate:
                logger.info("Reusing existing container %s at %s (port %d)", container_name, data_dir, port)
                
                if running:
                    logger.debug("Stopping reused container before startup to clear persisted overrides")
                    self.stop_instance(worker_id)
                    time.sleep(1)

                self._reset_persisted_overrides(data_dir)
                self._kill_stale_port_holder(port)
                self._start_instance_internal(container_name)

                # Wait for database connecting
                self._wait_for_instance_ready(port, container_name)

                # Ensure schema is initialized
                self._initialize_schema(port, container_name)

                instance = InstanceConfig(
                    worker_id=worker_id,
                    port=port,
                    data_dir=data_dir,
                    running=True
                )
            else:
                if exists:
                    logger.info("Removing old container %s", container_name)
                    self._remove_container(container_name)

                logger.info(
                    "Creating new container %s on mapped port %d",
                    container_name,
                    port
                )
                self._kill_stale_port_holder(port)
                instance = self._create_instance(worker_id, port, data_dir, container_name)

            self.instances[worker_id] = instance

        return list(self.instances.values())

    def _reset_persisted_overrides(self, data_dir: Path) -> None:
        """Clear persisted ALTER SYSTEM overrides before reusing an instance.

        Reused worker data directories can carry forward extreme values in
        postgresql.auto.conf from prior runs. Clearing that file forces startup
        back to the safe base configuration in postgresql.conf.
        """
        auto_conf = data_dir / 'postgresql.auto.conf'
        if not auto_conf.exists():
            return

        backup_path = data_dir / 'postgresql.auto.conf.pre_reuse_backup'
        try:
            shutil.copy2(auto_conf, backup_path)

            with open(auto_conf, 'w', encoding='utf-8') as handle:
                handle.write("# Cleared before instance reuse to remove stale ALTER SYSTEM overrides.\n")

            logger.debug(
                "Cleared persisted overrides in %s (backup at %s)",
                auto_conf,
                backup_path,
            )
        except Exception as e:
            logger.warning("Could not clear pg auto config %s: %s", auto_conf, e)

    def _kill_stale_port_holder(self, port: int) -> None:
        """Kill any host process listening on the target mapping port.
        Required because Docker sometimes gets port collision errors if a host
        process is already bound to the same port.
        """
        try:
            lsof_output = subprocess.check_output(
                ["lsof", "-t", f"-i:{port}"],
                text=True,
                stderr=subprocess.DEVNULL
            ).strip()
            
            pids = lsof_output.split('\n')
            for pid in pids:
                if pid:
                    logger.warning("Killing rogue process %s holding port %d", pid, port)
                    subprocess.run(["kill", "-9", pid], check=False)
        except subprocess.CalledProcessError:
            # lsof returns 1 if no process found, which is exactly what we want
            pass

    def _wait_for_instance_ready(
        self,
        port: int,
        container_name: Optional[str] = None,
        max_attempts: int = 20,
        delay_seconds: float = 2.0,
    ) -> None:
        """Wait until a PostgreSQL instance accepts local connections."""
        current_user = getpass.getuser()

        host = container_name if (self.in_docker and container_name) else 'localhost'
        actual_port = 5432 if self.in_docker else port

        last_error: Optional[Exception] = None
        for attempt in range(1, max_attempts + 1):
            test_configs = [
                DatabaseConfig(
                    host=host,
                    port=str(actual_port),
                    dbname='postgres',
                    user=current_user,
                    password=''
                )
            ]

            if self.template_db_config:
                test_configs.append(
                    DatabaseConfig(
                        host=host,
                        port=str(actual_port),
                        dbname=self.template_db_config.dbname,
                        user=self.template_db_config.user,
                        password=self.template_db_config.password or ''
                    )
                )

            for test_config in test_configs:
                try:
                    conn = get_connection(config=test_config)
                    conn.close()
                    if attempt > 1:
                        logger.debug(
                            "Instance on %s:%d became ready after %d attempts",
                            host,
                            actual_port,
                            attempt,
                        )
                    return
                except Exception as e:
                    last_error = e

            time.sleep(delay_seconds)

        raise RuntimeError(
            f"Instance on {host}:{actual_port} did not become ready after "
            f"{max_attempts} attempts: {last_error}"
        )

    def _get_container_status(self, container_name: str) -> tuple[bool, bool]:
        """Returns (exists, is_running)."""
        try:
            container = self.docker_client.containers.get(container_name)
            return True, container.status == "running"
        except docker.errors.NotFound:
            return False, False

    def _remove_container(self, container_name: str) -> None:
        """Force remove a container and its volumes."""
        try:
            container = self.docker_client.containers.get(container_name)
            container.remove(force=True, v=True)
            logger.debug("Removed container %s", container_name)
        except docker.errors.NotFound:
            pass

    def _create_instance(self, worker_id: int, port: int, data_dir: Path, container_name: str) -> InstanceConfig:
        """Create and start a new PostgreSQL container via Docker API."""
        
        db_user = "postgres"
        db_password = ""
        db_name = "postgres"
        
        if self.template_db_config:
            db_user = self.template_db_config.user
            db_password = self.template_db_config.password or ""
            db_name = self.template_db_config.dbname
            
        environment = {
            "POSTGRES_USER": db_user,
            "POSTGRES_PASSWORD": db_password,
            "POSTGRES_DB": db_name,
        }
        
        # Enforce limits like max 1.5 CPUs and 1GB RAM per worker
        logger.debug("Running docker container %s with bind-mount %s ...", container_name, data_dir)
        
        try:
            # We map the port to the host and bind-mount the data_dir
            container = self.docker_client.containers.run(
                "postgres:17",
                name=container_name,
                network=self.network_name,
                detach=True,
                environment=environment,
                ports={'5432/tcp': port},
                volumes={str(data_dir.absolute()): {'bind': '/var/lib/postgresql/data', 'mode': 'rw'}},
                mem_limit="1g",
                nano_cpus=1500000000,  # 1.5 CPUs
                command="postgres -c logging_collector=off -c log_destination=stderr"
            )
        except docker.errors.APIError as e:
            raise RuntimeError(f"Failed to create docker container {container_name}: {e}")
            
        # Wait for the container to become ready
        self._wait_for_instance_ready(port, container_name)
        
        # Initialize the schema/dbgen data
        self._initialize_schema(port, container_name)
        
        logger.info("Successfully created instance for worker-%d on port %d", worker_id, port)
        
        return InstanceConfig(
            worker_id=worker_id,
            port=port,
            data_dir=data_dir,
            running=True
        )

    def _start_instance_internal(self, container_name: str) -> None:
        """Start a stopped Docker container."""
        try:
            container = self.docker_client.containers.get(container_name)
            container.start()
        except Exception as e:
            logger.warning("Error starting container %s: %s", container_name, e)

    def _initialize_schema(self, port: int, container_name: str) -> None:
        """Initialize schema by delegating to the schema_provider."""
        if not self.schema_provider or not self.template_db_config:
            return

        instance_config = DatabaseConfig(
            host=container_name if self.in_docker else 'localhost',
            port=5432 if self.in_docker else port,
            dbname=self.template_db_config.dbname,
            user=self.template_db_config.user,
            password=self.template_db_config.password or ''
        )

        try:
            if self.schema_provider.validate(instance_config):
                logger.debug("Schema already valid on port %d", port)
                return

            logger.info("Preparing schema on port %d...", port)
            self.schema_provider.prepare(instance_config)
            logger.debug("Schema preparation complete on port %d", port)

        except Exception as e:
            logger.error("Failed to initialize schema on port %d: %s", port, e)
            raise

    def start_instance(self, worker_id: int) -> bool:
        if worker_id not in self.instances:
            return False
        
        instance = self.instances[worker_id]
        if instance.running:
            return True
            
        container_name = f"pbt-worker-{worker_id}"
        self._kill_stale_port_holder(instance.port)
        
        try:
            self._start_instance_internal(container_name)
            instance.running = True
            logger.info("Started worker-%d", worker_id)
            return True
        except Exception as e:
            logger.error("Failed to start worker-%d: %s", worker_id, e)
            return False

    def stop_instance(self, worker_id: int, mode: str = 'fast') -> bool:
        if worker_id not in self.instances:
            return False
            
        instance = self.instances[worker_id]
        if not instance.running:
            return True
            
        container_name = f"pbt-worker-{worker_id}"
        try:
            container = self.docker_client.containers.get(container_name)
            # Use immediate timeout since Docker handles stopping postgres
            container.stop(timeout=10)
            instance.running = False
            logger.info("Stopped worker-%d", worker_id)
            return True
        except Exception as e:
            logger.error("Failed to stop worker-%d: %s", worker_id, e)
            return False

    def start_all(self) -> bool:
        logger.info("Starting all %d instances...", len(self.instances))
        return all(self.start_instance(w) for w in self.instances)

    def recover_instance(self, worker_id: int) -> bool:
        """Recover a worker instance from potentially bad persisted config."""
        if worker_id not in self.instances:
            logger.error("No instance configured for worker-%d", worker_id)
            return False

        instance = self.instances[worker_id]
        container_name = f"pbt-worker-{worker_id}"

        # 1. Force stop container
        self.stop_instance(worker_id)
        
        # 2. Prevent port mapping collisions
        self._kill_stale_port_holder(instance.port)
        
        # 3. Clean up bad configs from bind-mount host directory
        self._reset_persisted_overrides(instance.data_dir)

        try:
            # 4. Restart the container
            self._start_instance_internal(container_name)
            
            # 5. Wait for it to become ready
            self._wait_for_instance_ready(instance.port, container_name)
            
            # 6. Initialize schema if lost
            self._initialize_schema(instance.port, container_name)
            instance.running = True
            logger.info(
                "Recovered instance for worker-%d on port %d",
                worker_id,
                instance.port,
            )
            return True
        except Exception as e:
            instance.running = False
            logger.error("Failed to recover instance for worker-%d: %s", worker_id, e)
            return False

    def stop_all(self, mode: str = 'fast') -> bool:
        logger.info("Stopping all %d instances...", len(self.instances))
        return all(self.stop_instance(w) for w in self.instances)

    def verify_instances(self) -> Dict[int, bool]:
        logger.info("Verifying %d instances...", len(self.instances))
        results = {}
        for worker_id, instance in self.instances.items():
            results[worker_id] = False
            try:
                container_name = f"pbt-worker-{worker_id}"
                test_config = DatabaseConfig(
                    host=container_name if self.in_docker else 'localhost',
                    port=5432 if self.in_docker else instance.port,
                    dbname=self.template_db_config.dbname if self.template_db_config else 'postgres',
                    user=self.template_db_config.user if self.template_db_config else 'postgres',
                    password=self.template_db_config.password if self.template_db_config else ''
                )
                conn = get_connection(test_config)
                with conn.cursor() as cur:
                    cur.execute("SELECT 1")
                conn.close()
                results[worker_id] = True
            except Exception as e:
                logger.error("Verification failed for worker-%d (%s:%d): %s", worker_id, test_config.host, test_config.port, e)
        return results

    def cleanup(self, remove_data: bool = False) -> None:
        """Clean up Docker containers and volumes."""
        logger.info("Cleaning up instance manager (remove_data=%s)", remove_data)
        self.stop_all()
        if remove_data:
            for worker_id in list(self.instances.keys()):
                container_name = f"pbt-worker-{worker_id}"
                self._remove_container(container_name)
        self.instances.clear()
