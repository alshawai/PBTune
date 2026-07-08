"""Tests for evaluator system metrics delegation to the environment."""

from __future__ import annotations

from unittest.mock import MagicMock

import pytest
import psycopg2

from src.config.database import DatabaseConfig
from src.tuner.benchmark.orchestrator import (
    WorkloadOrchestrator,
    WorkloadOrchestratorConfig,
)
from src.utils.metrics import MetricConfig, WorkloadType


def _make_workload_orchestrator(
    worker_memory_budget_bytes: int | None, mock_env: MagicMock | None = None
) -> WorkloadOrchestrator:
    db_config = DatabaseConfig(
        user="postgres",
        password="postgres",
        host="127.0.0.1",
        port=5440,
        dbname="test_dataset",
    )
    config = WorkloadOrchestratorConfig(
        workload_type=WorkloadType.OLTP,
        metric_config=MetricConfig.for_oltp(),
        db_config=db_config,
        worker_memory_budget_bytes=worker_memory_budget_bytes,
    )
    return WorkloadOrchestrator(
        config=config, workload_executor=MagicMock(), env=mock_env
    )


def test_collect_system_metrics_delegates_to_environment() -> None:
    """When env is set, evaluator should delegate memory + cache hit to it."""
    mock_env = MagicMock()
    mock_env.collect_memory_utilization.return_value = 0.5
    mock_env.collect_cache_hit_ratio.return_value = 0.87

    evaluator = _make_workload_orchestrator(
        worker_memory_budget_bytes=4 * 1024, mock_env=mock_env
    )

    metrics = evaluator.collect_system_metrics(worker_id=0)

    assert metrics["memory_utilization"] == pytest.approx(0.5)
    assert metrics["cache_hit_ratio"] == pytest.approx(0.87)
    mock_env.collect_memory_utilization.assert_called_once_with(0)
    mock_env.collect_cache_hit_ratio.assert_called_once_with(0)


def test_collect_system_metrics_needs_environment_delegation() -> None:
    """Evaluator should not perform SQL fallback when env is present."""
    mock_env = MagicMock()
    mock_env.collect_memory_utilization.return_value = 0.0
    mock_env.collect_cache_hit_ratio.return_value = 0.92

    evaluator = _make_workload_orchestrator(
        worker_memory_budget_bytes=None, mock_env=mock_env
    )

    metrics = evaluator.collect_system_metrics(worker_id=0)

    assert metrics["memory_utilization"] == pytest.approx(0.0)
    assert metrics["cache_hit_ratio"] == pytest.approx(0.92)


def test_pg_stat_database_snapshot_helper_retries_transient_connection_failure() -> (
    None
):
    """The stats snapshot helper should retry once before giving up."""
    evaluator = _make_workload_orchestrator(worker_memory_budget_bytes=4 * 1024)

    cursor = MagicMock()
    cursor.fetchone.return_value = (12, 34, 56, 78, 9, 10, 11)

    connection = MagicMock()
    connection.closed = False
    connection.cursor.return_value = cursor

    evaluator.connect = MagicMock(
        side_effect=[psycopg2.OperationalError("temporary failure"), connection]
    )
    evaluator.disconnect = MagicMock()

    snapshot_reader = evaluator._fetch_pg_stat_database_snapshot
    snapshot = snapshot_reader(evaluator.config.db_config)

    assert snapshot == (12, 34, 56, 78, 9, 10, 11)
    assert evaluator.connect.call_count == 2
    cursor.close.assert_called_once()
    evaluator.disconnect.assert_called_once_with(connection)
