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
- Snapshot handling using host-directory copies via containers.
"""

import os
import shutil
import time
import hashlib
import json
import re
from datetime import datetime, timezone
from contextlib import contextmanager
from typing import Optional, List, Dict, Any
from pathlib import Path

import psycopg2
import requests
from docker import errors as docker_errors

from src.utils.environments.base import DatabaseEnvironment, InstanceConfig
from src.benchmarks.executor import BenchmarkExecutor
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

LOGGER = get_logger("DockerEnvironment")


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
        image_name: str = "postgres:18",
        base_port: int = 5440,
        base_dir: Path = Path("./.instances"),
        container_prefix: str = "pbt-worker",
        force_recreate_baseline: bool = False,
    ):
        """Initialize Docker environment with configuration."""
        super().__init__(
            run_id,
            db_config,
            schema_provider,
            force_recreate_baseline=force_recreate_baseline,
        )
        self.cpu_cores = cpu_cores
        self.ram_bytes = ram_bytes
        self.image_name = image_name
        self.base_port = base_port
        self.base_dir = base_dir
        self.container_prefix = container_prefix

        self.client = docker.from_env(timeout=30)
        self.instances: Dict[int, InstanceConfig] = {}
        self._snapshot_timeout = self._derive_snapshot_timeout()
        self._ready_timeout = 60
        self._restore_ready_timeout = self._derive_restore_ready_timeout()
        self._restore_api_timeout = self._derive_restore_api_timeout()

        # Ensure network exists
        self.network_name = "pbt-network"
        try:
            self.client.networks.get(self.network_name)
        except docker_errors.NotFound:
            LOGGER.info("Creating Docker network '%s'...", self.network_name)
            self.client.networks.create(self.network_name, driver="bridge")
            LOGGER.debug("➤ Network '%s' created successfully", self.network_name)

    def _container_name(self, worker_id: int) -> str:
        """Build the Docker container name for a worker."""
        return f"{self.container_prefix}-{worker_id}"

    def _worker_host_pgdata_dir(self, worker_id: int) -> Path:
        """Resolve the host-side PGDATA directory for a worker's bind mount.

        When ``base_dir`` points to an external drive, all database I/O
        flows through that drive while Docker still provides process
        isolation, cgroup resource limits, and network namespacing.
        """
        return (
            self.base_dir
            / self._get_instance_subpath()
            / f"worker_{worker_id}"
            / "pgdata"
        )

    def _worker_port(self, worker_id: int) -> int:
        """Resolve a worker's host port."""
        return self.base_port + worker_id

    def _container_runtime_kwargs(
        self,
        worker_id: int,
        volumes: Optional[Dict[str, Dict[str, str]]] = None,
    ) -> Dict[str, Any]:
        """Build runtime kwargs shared across worker container launches."""
        kwargs: Dict[str, Any] = {
            "environment": {
                "POSTGRES_USER": self.base_config.user,
                "POSTGRES_PASSWORD": self.base_config.password,
                "POSTGRES_DB": self.base_config.dbname,
                "PGDATA": "/pgdata/data",
            },
            "ports": {"5432/tcp": self._worker_port(worker_id)},
            "mem_limit": self.ram_bytes if self.ram_bytes > 0 else None,
            "nano_cpus": int(self.cpu_cores * 1e9) if self.cpu_cores > 0 else None,
            "network": self.network_name,
            "detach": True,
        }

        if volumes is not None:
            kwargs["volumes"] = volumes

        return kwargs

    def _remove_worker_container(
        self,
        worker_id: int,
        purpose: str,
        timeout: Optional[int] = None,
    ) -> bool:
        """Remove an existing worker container if present."""
        container_name = self._container_name(worker_id)
        try:
            with self._with_timeout(timeout):
                old_container = self.client.containers.get(container_name)
                old_container.remove(force=True)
        except docker_errors.NotFound:
            return True
        except docker_errors.DockerException as exc:
            LOGGER.error(
                "Failed removing worker container '%s' during %s: %s",
                container_name,
                purpose,
                exc,
            )
            return False
        return True

    def _prepare_worker_pgdata_dir(self, worker_id: int) -> Path:
        """Prepare a clean host directory for a worker's PGDATA bind mount.

        Removes any existing data and creates a fresh directory with
        permissions that allow the container's ``postgres`` user
        (typically UID 999) to write.
        """
        pgdata_dir = self._worker_host_pgdata_dir(worker_id)
        self._force_remove_host_dir(pgdata_dir)
        pgdata_dir.mkdir(parents=True, exist_ok=True)
        os.chmod(pgdata_dir, 0o777)
        return pgdata_dir

    def _force_remove_host_dir(self, target_dir: Path) -> None:
        """Remove a host directory that may contain Docker-owned files.

        Files created by containerized PostgreSQL (UID 999) are not
        deletable by the host user.  This method mounts the *parent*
        directory into a disposable container and ``rm -rf``'s the
        child, side-stepping the ownership mismatch.
        """
        if not target_dir.exists():
            return

        child_name = target_dir.name
        parent_dir = str(target_dir.parent)

        try:
            container = self.client.containers.run(
                self.image_name,
                entrypoint=["rm", "-rf", f"/host/{child_name}"],
                volumes={parent_dir: {"bind": "/host", "mode": "rw"}},
                detach=True,
            )
            result = container.wait()
            if result.get("StatusCode", 1) != 0:
                raise docker_errors.DockerException(
                    f"rm -rf failed with exit code {result.get('StatusCode')}: "
                    f"{container.logs().decode('utf-8', errors='replace')}"
                )
            container.remove(force=True)
        except docker_errors.DockerException as exc:
            LOGGER.debug(
                "Container-based removal of '%s' failed: %s; "
                "attempting host-side rmtree as fallback",
                target_dir,
                exc,
            )
            shutil.rmtree(target_dir, ignore_errors=True)

    def _ensure_container_running_after_timeout(
        self,
        worker_id: int,
        action_label: str,
    ) -> bool:
        """Recover from Docker client timeout by checking container state."""
        container_name = self._container_name(worker_id)
        try:
            container = self.client.containers.get(container_name)
            container.reload()
            if container.status != "running":
                container.start()
            return True
        except docker_errors.DockerException as state_exc:
            LOGGER.error(
                "Container '%s' unavailable after %s timeout: %s",
                container_name,
                action_label,
                state_exc,
            )
            return False

    def _ensure_container_stopped_after_timeout(
        self,
        worker_id: int,
        action_label: str,
    ) -> bool:
        """Recover from Docker client timeout by checking stop completion."""
        container_name = self._container_name(worker_id)
        try:
            container = self.client.containers.get(container_name)
            container.reload()
            if container.status == "running":
                LOGGER.warning(
                    "Container '%s' is still running after %s timeout",
                    container_name,
                    action_label,
                )
                return False
            return True
        except docker_errors.NotFound:
            # If container is gone, stop/remove succeeded from caller perspective.
            return True
        except docker_errors.DockerException as state_exc:
            LOGGER.error(
                "Container '%s' unavailable after %s timeout: %s",
                container_name,
                action_label,
                state_exc,
            )
            return False

    def _launch_worker_container(
        self,
        image_name: str,
        worker_id: int,
        action_label: str,
        volumes: Optional[Dict[str, Dict[str, str]]] = None,
        timeout: Optional[int] = None,
    ) -> tuple[bool, Optional[Exception]]:
        """Launch a worker container with shared timeout + recovery handling."""
        container_name = self._container_name(worker_id)
        kwargs = self._container_runtime_kwargs(worker_id=worker_id, volumes=volumes)

        try:
            with self._with_timeout(timeout):
                self.client.containers.run(
                    image_name,
                    name=container_name,
                    **kwargs,
                )
            return True, None
        except requests.exceptions.ReadTimeout as exc:
            LOGGER.warning(
                "Docker timed out %s '%s'; checking container state: %s",
                action_label,
                container_name,
                exc,
            )
            recovered = self._ensure_container_running_after_timeout(
                worker_id=worker_id,
                action_label=action_label,
            )
            return recovered, (None if recovered else exc)
        except docker_errors.DockerException as exc:
            LOGGER.error(
                "Failed %s '%s' for worker %d: %s",
                action_label,
                container_name,
                worker_id,
                exc,
            )
            return False, exc

    def _seed_pgdata_dir_from_snapshot(
        self,
        worker_id: int,
    ) -> Optional[Path]:
        """Prepare a clean host PGDATA directory and seed it from the baseline snapshot.

        Copies the snapshot's PGDATA directory into the worker's bind-mount
        directory using a container (to handle UID 999 ownership).
        """
        snapshot_pgdata = self._snapshot_host_dir()
        if not snapshot_pgdata.exists():
            LOGGER.error(
                "Snapshot PGDATA dir '%s' does not exist; cannot seed worker %d",
                snapshot_pgdata,
                worker_id,
            )
            return None

        pgdata_dir = self._prepare_worker_pgdata_dir(worker_id)

        try:
            with self._with_timeout(self._restore_api_timeout):
                container = self.client.containers.run(
                    self.image_name,
                    user="999:999",  # Copy as postgres user to avoid chown errors on exFAT/NTFS
                    entrypoint=["bash", "-lc"],
                    command=["set -euo pipefail; cp -R /source/. /dest/"],
                    volumes={
                        str(snapshot_pgdata): {"bind": "/source", "mode": "ro"},
                        str(pgdata_dir): {"bind": "/dest", "mode": "rw"},
                    },
                    detach=True,
                )
                result = container.wait()
                if result.get("StatusCode", 1) != 0:
                    raise docker_errors.DockerException(
                        f"Copy failed with exit code {result.get('StatusCode')}: "
                        f"{container.logs().decode('utf-8', errors='replace')}"
                    )
                container.remove(force=True)

            return pgdata_dir
        except docker_errors.DockerException as exc:
            LOGGER.error(
                "Failed to seed PGDATA dir '%s' from snapshot '%s': %s",
                pgdata_dir,
                snapshot_pgdata,
                exc,
            )
            return None

    def rebuild_worker_instance(self, worker_id: int) -> bool:
        """Recreate one worker from scratch after snapshot restore failure.

        This path mirrors startup-style clean initialization: remove worker
        container + PGDATA volume, launch from base PostgreSQL image, then
        prepare schema for the configured benchmark profile.
        """
        container_name = self._container_name(worker_id)
        port = self._worker_port(worker_id)

        LOGGER.error(
            "Snapshot restore failed for worker %d; rebuilding clean slate instance",
            worker_id,
        )

        if not self._remove_worker_container(worker_id=worker_id, purpose="rebuild"):
            return False

        pgdata_dir = self._prepare_worker_pgdata_dir(worker_id)

        volumes = {str(pgdata_dir): {"bind": "/pgdata/data", "mode": "rw"}}
        launched, _ = self._launch_worker_container(
            image_name=self.image_name,
            worker_id=worker_id,
            action_label="creating clean-slate worker",
            volumes=volumes,
            timeout=self._restore_api_timeout,
        )
        if not launched:
            return False

        try:
            self._wait_for_ready(
                container_name,
                port,
                timeout=self._restore_ready_timeout,
                context="clean-rebuild",
            )

            config = self.get_db_config(worker_id)
            self._ensure_database_exists(config)
            self.schema_provider.prepare(config)

            if not self.schema_provider.validate(config):
                LOGGER.error(
                    "Schema validation failed after clean-slate rebuild for worker %d",
                    worker_id,
                )
                return False

            if worker_id in self.instances:
                self.instances[worker_id].running = True

            LOGGER.info("Clean-slate rebuild completed for worker %d", worker_id)
            return True
        except (
            RuntimeError,
            psycopg2.Error,
            OSError,
            ValueError,
            docker_errors.DockerException,
        ) as exc:
            LOGGER.error(
                "Clean-slate rebuild failed for worker %d: %s",
                worker_id,
                exc,
            )
            return False

    def setup_instances(
        self, num_workers: int, force_recreate: bool = False
    ) -> List[InstanceConfig]:
        """Create and start the Docker containers for N workers."""
        if num_workers <= 0:
            raise ValueError("Must specify at least 1 worker")

        LOGGER.info(
            "%sSetting up %d PostgreSQL containers (force_recreate=%s)%s",
            ColorCode.BOLD,
            num_workers,
            force_recreate,
            ColorCode.RESET,
        )

        if self.force_recreate_baseline:
            self._remove_baseline_snapshot()

        baseline_snapshot_available = bool(
            self.schema_provider and self.snapshot_exists(worker_id=0)
        )

        for worker_id in range(num_workers):
            port = self._worker_port(worker_id)
            container_name = self._container_name(worker_id)
            LOGGER.info("  Setting up container '%s' on port %d:", container_name, port)

            recreate_worker0_for_baseline = (
                worker_id == 0
                and self.schema_provider is not None
                and not force_recreate
                and not baseline_snapshot_available
            )

            if force_recreate:
                LOGGER.debug("    Removing existing container '%s'...", container_name)
                if not self._remove_worker_container(
                    worker_id=worker_id,
                    purpose="forced recreate",
                ):
                    raise RuntimeError(
                        f"Failed to remove container '{container_name}' during forced recreate"
                    )

                LOGGER.debug(
                    "%s    ➤ Container '%s' removed successfully%s",
                    ColorCode.GREEN,
                    container_name,
                    ColorCode.RESET,
                )

            try:  # Exists already
                container = self.client.containers.get(container_name)
                if recreate_worker0_for_baseline:
                    LOGGER.debug(
                        "    Recreating existing worker-0 container because baseline snapshot is missing"
                    )
                    container.remove(force=True)
                    raise docker_errors.NotFound("recreate worker-0 for baseline")

                if container.status != "running":
                    LOGGER.debug(
                        "    Starting stopped container '%s'...", container_name
                    )
                    container.start()
                running = True

                LOGGER.debug(
                    "%s  ➤ Container '%s' already exists, reusing it.%s",
                    ColorCode.GREEN,
                    container_name,
                    ColorCode.RESET,
                )
            except docker_errors.NotFound:  # Create it
                LOGGER.debug("    Creating container '%s'...", container_name)
                pgdata_dir = self._prepare_worker_pgdata_dir(worker_id)
                volumes = {str(pgdata_dir): {"bind": "/pgdata/data", "mode": "rw"}}
                launched, launch_error = self._launch_worker_container(
                    image_name=self.image_name,
                    worker_id=worker_id,
                    action_label="creating worker container",
                    volumes=volumes,
                    timeout=self._ready_timeout,
                )
                if not launched:
                    raise RuntimeError(
                        f"Failed to create container '{container_name}'"
                    ) from launch_error

                running = True
                LOGGER.debug(
                    "%s  ➤ Container '%s' created successfully.%s",
                    ColorCode.GREEN,
                    container_name,
                    ColorCode.RESET,
                )

            self.instances[worker_id] = InstanceConfig(
                worker_id=worker_id,
                port=port,
                data_dir=self._worker_host_pgdata_dir(worker_id).parent,
                running=running,
            )
            self._wait_for_ready(container_name, port)

            # Auto-initialize schema natively and leverage snapshots to accelerate parallel workers
            if self.schema_provider:
                LOGGER.debug("  Initializing schema for worker %d...", worker_id)
                self.initialize_schema(worker_id)

                if worker_id == 0:
                    baseline_snapshot_available = self.snapshot_exists(worker_id=0)
                    if baseline_snapshot_available:
                        LOGGER.debug(
                            "    Baseline snapshot already exists; skipping snapshot creation"
                        )
                    else:
                        LOGGER.debug(
                            "    Caching worker 0 baseline snapshot for fast-path initialization...",
                        )
                        snapshot_id = self.create_snapshot(worker_id=0)
                        if not snapshot_id:
                            raise RuntimeError(
                                "Failed to create baseline Docker snapshot for worker 0"
                            )
                        baseline_snapshot_available = True

            LOGGER.debug(
                "%s  ➤ Container '%s' set up successfully.%s",
                ColorCode.GREEN,
                container_name,
                ColorCode.RESET,
            )

        LOGGER.info(
            "%s%s➤ All %d containers set up successfully.%s",
            ColorCode.BOLD,
            ColorCode.GREEN,
            num_workers,
            ColorCode.RESET,
        )

        return list(self.instances.values())

    def start_instance(self, worker_id: int) -> bool:
        """Start a stopped container."""
        container_name = self._container_name(worker_id)
        try:
            container = self.client.containers.get(container_name)
            container.start()
            if worker_id in self.instances:
                self.instances[worker_id].running = True
            return True
        except docker_errors.DockerException as exc:
            LOGGER.warning(
                "Failed to start container '%s' for worker %d: %s",
                container_name,
                worker_id,
                exc,
            )
            return False
        except (requests.exceptions.RequestException, TimeoutError, OSError) as exc:
            LOGGER.warning(
                "Unexpected error while starting container '%s' for worker %d: %s",
                container_name,
                worker_id,
                exc,
                exc_info=True,
            )
            return False

    def stop_instance(self, worker_id: int, mode: str = "fast") -> bool:
        """Stop a running container."""
        container_name = self._container_name(worker_id)
        try:
            with self._with_timeout(self._restore_api_timeout):
                container = self.client.containers.get(container_name)
                container.stop(timeout=5)
            if worker_id in self.instances:
                self.instances[worker_id].running = False
            return True
        except requests.exceptions.ReadTimeout as exc:
            LOGGER.warning(
                "Docker timed out stopping container '%s'; checking state: %s",
                container_name,
                exc,
            )
            stopped = self._ensure_container_stopped_after_timeout(
                worker_id=worker_id,
                action_label="stopping worker container",
            )
            if stopped and worker_id in self.instances:
                self.instances[worker_id].running = False
            return stopped
        except docker_errors.DockerException as exc:
            LOGGER.warning(
                "Failed to stop container '%s' for worker %d: %s",
                container_name,
                worker_id,
                exc,
            )
            return False
        except (requests.exceptions.RequestException, TimeoutError, OSError) as exc:
            LOGGER.warning(
                "Unexpected error while stopping container '%s' for worker %d: %s",
                container_name,
                worker_id,
                exc,
                exc_info=True,
            )
            return False

    def stop_all(self, mode: str = "fast") -> bool:
        """Stop all running containers associated with this environment."""
        for worker_id in list(self.instances):
            self.stop_instance(worker_id, mode)

        managed_name_pattern = re.compile(rf"^{re.escape(self.container_prefix)}-\d+$")
        try:
            for container in self.client.containers.list(all=False):
                container_name = getattr(container, "name", "")
                if not managed_name_pattern.match(container_name):
                    continue
                try:
                    with self._with_timeout(self._restore_api_timeout):
                        container.stop(timeout=5)
                except requests.exceptions.ReadTimeout as exc:
                    LOGGER.debug(
                        "Docker timed out stopping container '%s' during stop_all; checking state: %s",
                        container_name,
                        exc,
                    )
                    try:
                        container.reload()
                        if getattr(container, "status", "") == "running":
                            LOGGER.debug(
                                "Container '%s' is still running after timeout in stop_all",
                                container_name,
                            )
                    except docker_errors.DockerException as reload_exc:
                        LOGGER.debug(
                            "Unable to verify container '%s' state after stop timeout: %s",
                            container_name,
                            reload_exc,
                        )
                except (
                    docker_errors.DockerException,
                    requests.exceptions.RequestException,
                    TimeoutError,
                    OSError,
                ) as exc:
                    LOGGER.debug(
                        "Failed to stop container '%s' during stop_all: %s",
                        container_name,
                        exc,
                    )
        except (
            docker_errors.DockerException,
            requests.exceptions.RequestException,
            TimeoutError,
            OSError,
        ) as exc:
            LOGGER.debug(
                "Unable to list running Docker containers during stop_all: %s", exc
            )

        return True

    def recover_instance(self, worker_id: int) -> bool:
        """Restart a failed container."""
        try:
            return self.start_instance(worker_id)
        except (
            docker_errors.DockerException,
            requests.exceptions.RequestException,
            TimeoutError,
            OSError,
            RuntimeError,
        ) as exc:
            LOGGER.warning(
                "Unexpected error while recovering worker %d: %s",
                worker_id,
                exc,
                exc_info=True,
            )
            return False

    def restart_instance(self, worker_id: int) -> bool:
        """
        Restart a specific worker's Docker container.

        Uses Docker's native restart command which handles stop+start
        atomically, then waits for PostgreSQL readiness.
        """
        container_name = self._container_name(worker_id)
        try:
            container = self.client.containers.get(container_name)
            LOGGER.info("Restarting container '%s'...", container_name)
            container.restart(timeout=10)
            db_config = self.get_db_config(worker_id)
            self._wait_for_ready(
                container_name,
                db_config.port,
                timeout=self._ready_timeout,
                context="restart",
            )
            if not self.reset_statistics(worker_id):
                LOGGER.warning(
                    "Container '%s' restarted but statistics reset failed.",
                    container_name,
                )
            LOGGER.info("Container '%s' restarted successfully.", container_name)
            return True
        except (
            docker_errors.DockerException,
            requests.exceptions.RequestException,
            TimeoutError,
            OSError,
            RuntimeError,
        ) as exc:
            LOGGER.error("Failed to restart container '%s': %s", container_name, exc)
            return False

    def verify_instances(self) -> dict[int, bool]:
        """Check status of containers."""
        res = {}
        for worker_id, instance_config in self.instances.items():
            container_name = self._container_name(worker_id)
            try:
                container = self.client.containers.get(container_name)
                res[worker_id] = container.status == "running"
                instance_config.running = res[worker_id]
            except (
                docker_errors.DockerException,
                requests.exceptions.RequestException,
                TimeoutError,
                OSError,
            ) as exc:
                res[worker_id] = False
                instance_config.running = False
                LOGGER.debug(
                    "Unable to verify container '%s' for worker %d: %s",
                    container_name,
                    worker_id,
                    exc,
                )
        return res

    def cleanup(self, remove_data: bool = False) -> None:
        """Remove containers and optionally their host PGDATA directories."""
        for worker_id in list(self.instances):
            container_name = self._container_name(worker_id)
            try:
                container = self.client.containers.get(container_name)
                container.remove(force=True)
            except docker_errors.NotFound:
                pass
            except (
                docker_errors.DockerException,
                requests.exceptions.RequestException,
                TimeoutError,
                OSError,
            ) as exc:
                LOGGER.debug(
                    "Unable to remove container '%s' for worker %d during cleanup: %s",
                    container_name,
                    worker_id,
                    exc,
                )

            if remove_data:
                pgdata_dir = self._worker_host_pgdata_dir(worker_id)
                self._force_remove_host_dir(pgdata_dir)
                LOGGER.debug(
                    "Removed host PGDATA directory '%s' for worker %d",
                    pgdata_dir,
                    worker_id,
                )
        self.instances.clear()

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
            Timeout in seconds for snapshot copy. Returns ``None``
            (unlimited) for TPC-H, where the dataset size scales with
            ``scale_factor`` and PostgreSQL's own ``statement_timeout``
            safeguards execution. Returns 120s for Sysbench, whose
            dataset size is bounded and predictable.
        """
        from src.benchmarks.tpch.executor import TPCHExecutor

        if isinstance(self.schema_provider, TPCHExecutor):
            return None
        return 120

    def _derive_restore_ready_timeout(self) -> int:
        """Derive startup timeout after snapshot restore.

        Restoring from an image snapshot can trigger WAL replay/recovery,
        especially for larger OLAP datasets. Keep normal startup strict,
        but allow longer readiness for restore paths.
        """
        from src.benchmarks.tpch.executor import TPCHExecutor

        if isinstance(self.schema_provider, TPCHExecutor):
            return 240
        return 90

    def _derive_restore_api_timeout(self) -> Optional[int]:
        """Derive Docker API timeout used by snapshot restore operations.

        Snapshot restore can involve slow container creation on busy daemons.
        Keep a longer timeout for Sysbench and disable timeout for TPC-H,
        where larger images/volumes can exceed fixed API time budgets.
        """
        from src.benchmarks.tpch.executor import TPCHExecutor

        if isinstance(self.schema_provider, TPCHExecutor):
            return None
        return 180

    def _checkpoint_instance(self, worker_id: int) -> None:
        """Issue a CHECKPOINT before snapshot creation to reduce recovery time on restore."""
        db_config = self.get_db_config(worker_id)
        conn = None
        cursor = None
        try:
            conn = get_connection(config=db_config, connect_timeout=5)
            cursor = conn.cursor()
            cursor.execute("CHECKPOINT")
            conn.commit()
        except (psycopg2.Error, RuntimeError, OSError, ValueError) as exc:
            LOGGER.warning(
                "Failed to issue CHECKPOINT before snapshot for worker %d: %s",
                worker_id,
                exc,
            )
        finally:
            if cursor is not None:
                cursor.close()
            if conn is not None:
                conn.close()

    def _snapshot_profile_context(self) -> Dict[str, Any]:
        """Build a stable benchmark-profile payload used for snapshot identity."""
        provider = self.schema_provider
        provider_name = f"{provider.__class__.__module__}.{provider.__class__.__name__}"

        context: Dict[str, Any] = {
            "provider": provider_name,
        }
        for attribute in (
            "tables",
            "table_size",
            "num_tables",
            "scale_factor",
            "script",
        ):
            if not hasattr(provider, attribute):
                continue
            value = getattr(provider, attribute)
            if isinstance(value, (str, int, float, bool)) or value is None:
                context[attribute] = value

        return context

    def _snapshot_profile_signature(self) -> str:
        """Compute a compact signature for the current benchmark schema profile."""
        profile_payload = json.dumps(
            self._snapshot_profile_context(),
            sort_keys=True,
            separators=(",", ":"),
            default=str,
        )
        return hashlib.sha1(profile_payload.encode("utf-8")).hexdigest()[:12]

    def _default_snapshot_id(self) -> str:
        """Build a Docker-safe snapshot repository name for this profile."""
        return f"pg-snapshot-baseline-{self._snapshot_profile_signature()}"

    def _snapshot_host_dir(self) -> Path:
        """Host directory for the baseline PGDATA snapshot.

        Stored alongside instance data under ``base_dir`` so that when
        the data root points to an external drive, the snapshot lives
        there too.
        """
        return self.base_dir / ".snapshots" / self._default_snapshot_id() / "pgdata"

    def _snapshot_manifest_path(self) -> Path:
        """Path to snapshot metadata manifest, stored next to the snapshot."""
        return self._snapshot_host_dir().parent / "manifest.json"

    def _write_snapshot_manifest(self, snapshot_id: str) -> None:
        """Persist snapshot metadata for traceability."""
        manifest_path = self._snapshot_manifest_path()
        manifest_path.parent.mkdir(parents=True, exist_ok=True)

        payload = {
            "snapshot_id": snapshot_id,
            "snapshot_dir": str(self._snapshot_host_dir()),
            "base_image": self.image_name,
            "profile_signature": self._snapshot_profile_signature(),
            "profile_context": self._snapshot_profile_context(),
            "created_at_utc": datetime.now(tz=timezone.utc).isoformat(),
        }
        manifest_path.write_text(json.dumps(payload, indent=2), encoding="utf-8")

    def _read_snapshot_manifest(self) -> Optional[Dict[str, Any]]:
        """Load snapshot metadata manifest, returning None when missing or invalid."""
        manifest_path = self._snapshot_manifest_path()
        if not manifest_path.exists():
            return None

        try:
            return json.loads(manifest_path.read_text(encoding="utf-8"))
        except (OSError, TypeError, ValueError) as exc:
            LOGGER.debug("Snapshot manifest '%s' is unreadable: %s", manifest_path, exc)
            return None

    def _remove_snapshot_manifest(self) -> None:
        """Remove local snapshot metadata manifest if present."""
        manifest_path = self._snapshot_manifest_path()
        if manifest_path.exists():
            manifest_path.unlink()

    def _remove_baseline_snapshot(self) -> None:
        """Remove existing baseline snapshot directory and metadata."""
        snapshot_dir = self._snapshot_host_dir()
        if snapshot_dir.exists():
            snapshot_id = self._default_snapshot_id()
            LOGGER.info(
                "Removing baseline snapshot '%s' at '%s' (force_recreate_baseline=True)",
                snapshot_id,
                snapshot_dir,
            )
            self._force_remove_host_dir(snapshot_dir)

        self._remove_snapshot_manifest()

    def create_snapshot(self, worker_id: int = 0) -> str:
        """Create a baseline snapshot by copying the worker's host PGDATA directory.

        Issues a CHECKPOINT, stops the container for a clean shutdown,
        copies the PGDATA directory to the snapshot location via a
        container (to preserve UID 999 ownership), then restarts.
        """
        container_name = self._container_name(worker_id)
        snapshot_id = self._default_snapshot_id()
        port = self.base_port + worker_id
        source_pgdata = self._worker_host_pgdata_dir(worker_id)
        snapshot_pgdata = self._snapshot_host_dir()

        try:
            container = self.client.containers.get(container_name)
            self._checkpoint_instance(worker_id)

            # Stop for a clean shutdown — avoids crash-recovery on restore.
            container.reload()
            if container.status == "running":
                container.stop(timeout=45)

            # Prepare the snapshot directory
            self._force_remove_host_dir(snapshot_pgdata)
            snapshot_pgdata.mkdir(parents=True, exist_ok=True)
            os.chmod(snapshot_pgdata, 0o777)

            # Copy worker PGDATA → snapshot dir via container
            with self._with_timeout(self._snapshot_timeout):
                copy_container = self.client.containers.run(
                    self.image_name,
                    user="999:999",  # Copy as postgres user to avoid chown errors on exFAT/NTFS
                    entrypoint=["bash", "-lc"],
                    command=["set -euo pipefail; cp -R /source/. /dest/"],
                    volumes={
                        str(source_pgdata): {"bind": "/source", "mode": "ro"},
                        str(snapshot_pgdata): {"bind": "/dest", "mode": "rw"},
                    },
                    detach=True,
                )
                result = copy_container.wait()
                if result.get("StatusCode", 1) != 0:
                    raise docker_errors.DockerException(
                        f"Copy failed with exit code {result.get('StatusCode')}: "
                        f"{copy_container.logs().decode('utf-8', errors='replace')}"
                    )
                copy_container.remove(force=True)

            # Restart the worker
            container.start()
            self._wait_for_ready(
                container_name,
                port,
                timeout=self._ready_timeout,
                context="snapshot-post-start",
            )

            self._write_snapshot_manifest(snapshot_id=snapshot_id)
            return snapshot_id
        except (docker_errors.DockerException, RuntimeError) as e:
            LOGGER.error("Failed to create snapshot from %s: %s", container_name, e)
            return ""

    def snapshot_exists(self, worker_id: int = 0) -> bool:
        """Check whether the baseline snapshot directory exists and is valid."""
        del worker_id  # Snapshot identity is run-scoped, not worker-scoped.
        snapshot_pgdata = self._snapshot_host_dir()
        if not snapshot_pgdata.exists() or not any(snapshot_pgdata.iterdir()):
            return False

        manifest = self._read_snapshot_manifest()
        if manifest is None:
            LOGGER.debug(
                "Snapshot dir '%s' exists but manifest is missing/invalid; treating as stale",
                snapshot_pgdata,
            )
            return False

        expected_signature = self._snapshot_profile_signature()
        snapshot_id = self._default_snapshot_id()
        manifest_snapshot_id = str(manifest.get("snapshot_id", ""))
        manifest_signature = str(manifest.get("profile_signature", ""))

        if (
            manifest_snapshot_id != snapshot_id
            or manifest_signature != expected_signature
        ):
            LOGGER.debug(
                "Snapshot '%s' manifest mismatch (manifest_snapshot_id=%s, "
                "manifest_signature=%s, expected_signature=%s); treating as stale",
                snapshot_id,
                manifest_snapshot_id,
                manifest_signature,
                expected_signature,
            )
            return False

        return True

    def restore_snapshot(self, worker_id: int, snapshot_id: str = "") -> bool:
        """Restore a worker's PGDATA from the baseline snapshot directory."""
        container_name = self._container_name(worker_id)
        port = self._worker_port(worker_id)

        # Fail fast if the snapshot doesn't exist so we don't kill the running container
        snapshot_pgdata = self._snapshot_host_dir()
        if not snapshot_pgdata.exists():
            return False

        try:
            # Stop and remove current container
            if not self._remove_worker_container(
                worker_id=worker_id,
                purpose="snapshot restore",
                timeout=self._restore_api_timeout,
            ):
                return False

            pgdata_dir = self._seed_pgdata_dir_from_snapshot(
                worker_id=worker_id,
            )
            if not pgdata_dir:
                return False

            volumes = {str(pgdata_dir): {"bind": "/pgdata/data", "mode": "rw"}}
            launched, _ = self._launch_worker_container(
                image_name=self.image_name,
                worker_id=worker_id,
                action_label="creating snapshot-restored worker",
                volumes=volumes,
                timeout=self._restore_api_timeout,
            )
            if not launched:
                return False

            self._wait_for_ready(
                container_name,
                port,
                timeout=self._restore_ready_timeout,
                context="snapshot-restore",
            )
            if worker_id in self.instances:
                self.instances[worker_id].running = True
            return True
        except (docker_errors.DockerException, RuntimeError) as e:
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
            password=self.base_config.password,
        )

    def collect_memory_utilization(self, worker_id: int) -> float:
        """Collect container memory utilization ratio using cgroup usage/limit."""
        container_name = self._container_name(worker_id)
        try:
            container = self.client.containers.get(container_name)
            stats = container.stats(stream=False)
        except (docker_errors.NotFound, docker_errors.APIError) as exc:
            LOGGER.debug(
                "Unable to collect Docker memory stats for %s: %s", container_name, exc
            )
            return 0.0

        try:
            memory_stats = stats.get("memory_stats", {})
            usage = float(memory_stats.get("usage", 0.0))
            limit = float(memory_stats.get("limit", 0.0))
            if limit <= 0.0:
                return 0.0
            return max(0.0, min(1.0, usage / limit))
        except (TypeError, ValueError) as exc:
            LOGGER.debug(
                "Invalid Docker memory stats payload for %s: %s", container_name, exc
            )
            return 0.0

    def _wait_for_ready(
        self,
        container_name: str,
        port: int,
        timeout: int = 60,
        context: str = "startup",
    ) -> None:
        """Wait until PostgreSQL is accepting connections."""
        active_config = DatabaseConfig(
            host="127.0.0.1",
            port=port,
            dbname=self.base_config.dbname,
            user=self.base_config.user,
            password=self.base_config.password,
        )

        LOGGER.debug("  Waiting for container '%s' to be ready...", container_name)
        start_time = time.time()
        while time.time() - start_time < timeout:
            try:
                conn = get_connection(config=active_config, connect_timeout=2)
                conn.close()
                LOGGER.debug(
                    "%s  ➤ Container '%s' is ready after %.2f seconds.%s",
                    ColorCode.GREEN,
                    container_name,
                    time.time() - start_time,
                    ColorCode.RESET,
                )
                return
            except psycopg2.OperationalError:
                time.sleep(1)

        log_excerpt = ""
        try:
            container = self.client.containers.get(container_name)
            raw_logs = container.logs(tail=80)
            decoded_logs = raw_logs.decode("utf-8", errors="replace")
            if decoded_logs.strip():
                log_excerpt = "\nRecent container logs:\n" + decoded_logs
        except docker_errors.DockerException:
            pass

        raise RuntimeError(
            f"Database in container {container_name} failed to become ready "
            f"within {timeout}s during {context}.{log_excerpt}"
        )
