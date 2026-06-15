"""Regression tests for dead-worker rescue convergence and restart guards."""

from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import MagicMock, patch

from src.config.database import DatabaseConfig
from src.tuner.core.evolution import truncation_selection
from src.tuner.core.population import Population, PopulationConfig
from src.tuner.core.worker import Worker
from src.tuner.benchmark.orchestrator import (
    WorkloadOrchestrator,
    WorkloadOrchestratorConfig,
)
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

    rescued = population.rescue_dead_workers()

    assert rescued == 3
    assert all(worker.force_restart_next_eval for worker in population.workers)
    assert all(worker.metrics is None for worker in population.workers)

    population._ranges_calibrated = True
    result = population.record_generation()

    assert result.converged is False
    assert population.should_stop() is False


def test_should_stop_ignores_no_improvement_when_disabled() -> None:
    """No-improvement patience should be ignored when explicitly disabled."""
    population = Population(
        knob_space=MagicMock(),
        config=PopulationConfig(
            population_size=2,
            early_stopping_patience=10,
            disable_early_stopping=True,
        ),
    )
    population.current_generation = 3
    population.generations_without_improvement = 10

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
    worker = _make_worker()

    with patch.object(evaluator, "_perform_restart", return_value=True) as restart_mock:
        restart_occurred = evaluator.apply_configuration(
            connection=connection,
            worker=worker,
            knob_applicator=knob_applicator,
            force_restart=True,
            generation=3,
        )

    assert restart_occurred is True
    restart_mock.assert_called_once()


def test_evaluate_worker_consumes_force_restart_marker() -> None:
    """WorkloadOrchestrator should forward and clear force-restart marker after successful restart."""
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
        _metrics, _score, restart_occurred, _db_config, _timing = evaluator.evaluate_worker(
            worker,
            apply_config=True,
            generation=4,
        )

    assert restart_occurred is True
    assert apply_mock.call_args.kwargs["force_restart"] is True
    assert worker.force_restart_next_eval is False


def test_train_generation_sets_restore_due_flag_when_interval_matches() -> None:
    """train_generation should set _restore_due_this_gen when on restore interval."""
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
    population.evaluate_generation = MagicMock()
    population.rescue_dead_workers = MagicMock(return_value=0)
    population.update_metric_ranges_if_needed = MagicMock()
    population._finalize_scores = MagicMock()
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

    assert population._restore_due_this_gen is True
    population.evaluate_generation.assert_called_once()


