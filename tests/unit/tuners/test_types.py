"""Tests for src.tuners.utils.types."""

import pytest

from src.tuners.utils.types import (
    GenerationOutcome,
    TunerLifecycleConfig,
    TuningStrategy,
)


class TestTuningStrategy:
    def test_values(self):
        assert TuningStrategy.PBT.value == "pbt"
        assert TuningStrategy.BO.value == "bo"
        assert TuningStrategy.LHS.value == "lhs"

    def test_str_is_value(self):
        assert str(TuningStrategy.LHS) == "lhs"

    @pytest.mark.parametrize(
        "raw,expected",
        [
            ("pbt", TuningStrategy.PBT),
            ("BO", TuningStrategy.BO),
            ("  Lhs ", TuningStrategy.LHS),
            (TuningStrategy.PBT, TuningStrategy.PBT),
        ],
    )
    def test_from_value_coerces(self, raw, expected):
        assert TuningStrategy.from_value(raw) is expected

    def test_from_value_rejects_unknown(self):
        with pytest.raises(ValueError, match="Unknown tuning strategy"):
            TuningStrategy.from_value("genetic")


class TestGenerationOutcome:
    def test_defaults(self):
        outcome = GenerationOutcome(index=0)
        assert outcome.best_score_so_far == 0.0
        assert outcome.num_evaluations == 0
        assert outcome.converged is False
        assert outcome.payload == {}

    def test_to_dict_merges_payload(self):
        outcome = GenerationOutcome(
            index=3,
            best_score_so_far=1.2,
            best_score_this_generation=0.9,
            num_evaluations=4,
            converged=True,
            payload={"design_size": 16},
        )
        d = outcome.to_dict()
        assert d["generation"] == 3
        assert d["best_score_so_far"] == 1.2
        assert d["best_score"] == 0.9
        assert d["num_evaluations"] == 4
        assert d["converged"] is True
        assert d["design_size"] == 16


class TestTunerLifecycleConfig:
    def test_coerces_strategy_string(self):
        cfg = TunerLifecycleConfig(strategy="lhs")
        assert cfg.strategy is TuningStrategy.LHS

    def test_rejects_zero_workers(self):
        with pytest.raises(ValueError, match="num_parallel_workers"):
            TunerLifecycleConfig(strategy=TuningStrategy.LHS, num_parallel_workers=0)

    def test_defaults(self):
        cfg = TunerLifecycleConfig(strategy=TuningStrategy.PBT)
        assert cfg.knob_tier == "minimal"
        assert cfg.knob_source == "expert"
        assert cfg.use_docker is True
        assert cfg.random_seed == 42
