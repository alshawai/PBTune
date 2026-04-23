"""Targeted tests for base environment error handling paths."""

# pylint: disable=protected-access

from __future__ import annotations

from unittest.mock import MagicMock, call, patch

import psycopg2

from src.config.database import DatabaseConfig
from src.utils.environments.base import DatabaseEnvironment


class _DummyEnvironment(DatabaseEnvironment):
    """Minimal concrete subclass for exercising base helper methods."""

    def setup_instances(self, num_workers: int, force_recreate: bool = False):
        return []

    def start_instance(self, worker_id: int) -> bool:
        return True

    def stop_instance(self, worker_id: int, mode: str = "fast") -> bool:
        return True

    def stop_all(self, mode: str = "fast") -> bool:
        return True

    def recover_instance(self, worker_id: int) -> bool:
        return True

    def restart_instance(self, worker_id: int) -> bool:
        return True

    def verify_instances(self) -> dict[int, bool]:
        return {}

    def cleanup(self, remove_data: bool = False) -> None:
        return None

    def create_snapshot(self, worker_id: int = 0) -> str:
        return ""

    def restore_snapshot(self, worker_id: int) -> bool:
        return False

    def get_db_config(self, worker_id: int) -> DatabaseConfig:
        return self.base_config

    def collect_memory_utilization(self, worker_id: int) -> float:
        return 0.0


class _SchemaProvider:
    """No-op schema provider stand-in."""

    def prepare(self, _db_config: DatabaseConfig) -> None:
        return None

    def validate(self, _db_config: DatabaseConfig) -> bool:
        return True


def _make_env() -> _DummyEnvironment:
    db_config = DatabaseConfig(
        user="postgres",
        password="postgres",
        host="127.0.0.1",
        port=5432,
        dbname="test_dataset",
    )
    return _DummyEnvironment(
        run_id="test-run",
        db_config=db_config,
        schema_provider=_SchemaProvider(),
    )


def test_ensure_database_exists_handles_connection_failure_without_name_error() -> None:
    """Operational errors should be logged and swallowed without secondary NameError."""
    db_config = DatabaseConfig(
        user="postgres",
        password="postgres",
        host="127.0.0.1",
        port=5432,
        dbname="test_dataset",
    )
    env = _DummyEnvironment(
        run_id="test-run",
        db_config=db_config,
        schema_provider=_SchemaProvider(),
    )

    with (
        patch(
            "src.utils.environments.base.get_connection",
            side_effect=psycopg2.OperationalError("connection failed"),
        ),
        patch("src.utils.environments.base.LOGGER.error") as logger_error,
    ):
        # Should not raise; function handles psycopg2.Error internally.
        env._ensure_database_exists(db_config)

    logger_error.assert_called_once()
    assert "Failed to ensure database" in logger_error.call_args[0][0]


def test_reset_statistics_uses_pg_stat_reset() -> None:
    """reset_statistics should call pg_stat_reset() on the worker database."""
    env = _make_env()
    cursor = MagicMock()
    cursor.fetchone.return_value = (None,)
    conn = MagicMock()
    conn.cursor.return_value = cursor

    with patch("src.utils.environments.base.get_connection", return_value=conn):
        assert env.reset_statistics(worker_id=0) is True

    cursor.execute.assert_called_once_with("SELECT pg_stat_reset()")
    conn.commit.assert_called_once()
    conn.close.assert_called_once()


def test_reset_persisted_configuration_restarts_when_pending_restart() -> None:
    """RESET ALL should trigger restart when PostgreSQL reports pending_restart entries."""
    env = _make_env()
    db_config = env.base_config

    cursor = MagicMock()
    cursor.fetchone.return_value = (1,)
    conn = MagicMock()
    conn.cursor.return_value = cursor

    env.stop_instance = MagicMock(return_value=True)
    env.start_instance = MagicMock(return_value=True)
    env._wait_until_connectable = MagicMock(return_value=True)

    with patch("src.utils.environments.base.get_connection", return_value=conn):
        env._reset_persisted_configuration(worker_id=0, config=db_config)

    cursor.execute.assert_has_calls(
        [
            call("ALTER SYSTEM RESET ALL"),
            call("SELECT pg_reload_conf()"),
            call("SELECT count(*) FROM pg_settings WHERE pending_restart"),
        ]
    )
    env.stop_instance.assert_called_once_with(0)
    env.start_instance.assert_called_once_with(0)
    env._wait_until_connectable.assert_called_once_with(db_config)


def test_reset_persisted_configuration_skips_restart_when_no_pending_changes() -> None:
    """RESET ALL should avoid restart when no pending_restart flags remain."""
    env = _make_env()
    db_config = env.base_config

    cursor = MagicMock()
    cursor.fetchone.return_value = (0,)
    conn = MagicMock()
    conn.cursor.return_value = cursor

    env.stop_instance = MagicMock(return_value=True)
    env.start_instance = MagicMock(return_value=True)
    env._wait_until_connectable = MagicMock(return_value=True)

    with patch("src.utils.environments.base.get_connection", return_value=conn):
        env._reset_persisted_configuration(worker_id=0, config=db_config)

    env.stop_instance.assert_not_called()
    env.start_instance.assert_not_called()
    env._wait_until_connectable.assert_not_called()


def test_initialize_schema_resets_after_snapshot_restore() -> None:
    """Schema initialization should reset persisted settings before and after snapshot restore."""
    env = _make_env()
    db_config = env.base_config

    schema_provider = MagicMock()
    schema_provider.validate.side_effect = [False, True]
    schema_provider.prepare = MagicMock()
    env.schema_provider = schema_provider

    env.restore_snapshot = MagicMock(return_value=True)
    env._ensure_database_exists = MagicMock()
    env._reset_persisted_configuration = MagicMock()

    env.initialize_schema(worker_id=0)

    env._reset_persisted_configuration.assert_has_calls(
        [call(0, db_config), call(0, db_config)]
    )
    assert env._reset_persisted_configuration.call_count == 2
    schema_provider.prepare.assert_not_called()
