# Copyright (C) 2026 Ibrahim Al-Shawa and PBTune contributors
# Licensed under the GNU General Public License v3.0
# See LICENSE file for details

"""Seam tests for ticket #168 — strict improvement resets stagnation (bug B13).

The no-improvement early-stop counter (``generations_without_improvement``) must
only reset on a STRICT improvement of the historical-best score. The regression
(B13): the comparison used ``>=``, so an exact tie counted as an improvement and
pinned the counter near zero — a population that had stopped improving never
reached ``early_stopping_patience`` and never early-stopped.

These drive the confirmed seam ``Population._determine_overall_best`` (where the
counter is maintained) and observe through the public ``should_stop()``.
"""

from __future__ import annotations

from unittest.mock import MagicMock

from src.tuners.pbt.population import Population, PopulationConfig
from src.tuners.pbt.worker import PBTWorker
from src.utils.metrics import PerformanceMetrics


def _best_worker_with_score(score: float) -> PBTWorker:
    """A finalized 'best current' worker carrying a fixed score."""
    worker = PBTWorker(
        worker_id=0,
        knob_space=MagicMock(),
        knob_config={"shared_buffers": "64MB"},
    )
    worker.performance_score = score
    worker.metrics = PerformanceMetrics(latency_p95=10.0, throughput=100.0)
    return worker


def test_flat_population_reaches_patience_and_early_stops() -> None:
    """A flat (tied) best score accrues stagnation until patience triggers a stop."""
    patience = 3  # spec literal chosen for this scenario
    population = Population(
        knob_space=MagicMock(),
        config=PopulationConfig(
            population_size=2,
            early_stopping_patience=patience,
            max_generations=100,  # high, so the max-gen gate never fires first
            disable_early_stopping=False,
        ),
    )

    # First finalized result is a genuine improvement over the 0.0 baseline.
    population._determine_overall_best(_best_worker_with_score(50.0))
    assert population.generations_without_improvement == 0
    assert population.should_stop() is False

    # Each subsequent generation reports the SAME best score (flat / stagnant).
    for _ in range(patience):
        population._determine_overall_best(_best_worker_with_score(50.0))

    assert population.generations_without_improvement == patience
    assert population.should_stop() is True


def test_exact_tie_does_not_reset_the_no_improvement_counter() -> None:
    """Ties are not progress: the counter climbs by one per tied generation."""
    population = Population(
        knob_space=MagicMock(),
        config=PopulationConfig(population_size=2, early_stopping_patience=10),
    )

    population._determine_overall_best(_best_worker_with_score(42.0))  # improvement
    population._determine_overall_best(_best_worker_with_score(42.0))  # tie
    population._determine_overall_best(_best_worker_with_score(42.0))  # tie

    assert population.generations_without_improvement == 2


def test_strict_improvement_resets_counter_and_records_best() -> None:
    """A strictly higher score clears the counter and updates the recorded best."""
    population = Population(
        knob_space=MagicMock(),
        config=PopulationConfig(population_size=2, early_stopping_patience=10),
    )

    population._determine_overall_best(_best_worker_with_score(42.0))  # improvement
    population._determine_overall_best(_best_worker_with_score(42.0))  # tie -> 1
    population._determine_overall_best(_best_worker_with_score(42.0))  # tie -> 2
    assert population.generations_without_improvement == 2

    population._determine_overall_best(_best_worker_with_score(50.0))  # strict gain

    assert population.generations_without_improvement == 0
    assert population.best_overall_score == 50.0
