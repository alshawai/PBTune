"""Tests for evaluator system metrics delegation to the environment."""

from __future__ import annotations

from unittest.mock import MagicMock

import pytest

from src.config.database import DatabaseConfig
from src.tuner.evaluator.evaluator import Evaluator, EvaluatorConfig
from src.utils.metrics import MetricConfig, WorkloadType


def _make_evaluator(
    worker_memory_budget_bytes: int | None, mock_env: MagicMock | None = None
) -> Evaluator:
    db_config = DatabaseConfig(
        user="postgres",
        password="postgres",
        host="127.0.0.1",
        port=5440,
        dbname="test_dataset",
    )
    config = EvaluatorConfig(
        workload_type=WorkloadType.OLTP,
        metric_config=MetricConfig.for_oltp(),
        db_config=db_config,
        worker_memory_budget_bytes=worker_memory_budget_bytes,
    )
    return Evaluator(config=config, workload_executor=MagicMock(), env=mock_env)


def test_collect_system_metrics_delegates_to_environment() -> None:
    """When env is set, evaluator should delegate memory + cache hit to it."""
    mock_env = MagicMock()
    mock_env.collect_memory_utilization.return_value = 0.5
    mock_env.collect_cache_hit_ratio.return_value = 0.87

    evaluator = _make_evaluator(worker_memory_budget_bytes=4 * 1024, mock_env=mock_env)

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

    evaluator = _make_evaluator(worker_memory_budget_bytes=None, mock_env=mock_env)

    metrics = evaluator.collect_system_metrics(worker_id=0)

    assert metrics["memory_utilization"] == pytest.approx(0.0)
    assert metrics["cache_hit_ratio"] == pytest.approx(0.92)
