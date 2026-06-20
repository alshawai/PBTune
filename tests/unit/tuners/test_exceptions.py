"""Tests for src.tuners.utils.exceptions."""

import pytest

from src.tuners.utils.exceptions import (
    GenerationEvaluationError,
    KnobSpaceEmptyError,
    TunerConfigError,
    TunerError,
    TunerSetupError,
)


class TestExceptionHierarchy:
    def test_all_derive_from_tuner_error(self):
        for exc in (
            TunerConfigError,
            TunerSetupError,
            KnobSpaceEmptyError,
            GenerationEvaluationError,
        ):
            assert issubclass(exc, TunerError)

    def test_knob_space_empty_is_setup_error(self):
        assert issubclass(KnobSpaceEmptyError, TunerSetupError)

    def test_config_error_is_not_setup_error(self):
        assert not issubclass(TunerConfigError, TunerSetupError)

    def test_caught_by_base(self):
        with pytest.raises(TunerError):
            raise KnobSpaceEmptyError("no knobs")
        with pytest.raises(TunerError):
            raise TunerConfigError("bad config")

    def test_setup_error_catches_subclass(self):
        with pytest.raises(TunerSetupError):
            raise KnobSpaceEmptyError("no knobs")
