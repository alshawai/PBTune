"""Regression tests for dead-worker rescue convergence and restart guards."""

from __future__ import annotations

from unittest.mock import MagicMock, patch

import pytest

from src.config.database import DatabaseConfig
from src.tuner.core.population import Population, PopulationConfig
from src.tuner.core.worker import Worker
from src.tuner.evaluator.evaluator import Evaluator, EvaluatorConfig
from src.benchmarks.executor import BenchmarkExecutor
from src.utils.applicator import ApplicationResult
from src.utils.metrics import MetricConfig, PerformanceMetrics, WorkloadType


class _ClosedConnection:
    """Connection stub that appears closed to skip stats sampling."""

    closed = True

    def close(self) -> None:
        return


class _HealthyBenchmarkExecutor(BenchmarkExecutor):
    """Benchmark executor stub that always returns valid metrics."""

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
        return PerformanceMetrics(
            latency_p95=10.0,
            throughput=1000.0,
            total_queries=100,
            total_time=1.0,
        )


def _make_evaluator(executor: BenchmarkExecutor) -> Evaluator:
    mock_env = MagicMock()

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
    return Evaluator(config=config, workload_executor=executor, env=mock_env)


def _make_worker() -> Worker:
    worker = Worker(
        worker_id=0, knob_space=MagicMock(), knob_config={"shared_buffers": "256MB"}
    )
    worker.db_config = DatabaseConfig(
        user="postgres",
        password="postgres",
        host="127.0.0.1",
        port=5440,
        dbname="test_dataset",
    )
    worker.port = 5440
    return worker


def test_record_generation_not_converged_after_all_dead_resample() -> None:
    """All-dead rescue/resample generations should never be marked as converged."""
    knob_space = MagicMock()
    knob_space.sample_diverse_configs.return_value = [
        {"shared_buffers": "64MB"},
        {"shared_buffers": "96MB"},
        {"shared_buffers": "128MB"},
        {"shared_buffers": "160MB"},
        {"shared_buffers": "192MB"},
    ]
    knob_space.sample_random_config.return_value = {"shared_buffers": "224MB"}
    knob_space.perturb_config.return_value = {"shared_buffers": "256MB"}

    population = Population(
        knob_space=knob_space,
        config=PopulationConfig(population_size=3, dead_config_threshold=1.0),
    )

    population.workers = [
        Worker(
            worker_id=idx,
            knob_space=MagicMock(),
            knob_config={"shared_buffers": "32MB"},
        )
        for idx in range(3)
    ]

    for worker in population.workers:
        worker.metrics = PerformanceMetrics(failure_type="crash_dead")
        worker.performance_score = -1.0

    population.env = MagicMock()
    population.env.recover_instance.return_value = True

    rescued = population.rescue_dead_workers(lambda _w: (PerformanceMetrics(), 0.0))

    assert rescued == 3
    assert all(worker.force_restart_next_eval for worker in population.workers)
    assert all(worker.metrics is None for worker in population.workers)

    population._ranges_updated = True
    result = population.record_generation()

    assert result.converged is False
    assert population.should_stop() is False


def test_apply_configuration_force_restart_overrides_interval_deferral() -> None:
    """Forced restart must execute even when restart interval would defer it."""
    evaluator = _make_evaluator(_HealthyBenchmarkExecutor())
    connection = MagicMock()
    knob_applicator = MagicMock()
    knob_applicator.apply.return_value = ApplicationResult(
        success=True,
        applied_count=1,
        restart_required={"shared_buffers"},
    )

    with patch.object(evaluator, "_perform_restart", return_value=True) as restart_mock:
        restart_occurred = evaluator.apply_configuration(
            connection=connection,
            knob_config={"shared_buffers": "256MB"},
            knob_applicator=knob_applicator,
            force_restart=True,
            generation=3,
            worker_id=0,
        )

    assert restart_occurred is True
    restart_mock.assert_called_once()


def test_evaluate_worker_consumes_force_restart_marker() -> None:
    """Evaluator should forward and clear force-restart marker after successful restart."""
    evaluator = _make_evaluator(_HealthyBenchmarkExecutor())
    worker = _make_worker()
    worker.force_restart_next_eval = True

    with (
        patch.object(evaluator, "connect", return_value=_ClosedConnection()),
        patch.object(evaluator, "apply_configuration", return_value=True) as apply_mock,
        patch.object(
            evaluator,
            "collect_system_metrics",
            return_value={"cache_hit_ratio": 0.0, "memory_utilization": 0.0},
        ),
        patch.object(evaluator, "_vacuum_after_dml", return_value=None),
    ):
        _metrics, _score, restart_occurred = evaluator.evaluate_worker(
            worker,
            apply_config=True,
            generation=4,
        )

    assert restart_occurred is True
    assert apply_mock.call_args.kwargs["force_restart"] is True
    assert worker.force_restart_next_eval is False


