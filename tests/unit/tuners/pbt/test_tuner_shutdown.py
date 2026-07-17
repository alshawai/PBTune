"""PBTTuner failure-handling + teardown behavior on the unified tuner.

Repointed in refactor step 2f from the retired legacy ``src.tuner.main.PBTTuner``
to ``src.tuners.pbt.tuner.PBTTuner``. The legacy ``run()`` orchestration
assertions (KeyboardInterrupt propagation, ``save_final_results`` not-called,
the deleted ``save_intermediate_results`` mock) are gone — that lifecycle is now
``BaseTuner.run()`` and is covered generically in ``test_base.py`` (see
``test_teardown_runs_even_if_step_raises``). What remains PBT-specific and worth
pinning here is:

* ``evaluate_worker``'s failure ladder — connection/timeout/runtime/unexpected
  errors map to the dead-config or crash fallback score instead of aborting the
  generation, and never let a recovery error escape;
* ``teardown()`` stopping instances (and cleaning up only when requested).
"""

from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import MagicMock

import psycopg2

from src.tuners.pbt.tuner import PBTTuner


def _make_eval_tuner(*, dead_config_score=0.0, crash_score=5.0) -> PBTTuner:
    """Bare PBTTuner with just the surface ``evaluate_worker`` reads."""
    tuner = PBTTuner.__new__(PBTTuner)
    tuner.current_generation = 0
    tuner._restarted_this_generation = False
    tuner.restart_count = 0
    tuner.population = MagicMock()
    tuner.pbt_config = SimpleNamespace(
        dead_config_score=dead_config_score, crash_score=crash_score
    )
    tuner.orchestrator = MagicMock()
    # The failure path computes a fallback breakdown via the shared scorer.
    tuner.orchestrator.scorer.compute_breakdown.return_value = SimpleNamespace(
        final_score=0.0
    )
    tuner.env = MagicMock()
    return tuner


def _worker() -> SimpleNamespace:
    return SimpleNamespace(worker_id=0, logger=MagicMock(), port=None)


def test_connection_error_maps_to_dead_config_and_recovers() -> None:
    """Connection failures attempt recovery and return the dead-config score."""
    tuner = _make_eval_tuner(dead_config_score=0.0)
    tuner.orchestrator.evaluate_worker.side_effect = ConnectionError("pg unreachable")
    tuner.env.recover_instance.return_value = True

    metrics, score = tuner.evaluate_worker(_worker())

    assert score == 0.0
    assert metrics.failure_type == "crash_dead"
    tuner.env.recover_instance.assert_called_once_with(0)


def test_psycopg2_error_maps_to_dead_config() -> None:
    """psycopg2 errors travel the same dead-config branch as ConnectionError."""
    tuner = _make_eval_tuner(dead_config_score=1.0)
    tuner.orchestrator.evaluate_worker.side_effect = psycopg2.OperationalError("boom")
    tuner.env.recover_instance.return_value = False

    metrics, score = tuner.evaluate_worker(_worker())

    assert score == 1.0
    assert metrics.failure_type == "crash_dead"


def test_timeout_error_maps_to_crash_score() -> None:
    tuner = _make_eval_tuner(crash_score=5.0)
    tuner.orchestrator.evaluate_worker.side_effect = TimeoutError("slow")

    metrics, score = tuner.evaluate_worker(_worker())

    assert score == 5.0
    assert metrics.failure_type == "crash_timeout"


def test_runtime_error_maps_to_crash_score() -> None:
    tuner = _make_eval_tuner(crash_score=5.0)
    tuner.orchestrator.evaluate_worker.side_effect = RuntimeError("kaboom")

    metrics, score = tuner.evaluate_worker(_worker())

    assert score == 5.0
    assert metrics.failure_type == "crash_runtime"


def test_unexpected_error_maps_to_crash_score() -> None:
    tuner = _make_eval_tuner(crash_score=5.0)
    tuner.orchestrator.evaluate_worker.side_effect = ValueError("surprise")

    metrics, score = tuner.evaluate_worker(_worker())

    assert score == 5.0
    assert metrics.failure_type == "crash_unexpected"


def test_recovery_exception_after_connection_failure_does_not_escape() -> None:
    """A recovery error is logged, not raised — the fallback still returns."""
    tuner = _make_eval_tuner(dead_config_score=0.0)
    tuner.orchestrator.evaluate_worker.side_effect = ConnectionError("pg down")
    tuner.env.recover_instance.side_effect = RuntimeError("docker read timeout")

    metrics, score = tuner.evaluate_worker(_worker())

    assert score == 0.0
    assert metrics.failure_type == "crash_dead"
    tuner.env.recover_instance.assert_called_once_with(0)


def test_teardown_stops_instances_without_cleanup_by_default() -> None:
    tuner = PBTTuner.__new__(PBTTuner)
    tuner.env = MagicMock()
    tuner.lifecycle = SimpleNamespace(cleanup_instances=False)

    tuner.teardown()

    tuner.env.stop_all.assert_called_once()
    tuner.env.cleanup.assert_not_called()


def test_teardown_cleans_up_when_requested() -> None:
    tuner = PBTTuner.__new__(PBTTuner)
    tuner.env = MagicMock()
    tuner.lifecycle = SimpleNamespace(cleanup_instances=True)

    tuner.teardown()

    tuner.env.stop_all.assert_called_once()
    tuner.env.cleanup.assert_called_once_with(remove_data=True)
