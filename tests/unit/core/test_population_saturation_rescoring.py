"""Tests for population saturation handling and rescoring behavior."""

from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import MagicMock

from src.tuner.core.population import Population, PopulationConfig
from src.tuner.core.worker import Worker
from src.utils.metrics import PerformanceMetrics
from src.utils.scoring.contracts import ScoreBreakdown


class _MetricConfigStub:
    """Stub metric config used to verify rescoring logic."""

    def __init__(self, expand: bool) -> None:
        self._expand = expand
        self.expand_calls = 0
        self.rescore_calls = 0

    def expand_ranges_for_metrics(self, _metrics_list, expansion_factor=0.25):
        _ = expansion_factor
        self.expand_calls += 1
        return self._expand

    def compute_score(self, metrics: PerformanceMetrics, worker_logger=None) -> ScoreBreakdown:
        self.rescore_calls += 1
        final_score = float(metrics.throughput / 10.0)
        return ScoreBreakdown(final_score=final_score)

    def compute_score_value(self, metrics: PerformanceMetrics) -> float:
        breakdown = self.compute_score(metrics)
        return breakdown.final_score


def _make_worker(worker_id: int, throughput: float, score: float) -> Worker:
    knob_space = MagicMock()
    knob_space.sample_random_config.return_value = {"shared_buffers": "256MB"}
    worker = Worker(worker_id=worker_id, knob_space=knob_space)
    worker.metrics = PerformanceMetrics(latency_p95=10.0, throughput=throughput)
    worker.performance_score = score
    return worker


def test_finalize_scores_grounds_best_to_current() -> None:
    """When ranges expand, population should rescore workers and ground historical best."""
    metric_config = _MetricConfigStub(expand=True)
    evaluator = SimpleNamespace(config=SimpleNamespace(metric_config=metric_config))

    population = Population(
        knob_space=MagicMock(),
        config=PopulationConfig(population_size=2, dead_config_threshold=6.0),
        orchestrator=evaluator,
    )
    population._ranges_calibrated = True

    worker_a = _make_worker(worker_id=0, throughput=100.0, score=20.0)
    worker_b = _make_worker(worker_id=1, throughput=80.0, score=18.0)
    population.workers = [worker_a, worker_b]

    population.best_overall_metrics = PerformanceMetrics(
        latency_p95=9.0, throughput=120.0
    )
    population.best_overall_score = 50.0

    population._finalize_scores()

    assert metric_config.expand_calls == 1
    assert worker_a.performance_score == 10.0
    assert worker_b.performance_score == 8.0
    # Historical best throughput=120.0 rescored -> 12.0, which > 10.0
    assert population.best_overall_score == 12.0


def test_finalize_scores_overwrites_best_if_worse() -> None:
    """If the rescored historical best is worse than the current best, it should be overwritten."""
    metric_config = _MetricConfigStub(expand=True)
    evaluator = SimpleNamespace(config=SimpleNamespace(metric_config=metric_config))

    population = Population(
        knob_space=MagicMock(),
        config=PopulationConfig(population_size=2, dead_config_threshold=6.0),
        orchestrator=evaluator,
    )
    population._ranges_calibrated = True

    worker_a = _make_worker(worker_id=0, throughput=100.0, score=20.0)
    worker_b = _make_worker(worker_id=1, throughput=80.0, score=18.0)
    population.workers = [worker_a, worker_b]

    # Historical best has worse throughput than worker_a
    population.best_overall_metrics = PerformanceMetrics(
        latency_p95=9.0, throughput=50.0
    )
    population.best_overall_score = 50.0

    population._finalize_scores()

    assert metric_config.expand_calls == 1
    assert worker_a.performance_score == 10.0
    # Historical best throughput=50.0 rescored -> 5.0, which < 10.0
    assert population.best_overall_score == 10.0


def test_finalize_scores_always_rescores_workers() -> None:
    """Even when no range expansion is needed, workers should be rescored if features are refined."""
    metric_config = _MetricConfigStub(expand=False)
    evaluator = SimpleNamespace(config=SimpleNamespace(metric_config=metric_config))

    population = Population(
        knob_space=MagicMock(),
        config=PopulationConfig(population_size=1, dead_config_threshold=6.0),
        orchestrator=evaluator,
    )
    population._ranges_calibrated = True
    population._features_refined = True

    worker = _make_worker(worker_id=0, throughput=100.0, score=22.0)
    population.workers = [worker]
    population.best_overall_metrics = PerformanceMetrics(
        latency_p95=9.0, throughput=120.0
    )
    population.best_overall_score = 30.0

    population._finalize_scores()

    assert metric_config.expand_calls == 1
    assert worker.performance_score == 10.0
    assert population.best_overall_score == 12.0