def test_train_generation_rebuilds_worker_when_snapshot_restore_fails() -> None:
    """Generation should attempt clean-slate rebuild when snapshot restore fails."""
    knob_space = MagicMock()
    population = Population(
        knob_space=knob_space,
        config=PopulationConfig(population_size=2),
    )
    population.workers = [
        Worker(worker_id=0, knob_space=MagicMock()),
        Worker(worker_id=1, knob_space=MagicMock()),
    ]
    population.enable_snapshots = True
    population.restore_interval = 5
    population.current_generation = 5
    population.env = MagicMock()
    population.env.restore_snapshot.side_effect = [True, False]
    population.env.rebuild_worker_instance.return_value = True
    population.evaluate_generation = MagicMock()
    population.rescue_dead_workers = MagicMock(return_value=0)
    population.update_metric_ranges_if_needed = MagicMock()
    population._finalize_scores = MagicMock()
    population.exploit_and_explore = MagicMock(return_value=0)
    population.record_generation = MagicMock(
        return_value=MagicMock(
            generation=5,
            best_score=0.0,
            mean_score=0.0,
            std_score=0.0,
            converged=False,
            num_exploited=0,
        )
    )

    population.train_generation(
        lambda _w: (PerformanceMetrics(), 0.0),
        parallel=False,
    )

    population.env.rebuild_worker_instance.assert_called_once_with(1)
    population.evaluate_generation.assert_called_once()


def test_train_generation_raises_when_snapshot_restore_and_rebuild_fail() -> None:
    """Generation should abort only if both restore and rebuild fail for a worker."""
    knob_space = MagicMock()
    population = Population(
        knob_space=knob_space,
        config=PopulationConfig(population_size=2),
    )
    population.workers = [
        Worker(worker_id=0, knob_space=MagicMock()),
        Worker(worker_id=1, knob_space=MagicMock()),
    ]
    population.enable_snapshots = True
    population.restore_interval = 5
    population.current_generation = 5
    population.env = MagicMock()
    population.env.restore_snapshot.side_effect = [True, False]
    population.env.rebuild_worker_instance.return_value = False
    population.evaluate_generation = MagicMock()

    with pytest.raises(RuntimeError, match="Snapshot restore recovery failed"):
        population.train_generation(
            lambda _w: (PerformanceMetrics(), 0.0),
            parallel=False,
        )

    population.evaluate_generation.assert_not_called()


def test_saturation_detection_expands_ranges_for_high_latency_low_throughput() -> None:
    """Out-of-bounds in the opposite direction should also trigger expansion."""
    knob_space = MagicMock()
    population = Population(
        knob_space=knob_space,
        config=PopulationConfig(population_size=2, dead_config_threshold=6.0),
    )

    metric_config = MetricConfig.for_oltp()
    metric_config.latency_min = 80.0
    metric_config.latency_max = 160.0
    metric_config.throughput_min = 100.0
    metric_config.throughput_max = 200.0

    population.evaluator = MagicMock()
    population.evaluator.config.metric_config = metric_config
    population._ranges_updated = True
    population.current_generation = 5

    workers = []
    worker_points = [(240.0, 70.0, 0.12), (230.0, 75.0, 0.13)]
    for worker_id, (latency, throughput, memory) in enumerate(worker_points):
        worker = Worker(worker_id=worker_id, knob_space=MagicMock(), knob_config={})
        worker.metrics = PerformanceMetrics(
            latency_p95=latency,
            throughput=throughput,
            memory_utilization=memory,
            error_rate=0.0,
        )
        worker.performance_score = 9.0 + worker_id
        workers.append(worker)

    population.workers = workers
    population.best_overall_metrics = workers[0].metrics
    population.best_overall_score = workers[0].performance_score

    old_latency_max = metric_config.latency_max
    old_throughput_min = metric_config.throughput_min

    saturation_check = population._finalize_scores
    saturation_check()

    assert metric_config.latency_max > old_latency_max
    assert metric_config.throughput_min < old_throughput_min
