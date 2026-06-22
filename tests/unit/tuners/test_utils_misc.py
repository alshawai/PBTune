"""Tests for src.tuners.utils.knob_filter (DB-free paths).

Calibration/rescoring coverage now lives in ``test_calibration.py`` (the
canonical home for the relocated ``src/utils/rescoring.py`` module).
"""

from src.tuners.utils.knob_filter import compute_unsupported_knobs


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
