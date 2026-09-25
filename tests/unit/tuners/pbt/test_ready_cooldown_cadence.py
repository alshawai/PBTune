# Copyright (C) 2026 Ibrahim Al-Shawa and PBTune contributors
# Licensed under the GNU General Public License v3.0
# See LICENSE file for details

"""Seam tests for ticket #164 — ``ready_interval`` as a recurring cooldown (bug B1).

These pin the corrected exploit-cadence invariant at the ``PBTWorker`` code
seam. The PBT specification (Jaderberg et al. 2017, §4.1) measures readiness as
the steps elapsed *since the last time a member became ready*: a worker that
just adopted an elite's configuration must serve a full ``ready_interval``
cooldown before it may exploit again.

The regression these guard against (B1): ``clone_from`` used to *maintain*
``step_count``, so once a worker crossed the threshold it stayed permanently
ready and re-exploited on every subsequent generation. See
``src.analysis.pbt_invariants.check_exploit_cadence`` for the invariant this
mirrors and ``docs/architecture/decisions/ADR-008-pbt-readiness-cooldown.md``.

This file deliberately does NOT touch the frozen-trace characterization suite
(``test_trace_regression.py``): that suite pins the *pre-fix* symptom against a
recorded run and must stay red-forever-green. These tests prove the corrected
behavior at the live code seam instead.
"""

from __future__ import annotations

from unittest.mock import MagicMock

from src.tuners.pbt.evolution import execute_exploit_explore
from src.tuners.pbt.worker import PBTWorker
from src.utils.metrics import PerformanceMetrics


def _metrics() -> PerformanceMetrics:
    """A healthy, non-failure measurement."""
    return PerformanceMetrics(latency_p95=10.0, throughput=100.0)


def test_clone_from_rearms_readiness_and_needs_full_interval_to_requalify() -> None:
    """Adopting an elite config resets the readiness counter (step_count -> 0).

    After the exploit, the worker must complete ``ready_interval`` fresh
    evaluations before ``is_ready()`` is true again — the recurring cooldown.
    """
    ready_interval = 3  # spec literal: the "thorough" preset's cadence
    adopter = PBTWorker(
        worker_id=0,
        knob_space=MagicMock(),
        knob_config={"shared_buffers": "64MB"},
        ready_interval=ready_interval,
    )
    elite = PBTWorker(
        worker_id=1,
        knob_space=MagicMock(),
        knob_config={"shared_buffers": "512MB"},
        ready_interval=ready_interval,
    )

    # Warm up to readiness through the public evaluation seam.
    for _ in range(ready_interval):
        adopter.update_metrics(_metrics(), 50.0)
    assert adopter.is_ready() is True

    # EXPLOIT: adopt the elite's configuration.
    adopter.clone_from(elite, current_generation=2)

    # The cooldown re-arms immediately: the worker is NOT ready right after
    # adoption, and the elite's config was actually taken on.
    assert adopter.step_count == 0
    assert adopter.is_ready() is False
    assert adopter.knob_config is not None
    assert adopter.knob_config["shared_buffers"] == "512MB"

    # It must serve the full interval again: not ready until the interval'th eval.
    for completed in range(1, ready_interval):
        adopter.update_metrics(_metrics(), 55.0)
        assert adopter.is_ready() is False, (
            f"re-armed too early after {completed}/{ready_interval} evaluations"
        )

    adopter.update_metrics(_metrics(), 55.0)
    assert adopter.is_ready() is True


def test_exploit_cadence_gaps_never_shorter_than_ready_interval() -> None:
    """Across simulated generations, one worker's exploit gaps respect the cooldown.

    A stable score ranking keeps worker 0 the perennial poor performer and
    worker 4 the sole elite, so worker 0 is the exploit candidate whenever it is
    ready. With the cooldown, consecutive exploit generations must be at least
    ``ready_interval`` apart (mirrors ``check_exploit_cadence``'s gap test).
    Under bug B1 the same worker exploited every generation (gap == 1).
    """
    ready_interval = 3
    knob_space = MagicMock()
    # Identity perturbation so adopted configs stay comparable and no RNG noise
    # leaks into the score ranking we control below.
    knob_space.perturb_config.side_effect = lambda config, **_: dict(config)

    workers = [
        PBTWorker(
            worker_id=i,
            knob_space=knob_space,
            knob_config={"shared_buffers": f"{64 * (i + 1)}MB"},
            ready_interval=ready_interval,
        )
        for i in range(5)
    ]
    # Fixed ranking by worker_id: 0 worst, 4 best (all above the dead threshold).
    fixed_scores = {0: 10.0, 1: 20.0, 2: 30.0, 3: 40.0, 4: 90.0}

    exploit_generations: list[int] = []
    for generation in range(12):
        # Evaluate every worker (public seam; increments step_count).
        for worker in workers:
            worker.update_metrics(_metrics(), fixed_scores[worker.worker_id])

        pairs = execute_exploit_explore(
            workers=workers,
            exploit_quantile=0.2,  # -> quantile size 1: bottom {0} copies top {4}
            perturbation_factors=(0.8, 1.2),
            current_generation=generation,
            require_ready=True,
            dead_config_threshold=6.0,
        )
        if pairs:
            exploit_generations.append(generation)

    assert exploit_generations, "expected at least one exploit event across the run"
    gaps = [
        later - earlier
        for earlier, later in zip(
            exploit_generations, exploit_generations[1:], strict=False
        )
    ]
    assert all(gap >= ready_interval for gap in gaps), (
        f"cooldown never re-arms: exploit generations={exploit_generations}, "
        f"gaps={gaps}, ready_interval={ready_interval}"
    )
