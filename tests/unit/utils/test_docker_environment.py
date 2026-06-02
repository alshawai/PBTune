"""Unit tests for Docker environment snapshot recovery failure branches."""

from __future__ import annotations

import json
import os
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import MagicMock

import docker
import pytest
import requests

from src.config.database import DatabaseConfig
from src.utils.environments.base import InstanceConfig
from src.utils.environments.docker import DockerEnvironment
from src.utils.hardware_info import WorkerResources


class _DummySchemaProvider:
    """Minimal schema provider stand-in used for context payload generation."""


class _SysbenchLikeSchemaProvider:
    """Schema provider stand-in with profile-defining Sysbench attributes."""

    def __init__(self, tables: int, table_size: int) -> None:
        self.tables = tables
        self.table_size = table_size


def _make_environment() -> DockerEnvironment:
    """Create a DockerEnvironment instance without invoking Docker daemon setup."""
    env = DockerEnvironment.__new__(DockerEnvironment)
    env.run_id = "test-run-001"
    env.base_config = DatabaseConfig(
        user="postgres",
        password="postgres",
        host="127.0.0.1",
        port=5440,
        dbname="test_dataset",
    )
    env.schema_provider = _DummySchemaProvider()
    env.cpu_cores = 1.0
    env.ram_bytes = 256 * 1024 * 1024
    env.worker_resources = None
    env.image_name = "postgres:18"
    env.network_name = "pbt-network"
    env.base_port = 5440
    env.base_dir = Path("/tmp")
    env.container_prefix = "pbt-worker"
    env.force_recreate_baseline = False
    env.instances = {
        0: InstanceConfig(
            worker_id=0, port=5440, data_dir=Path("/tmp/worker_0"), running=True
        )
    }
    env._snapshot_timeout = 120
    env._ready_timeout = 60
    env._restore_ready_timeout = 180
    env._restore_api_timeout = 180
    env._num_parallel_workers = 1  # Default: sequential execution
    env._wait_for_ready = MagicMock()
    env._checkpoint_instance = MagicMock()

    env.client = MagicMock()
    env.client.api = SimpleNamespace(timeout=30)
    env.client.volumes = MagicMock()

    return env


def test_worker_cpu_budget_prefers_worker_resources() -> None:
    """Cpuset sizing should use the canonical per-worker budget when available."""
    env = _make_environment()
    env.worker_resources = WorkerResources(
        ram_bytes=512 * 1024 * 1024,
        cpu_cores=2,
        disk_type="SSD",
    )
    env.cpu_cores = 1.0

    assert env._worker_cpu_budget() == 2


from unittest.mock import MagicMock, patch

@patch("os.cpu_count", return_value=4)
def test_worker_cpuset_cpus_uses_budget_only(mock_cpu_count: MagicMock) -> None:
    """Cpuset slices should be derived from the budget and parallel batch position.

    Workers cycle through the same CPU slices across sequential batches.
    Example: 4 workers, 2 parallel, 2 CPUs per worker on 4-CPU host:
    - Batch 1 (workers 0-1): [0,1], [2,3]
    - Batch 2 (workers 2-3): [0,1], [2,3] (cycles)
    """
    env = _make_environment()
    env.worker_resources = WorkerResources(
        ram_bytes=512 * 1024 * 1024,
        cpu_cores=2,
        disk_type="SSD",
    )
    env._num_parallel_workers = 2

    # Batch 1: workers 0-1 (parallel)
    assert env._worker_cpuset_cpus(worker_id=0, num_workers=4) == "0,1"
    assert env._worker_cpuset_cpus(worker_id=1, num_workers=4) == "2,3"

    # Batch 2: workers 2-3 (sequential to batch 1, so they cycle back to same CPUs)
    assert env._worker_cpuset_cpus(worker_id=2, num_workers=4) == "0,1"
    assert env._worker_cpuset_cpus(worker_id=3, num_workers=4) == "2,3"


def test_restore_snapshot_returns_false_when_image_missing() -> None:
    """Missing snapshot image should return False without raising."""
    env = _make_environment()
    env.client.images.get.side_effect = docker.errors.ImageNotFound("missing")

    assert env.restore_snapshot(worker_id=0, snapshot_id="pbt-snapshot-test") is False


