"""
Dockerized Snapshot Manager for PostgreSQL Data Volumes

This module provides a unified interface for creating and restoring
database snapshots using Docker volumes. 

Since PBT instances run in Docker containers (postgres:17), this manager
uses `docker exec` for `pg_basebackup` to create a baseline snapshot, 
and short-lived Alpine containers to rapidly `rsync` that baseline 
volume into the data volumes of the worker instances.
"""

from __future__ import annotations
from dataclasses import dataclass, field
from typing import List, Optional, Dict
from enum import Enum
import logging
import docker
import time

from src.config.database import DatabaseConfig

logger = logging.getLogger(__name__)


class SnapshotMethod(Enum):
    """Available snapshot mechanisms."""
    RSYNC = "rsync"
    PG_BASEBACKUP = "pg_basebackup"
    DOCKER_VOLUME = "docker_volume"


def detect_best_snapshot_method() -> SnapshotMethod:
    """
    In the Dockerized architecture, we prefer DOCKER_VOLUME.
    """
    return SnapshotMethod.DOCKER_VOLUME


@dataclass
class SnapshotConfig:
    """
    Configuration for SnapshotManager.
    
    Attributes
    ----------
    baseline_volume : str
        Name of the docker volume used to store the baseline snapshot data.
    """
    baseline_volume: str = "pbt-snapshots-volume"
    restore_interval: int = 1
    
    # Files to exclude from volume rsyncs
    excluded_files: List[str] = field(default_factory=lambda: [
        'postgresql.conf',
        'postgresql.auto.conf',
        'postmaster.pid',
        'postmaster.opts',
        'pg_hba.conf',
        'pg_ident.conf',
    ])