def test_train_generation_clears_restore_due_flag_off_interval() -> None:
    """train_generation should NOT set _restore_due_this_gen off-interval."""
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
    population.current_generation = 3
    population.env = MagicMock()
    population.evaluate_generation = MagicMock()
    population.rescue_dead_workers = MagicMock(return_value=0)
    population.update_metric_ranges_if_needed = MagicMock()
    population._finalize_scores = MagicMock()
    population.record_generation = MagicMock(
        return_value=MagicMock(
            generation=3,
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

    assert population._restore_due_this_gen is False
    population.evaluate_generation.assert_called_once()


def test_train_generation_logs_historical_best_worker_metrics_table() -> None:
    """Generation logging should use the finalized historical best worker."""
    population = Population(
        knob_space=MagicMock(),
        config=PopulationConfig(population_size=2),
    )
    population.current_generation = 7
    population.best_overall_metrics = PerformanceMetrics(
        latency_p95=5.0,
        throughput=250.0,
        total_queries=10,
        total_time=1.0,
    )
    population.best_overall_score = 99.1234
    population.best_overall_config = {"shared_buffers": "256MB"}
    population.orchestrator = MagicMock()
    population.orchestrator.refine_workload_features_from_generation.return_value = True
    population.workers = [
        Worker(worker_id=0, knob_space=MagicMock(), knob_config={}),
        Worker(worker_id=1, knob_space=MagicMock(), knob_config={}),
    ]

    metrics = PerformanceMetrics(
        latency_p95=12.0,
        throughput=100.0,
        total_queries=10,
        total_time=1.0,
    )
    for worker in population.workers:
        worker.metrics = metrics
        worker.performance_score = 80.0 + worker.worker_id
    call_order: list[str] = []

    def _evaluate(worker: Worker) -> tuple[PerformanceMetrics, float]:
        return metrics, 80.0 + worker.worker_id

    population.evaluate_generation = MagicMock()
    population.rescue_dead_workers = MagicMock(return_value=0)
    population.update_metric_ranges_if_needed = MagicMock()
    population._finalize_scores = MagicMock(
        side_effect=lambda: call_order.append("finalize")
    )
    population.record_generation = MagicMock(return_value=MagicMock(num_exploited=0))

    with patch("src.tuner.core.population.log_worker_metrics_table") as log_table:
        with patch(
            "src.tuner.core.population.execute_exploit_explore", return_value=[]
        ):
            population.train_generation(
                _evaluate,
                parallel=False,
                max_workers=1,
            )

    assert call_order == ["finalize"]
    assert log_table.call_count == 1
    args, kwargs = log_table.call_args
    assert len(args[1]) == 2
    # The title may include ANSI color/glyph decorations; assert the
    # essential substring is present instead of exact equality.
    assert "Generation 7 Worker Metrics" in kwargs["title"]
    assert kwargs["best_worker_label"] == "Best Worker"
    assert kwargs["best_worker_metric"] is not None
    assert kwargs["best_worker_metric"]["score"] == 99.1234
    assert kwargs["best_worker_metric"]["latency_p95"] == "5.00ms"


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

    population.orchestrator = MagicMock()
    population.orchestrator.config.metric_config = metric_config
    population._ranges_calibrated = True
    population.current_generation = 5

    # Provide a mock scoring engine so rescoring uses a deterministic final_score
    mock_engine = MagicMock()
    mock_engine.compute_breakdown = lambda m, worker_logger=None: SimpleNamespace(
        final_score=float(getattr(m, "throughput", 0.0) / 10.0)
    )
    population.orchestrator.scorer = mock_engine

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

    # Get the old anchor values from the normalizer
    lat_metric = f"latency_{metric_config.latency_metric}"
    if metric_config._normalizer and lat_metric in metric_config._normalizer.anchors:
        _, old_lat_low, old_lat_high = metric_config._normalizer.anchors[lat_metric]
    else:
        old_lat_low, old_lat_high = None, None

    if metric_config._normalizer and "throughput" in metric_config._normalizer.anchors:
        _, old_thr_low, old_thr_high = metric_config._normalizer.anchors["throughput"]
    else:
        old_thr_low, old_thr_high = None, None

    saturation_check = population._finalize_scores
    saturation_check()

    # Check that the normalizer anchors were expanded
    if old_lat_high is not None:
        _, new_lat_low, new_lat_high = metric_config._normalizer.anchors.get(
            lat_metric, (1, old_lat_low, old_lat_high)
        )
        assert new_lat_high > old_lat_high

    if old_thr_low is not None:
        _, new_thr_low, new_thr_high = metric_config._normalizer.anchors.get(
            "throughput", (1, old_thr_low, old_thr_high)
        )
        assert new_thr_low < old_thr_low


def test_truncation_selection_rescues_dead_workers_before_ready_interval() -> None:
    """Dead workers must enter the rescue pool even when no worker is ready.

    Regression: previously ``truncation_selection`` returned ``[]`` whenever
    fewer than two workers had ``step_count >= ready_interval``, which
    silently skipped dead-worker rescue during the warm-up window of
    presets like ``thorough`` (ready_interval=3).
    """
    workers = [
        Worker(
            worker_id=idx,
            knob_space=MagicMock(),
            knob_config={"shared_buffers": "256MB"},
            ready_interval=3,
        )
        for idx in range(8)
    ]

    # Every worker has only 1 evaluation under its belt — none is "ready".
    for worker in workers:
        worker.step_count = 1

    # Two workers crashed (score below the dead threshold); six are alive.
    workers[4].performance_score = 0.0
    workers[5].performance_score = 0.0
    for idx in (0, 1, 2, 3, 6, 7):
        workers[idx].performance_score = 80.0 + idx

    pairs = truncation_selection(
        workers,
        exploit_quantile=0.2,
        require_ready=True,
        dead_config_threshold=6.0,
    )

    poor_ids = {workers[poor_idx].worker_id for poor_idx, _ in pairs}
    elite_ids = {workers[elite_idx].worker_id for _, elite_idx in pairs}

    # Both dead workers must be paired for rescue.
    assert {4, 5}.issubset(poor_ids)
    # Elites must come from the alive pool, not from dead workers.
    assert elite_ids.isdisjoint({4, 5})
    # No worker should appear as both poor and elite in the same pairing.
    assert poor_ids.isdisjoint(elite_ids)


def test_truncation_selection_returns_empty_when_no_dead_and_no_ready() -> None:
    """The early-return guard still fires when there is nothing to do."""
    workers = [
        Worker(
            worker_id=idx,
            knob_space=MagicMock(),
            knob_config={"shared_buffers": "256MB"},
            ready_interval=5,
        )
        for idx in range(4)
    ]
    for worker in workers:
        worker.step_count = 1
        worker.performance_score = 80.0  # all healthy, none ready

    pairs = truncation_selection(
        workers,
        exploit_quantile=0.2,
        require_ready=True,
        dead_config_threshold=6.0,
    )

    assert pairs == []
