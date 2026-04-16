"""Unit tests for restart manager recovery and error-context behavior."""

from __future__ import annotations

import json
from pathlib import Path
from unittest.mock import patch

import psycopg2

from src.config.database import DatabaseConfig
from src.utils.restart_manager import PostgresRestartManager, RestartConfig


def _make_manager() -> PostgresRestartManager:
    """Build a restart manager without triggering Docker initialization."""
    db_config = DatabaseConfig(
        user="postgres",
        password="postgres",
        host="127.0.0.1",
        port=5440,
        dbname="test_dataset",
    )
    restart_config = RestartConfig(
        method="pg_ctl",
        data_dir="/tmp",
        rollback_on_failure=True,
        backup_enabled=False,
        run_id="worker-2-gen-5",
        benchmark_type="oltp",
    )
    return PostgresRestartManager(
        db_config=db_config,
        restart_config=restart_config,
        worker_id=2,
    )


def test_restart_rolls_back_when_restart_command_fails() -> None:
    """A failed restart command should trigger restore when rollback is enabled."""
    manager = _make_manager()
    backup_path = Path("/tmp/postgresql.auto.conf.backup")

    with (
        patch.object(manager, "backup_config", return_value=backup_path),
        patch.object(manager, "_restart_pg_ctl", return_value=False),
        patch.object(manager, "restore_config", return_value=True) as restore_mock,
    ):
        assert manager.restart() is False
        restore_mock.assert_called_once_with(backup_path)


def test_restart_rolls_back_when_connection_never_recovers() -> None:
    """A post-restart connection failure should trigger rollback restore."""
    manager = _make_manager()
    backup_path = Path("/tmp/postgresql.auto.conf.backup")

    with (
        patch.object(manager, "backup_config", return_value=backup_path),
        patch.object(manager, "_restart_pg_ctl", return_value=True),
        patch.object(manager, "_wait_for_connection", return_value=False),
        patch.object(manager, "restore_config", return_value=True) as restore_mock,
    ):
        assert manager.restart() is False
        restore_mock.assert_called_once_with(backup_path)


def test_wait_for_connection_fails_fast_on_startup_fatal() -> None:
    """Startup-fatal log detection should short-circuit connection retries."""
    manager = _make_manager()

    with (
        patch(
            "src.utils.restart_manager.get_connection",
            side_effect=psycopg2.OperationalError("database system is starting up"),
        ),
        patch.object(manager, "_read_startup_fatal_error", return_value="FATAL: invalid setting"),
    ):
        assert manager._wait_for_connection() is False


def test_context_payload_contains_required_fields() -> None:
    """Structured payload should include worker, benchmark, run, method and phase."""
    manager = _make_manager()

    payload = json.loads(manager._context_payload("restart-command", attempt=3))

    assert payload["worker_id"] == 2
    assert payload["benchmark"] == "oltp"
    assert payload["run_id"] == "worker-2-gen-5"
    assert payload["method"] == "pg_ctl"
    assert payload["phase"] == "restart-command"
    assert payload["attempt"] == 3