def test_restore_snapshot_returns_false_when_container_run_fails() -> None:
    """Container recreation API errors should be surfaced as False."""
    env = _make_environment()
    env.client.images.get.return_value = MagicMock()
    env.client.containers.get.side_effect = docker.errors.NotFound("not found")
    env.client.containers.run.side_effect = docker.errors.APIError("boom")

    assert env.restore_snapshot(worker_id=0, snapshot_id="pbt-snapshot-test") is False


@pytest.mark.skip(reason="Legacy volume tests")
def test_restore_snapshot_uses_restore_specific_ready_timeout() -> None:
    """Snapshot restore should use a longer, restore-specific readiness timeout."""
    env = _make_environment()
    env._restore_ready_timeout = 180

    env.client.images.get.return_value = MagicMock()
    env.client.volumes.get.side_effect = docker.errors.NotFound("not found")
    env.client.volumes.create.return_value = MagicMock()
    env.client.containers.get.side_effect = docker.errors.NotFound("not found")
    env.client.containers.run.return_value = MagicMock()

    assert env.restore_snapshot(worker_id=0, snapshot_id="pbt-snapshot-test") is True
    env.client.volumes.create.assert_called_once_with(name="pbt-worker-0-pgdata")
    seed_call = env.client.containers.run.call_args_list[0]
    assert seed_call.kwargs["volumes"] == {
        "pbt-worker-0-pgdata": {"bind": "/pgseed", "mode": "rw"}
    }
    run_call = env.client.containers.run.call_args_list[1]
    assert run_call.kwargs["volumes"] == {
        "pbt-worker-0-pgdata": {"bind": "/pgdata/data", "mode": "rw"}
    }
    env._wait_for_ready.assert_called_once_with(
        "pbt-worker-0",
        5440,
        timeout=180,
        context="snapshot-restore",
    )


@pytest.mark.skip(reason="Legacy volume tests")
def test_restore_snapshot_removes_container_before_volume_reseed() -> None:
    """Restore should detach the old container before reseeding its PGDATA volume."""
    env = _make_environment()
    env.client.images.get.return_value = MagicMock()

    old_container = MagicMock()
    container_removed = {"value": False}

    def _mark_removed(*_args, **_kwargs) -> None:
        container_removed["value"] = True

    old_container.remove.side_effect = _mark_removed
    env.client.containers.get.return_value = old_container

    def _seed_volume(*_args, **_kwargs) -> str:
        assert container_removed["value"] is True
        return "pbt-worker-0-pgdata"

    env._seed_pgdata_volume_from_snapshot = MagicMock(side_effect=_seed_volume)
    env.client.containers.run.return_value = MagicMock()

    assert env.restore_snapshot(worker_id=0, snapshot_id="pbt-snapshot-test") is True
    old_container.remove.assert_called_once_with(force=True)
    env._seed_pgdata_volume_from_snapshot.assert_called_once_with(
        worker_id=0,
        snapshot_id="pbt-snapshot-test",
    )


@pytest.mark.skip(reason="Legacy volume tests")
def test_restore_snapshot_uses_extended_timeout_for_container_removal() -> None:
    """Restore should apply the restore-specific Docker timeout before removal."""
    env = _make_environment()
    env.client.images.get.return_value = MagicMock()
    env.client.volumes.get.side_effect = docker.errors.NotFound("not found")
    env.client.volumes.create.return_value = MagicMock()
    env.client.containers.run.return_value = MagicMock()

    env._remove_worker_container = MagicMock(return_value=True)

    assert env.restore_snapshot(worker_id=0, snapshot_id="pbt-snapshot-test") is True
    env._remove_worker_container.assert_called_once_with(
        worker_id=0,
        purpose="snapshot restore",
        timeout=180,
    )


