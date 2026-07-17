"""Tests for src.tuners.utils.knob_filter (DB-free paths).

Calibration/rescoring coverage now lives in ``test_calibration.py`` (the
canonical home for the relocated ``src/utils/rescoring.py`` module).
"""

from src.knobs.knob_space import KnobDefinition, KnobScale, KnobSpace, KnobType
from src.tuners.utils.knob_filter import (
    apply_tuning_mode_filter,
    compute_unsupported_knobs,
)
from src.utils.types import TuningMode


def _knob(name: str, *, restart_required: bool) -> KnobDefinition:
    return KnobDefinition(
        name=name,
        knob_type=KnobType.INTEGER,
        scale=KnobScale.LINEAR,
        restart_required=restart_required,
    )


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


class TestApplyTuningModeFilter:
    def test_online_drops_restart_required_knobs(self):
        space = KnobSpace(
            [
                _knob("work_mem", restart_required=False),
                _knob("shared_buffers", restart_required=True),
            ]
        )
        view = apply_tuning_mode_filter(space, TuningMode.ONLINE)
        assert set(view.knobs) == {"work_mem"}
        # The original space is untouched (view is a new KnobSpace).
        assert set(space.knobs) == {"work_mem", "shared_buffers"}

    def test_offline_returns_space_unchanged(self):
        space = KnobSpace(
            [
                _knob("work_mem", restart_required=False),
                _knob("shared_buffers", restart_required=True),
            ]
        )
        assert apply_tuning_mode_filter(space, TuningMode.OFFLINE) is space

    def test_adaptive_returns_space_unchanged(self):
        space = KnobSpace([_knob("shared_buffers", restart_required=True)])
        assert apply_tuning_mode_filter(space, TuningMode.ADAPTIVE) is space