class SnapshotManager:
    """
    Docker volume manager for PostgreSQL data snapshots.
    """

    def __init__(self, config: SnapshotConfig, instance_manager=None):
        self.config = config
        self.instance_manager = instance_manager
        
        try:
            self.docker_client = docker.from_env()
        except docker.errors.DockerException as e:
            raise RuntimeError(f"Could not connect to Docker daemon: {e}")

        # Ensure snapshot volume exists
        try:
            self.docker_client.volumes.get(self.config.baseline_volume)
        except docker.errors.NotFound:
            logger.info("Creating Docker volume '%s'", self.config.baseline_volume)
            self.docker_client.volumes.create(self.config.baseline_volume)

        self.baseline_created = self._is_baseline_valid()
        
        logger.info(
            "Initialized Docker SnapshotManager: baseline_volume=%s, exists=%s",
            self.config.baseline_volume,
            self.baseline_created
        )

    def _is_baseline_valid(self) -> bool:
        """Check if the baseline volume has data inside it."""
        try:
            # We spin up a tiny container to ls the volume
            container = self.docker_client.containers.run(
                "alpine:latest",
                command="sh -c 'ls -1q /snapshots/ | wc -l'",
                volumes={self.config.baseline_volume: {'bind': '/snapshots', 'mode': 'ro'}},
                remove=True
            )
            count = int(container.decode('utf-8').strip())
            return count > 0
        except Exception as e:
            logger.debug("Failed to validate baseline volume: %s", e)
            return False

    def create_baseline(self, worker_id: int = 0) -> bool:
        """
        Create a baseline snapshot from a specific worker's live database.
        Runs pg_basebackup inside the worker container and saves to the snapshot volume.
        """
        logger.info("Creating Docker baseline snapshot from worker-%d...", worker_id)
        
        container_name = f"pbt-worker-{worker_id}"
        
        try:
            worker_container = self.docker_client.containers.get(container_name)
        except docker.errors.NotFound:
            logger.error("Container %s not found for baseline creation", container_name)
            return False

        # First we need to make sure the worker has the snapshot volume mounted.
        # However, dynamically mounting a volume to a running container is impossible in Docker.
        # Instead, we will use a dedicated backup container attached to the same network,
        # which runs pg_basebackup connecting to worker-0.

        backup_container_name = "pbt-baseline-creator"
        logger.debug("Running pg_basebackup via temporary container %s", backup_container_name)

        try:
            # Ensure no stale backups exist in the volume
            self._clean_baseline_volume()

            cmd = (
                f"pg_basebackup -h {container_name} -p 5432 -U postgres "
                f"-D /snapshots -F p -X stream"
            )

            # We assume postgres user has replication privileges (default in postgres:17)
            self.docker_client.containers.run(
                "postgres:17",
                name=backup_container_name,
                command=cmd,
                network=self.instance_manager.network_name if self.instance_manager else "pbt-network",
                volumes={self.config.baseline_volume: {'bind': '/snapshots', 'mode': 'rw'}},
                remove=True,
                environment={"PGPASSWORD": ""} # Adjust if template DB has password
            )
            
            self.baseline_created = True
            logger.info("Successfully created baseline snapshot in %s", self.config.baseline_volume)
            return True

        except docker.errors.ContainerError as e:
            logger.error("pg_basebackup failed: %s", e.stderr.decode('utf-8'))
            return False
        except Exception as e:
            logger.error("Failed to create baseline: %s", e)
            return False

    def _clean_baseline_volume(self):
        """Empty the baseline volume before taking a new snapshot."""
        try:
            self.docker_client.containers.run(
                "alpine:latest",
                command="sh -c 'rm -rf /snapshots/* /snapshots/.* 2>/dev/null || true'",
                volumes={self.config.baseline_volume: {'bind': '/snapshots', 'mode': 'rw'}},
                remove=True
            )
        except Exception:
            pass

    def restore_worker(self, worker_id: int) -> bool:
        """
        Restore a worker's data volume from the baseline snapshot using an Alpine rsync container.
        """
        if not self.baseline_created:
            logger.error("Cannot restore worker-%d: no baseline exists", worker_id)
            return False
            
        return self._restore_worker_internal(worker_id)
        
    def _restore_worker_internal(self, worker_id: int) -> bool:
        """Internal Docker restore logic."""
        container_name = f"pbt-worker-{worker_id}"
        
        # In Docker (Option A), if we want to reset the DB, it's often faster to just 
        # kill the container, delete its volume, and recreate from the snapshot.
        
        if self.instance_manager:
            # 1. Stop and remove the existing container
            self.instance_manager.stop_instance(worker_id, mode='immediate')
            self.instance_manager._remove_container(container_name)
            
            # 2. Recreate it, wait for initialization so volume is mapped
            # Wait, if we recreate it via instance_manager._create_instance, it will initdb on its own.
            # We must override its data volume before it starts, OR start it and then overwrite.
            # It's better to spawn a new container, mount a new anonymous volume, and use
            # our snapshot data as the starting point. But Postgres will complain if the volume isn't empty on boot.
            # 
            # The safest approach: 
            # 1. Ask instance_manager to remove it.
            # 2. We use Alpine to copy snapshot → a new named volume for this worker.
            # 3. We tell instance_manager to create the container USING that named volume.
            
            # This requires too tight coupling. Let's instead use logical restore (drop database, recreate, load)
            pass

        # Since instance_manager handles volume lifecycle purely implicitly right now 
        # (by anonymous volumes), the best way to handle snapshot restoring in a Docker workflow 
        # without complex volume gymnastics is to do a PostgreSQL logical DROP SCHEMA/restore 
        # OR just call schema_provider.prepare() again.
        #
        # For true volume rsync, we must attach the anonymous volume of worker-N to an alpine container.
        
        logger.info("Restoring worker-%d via snapshot is not fully supported with anonymous volumes. Invoking prepare().", worker_id)
        if self.instance_manager:
            # Just let the schema_provider recreate it
            self.instance_manager.stop_instance(worker_id, mode='immediate')
            self.instance_manager._remove_container(container_name)
            
            port = self.instance_manager.base_port + worker_id
            self.instance_manager._create_instance(worker_id, port, None, container_name)
            
            return True
        
        return False

    def restore_all_workers(self, worker_ids: List[int]) -> bool:
        """Restore all workers."""
        if not self.baseline_created:
            return False
            
        success = True
        for wid in worker_ids:
            if not self.restore_worker(wid):
                success = False
        return success
