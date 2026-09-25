# Copyright (C) 2026 Ibrahim Al-Shawa and PBTune contributors
# Licensed under the GNU General Public License v3.0
# See LICENSE file for details

"""Uniform donor sampling at ``population_size=8`` (bug B2, ticket #165).

``truncation_selection`` already draws the donor uniformly from the elite
bucket (``poor_worker.rng.choice(elite_workers)``); the historical monoculture
was caused purely by the quantile floor ``int(8 * 0.2) == 1`` shrinking that
bucket to a single worker. With ``exploit_quantile=0.25`` the bucket holds two
workers, so different poor workers draw different donors and donor identity
varies across a run.
"""

from __future__ import annotations

from typing import List
from unittest.mock import MagicMock

import numpy as np

from src.analysis.pbt_invariants import SessionTrace, check_donor_diversity
from src.tuners.pbt.evolution import get_elite_workers, truncation_selection
from src.tuners.pbt.worker import PBTWorker


def _make_ready_population(
    scores: List[float],
    *,
    ready_interval: int = 1,
    seed_base: int = 1000,
    ready: bool = True,
) -> List[PBTWorker]:
    """Build workers with distinct scores, seeded RNGs, and a chosen readiness."""
    workers: List[PBTWorker] = []
    for worker_id, score in enumerate(scores):
        worker = PBTWorker(
            worker_id=worker_id,
            knob_space=MagicMock(),
            knob_config={"shared_buffers": "256MB"},
            ready_interval=ready_interval,
            _rng=np.random.default_rng(seed_base + worker_id),
        )
        worker.performance_score = score
        worker.step_count = ready_interval if ready else 0
        workers.append(worker)
    return workers


def test_pop8_has_two_elites_and_two_exploit_pairs() -> None:
    """int(8 * 0.25) == 2 elites; two ready poor workers produce two pairs."""
    scores = [10.0, 20.0, 30.0, 40.0, 50.0, 60.0, 70.0, 80.0]
    workers = _make_ready_population(scores)

    # The elite bucket is a genuine pair, not a singleton argmax.
    elites = get_elite_workers(workers, quantile=0.25)
    assert {w.worker_id for w in elites} == {6, 7}

    pairs = truncation_selection(
        workers,
        exploit_quantile=0.25,
        require_ready=True,
        dead_config_threshold=6.0,
    )

    poor_ids = {workers[poor_idx].worker_id for poor_idx, _ in pairs}
    elite_ids = {workers[elite_idx].worker_id for _, elite_idx in pairs}

    assert len(pairs) == 2
    assert poor_ids == {0, 1}
    assert elite_ids.issubset({6, 7})


def test_donor_identity_varies_across_generations() -> None:
    """Over repeated selection the donor set has more than one member.

    Deterministic: each worker carries a seeded RNG stream that advances on
    every ``rng.choice`` draw, so the sequence of donors is fixed for the seed
    yet exercises both elites of the two-member bucket.
    """
    scores = [10.0, 20.0, 30.0, 40.0, 50.0, 60.0, 70.0, 80.0]
    workers = _make_ready_population(scores, seed_base=7)

    donors: set[int] = set()
    for _ in range(30):
        pairs = truncation_selection(
            workers,
            exploit_quantile=0.25,
            require_ready=True,
            dead_config_threshold=6.0,
        )
        donors.update(workers[elite_idx].worker_id for _, elite_idx in pairs)

    # Both elites of the {6, 7} bucket are sampled: no single-donor collapse.
    assert donors == {6, 7}
    assert len(donors) > 1


def test_check_donor_diversity_holds_on_corrected_trace() -> None:
    """The B2 invariant turns green when donors vary across the run."""
    history = []
    for generation in range(2, 12):
        primary = 6 if generation % 2 == 0 else 7
        secondary = 7 if primary == 6 else 6
        history.append(
            {
                "generation": generation,
                "num_exploited": 2,
                "exploitations": [
                    {"elite_worker_id": primary, "poor_worker_id": 0},
                    {"elite_worker_id": secondary, "poor_worker_id": 1},
                ],
            }
        )

    trace = SessionTrace.from_dict({"history": history})
    finding = check_donor_diversity(trace)

    assert finding.holds
    assert finding.observed["distinct_donors"] == [6, 7]
