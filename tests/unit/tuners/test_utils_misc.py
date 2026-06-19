"""Tests for src.tuners.utils.calibration and knob_filter (DB-free paths)."""

from src.tuners.utils.calibration import (
    MIN_OBSERVATIONS_FOR_RECALIBRATION,
    RecalibrationResult,
    maybe_recalibrate_scores,
)
from src.tuners.utils.knob_filter import compute_unsupported_knobs


class TestMaybeRecalibrate:
    def test_below_floor_skips(self):
        # Fewer than the floor → unapplied, no exception, no DB/scoring needed.
        result = maybe_recalibrate_scores([], benchmark="sysbench")
        assert isinstance(result, RecalibrationResult)
        assert result.applied is False
        assert result.metric_config is None
        assert result.rescored_values == []

    def test_floor_constant(self):
        assert MIN_OBSERVATIONS_FOR_RECALIBRATION == 3


class TestComputeUnsupportedKnobs:
    def test_returns_sorted_difference(self):
        configured = ["work_mem", "shared_buffers", "zzz_unsupported", "aaa_gone"]
        supported = {"work_mem", "shared_buffers"}
        assert compute_unsupported_knobs(configured, supported) == [
            "aaa_gone",
            "zzz_unsupported",
        ]

    def test_all_supported(self):
        assert compute_unsupported_knobs(["a", "b"], {"a", "b", "c"}) == []

    def test_empty_configured(self):
        assert compute_unsupported_knobs([], {"a"}) == []