@pytest.mark.skip(reason="Legacy volume tests")
def test_rebuild_worker_instance_recreates_clean_slate_and_prepares_schema() -> None:
    """Clean-slate rebuild should recreate volume/container and prepare schema."""
    env = _make_environment()
    env.client.volumes.get.side_effect = docker.errors.NotFound("not found")
    env.client.containers.get.side_effect = docker.errors.NotFound("not found")
    env.client.containers.run.return_value = MagicMock()

    env._ensure_database_exists = MagicMock()
    env.schema_provider = MagicMock()
    env.schema_provider.prepare = MagicMock()
    env.schema_provider.validate.return_value = True

    assert env.rebuild_worker_instance(worker_id=0) is True

    env.client.volumes.create.assert_called_once_with(name="pbt-worker-0-pgdata")
    env.client.containers.run.assert_called_once()
    run_call = env.client.containers.run.call_args
    assert run_call.args[0] == "postgres:18"
    assert run_call.kwargs["volumes"] == {
        "pbt-worker-0-pgdata": {"bind": "/pgdata/data", "mode": "rw"}
    }
    env._wait_for_ready.assert_called_once_with(
        "pbt-worker-0",
        5440,
        timeout=180,
        context="clean-rebuild",
    )
    env._ensure_database_exists.assert_called_once()
    env.schema_provider.prepare.assert_called_once()


def test_create_snapshot_returns_empty_string_on_api_error() -> None:
    """Snapshot creation should degrade gracefully on Docker API failures."""
    env = _make_environment()
    container = MagicMock()
    container.commit.side_effect = docker.errors.APIError("commit failed")
    env.client.containers.get.return_value = container

    assert env.create_snapshot(worker_id=0) == ""


def test_container_name_uses_configured_prefix() -> None:
    """Container names should respect caller-selected environment prefixes."""
    env = _make_environment()
    env.container_prefix = "eval-worker"

    assert env._container_name(0) == "eval-worker-0"


def test_default_snapshot_id_is_profile_scoped() -> None:
    """Snapshot repository names should include run and profile signatures."""
    env = _make_environment()

    snapshot_id = env._default_snapshot_id()
    assert snapshot_id.startswith("pg-snapshot-baseline-")
    assert snapshot_id.endswith(env._snapshot_profile_signature())


def test_default_snapshot_id_changes_when_schema_profile_changes() -> None:
    """Different benchmark schema profiles must not share snapshot identities."""
    standard_env = _make_environment()
    standard_env.schema_provider = _SysbenchLikeSchemaProvider(
        tables=10, table_size=100000
    )

    rapid_env = _make_environment()
    rapid_env.schema_provider = _SysbenchLikeSchemaProvider(tables=2, table_size=10000)

    assert standard_env._default_snapshot_id() != rapid_env._default_snapshot_id()


def test_snapshot_exists_requires_matching_manifest_signature(tmp_path: Path) -> None:
    """Existing images are treated as stale when manifest profile metadata mismatches."""
    env = _make_environment()
    env.base_dir = tmp_path
    env.client.images.get.return_value = MagicMock()

    snapshot_id = env._default_snapshot_id()
    manifest_path = tmp_path / ".snapshots" / f"{snapshot_id}.json"
    env._snapshot_manifest_path = MagicMock(return_value=manifest_path)
    manifest_path.parent.mkdir(parents=True, exist_ok=True)
    manifest_path.write_text(
        json.dumps(
            {
                "snapshot_id": snapshot_id,
                "image_id": "sha256:stale",
                "profile_signature": "deadbeefdead",
            }
        ),
        encoding="utf-8",
    )

    assert env.snapshot_exists(worker_id=0) is False


@pytest.mark.skip(reason="Legacy volume tests")
def test_snapshot_exists_returns_true_with_matching_manifest_signature(
    tmp_path: Path,
) -> None:
    """Existing images are reusable only when manifest profile metadata matches."""
    env = _make_environment()
    env.base_dir = tmp_path
    env.client.images.get.return_value = MagicMock()

    snapshot_id = env._default_snapshot_id()
    manifest_path = tmp_path / ".snapshots" / f"{snapshot_id}.json"
    env._snapshot_manifest_path = MagicMock(return_value=manifest_path)
    env._write_snapshot_manifest(snapshot_id=snapshot_id, image_id="sha256:current")

    assert env.snapshot_exists(worker_id=0) is True


