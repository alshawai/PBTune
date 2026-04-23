"""Unit tests for cleanup_instances utility runtime behavior."""

from __future__ import annotations

from argparse import Namespace
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import MagicMock, patch

from src.config.database import DatabaseConfig
from src.scripts import cleanup_instances


def test_cleanup_constructs_manager_and_stops_instances(tmp_path: Path) -> None:
    """Cleanup should build manager, register worker dirs, and stop all instances."""
    base_dir = tmp_path / "pg_instances"
    (base_dir / "worker_0").mkdir(parents=True)
    (base_dir / "worker_2").mkdir(parents=True)
    (base_dir / "worker_invalid").mkdir(parents=True)

    fake_manager = SimpleNamespace(instances={}, stop_all=MagicMock())

    args = Namespace(remove_data=False, base_dir=str(base_dir), force=False)

    with (
        patch(
            "src.scripts.cleanup_instances.argparse.ArgumentParser.parse_args",
            return_value=args,
        ),
        patch("src.scripts.cleanup_instances.DatabaseConfig.from_env") as from_env_mock,
        patch(
            "src.scripts.cleanup_instances.EnvironmentFactory.create",
            return_value=fake_manager,
        ) as create_mock,
        patch("src.scripts.cleanup_instances.shutil.rmtree") as rmtree_mock,
    ):
        from_env_mock.return_value = DatabaseConfig(
            user="postgres",
            password="postgres",
            host="127.0.0.1",
            port=5432,
            dbname="test_dataset",
        )

        result = cleanup_instances.main()

    assert result == 0
    assert create_mock.called
    fake_manager.stop_all.assert_called_once_with(mode="immediate")
    assert sorted(fake_manager.instances.keys()) == [0, 2]
    rmtree_mock.assert_not_called()


def test_cleanup_dry_run_returns_zero_when_base_dir_missing(tmp_path: Path) -> None:
    """Cleanup should exit cleanly when no instance directory exists."""
    missing_dir = tmp_path / "does_not_exist"
    args = Namespace(remove_data=False, base_dir=str(missing_dir), force=False)

    with patch(
        "src.scripts.cleanup_instances.argparse.ArgumentParser.parse_args",
        return_value=args,
    ):
        result = cleanup_instances.main()

    assert result == 0
