"""Targeted tests for base environment error handling paths."""

from __future__ import annotations

from unittest.mock import patch

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

    def verify_instances(self) -> dict[int, bool]:
        return {}

    def cleanup(self, remove_data: bool = False) -> None:
        return None

    def apply_knobs(self, worker_id: int, knobs):
        return None

    def create_snapshot(self, worker_id: int = 0) -> str:
        return ""

    def restore_snapshot(self, worker_id: int) -> bool:
        return False

    def get_db_config(self, worker_id: int) -> DatabaseConfig:
        return self.base_config


class _SchemaProvider:
    """No-op schema provider stand-in."""

    def prepare(self, db_config: DatabaseConfig) -> None:
        return None

    def validate(self, db_config: DatabaseConfig) -> bool:
        return True



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
