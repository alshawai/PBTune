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
        self._normalizer = None
        self.scoring_policy = "fixed_v1"
        self.workload_type = type("obj", (object,), {"value": "oltp"})()
        self.latency_metric = "p95"
        self.workload_features = {}
        self.weight_latency = 0.5
        self.weight_throughput = 0.3
        self.weight_memory = 0.05
        self.weight_error = 0.15

    def expand_ranges_for_metrics(self, _metrics_list, expansion_factor=0.25):
        _ = expansion_factor
        self.expand_calls += 1
        return self._expand

    def compute_score(
        self, metrics: PerformanceMetrics, worker_logger=None
    ) -> ScoreBreakdown:
        self.rescore_calls += 1
        final_score = float(metrics.throughput / 10.0)
        return ScoreBreakdown(final_score=final_score)


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
    orchestrator = SimpleNamespace(config=SimpleNamespace(metric_config=metric_config))

    population = Population(
        knob_space=MagicMock(),
        config=PopulationConfig(population_size=2, dead_config_threshold=6.0),
        orchestrator=orchestrator,  # type: ignore
    )
    population._ranges_calibrated = True
    # Provide a no-op feature-weight updater for the test orchestrator
    population.orchestrator.maybe_update_feature_weights = (  # type: ignore
        lambda *args, **kwargs: False
    )

    worker_a = _make_worker(worker_id=0, throughput=100.0, score=20.0)
    worker_b = _make_worker(worker_id=1, throughput=80.0, score=18.0)
    population.workers = [worker_a, worker_b]

    population.best_overall_metrics = PerformanceMetrics(
        latency_p95=9.0, throughput=120.0
    )
    population.best_overall_score = 50.0

    # Provide a mock scoring engine on the orchestrator so rescoring uses the
    # stub's compute_score implementation deterministically.
    mock_engine = MagicMock()
    mock_engine.compute_breakdown = lambda m, worker_logger=None: (
        metric_config.compute_score(m, worker_logger=worker_logger)
    )
    population.orchestrator.scorer = mock_engine

    population._finalize_scores()

    assert metric_config.expand_calls == 1
    assert worker_a.performance_score == 10.0
    assert worker_b.performance_score == 8.0
    # Historical best throughput=120.0 rescored -> 12.0, which > 10.0
    assert population.best_overall_score == 12.0


def test_finalize_scores_overwrites_best_if_worse() -> None:
    """If the rescored historical best is worse than the current best, it should be overwritten."""
    metric_config = _MetricConfigStub(expand=True)
    orchestrator = SimpleNamespace(config=SimpleNamespace(metric_config=metric_config))

    population = Population(
        knob_space=MagicMock(),
        config=PopulationConfig(population_size=2, dead_config_threshold=6.0),
        orchestrator=orchestrator,
    )
    population._ranges_calibrated = True
    # Provide a no-op feature-weight updater for the test orchestrator
    population.orchestrator.maybe_update_feature_weights = lambda *args, **kwargs: False

    worker_a = _make_worker(worker_id=0, throughput=100.0, score=20.0)
    worker_b = _make_worker(worker_id=1, throughput=80.0, score=18.0)
    population.workers = [worker_a, worker_b]

    # Historical best has worse throughput than worker_a
    population.best_overall_metrics = PerformanceMetrics(
        latency_p95=9.0, throughput=50.0
    )
    population.best_overall_score = 50.0

    mock_engine = MagicMock()
    mock_engine.compute_breakdown = lambda m, worker_logger=None: (
        metric_config.compute_score(m, worker_logger=worker_logger)
    )
    population.orchestrator.scorer = mock_engine

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
    # When features are refined, orchestrator.maybe_update_feature_weights
    # should indicate an update so rescoring proceeds in the test.
    population.orchestrator.maybe_update_feature_weights = lambda *args, **kwargs: True

    worker = _make_worker(worker_id=0, throughput=100.0, score=22.0)
    population.workers = [worker]
    population.best_overall_metrics = PerformanceMetrics(
        latency_p95=9.0, throughput=120.0
    )
    population.best_overall_score = 30.0

    mock_engine = MagicMock()
    mock_engine.compute_breakdown = lambda m, worker_logger=None: (
        metric_config.compute_score(m, worker_logger=worker_logger)
    )
    population.orchestrator.scorer = mock_engine

    population._finalize_scores()

    assert metric_config.expand_calls == 1
    assert worker.performance_score == 10.0
    assert population.best_overall_score == 12.0


