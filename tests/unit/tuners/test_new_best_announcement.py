"""Regression tests for the "NEW BEST SCORE" announcement gate (bug B14).

Ticket #172. The announcement used to fire on ``current_best > prev_best``,
where ``prev_best`` was captured *before* the generation ran and ``current_best``
*after* it. On a generation that recalibrates the normalizer, those two values
live on different rulers, so an unchanged carried-over config's score rises and a
phantom "NEW BEST SCORE" is announced although no better configuration was
found. The fix gates the announcement on an explicit, single-ruler
``improved`` signal supplied by the strategy (``GenerationOutcome.strictly_improved``,
set by ``Population._determine_overall_best`` after the incumbent has been
rescored onto the current ruler), falling back to the best-delta compare only
when the strategy supplies no signal.
"""

from __future__ import annotations

from unittest.mock import MagicMock

from src.tuners.utils import tuner_logging


def _announced(monkeypatch, *, improved, current_best, prev_best) -> bool:
    """Invoke log_round_end and report whether "NEW BEST SCORE" was logged."""
    logger = MagicMock()
    monkeypatch.setattr(tuner_logging, "LOGGER", logger)
    tuner_logging.log_round_end(
        outcome_index=1,
        outcome_best_score=current_best,
        outcome_payload={},
        prev_best=prev_best,
        current_best=current_best,
        improved=improved,
        elapsed_seconds=0.0,
        emits_stop_status=False,
        stopped=False,
        stop_reason=None,
        round_label="Generation",
    )
    return any(
        call.args and "NEW BEST SCORE" in str(call.args[0])
        for call in logger.info.call_args_list
    )


def test_recalibration_inflated_best_does_not_announce(monkeypatch) -> None:
    """B14: improved=False suppresses the announcement even though the
    recalibration raised current_best above the pre-step prev_best."""
    assert not _announced(
        monkeypatch, improved=False, current_best=94.08, prev_best=92.56
    )


def test_real_improvement_is_announced(monkeypatch) -> None:
    """A genuine, same-ruler improvement (improved=True) is announced."""
    assert _announced(monkeypatch, improved=True, current_best=95.0, prev_best=90.0)


def test_fallback_announces_on_best_delta_when_no_signal(monkeypatch) -> None:
    """Strategies that supply no signal (improved=None) keep the legacy
    best-delta behaviour: announce iff current_best > prev_best."""
    assert _announced(monkeypatch, improved=None, current_best=91.0, prev_best=90.0)
    assert not _announced(
        monkeypatch, improved=None, current_best=90.0, prev_best=90.0
    )
