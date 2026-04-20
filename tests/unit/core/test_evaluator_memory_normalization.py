"""Tests for worker-scoped memory normalization in evaluator system metrics."""

from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import MagicMock, patch

import pytest

from src.config.database import DatabaseConfig
from src.tuner.evaluator.evaluator import Evaluator, EvaluatorConfig
from src.utils.metrics import MetricConfig, WorkloadType


class _StubCursor:
    """Cursor stub returning a fixed cache-hit ratio."""

    def __init__(self, cache_hit_ratio: float) -> None:
        self._cache_hit_ratio = cache_hit_ratio

    def execute(self, _query: str) -> None:
        return None

    def fetchone(self) -> tuple[float]:
        return (self._cache_hit_ratio,)

    def close(self) -> None:
        return None


class _StubConnection:
    """Connection stub used by collect_system_metrics."""

    closed = False

    def __init__(self, cache_hit_ratio: float = 0.0) -> None:
        self._cache_hit_ratio = cache_hit_ratio

    def cursor(self) -> _StubCursor:
        return _StubCursor(cache_hit_ratio=self._cache_hit_ratio)


class _StubProcess:
    """psutil.Process stand-in exposing only memory_info()."""

    def __init__(self, rss_bytes: int) -> None:
        self._rss_bytes = rss_bytes

    def memory_info(self) -> SimpleNamespace:
        return SimpleNamespace(rss=self._rss_bytes)


def _make_evaluator(worker_memory_budget_bytes: int | None) -> Evaluator:
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
    return Evaluator(config=config, workload_executor=MagicMock())


def test_collect_system_metrics_uses_worker_memory_budget_denominator() -> None:
    """When configured, evaluator should normalize memory by worker RAM budget."""
    evaluator = _make_evaluator(worker_memory_budget_bytes=4 * 1024)
    connection = _StubConnection(cache_hit_ratio=0.87)

    postgres_processes = [_StubProcess(1024), _StubProcess(1024)]
    with (
        patch.object(evaluator, "_get_postmaster_pid", return_value=999),
        patch.object(evaluator, "_get_all_postgres_processes", return_value=postgres_processes),
        patch(
            "src.tuner.evaluator.evaluator.psutil.virtual_memory",
            return_value=SimpleNamespace(total=8 * 1024),
        ),
    ):
        metrics = evaluator.collect_system_metrics(connection, port=5440, worker_id=0)

    assert metrics["memory_utilization"] == pytest.approx(0.5)
    assert metrics["cache_hit_ratio"] == pytest.approx(0.87)


def test_collect_system_metrics_falls_back_to_host_denominator_without_budget() -> None:
    """Without worker budget, evaluator should preserve host-total fallback behavior."""
    evaluator = _make_evaluator(worker_memory_budget_bytes=None)
    connection = _StubConnection(cache_hit_ratio=0.92)

    postgres_processes = [_StubProcess(1024), _StubProcess(1024)]
    with (
        patch.object(evaluator, "_get_postmaster_pid", return_value=999),
        patch.object(evaluator, "_get_all_postgres_processes", return_value=postgres_processes),
        patch(
            "src.tuner.evaluator.evaluator.psutil.virtual_memory",
            return_value=SimpleNamespace(total=8 * 1024),
        ),
    ):
        metrics = evaluator.collect_system_metrics(connection, port=5440, worker_id=0)

    assert metrics["memory_utilization"] == pytest.approx(0.25)
    assert metrics["cache_hit_ratio"] == pytest.approx(0.92)