def test_finalize_scores_rescores_on_first_calibration() -> None:
    """First-time normalizer calibration must trigger a rescore.

    Regression: previously _finalize_scores skipped rescoring on the
    generation where _ranges_calibrated transitioned False → True,
    leaving every worker's score computed against the uncalibrated
    fallback normalizer. The _just_calibrated one-shot flag set by
    update_metric_ranges_if_needed() must drive the rescore path.
    """
    # ranges_expanded=False isolates the calibration trigger from the
    # incremental saturation path.
    metric_config = _MetricConfigStub(expand=False)
    orchestrator = SimpleNamespace(config=SimpleNamespace(metric_config=metric_config))

    population = Population(
        knob_space=MagicMock(),
        config=PopulationConfig(population_size=2, dead_config_threshold=6.0),
        orchestrator=orchestrator,  # type: ignore
    )
    population._ranges_calibrated = True
    population._just_calibrated = True  # one-shot calibration signal
    # Feature weights have nothing to update yet — only calibration
    # should be driving the rescore here.
    population.orchestrator.maybe_update_feature_weights = (  # type: ignore
        lambda *args, **kwargs: False
    )

    worker_a = _make_worker(worker_id=0, throughput=100.0, score=20.0)
    worker_b = _make_worker(worker_id=1, throughput=80.0, score=18.0)
    population.workers = [worker_a, worker_b]
    population.best_overall_metrics = PerformanceMetrics(
        latency_p95=9.0, throughput=120.0
    )
    population.best_overall_score = 50.0

    mock_engine = MagicMock()
    mock_engine.compute_breakdown = lambda m, worker_logger=None: (
        metric_config.compute_score(m, worker_logger=worker_logger)
    )
    population.orchestrator.scorer = mock_engine

    population._finalize_scores()

    # Workers were rescored using the freshly-calibrated normalizer.
    assert worker_a.performance_score == 10.0
    assert worker_b.performance_score == 8.0
    # Historical best (throughput=120) was rescored too.
    assert population.best_overall_score == 12.0
    # The one-shot flag was consumed.
    assert population._just_calibrated is False


def test_finalize_scores_skips_rescore_when_nothing_changed() -> None:
    """No rescore should happen when nothing about scoring has changed.

    Guard against the previous fix over-firing: if no range expansion,
    no feature-weight update, and no fresh calibration event, the
    rescore path must remain skipped.
    """
    metric_config = _MetricConfigStub(expand=False)
    orchestrator = SimpleNamespace(config=SimpleNamespace(metric_config=metric_config))

    population = Population(
        knob_space=MagicMock(),
        config=PopulationConfig(population_size=1, dead_config_threshold=6.0),
        orchestrator=orchestrator,  # type: ignore
    )
    population._ranges_calibrated = True
    population._just_calibrated = False  # already-consumed
    population.orchestrator.maybe_update_feature_weights = (  # type: ignore
        lambda *args, **kwargs: False
    )

    worker = _make_worker(worker_id=0, throughput=100.0, score=22.0)
    population.workers = [worker]
    population.best_overall_metrics = PerformanceMetrics(
        latency_p95=9.0, throughput=120.0
    )
    population.best_overall_score = 30.0

    mock_engine = MagicMock()
    mock_engine.compute_breakdown = lambda m, worker_logger=None: (
        metric_config.compute_score(m, worker_logger=worker_logger)
    )
    population.orchestrator.scorer = mock_engine

    population._finalize_scores()

    # Score unchanged because the rescore path was skipped.
    assert worker.performance_score == 22.0
    assert population.best_overall_score == 30.0
