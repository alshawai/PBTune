"""Fault-injection tests for evaluator benchmark failure and readiness repair paths."""

from __future__ import annotations

from unittest.mock import MagicMock, patch

import pytest

from src.config.database import DatabaseConfig
from src.tuner.core.worker import Worker
from src.tuner.evaluator.evaluator import Evaluator, EvaluatorConfig
from src.benchmarks.executor import BenchmarkExecutor
from src.utils.metrics import MetricConfig, PerformanceMetrics, WorkloadType


class _ClosedConnection:
    """Connection stub that appears closed to skip stats sampling."""

    closed = True

    def close(self) -> None:
        return


class _FailingBenchmarkExecutor(BenchmarkExecutor):
    """Benchmark executor that fails during execution."""

    def prepare(self, db_config: DatabaseConfig) -> None:
        return

    def validate(self, db_config: DatabaseConfig) -> bool:
        return True

    def execute(
        self,
        db_config: DatabaseConfig,
        worker_id: int | None = None,
        **kwargs,
    ) -> PerformanceMetrics:
        raise RuntimeError("injected benchmark failure")


class _InvalidBenchmarkExecutor(BenchmarkExecutor):
    """Benchmark executor that remains invalid even after prepare()."""

    def __init__(self) -> None:
        self.prepare_called = False

    def prepare(self, db_config: DatabaseConfig) -> None:
        self.prepare_called = True

    def validate(self, db_config: DatabaseConfig) -> bool:
        return False

    def execute(
        self,
        db_config: DatabaseConfig,
        worker_id: int | None = None,
        **kwargs,
    ) -> PerformanceMetrics:
        return PerformanceMetrics()


def _make_evaluator(executor: BenchmarkExecutor) -> Evaluator:
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
    )
    return Evaluator(config=config, workload_executor=executor, env=MagicMock())


def _make_worker() -> Worker:
    worker = Worker(worker_id=0, knob_space=MagicMock(), knob_config={})
    worker.db_config = DatabaseConfig(
        user="postgres",
        password="postgres",
        host="127.0.0.1",
        port=5440,
        dbname="test_dataset",
    )
    worker.port = 5440
    return worker


def test_evaluate_worker_raises_on_benchmark_execution_failure() -> None:
    """Injected benchmark execution failures should propagate as RuntimeError."""
    evaluator = _make_evaluator(_FailingBenchmarkExecutor())
    worker = _make_worker()

    with (
        patch.object(evaluator, "connect", return_value=_ClosedConnection()),
        patch.object(evaluator, "collect_system_metrics", return_value={}),
        patch.object(evaluator, "_vacuum_after_dml", return_value=None),
    ):
        with pytest.raises(RuntimeError, match="Workload execution failed"):
            evaluator.evaluate_worker(worker, apply_config=False, generation=3)


def test_ensure_benchmark_ready_raises_if_schema_still_invalid() -> None:
    """Benchmark readiness repair should fail fast when validate() never passes."""
    executor = _InvalidBenchmarkExecutor()
    evaluator = _make_evaluator(executor)

    with pytest.raises(RuntimeError, match="Benchmark validation still failing"):
        evaluator._ensure_benchmark_ready(
            db_config=DatabaseConfig(
                user="postgres",
                password="postgres",
                host="127.0.0.1",
                port=5440,
                dbname="test_dataset",
            ),
            worker_logger=MagicMock(),
        )

    assert executor.prepare_called is True