def test_setup_instances_uses_absolute_bind_paths_for_relative_base_dir() -> None:
    """Relative Docker data roots should still produce absolute bind mounts."""
    env = _make_environment()
    env.base_dir = Path(os.path.relpath(Path("/tmp/pbt-docker-root"), start=Path.cwd()))

    env.client.containers.get.side_effect = docker.errors.NotFound("missing")
    env.client.containers.run.return_value = MagicMock()

    env._wait_for_ready = MagicMock()
    env.initialize_schema = MagicMock()
    env.snapshot_exists = MagicMock(return_value=True)
    env.create_snapshot = MagicMock()

    env.setup_instances(num_workers=1, force_recreate=False, num_parallel_workers=1)

    worker_run_call = next(
        call
        for call in env.client.containers.run.call_args_list
        if call.kwargs.get("name") == "pbt-worker-0"
    )
    bind_source = next(iter(worker_run_call.kwargs["volumes"]))
    assert Path(bind_source).is_absolute()


def test_setup_instances_reuses_existing_snapshot_without_recommit() -> None:
    """When a baseline snapshot already exists, setup should skip create_snapshot."""
    env = _make_environment()
    env.container_prefix = "eval-worker"

    env.client.containers.get.side_effect = docker.errors.NotFound("missing")
    env.client.containers.run.return_value = MagicMock()

    env._wait_for_ready = MagicMock()
    env.initialize_schema = MagicMock()
    env.schema_provider = _DummySchemaProvider()
    env.snapshot_exists = MagicMock(return_value=True)
    env.create_snapshot = MagicMock()

    env.setup_instances(num_workers=1, force_recreate=False, num_parallel_workers=1)

    first_get_call = env.client.containers.get.call_args_list[0]
    assert first_get_call.args[0] == "eval-worker-0"
    env.create_snapshot.assert_not_called()


@pytest.mark.skip(reason="Legacy volume tests")
def test_setup_instances_recreates_worker0_when_baseline_snapshot_missing() -> None:
    """Worker 0 should not reuse an existing container when baseline snapshot is missing."""
    env = _make_environment()

    existing_container = MagicMock()
    existing_container.status = "running"
    env.client.containers.get.return_value = existing_container
    env.client.containers.run.return_value = MagicMock()

    env._wait_for_ready = MagicMock()
    env.initialize_schema = MagicMock()
    env.schema_provider = _DummySchemaProvider()
    env.snapshot_exists = MagicMock(return_value=False)
    env.create_snapshot = MagicMock()

    env.setup_instances(num_workers=1, force_recreate=False, num_parallel_workers=1)

    existing_container.remove.assert_called_once_with(force=True)
    env.client.containers.run.assert_called_once()
    env.create_snapshot.assert_called_once_with(worker_id=0)


def test_setup_instances_raises_when_baseline_snapshot_creation_fails() -> None:
    """Setup should fail immediately when the required baseline snapshot is missing."""
    env = _make_environment()

    env.client.containers.get.side_effect = docker.errors.NotFound("missing")
    env.client.containers.run.return_value = MagicMock()

    env._wait_for_ready = MagicMock()
    env.initialize_schema = MagicMock()
    env.schema_provider = _DummySchemaProvider()
    env.snapshot_exists = MagicMock(return_value=False)
    env.create_snapshot = MagicMock(return_value="")

    with pytest.raises(
        RuntimeError,
        match="Failed to create baseline Docker snapshot for worker 0",
    ):
        env.setup_instances(num_workers=1, force_recreate=False, num_parallel_workers=1)

    env.create_snapshot.assert_called_once_with(worker_id=0)


def test_setup_instances_force_recreate_baseline_removes_snapshot_once() -> None:
    """Forced baseline recreation should remove stale snapshots before worker setup."""
    env = _make_environment()
    env.force_recreate_baseline = True

    env.client.containers.get.side_effect = docker.errors.NotFound("missing")
    env.client.containers.run.return_value = MagicMock()

    env._wait_for_ready = MagicMock()
    env.initialize_schema = MagicMock()
    env.schema_provider = _DummySchemaProvider()
    env.snapshot_exists = MagicMock(return_value=True)
    env.create_snapshot = MagicMock()
    env._remove_baseline_snapshot = MagicMock()

    env.setup_instances(num_workers=1, force_recreate=False, num_parallel_workers=1)

    env._remove_baseline_snapshot.assert_called_once()


