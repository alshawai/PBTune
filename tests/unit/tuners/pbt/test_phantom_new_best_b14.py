"""Population-level regression for the phantom "new best" ruler bug (B14).

Ticket #172. A normalizer recalibration/expansion rescores the historical best
onto the new ruler (``population.py`` :meth:`_finalize_scores`), so an unchanged
carried-over config's ``best_overall_score`` rises. That rise must NOT be
mistaken for tuning progress: :meth:`_determine_overall_best` compares the
current best against the *rescored* incumbent on one consistent ruler, so a pure
ruler shift is not a strict improvement. These tests pin that
``last_generation_strictly_improved`` (the signal the tuner uses to gate the
"NEW BEST SCORE" announcement) and ``generations_without_improvement`` both
reflect real progress, not ruler movement.
"""

from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import MagicMock

from src.tuners.pbt.population import Population, PopulationConfig
from src.tuners.pbt.worker import PBTWorker
from src.utils.metrics import PerformanceMetrics
from src.utils.scoring.contracts import ScoreBreakdown


class _ThroughputOverTenScorer:
    """Deterministic scorer: score == throughput / 10, with expansion on."""

    def __init__(self) -> None:
        self.scoring_policy = "fixed_v1"
        self.workload_type = type("obj", (object,), {"value": "oltp"})()

    def expand_ranges_for_metrics(self, _metrics, expansion_factor=0.25):
        return True  # a recalibration/expansion happened this generation

    def compute_breakdown(self, metrics, worker_logger=None):
        return ScoreBreakdown(final_score=float(metrics.throughput) / 10.0)


def _population_after_recalibration(worker_throughput: float) -> Population:
    scorer = _ThroughputOverTenScorer()
    orchestrator = SimpleNamespace(
        config=SimpleNamespace(metric_config=scorer),
        reload_scoring_engine=lambda *_a, **_k: None,
        maybe_update_feature_weights=lambda *_a, **_k: False,
        scorer=scorer,
    )
    population = Population(
        knob_space=MagicMock(),
        config=PopulationConfig(population_size=1, dead_config_threshold=6.0),
        orchestrator=orchestrator,  # type: ignore[arg-type]
    )
    population._ranges_calibrated = True
    knob_space = MagicMock()
    worker = PBTWorker(worker_id=0, knob_space=knob_space)
    worker.metrics = PerformanceMetrics(latency_p95=10.0, throughput=worker_throughput)
    worker.performance_score = worker_throughput / 10.0
    population.workers = [worker]
    # Incumbent is the SAME config family, last scored on a looser ruler (8.0),
    # whose metrics rescore to 12.0 under this generation's ruler.
    population.best_overall_metrics = PerformanceMetrics(
        latency_p95=10.0, throughput=120.0
    )
    population.best_overall_score = 8.0
    return population


def test_pure_ruler_shift_on_unchanged_incumbent_is_not_progress() -> None:
    """B14: recalibration lifts the incumbent 8.0 -> 12.0, but the current best
    only equals the rescored incumbent, so it is NOT a strict improvement: the
    announcement signal stays False and the stagnation counter advances."""
    population = _population_after_recalibration(worker_throughput=120.0)
    population.generations_without_improvement = 3

    population._finalize_scores()

    assert population.best_overall_score == 12.0  # ruler moved the incumbent up
    assert population.last_generation_strictly_improved is False  # but no progress
    assert population.generations_without_improvement == 4  # counter advanced


def test_genuine_gain_over_rescored_incumbent_is_progress() -> None:
    """A config that truly beats the rescored incumbent (20.0 > 12.0) is a
    strict improvement: the signal is True and the stagnation counter resets."""
    population = _population_after_recalibration(worker_throughput=200.0)
    population.generations_without_improvement = 5

    population._finalize_scores()

    assert population.best_overall_score == 20.0
    assert population.last_generation_strictly_improved is True
    assert population.generations_without_improvement == 0
