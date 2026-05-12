"""Fault-injection tests for evaluator benchmark failure and readiness repair paths."""

from __future__ import annotations

from unittest.mock import MagicMock, patch

import pytest

from src.config.database import DatabaseConfig
from src.tuner.core.worker import Worker
from src.tuner.benchmark.orchestrator import (
    WorkloadOrchestrator,
    WorkloadOrchestratorConfig,
)
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


def _make_evaluator(executor: BenchmarkExecutor) -> WorkloadOrchestrator:
    mock_env = MagicMock()

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
    )
    return WorkloadOrchestrator(config=config, workload_executor=executor, env=mock_env)


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
        metrics, score, _ = evaluator.evaluate_worker(
            worker, apply_config=False, generation=3
        )
        assert score == 0.0
        assert metrics.failure_type == "EXECUTION_CRASH"


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


# ------------------------------------------------------------------
# Reliability gate unit tests
# ------------------------------------------------------------------


class TestReliabilityGate:
    """Direct tests for _apply_reliability_gate failure classification."""

    @pytest.fixture()
    def evaluator(self) -> WorkloadOrchestrator:
        return _make_evaluator(_FailingBenchmarkExecutor())

    @pytest.fixture()
    def logger(self) -> MagicMock:
        return MagicMock()

    def test_healthy_evaluation_no_failure_type(self, evaluator, logger):
        """Normal metrics should not be classified as a failure."""
        metrics = PerformanceMetrics(throughput=100.0, error_rate=0.01)
        evaluator._apply_reliability_gate(metrics, logger)
        assert metrics.failure_type is None

    def test_high_error_rate(self, evaluator, logger):
        """Error rate >= 50% should be classified as HIGH_ERROR_RATE."""
        metrics = PerformanceMetrics(throughput=50.0, error_rate=0.50)
        evaluator._apply_reliability_gate(metrics, logger)
        assert metrics.failure_type == "HIGH_ERROR_RATE"

    def test_high_error_rate_above_threshold(self, evaluator, logger):
        """Error rate well above 50% should still be HIGH_ERROR_RATE."""
        metrics = PerformanceMetrics(throughput=10.0, error_rate=0.95)
        evaluator._apply_reliability_gate(metrics, logger)
        assert metrics.failure_type == "HIGH_ERROR_RATE"

    def test_near_zero_throughput(self, evaluator, logger):
        """Throughput <= 0.1 TPS should be classified as NEAR_ZERO_THROUGHPUT."""
        metrics = PerformanceMetrics(throughput=0.05, error_rate=0.0)
        evaluator._apply_reliability_gate(metrics, logger)
        assert metrics.failure_type == "NEAR_ZERO_THROUGHPUT"

    def test_zero_throughput(self, evaluator, logger):
        """Zero throughput should be NEAR_ZERO_THROUGHPUT."""
        metrics = PerformanceMetrics(throughput=0.0, error_rate=0.0)
        evaluator._apply_reliability_gate(metrics, logger)
        assert metrics.failure_type == "NEAR_ZERO_THROUGHPUT"

    def test_degraded_error_rate(self, evaluator, logger):
        """Error rate between 10% and 50% should be classified as DEGRADED."""
        metrics = PerformanceMetrics(throughput=50.0, error_rate=0.25)
        evaluator._apply_reliability_gate(metrics, logger)
        assert metrics.failure_type == "DEGRADED"

    def test_degraded_at_threshold(self, evaluator, logger):
        """Error rate exactly at 10% should be DEGRADED."""
        metrics = PerformanceMetrics(throughput=50.0, error_rate=0.10)
        evaluator._apply_reliability_gate(metrics, logger)
        assert metrics.failure_type == "DEGRADED"

    def test_just_below_degraded_threshold(self, evaluator, logger):
        """Error rate just below 10% should remain healthy."""
        metrics = PerformanceMetrics(throughput=50.0, error_rate=0.09)
        evaluator._apply_reliability_gate(metrics, logger)
        assert metrics.failure_type is None

    def test_high_error_rate_takes_priority_over_near_zero_throughput(
        self, evaluator, logger
    ):
        """When both error rate and throughput are bad, HIGH_ERROR_RATE wins."""
        metrics = PerformanceMetrics(throughput=0.01, error_rate=0.80)
        evaluator._apply_reliability_gate(metrics, logger)
        assert metrics.failure_type == "HIGH_ERROR_RATE"

    def test_near_zero_throughput_takes_priority_over_degraded(self, evaluator, logger):
        """When throughput is near-zero and error rate is moderate, throughput wins."""
        metrics = PerformanceMetrics(throughput=0.05, error_rate=0.15)
        evaluator._apply_reliability_gate(metrics, logger)
        assert metrics.failure_type == "NEAR_ZERO_THROUGHPUT"

    def test_does_not_overwrite_existing_failure_type(self, evaluator, logger):
        """Pre-existing failure_type (e.g. EXECUTION_CRASH) should not be overwritten."""
        metrics = PerformanceMetrics(
            throughput=0.0, error_rate=1.0, failure_type="EXECUTION_CRASH"
        )
        evaluator._apply_reliability_gate(metrics, logger)
        assert metrics.failure_type == "EXECUTION_CRASH"