def test_setup_instances_tracks_worker_before_ready_wait() -> None:
    """Workers should be tracked even when readiness waiting fails."""
    env = _make_environment()

    env.client.containers.get.side_effect = docker.errors.NotFound("missing")
    env.client.containers.run.return_value = MagicMock()
    env._wait_for_ready = MagicMock(side_effect=RuntimeError("readiness failed"))

    with pytest.raises(RuntimeError, match="readiness failed"):
        env.setup_instances(num_workers=1, force_recreate=False, num_parallel_workers=1)

    assert 0 in env.instances
    assert env.instances[0].running is True
    assert env.instances[0].port == 5440


def test_stop_all_stops_untracked_running_prefixed_containers() -> None:
    """stop_all should stop running prefixed containers even when not tracked."""
    env = _make_environment()
    env.instances = {}

    managed = MagicMock()
    managed.name = "pbt-worker-9"
    other = MagicMock()
    other.name = "unrelated-service"
    env.client.containers.list.return_value = [managed, other]

    assert env.stop_all() is True

    managed.stop.assert_called_once_with(timeout=5)
    other.stop.assert_not_called()


def test_stop_instance_recovers_when_stop_timeout_but_container_exited() -> None:
    """Timeout during stop should be treated as success if container already exited."""
    env = _make_environment()

    container = MagicMock()
    container.status = "exited"
    container.stop.side_effect = requests.exceptions.ReadTimeout("timeout")
    env.client.containers.get.return_value = container

    assert env.stop_instance(worker_id=0) is True
    assert env.instances[0].running is False
    container.reload.assert_called_once()


def test_stop_instance_fails_when_stop_timeout_and_container_still_running() -> None:
    """Timeout during stop should return False when container stays running."""
    env = _make_environment()

    container = MagicMock()
    container.status = "running"
    container.stop.side_effect = requests.exceptions.ReadTimeout("timeout")
    env.client.containers.get.return_value = container

    assert env.stop_instance(worker_id=0) is False
    assert env.instances[0].running is True
    container.reload.assert_called_once()


def test_stop_all_recovers_from_stop_timeout_if_container_exited() -> None:
    """stop_all should tolerate stop timeout when container already exited."""
    env = _make_environment()
    env.instances = {}

    managed = MagicMock()
    managed.name = "pbt-worker-9"
    managed.status = "exited"
    managed.stop.side_effect = requests.exceptions.ReadTimeout("timeout")
    env.client.containers.list.return_value = [managed]

    assert env.stop_all() is True
    managed.reload.assert_called_once()


def test_start_instance_returns_false_on_read_timeout() -> None:
    """ReadTimeout from Docker SDK calls should not crash startup paths."""
    env = _make_environment()
    env.client.containers.get.side_effect = requests.exceptions.ReadTimeout("timeout")

    assert env.start_instance(worker_id=0) is False


def test_verify_instances_marks_worker_unhealthy_on_read_timeout() -> None:
    """Verification should downgrade timed-out workers to unhealthy instead of raising."""
    env = _make_environment()
    env.client.containers.get.side_effect = requests.exceptions.ReadTimeout("timeout")

    with pytest.raises(RuntimeError):
        env.verify_instances()

    # verify_instances marks the instance as not running before raising
    assert env.instances[0].running is False


@pytest.mark.skip(reason="Legacy volume tests")
def test_create_snapshot_writes_metadata_manifest(tmp_path: Path) -> None:
    """Snapshot creation should persist project-local metadata under pg_snapshots/."""
    env = _make_environment()
    env.base_dir = tmp_path

    container = MagicMock()
    snapshot_image = MagicMock()
    snapshot_image.id = "sha256:test-image-id"
    container.commit.return_value = snapshot_image
    env.client.containers.get.return_value = container

    expected_snapshot_id = env._default_snapshot_id()
    manifest_path = tmp_path / ".snapshots" / f"{expected_snapshot_id}.json"
    env._snapshot_manifest_path = MagicMock(return_value=manifest_path)

    snapshot_id = env.create_snapshot(worker_id=0)

    assert snapshot_id == expected_snapshot_id

    assert manifest_path.exists()

    manifest_data = json.loads(manifest_path.read_text(encoding="utf-8"))
    assert manifest_data["snapshot_id"] == expected_snapshot_id
    assert manifest_data["image_id"] == "sha256:test-image-id"
    assert manifest_data["profile_signature"] == env._snapshot_profile_signature()
    assert "provider" in manifest_data["profile_context"]
