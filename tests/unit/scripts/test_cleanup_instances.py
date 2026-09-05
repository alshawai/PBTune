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
    worker_0 = base_dir / "worker_0"
    worker_0.mkdir(parents=True)
    (worker_0 / "PG_VERSION").write_text("18", encoding="utf-8")
    worker_2_pgdata = base_dir / "worker_2" / "pgdata"
    worker_2_pgdata.mkdir(parents=True)
    (worker_2_pgdata / "PG_VERSION").write_text("18", encoding="utf-8")
    (base_dir / "worker_invalid").mkdir(parents=True)

    fake_manager = SimpleNamespace(instances={}, stop_all=MagicMock())

    args = Namespace(
        remove_data=False,
        remove_snapshots=False,
        data_dir=str(base_dir),
        force=False,
        docker_only=False,
    )

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
        patch("docker.from_env", side_effect=RuntimeError("Docker unavailable")),
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
    assert fake_manager.instances[0].data_dir == worker_0
    assert fake_manager.instances[2].data_dir == worker_2_pgdata
    rmtree_mock.assert_not_called()


def test_cleanup_dry_run_returns_zero_when_base_dir_missing(tmp_path: Path) -> None:
    """Cleanup should exit cleanly when no instance directory exists."""
    missing_dir = tmp_path / "does_not_exist"
    args = Namespace(
        remove_data=False,
        remove_snapshots=False,
        data_dir=str(missing_dir),
        force=False,
        docker_only=False,
    )

    with (
        patch(
            "src.scripts.cleanup_instances.argparse.ArgumentParser.parse_args",
            return_value=args,
        ),
        patch("docker.from_env", side_effect=RuntimeError("Docker unavailable")),
    ):
        result = cleanup_instances.main()

    assert result == 0


def test_docker_only_cleanup_does_not_invoke_bare_metal_manager(
    tmp_path: Path,
) -> None:
    """Docker PGDATA directories must never trigger a pg_ctl cleanup path."""
    base_dir = tmp_path / "instances"
    (base_dir / "run" / "worker_0" / "pgdata").mkdir(parents=True)
    container = MagicMock(name="pbt-worker-0")
    container.name = "pbt-worker-0"
    client = MagicMock()
    client.containers.list.return_value = [container]
    args = Namespace(
        remove_data=False,
        remove_snapshots=False,
        data_dir=str(base_dir),
        force=True,
        docker_only=True,
    )

    with (
        patch(
            "src.scripts.cleanup_instances.argparse.ArgumentParser.parse_args",
            return_value=args,
        ),
        patch("docker.from_env", return_value=client),
        patch(
            "src.scripts.cleanup_instances.EnvironmentFactory.create"
        ) as create_manager,
    ):
        result = cleanup_instances.main()

    assert result == 0
    container.stop.assert_called_once_with(timeout=5)
    container.remove.assert_called_once_with(force=True)
    create_manager.assert_not_called()


def test_docker_pgdata_is_not_treated_as_bare_metal_without_flag(
    tmp_path: Path,
) -> None:
    """Default cleanup must recognize Docker's nested PGDATA layout."""
    base_dir = tmp_path / "instances"
    docker_pgdata = base_dir / "run" / "worker_0" / "pgdata" / "data"
    docker_pgdata.mkdir(parents=True)
    (docker_pgdata / "PG_VERSION").write_text("18", encoding="utf-8")
    args = Namespace(
        remove_data=False,
        remove_snapshots=False,
        data_dir=str(base_dir),
        force=True,
        docker_only=False,
    )

    with (
        patch(
            "src.scripts.cleanup_instances.argparse.ArgumentParser.parse_args",
            return_value=args,
        ),
        patch("docker.from_env", side_effect=RuntimeError("Docker unavailable")),
        patch(
            "src.scripts.cleanup_instances.EnvironmentFactory.create"
        ) as create_manager,
    ):
        result = cleanup_instances.main()

    assert result == 0
    create_manager.assert_not_called()


def test_missing_pg_ctl_is_reported_without_traceback(tmp_path: Path) -> None:
    """A missing bare-metal pg_ctl executable should produce a clean failure."""
    base_dir = tmp_path / "instances"
    worker_dir = base_dir / "worker_0"
    worker_dir.mkdir(parents=True)
    (worker_dir / "PG_VERSION").write_text("18", encoding="utf-8")
    fake_manager = SimpleNamespace(
        instances={},
        stop_all=MagicMock(side_effect=FileNotFoundError(2, "missing", "pg_ctl")),
    )
    args = Namespace(
        remove_data=False,
        remove_snapshots=False,
        data_dir=str(base_dir),
        force=True,
        docker_only=False,
    )

    with (
        patch(
            "src.scripts.cleanup_instances.argparse.ArgumentParser.parse_args",
            return_value=args,
        ),
        patch("docker.from_env", side_effect=RuntimeError("Docker unavailable")),
        patch(
            "src.scripts.cleanup_instances.EnvironmentFactory.create",
            return_value=fake_manager,
        ),
    ):
        result = cleanup_instances.main()

    assert result == 1
