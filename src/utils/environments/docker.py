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
from typing import Optional, List, Dict, Any, TYPE_CHECKING
from pathlib import Path

import psycopg2
import requests
from docker import errors as docker_errors

from src.utils.environments.base import DatabaseEnvironment, InstanceConfig
from src.utils.hardware_info import WorkerResources
from src.benchmarks.executor import BenchmarkExecutor
from src.utils.logger import get_logger, get_color_context
from src.database.connection import get_connection
from src.config.database import DatabaseConfig

if TYPE_CHECKING:
    from src.utils.types import WorkerResourceAllocation

try:
    import docker
except ImportError as exc:
    raise ImportError(
        "The 'docker' Python package is not installed. "
        "Install it with: pip install docker>=7.0.0"
    ) from exc

LOGGER = get_logger("DockerEnvironment")
COLORS = get_color_context()


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
        worker_resources: Optional[WorkerResources] = None,
        image_name: str = "postgres:18",
        base_port: int = 5440,
        base_dir: Path = Path("./.instances"),
        container_prefix: str = "pbt-worker",
        force_recreate_baseline: bool = False,
        data_device_node: Optional[str] = None,
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
        self.worker_resources = worker_resources
        self.image_name = image_name
        self.base_port = base_port
        self.base_dir = base_dir
        self.container_prefix = container_prefix
        self.data_device_node = data_device_node

        self.client = docker.from_env(timeout=30)
        self.instances: Dict[int, InstanceConfig] = {}
        self._snapshot_timeout = self._derive_snapshot_timeout()
        self._ready_timeout = 60
        self._restore_ready_timeout = self._derive_restore_ready_timeout()
        self._restore_api_timeout = self._derive_restore_api_timeout()
        # ``_num_parallel_workers`` is set in ``setup_instances``; default
        # to 1 so resource allocation queries before that call still work.
        self._num_parallel_workers = 1

        # Capture the Docker daemon version once at init so SessionEnvironment
        # has it before setup_instances runs. Failures fall back to ``None``.
        try:
            version_info = self.client.version()
            self.docker_version = version_info.get("Version") if version_info else None
        except (docker_errors.DockerException, requests.RequestException, OSError) as exc:
            LOGGER.debug("Failed to capture Docker version: %s", exc)
            self.docker_version = None

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

    def _host_path(self, *parts: str) -> Path:
        """Resolve a host path that can be safely used in a Docker bind mount."""
        return self.base_dir.joinpath(*parts).expanduser().resolve()

    def _docker_bind_path(self, path: Path) -> str:
        """Convert a host path into the absolute path Docker expects for binds."""
        return str(path.expanduser().resolve())

    def _worker_host_pgdata_dir(self, worker_id: int) -> Path:
        """Resolve the host-side PGDATA directory for a worker's bind mount.

        When ``base_dir`` points to an external drive, all database I/O
        flows through that drive while Docker still provides process
        isolation, cgroup resource limits, and network namespacing.
        """
        return self._host_path(
            self._get_instance_subpath(),
            f"worker_{worker_id}",
            "pgdata",
        )

    def _worker_port(self, worker_id: int) -> int:
        """Resolve a worker's host port."""
        return self.base_port + worker_id

    def _worker_cpuset_cpus(
        self, worker_id: int, num_workers: int, concurrency: Optional[int] = None
    ) -> Optional[str]:
        """Assign each worker a deterministic, non-overlapping CPU slice.

        With parallel execution, workers are grouped into batches. Within each batch,
        workers get different CPU subsets. Across batches (sequential), workers reuse
        the same CPU subsets.

        Example with 8 workers, 4 parallel, 2 CPUs per worker:
        - Batch 1 (workers 0-3 parallel): [0,1], [2,3], [4,5], [6,7]
        - Batch 2 (workers 4-7 parallel): [0,1], [2,3], [4,5], [6,7] (reused)
        """
        del num_workers, concurrency  # Not needed; budget already encodes parallelism
        worker_cpu_budget = self._worker_cpu_budget()
        if worker_cpu_budget <= 0:
            return None

        host_cpu_count = os.cpu_count() or 1
        # Determine position within parallel batch (cycles back to 0 for sequential batches)
        within_batch_id = worker_id % self._num_parallel_workers
        start_index = within_batch_id * worker_cpu_budget

        # Clamp slice to available host CPUs
        cpu_slice = [
            cpu_id
            for cpu_id in range(start_index, start_index + worker_cpu_budget)
            if cpu_id < host_cpu_count
        ]

        if not cpu_slice:
            return None

        return ",".join(str(cpu_id) for cpu_id in cpu_slice)

    def _worker_cpu_budget(self) -> int:
        """Return the per-worker CPU budget used for cpuset sizing.

        Prefer the concrete `WorkerResources` object when available because it is
        the canonical per-worker resource allocation computed by hardware
        detection. Fall back to the legacy `cpu_cores` value for compatibility
        with tests and older call sites.
        """
        if self.worker_resources is not None:
            return max(0, int(self.worker_resources.cpu_cores))
        return max(0, int(self.cpu_cores))

    def _container_runtime_kwargs(
        self,
        worker_id: int,
        num_workers: int,
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
            # CPU quota and cpuset must agree on the per-worker budget. Both
            # read ``_worker_cpu_budget()`` (which prefers the canonical
            # ``worker_resources.cpu_cores`` and falls back to the legacy
            # ``cpu_cores`` scalar) so a direct constructor call that leaves
            # ``cpu_cores`` at its 0.0 default cannot silently drop the CPU
            # quota while cpuset still pins cores.
            "nano_cpus": (
                int(self._worker_cpu_budget() * 1e9)
                if self._worker_cpu_budget() > 0
                else None
            ),
            "cpuset_cpus": self._worker_cpuset_cpus(worker_id, num_workers),
            "network": self.network_name,
            "detach": True,
        }

        # Per-worker disk I/O bandwidth + IOPS limits via cgroup blkio /
        # cgroup v2 io.max. Only emit kwargs when (a) the worker resources
        # carry a non-zero budget for that field and (b) we have a device
        # node to target -- omitting the kwargs entirely is the safe
        # default (Docker treats absence as unlimited).
        wr = self.worker_resources
        device_node = self.data_device_node
        if wr is not None and device_node:
            if getattr(wr, "disk_read_bps", 0) > 0:
                kwargs["device_read_bps"] = [
                    {"Path": device_node, "Rate": int(wr.disk_read_bps)}
                ]
            if getattr(wr, "disk_write_bps", 0) > 0:
                kwargs["device_write_bps"] = [
                    {"Path": device_node, "Rate": int(wr.disk_write_bps)}
                ]
            if getattr(wr, "disk_read_iops", 0) > 0:
                kwargs["device_read_iops"] = [
                    {"Path": device_node, "Rate": int(wr.disk_read_iops)}
                ]
            if getattr(wr, "disk_write_iops", 0) > 0:
                kwargs["device_write_iops"] = [
                    {"Path": device_node, "Rate": int(wr.disk_write_iops)}
                ]

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
                old_container.remove(force=True, v=True)
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

    def _prepare_worker_pgdata_dir(self, worker_id: int, quiet: bool = False) -> Path:
        """
        Prepare a clean host directory for a worker's PGDATA bind mount.

        Removes any existing data and creates a fresh directory with
        permissions that allow the container's ``postgres`` user
        (typically UID 999) to write.
        """
        pgdata_dir = self._worker_host_pgdata_dir(worker_id)
        self._force_remove_host_dir(pgdata_dir, quiet=quiet)
        pgdata_dir.mkdir(parents=True, exist_ok=True)
        os.chmod(pgdata_dir, 0o777)
        return pgdata_dir

    def _force_remove_host_dir(self, target_dir: Path, quiet: bool = False) -> None:
        """Remove a host directory that may contain Docker-owned files.

        Files created by containerized PostgreSQL (UID 999) are not
        deletable by the host user.  This method mounts the *parent*
        directory into a disposable container and ``rm -rf``'s the
        child, side-stepping the ownership mismatch.
        """
        if not quiet:
            LOGGER.debug(
                "  Force-removing host directory '%s' via containerized rm -rf",
                target_dir,
            )
        if not target_dir.exists():
            return

        child_name = target_dir.name
        parent_dir = str(target_dir.parent)

        try:
            container = self.client.containers.run(
                self.image_name,
                entrypoint=["rm", "-rf", f"/host/{child_name}"],
                volumes={
                    self._docker_bind_path(Path(parent_dir)): {
                        "bind": "/host",
                        "mode": "rw",
                    }
                },
                detach=True,
            )
            result = container.wait()
            if result.get("StatusCode", 1) != 0:
                raise docker_errors.DockerException(
                    f"rm -rf failed with exit code {result.get('StatusCode')}: "
                    f"{container.logs().decode('utf-8', errors='replace')}"
                )
            container.remove(force=True, v=True)
        except docker_errors.DockerException as exc:
            if not quiet:
                LOGGER.debug(
                    "  ➤ Container-based removal of '%s' failed: %s; "
                    "attempting host-side rmtree as fallback...",
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
        num_workers: int,
        action_label: str,
        volumes: Optional[Dict[str, Dict[str, str]]] = None,
        timeout: Optional[int] = None,
    ) -> tuple[bool, Optional[Exception]]:
        """Launch a worker container with shared timeout + recovery handling."""
        container_name = self._container_name(worker_id)
        kwargs = self._container_runtime_kwargs(
            worker_id=worker_id,
            num_workers=num_workers,
            volumes=volumes,
        )

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
                "  ➤ Docker timed out %s '%s'; checking container state: %s",
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
        quiet: bool = False,
    ) -> Optional[Path]:
        """Prepare a clean host PGDATA directory and seed it from the baseline snapshot.

        Copies the snapshot's PGDATA directory into the worker's bind-mount
        directory using a container (to handle UID 999 ownership).

        The freshly-written ``postgresql.auto.conf`` is preserved across the
        wipe so the knob configuration applied by the orchestrator survives
        a restore-as-restart. The snapshot itself was created with auto.conf
        excluded (see ``create_snapshot``), so the post-copy state has only
        the per-worker auto.conf the orchestrator just wrote — which is the
        whole point of the apply_only -> restore-as-restart sequence.
        """
        snapshot_pgdata = self._snapshot_host_dir()
        if not snapshot_pgdata.exists():
            LOGGER.error(
                "Snapshot PGDATA dir '%s' does not exist; cannot seed worker %d",
                snapshot_pgdata,
                worker_id,
            )
            return None

        # Do NOT wipe the destination from the host side: that would destroy
        # the postgresql.auto.conf the orchestrator just wrote via apply_only.
        # The wipe + preserve + copy + restore happens atomically inside the
        # copy container (which runs as UID 999 and can manage postgres-owned
        # files without chown errors).
        pgdata_dir = self._worker_host_pgdata_dir(worker_id)
        if not pgdata_dir.exists():
            # Brand-new path (e.g. first restore on a fresh repo): create
            # with permissive mode so the copy container can write into it.
            pgdata_dir.mkdir(parents=True, exist_ok=True)
            os.chmod(pgdata_dir, 0o777)
        # If the dir already exists, don't mkdir or chmod — the host user
        # may not own the contents (postgres UID 999 created them) and
        # ``os.chmod`` would raise PermissionError. The copy container
        # handles all in-directory mutation as 999:999.

        try:
            with self._with_timeout(self._restore_api_timeout):
                container = self.client.containers.run(
                    self.image_name,
                    user="999:999",  # Copy as postgres user to avoid chown errors on exFAT/NTFS
                    entrypoint=["bash", "-lc"],
                    command=[
                        # 1. Preserve auto.conf if present (apply_only just
                        #    wrote the worker's knobs there).
                        # 2. Wipe everything in /dest (worker's stale PGDATA).
                        # 3. Copy snapshot in (snapshot has no auto.conf).
                        # 4. Restore preserved auto.conf if we saved one.
                        # find -mindepth 1 -delete leaves /dest itself intact
                        # but removes every child (regular files, dirs, dotfiles).
                        "set -euo pipefail; "
                        "PRESERVED=0; "
                        "if [ -f /dest/postgresql.auto.conf ]; then "
                        "  cp -p /dest/postgresql.auto.conf /tmp/auto.conf.preserve; "
                        "  PRESERVED=1; "
                        "fi; "
                        "find /dest -mindepth 1 -delete; "
                        "cp -R /source/. /dest/; "
                        "if [ \"$PRESERVED\" = \"1\" ]; then "
                        "  cp -p /tmp/auto.conf.preserve /dest/postgresql.auto.conf; "
                        "fi"
                    ],
                    volumes={
                        self._docker_bind_path(snapshot_pgdata): {
                            "bind": "/source",
                            "mode": "ro",
                        },
                        self._docker_bind_path(pgdata_dir): {
                            "bind": "/dest",
                            "mode": "rw",
                        },
                    },
                    detach=True,
                )
                result = container.wait()
                if result.get("StatusCode", 1) != 0:
                    raise docker_errors.DockerException(
                        f"Copy failed with exit code {result.get('StatusCode')}: "
                        f"{container.logs().decode('utf-8', errors='replace')}"
                    )
                container.remove(force=True, v=True)

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

        volumes = {
            self._docker_bind_path(pgdata_dir): {
                "bind": "/pgdata/data",
                "mode": "rw",
            }
        }
        launched, _ = self._launch_worker_container(
            image_name=self.image_name,
            worker_id=worker_id,
            num_workers=1,
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

    def get_resource_allocations(self) -> "List[WorkerResourceAllocation]":
        """Return per-worker resource allocations enforced via cgroups.

        Reads the same ``cpuset_cpus`` and ``mem_limit`` values that go
        into ``_container_runtime_kwargs`` so the JSON record matches
        what Docker actually applied.
        """
        from src.utils.types import WorkerResourceAllocation

        worker_ids = sorted(self.instances.keys())
        if not worker_ids:
            # Fall back to a single-worker projection so the SessionEnvironment
            # builder still has something useful when called before setup.
            worker_ids = [0]
        num_workers = len(worker_ids)
        allocations: List[WorkerResourceAllocation] = []
        per_worker_ram = (
            int(self.worker_resources.ram_bytes)
            if self.worker_resources is not None
            else int(self.ram_bytes)
        )
        per_worker_cpu = (
            int(self.worker_resources.cpu_cores)
            if self.worker_resources is not None
            else int(self.cpu_cores) if self.cpu_cores else 0
        )
        docker_mem_limit = self.ram_bytes if self.ram_bytes > 0 else None
        wr = self.worker_resources
        per_worker_disk_read_bps = int(getattr(wr, "disk_read_bps", 0)) if wr else 0
        per_worker_disk_write_bps = int(getattr(wr, "disk_write_bps", 0)) if wr else 0
        per_worker_disk_read_iops = int(getattr(wr, "disk_read_iops", 0)) if wr else 0
        per_worker_disk_write_iops = int(getattr(wr, "disk_write_iops", 0)) if wr else 0
        for worker_id in worker_ids:
            allocations.append(
                WorkerResourceAllocation(
                    worker_id=worker_id,
                    cpu_cores=per_worker_cpu,
                    cpuset_cpus=self._worker_cpuset_cpus(worker_id, num_workers),
                    ram_bytes=per_worker_ram,
                    docker_memory_limit_bytes=docker_mem_limit,
                    disk_read_bps=per_worker_disk_read_bps,
                    disk_write_bps=per_worker_disk_write_bps,
                    disk_read_iops=per_worker_disk_read_iops,
                    disk_write_iops=per_worker_disk_write_iops,
                    disk_device_path=self.data_device_node,
                )
            )
        return allocations

    def setup_instances(
        self,
        num_workers: int,
        force_recreate: bool = False,
        num_parallel_workers: int = 1,
    ) -> List[InstanceConfig]:
        """Create and start the Docker containers for N workers."""
        if num_workers <= 0:
            raise ValueError("Must specify at least 1 worker")

        # Store num_parallel_workers for CPU allocation within _worker_cpuset_cpus
        self._num_parallel_workers = num_parallel_workers

        if self.force_recreate_baseline:
            self._remove_baseline_snapshot()

        baseline_snapshot_available = bool(
            self.schema_provider and self.snapshot_exists(worker_id=0)
        )

        for worker_id in range(num_workers):
            port = self._worker_port(worker_id)
            container_name = self._container_name(worker_id)
            LOGGER.info(
                " %sSetting up container '%s' on port %d:%s",
                COLORS.sky_blue,
                container_name,
                port,
                COLORS.reset,
            )

            recreate_worker0_for_baseline = (
                worker_id == 0
                and self.schema_provider is not None
                and not force_recreate
                and not baseline_snapshot_available
            )

            if force_recreate:
                LOGGER.debug(
                    "  %sRemoving existing container '%s'...%s",
                    COLORS.italic,
                    container_name,
                    COLORS.reset,
                )
                if not self._remove_worker_container(
                    worker_id=worker_id,
                    purpose="forced recreate",
                ):
                    raise RuntimeError(
                        f"Failed to remove container '{container_name}' during forced recreate"
                    )

                LOGGER.debug(
                    "%s  ➤ Container '%s' removed successfully%s",
                    COLORS.italic,
                    container_name,
                    COLORS.reset,
                )

            try:  # Exists already
                container = self.client.containers.get(container_name)
                if recreate_worker0_for_baseline:
                    LOGGER.debug(
                        "  Recreating existing worker-0 container because baseline snapshot is missing"
                    )
                    container.remove(force=True, v=True)
                    raise docker_errors.NotFound("recreate worker-0 for baseline")

                if container.status != "running":
                    LOGGER.debug(
                        "   Starting stopped container '%s'...", container_name
                    )
                    container.start()
                running = True

                LOGGER.debug(
                    "%s  ➤ Container '%s' already exists, reusing it.%s",
                    COLORS.italic,
                    container_name,
                    COLORS.reset,
                )
            except docker_errors.NotFound:  # Create it
                LOGGER.debug("  Creating container '%s'...", container_name)
                pgdata_dir = self._prepare_worker_pgdata_dir(worker_id)
                volumes = {
                    self._docker_bind_path(pgdata_dir): {
                        "bind": "/pgdata/data",
                        "mode": "rw",
                    }
                }
                launched, launch_error = self._launch_worker_container(
                    image_name=self.image_name,
                    worker_id=worker_id,
                    num_workers=num_workers,
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
                    COLORS.italic,
                    container_name,
                    COLORS.reset,
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
                self.initialize_schema(worker_id)

                if worker_id == 0:
                    baseline_snapshot_available = self.snapshot_exists(worker_id=0)
                    if baseline_snapshot_available:
                        LOGGER.debug(
                            "  %s➤ Baseline snapshot already exists; skipping snapshot creation%s",
                            COLORS.italic,
                            COLORS.reset,
                        )
                    else:
                        LOGGER.debug(
                            "  Caching worker 0 baseline snapshot for fast-path initialization...",
                        )
                        snapshot_id = self.create_snapshot(worker_id=0)
                        if not snapshot_id:
                            raise RuntimeError(
                                "Failed to create baseline Docker snapshot for worker 0"
                            )
                        baseline_snapshot_available = True

            LOGGER.debug(
                " ➤ Container '%s' set up successfully.",
                container_name,
            )

        LOGGER.info(
            "%s➤ All %d containers set up successfully.%s",
            COLORS.bold,
            num_workers,
            COLORS.reset,
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
        LOGGER.debug("  Stopping container for [worker-%d]...", worker_id)
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
        LOGGER.info("%sStopping PostgreSQL instances...%s", COLORS.bold, COLORS.reset)
        for worker_id in list(self.instances):
            self.stop_instance(worker_id, mode)
        LOGGER.debug("➤ All stop commands issued, verifying container states...")

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
                        "  Docker timed out stopping container '%s' during stop_all; checking state: %s",
                        container_name,
                        exc,
                    )
                    try:
                        container.reload()
                        if getattr(container, "status", "") == "running":
                            LOGGER.debug(
                                "  Container '%s' is still running after timeout in stop_all",
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
                        "  Failed to stop container '%s' during stop_all: %s",
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
                "  Unable to list running Docker containers during stop_all: %s", exc
            )

        LOGGER.info("%s➤ All containers stopped.%s", COLORS.bold, COLORS.reset)
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

    def _checkpoint_instance(self, worker_id: int) -> bool:
        """Issue a CHECKPOINT to flush dirty buffers and recycle WAL.

        Called before ``container.restart()`` so PostgreSQL has minimal
        work during shutdown and can exit cleanly within the Docker
        stop-timeout.  Without this, large ``shared_buffers`` configs
        (common with LHS-sampled knob values) cause the shutdown
        checkpoint to exceed the timeout — Docker SIGKILLs the
        process, WAL is never recycled, and disk usage grows
        monotonically across iterations.
        """
        try:
            db_config = self.get_db_config(worker_id)
            conn = get_connection(config=db_config, connect_timeout=5)
            conn.autocommit = True
            cursor = conn.cursor()
            cursor.execute("CHECKPOINT")
            cursor.close()
            conn.close()
            LOGGER.debug(
                "Pre-restart CHECKPOINT completed for worker %d", worker_id
            )
            return True
        except Exception as exc:  # noqa: BLE001
            LOGGER.debug(
                "Pre-restart CHECKPOINT for worker %d failed (non-fatal): %s",
                worker_id,
                exc,
            )
            return False

    def restart_instance(self, worker_id: int, quiet: bool = False) -> bool:
        """
        Restart a specific worker's Docker container.

        Uses Docker's native restart command which handles stop+start
        atomically, then waits for PostgreSQL readiness.
        """
        container_name = self._container_name(worker_id)
        try:
            container = self.client.containers.get(container_name)
            self._checkpoint_instance(worker_id)
            if not quiet:
                LOGGER.info("Restarting container '%s'...", container_name)
            container.restart(timeout=30)
            db_config = self.get_db_config(worker_id)
            self._wait_for_ready(
                container_name,
                db_config.port,
                timeout=self._ready_timeout,
                context="restart",
                quiet=quiet,
            )
            if not self.reset_statistics(worker_id):
                LOGGER.warning(
                    "Container '%s' restarted but statistics reset failed.",
                    container_name,
                )

            if not quiet:
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

    def verify_instances(self) -> None:
        """Check status of containers."""
        for worker_id, instance_config in self.instances.items():
            container_name = self._container_name(worker_id)
            try:
                self.client.containers.get(container_name)
            except (
                docker_errors.DockerException,
                requests.exceptions.RequestException,
                TimeoutError,
                OSError,
            ) as exc:
                instance_config.running = False
                LOGGER.error(
                    "Unable to verify container '%s' for worker %d: %s",
                    container_name,
                    worker_id,
                    exc,
                )
                raise RuntimeError(
                    f"Failed to verify container '{container_name}' for worker {worker_id}"
                ) from exc

        LOGGER.debug("➤ All containers verified successfully.")

    def cleanup(self, remove_data: bool = False) -> None:
        """Remove containers and optionally their host PGDATA directories."""
        LOGGER.info("%sCleaning up Docker environment...%s", COLORS.bold, COLORS.reset)

        for worker_id in list(self.instances):
            LOGGER.debug("  Cleaning up [Worker-%d]...", worker_id)
            container_name = self._container_name(worker_id)
            try:
                container = self.client.containers.get(container_name)
                container.remove(force=True, v=True)
            except docker_errors.NotFound:
                pass
            except (
                docker_errors.DockerException,
                requests.exceptions.RequestException,
                TimeoutError,
                OSError,
            ) as exc:
                LOGGER.debug(
                    "  Unable to remove container '%s' for [Worker-%d] during cleanup: %s",
                    container_name,
                    worker_id,
                    exc,
                )

            if remove_data:
                pgdata_dir = self._worker_host_pgdata_dir(worker_id)
                self._force_remove_host_dir(pgdata_dir)
                LOGGER.debug(
                    "  ➤ Removed host PGDATA directory '%s' for [Worker-%d]",
                    pgdata_dir,
                    worker_id,
                )
        self.instances.clear()
        LOGGER.info(
            "%s➤ Docker environment cleanup complete.%s", COLORS.bold, COLORS.reset
        )

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
        # The Docker SDK's APIClient may accept None to indicate "no timeout",
        # but the type stubs declare an `int` timeout. Silence the type checker
        # here while preserving runtime semantics by assigning the provided
        # value directly.
        self.client.api.timeout = seconds  # type: ignore[assignment]
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

    def _checkpoint_before_snapshot(self, worker_id: int) -> None:
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

        # Default provider identity is the full class path. Some benchmark
        # executors (e.g., Sysbench) expose multiple workload *scripts* that
        # only change the query mix but not the underlying physical dataset
        # (tables, rows, indexes). In such cases we collapse the provider
        # identity to a canonical family while preserving the dataset-defining
        # parameters so snapshots can be reused across script variants.
        provider_module = provider.__class__.__module__
        provider_class = provider.__class__.__name__

        if (
            provider_module == "src.benchmarks.sysbench.executor"
            and provider_class == "SysbenchExecutor"
        ):
            context: Dict[str, Any] = {
                "provider": "sysbench",
                "sysbench_tables": getattr(provider, "tables", None),
                "sysbench_table_size": getattr(provider, "table_size", None),
            }
            return context

        provider_name = f"{provider.__class__.__module__}.{provider.__class__.__name__}"
        generic_context: Dict[str, Any] = {"provider": provider_name}

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
                generic_context[attribute] = value

        return generic_context

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
        return self._host_path(".snapshots", self._default_snapshot_id(), "pgdata")

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
            LOGGER.debug(
                " Removing baseline snapshot '%s' at '%s' (force_recreate_baseline=True)",
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
            self._checkpoint_before_snapshot(worker_id)

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
                    command=[
                        "set -euo pipefail; cp -R /source/. /dest/ && rm -f /dest/postgresql.auto.conf"
                    ],
                    volumes={
                        self._docker_bind_path(source_pgdata): {
                            "bind": "/source",
                            "mode": "ro",
                        },
                        self._docker_bind_path(snapshot_pgdata): {
                            "bind": "/dest",
                            "mode": "rw",
                        },
                    },
                    detach=True,
                )
                result = copy_container.wait()
                if result.get("StatusCode", 1) != 0:
                    raise docker_errors.DockerException(
                        f"Copy failed with exit code {result.get('StatusCode')}: "
                        f"{copy_container.logs().decode('utf-8', errors='replace')}"
                    )
                copy_container.remove(force=True, v=True)

            # Restart the worker
            container.start()
            self._wait_for_ready(
                container_name,
                port,
                timeout=self._ready_timeout,
                context="snapshot-post-start",
            )

            self._write_snapshot_manifest(snapshot_id=snapshot_id)
            LOGGER.debug(
                "   %s➤ Baseline snapshot created for worker %d%s",
                COLORS.italic,
                worker_id,
                COLORS.reset,
            )
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
                "    Snapshot dir '%s' exists but manifest is missing/invalid; treating as stale",
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
                "    Snapshot '%s' manifest mismatch (manifest_snapshot_id=%s, "
                "manifest_signature=%s, expected_signature=%s); treating as stale",
                snapshot_id,
                manifest_snapshot_id,
                manifest_signature,
                expected_signature,
            )
            return False

        return True

    def restore_snapshot(
        self, worker_id: int, snapshot_id: str = "", quiet: bool = False
    ) -> bool:
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
                quiet=quiet,
            )
            if not pgdata_dir:
                return False

            volumes = {
                self._docker_bind_path(pgdata_dir): {
                    "bind": "/pgdata/data",
                    "mode": "rw",
                }
            }
            launched, _ = self._launch_worker_container(
                image_name=self.image_name,
                worker_id=worker_id,
                num_workers=1,
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
                quiet=quiet,
            )
            if worker_id in self.instances:
                self.instances[worker_id].running = True
            return True
        except (docker_errors.DockerException, RuntimeError) as e:
            LOGGER.error("Failed to restore snapshot to %s: %s", container_name, e)
            return False

    def clone_instances(
        self, source_worker_id: int, target_worker_ids: List[int]
    ) -> bool:
        """Clone the physical database state from a source worker to multiple target workers."""
        if not target_worker_ids:
            return True

        source_container_name = self._container_name(source_worker_id)
        source_port = self._worker_port(source_worker_id)
        source_pgdata = self._worker_host_pgdata_dir(source_worker_id)

        try:
            # 1. Checkpoint source to flush WAL (minimize recovery on targets)
            self._checkpoint_before_snapshot(source_worker_id)

            # 2. Stop source cleanly
            source_container = self.client.containers.get(source_container_name)
            source_container.reload()
            if source_container.status == "running":
                source_container.stop(timeout=45)

            # 3. Stop and prepare targets
            target_volumes = {}
            for target_id in target_worker_ids:
                # Stop and remove current target container
                if not self._remove_worker_container(
                    worker_id=target_id,
                    purpose="instance clone",
                    timeout=self._restore_api_timeout,
                ):
                    return False

                target_pgdata = self._worker_host_pgdata_dir(target_id)
                self._force_remove_host_dir(target_pgdata)
                target_pgdata.mkdir(parents=True, exist_ok=True)
                os.chmod(target_pgdata, 0o777)

                target_volumes[self._docker_bind_path(target_pgdata)] = {
                    "bind": f"/dest_{target_id}",
                    "mode": "rw",
                }

            # 4. Copy data using a throwaway container
            # Build the copy command for all targets
            copy_commands = ["set -euo pipefail"]
            for target_id in target_worker_ids:
                copy_commands.append(
                    f"cp -R /source/. /dest_{target_id}/ && rm -f /dest_{target_id}/postgresql.auto.conf"
                )

            volumes = {
                self._docker_bind_path(source_pgdata): {
                    "bind": "/source",
                    "mode": "ro",
                }
            }
            volumes.update(target_volumes)

            with self._with_timeout(self._snapshot_timeout):
                copy_container = self.client.containers.run(
                    self.image_name,
                    user="999:999",  # postgres user
                    entrypoint=["bash", "-lc"],
                    command=["; ".join(copy_commands)],
                    volumes=volumes,
                    detach=True,
                )
                result = copy_container.wait()
                if result.get("StatusCode", 1) != 0:
                    raise docker_errors.DockerException(
                        f"Clone copy failed with exit code {result.get('StatusCode')}: "
                        f"{copy_container.logs().decode('utf-8', errors='replace')}"
                    )
                copy_container.remove(force=True, v=True)

            # 5. Start source container back up
            source_container.start()
            self._wait_for_ready(
                source_container_name,
                source_port,
                timeout=self._ready_timeout,
                context="clone-post-start",
            )
            if source_worker_id in self.instances:
                self.instances[source_worker_id].running = True

            # 6. Recreate and Start target containers
            success = True
            for target_id in target_worker_ids:
                target_pgdata = self._worker_host_pgdata_dir(target_id)
                target_container_name = self._container_name(target_id)
                target_port = self._worker_port(target_id)

                vols = {
                    self._docker_bind_path(target_pgdata): {
                        "bind": "/pgdata/data",
                        "mode": "rw",
                    }
                }
                launched, _ = self._launch_worker_container(
                    image_name=self.image_name,
                    worker_id=target_id,
                    num_workers=1,
                    action_label="creating cloned worker",
                    volumes=vols,
                    timeout=self._restore_api_timeout,
                )
                if not launched:
                    success = False
                    continue

                try:
                    self._wait_for_ready(
                        target_container_name,
                        target_port,
                        timeout=self._restore_ready_timeout,
                        context="clone-restore",
                    )
                    if target_id in self.instances:
                        self.instances[target_id].running = True
                except RuntimeError as e:
                    LOGGER.error(
                        "Failed to wait for cloned target %d: %s", target_id, e
                    )
                    success = False

            return success

        except (docker_errors.DockerException, RuntimeError) as e:
            LOGGER.error("Failed to clone instances from %d: %s", source_worker_id, e)
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
                "  ➤ Unable to collect Docker memory stats for %s: %s",
                container_name,
                exc,
            )
            return 0.0

        try:
            memory_stats = stats.get("memory_stats", {})  # type: ignore
            usage = float(memory_stats.get("usage", 0.0))
            limit = float(memory_stats.get("limit", 0.0))
            if limit <= 0.0:
                return 0.0
            return max(0.0, min(1.0, usage / limit))
        except (TypeError, ValueError) as exc:
            LOGGER.debug(
                "  ➤ Invalid Docker memory stats payload for %s: %s",
                container_name,
                exc,
            )
            return 0.0

    def _wait_for_ready(
        self,
        container_name: str,
        port: int,
        timeout: int = 60,
        context: str = "startup",
        quiet: bool = False,
    ) -> None:
        """Wait until PostgreSQL is accepting connections."""
        active_config = DatabaseConfig(
            host="127.0.0.1",
            port=port,
            dbname=self.base_config.dbname,
            user=self.base_config.user,
            password=self.base_config.password,
        )

        if not quiet:
            LOGGER.debug("  Waiting for container '%s' to be ready...", container_name)
        start_time = time.time()
        while time.time() - start_time < timeout:
            try:
                conn = get_connection(config=active_config, connect_timeout=2)
                conn.close()
                if not quiet:
                    LOGGER.debug(
                        "  %s➤ Container '%s' is ready after %.2f seconds.%s",
                        COLORS.italic,
                        container_name,
                        time.time() - start_time,
                        COLORS.reset,
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
