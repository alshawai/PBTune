"""Unit tests for Docker environment snapshot recovery failure branches."""

from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace
from unittest.mock import MagicMock

import docker

from src.config.database import DatabaseConfig
from src.utils.environments.base import InstanceConfig
from src.utils.environments.docker import DockerEnvironment


class _DummySchemaProvider:
    """Minimal schema provider stand-in used for context payload generation."""



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
    env.image_name = "postgres:17"
    env.network_name = "pbt-network"
    env.base_port = 5440
    env.base_dir = Path("/tmp")
    env.container_prefix = "pbt-worker"
    env.instances = {
        0: InstanceConfig(worker_id=0, port=5440, data_dir=Path("/tmp/worker_0"), running=True)
    }
    env._snapshot_timeout = 120
    env._wait_for_ready = MagicMock()

    env.client = MagicMock()
    env.client.api = SimpleNamespace(timeout=30)

    return env


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

    env.setup_instances(num_workers=1, force_recreate=False)

    first_get_call = env.client.containers.get.call_args_list[0]
    assert first_get_call.args[0] == "eval-worker-0"
    env.create_snapshot.assert_not_called()
