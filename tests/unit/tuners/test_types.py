"""Tests for src.tuners.utils.types."""

import pytest

from src.tuners.utils.exceptions import TunerConfigError
from src.tuners.utils.types import (
    GenerationOutcome,
    TunerLifecycleConfig,
    TuningStrategy,
)
from src.utils.types import TuningMode


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
        # from_value keeps the enum-coercion ValueError contract.
        with pytest.raises(ValueError, match="Unknown tuning strategy"):
            TuningStrategy.from_value("genetic")


class TestGenerationOutcome:
    def test_defaults(self):
        outcome = GenerationOutcome(index=0)
        assert outcome.best_score_this_generation == 0.0
        assert outcome.converged is False
        assert outcome.payload == {}

    def test_no_redundant_running_best_fields(self):
        # best_score_so_far and num_evaluations are owned by BaseTuner now,
        # not carried per-generation.
        outcome = GenerationOutcome(index=0)
        assert not hasattr(outcome, "best_score_so_far")
        assert not hasattr(outcome, "num_evaluations")

    def test_to_dict_merges_payload(self):
        outcome = GenerationOutcome(
            index=3,
            best_score_this_generation=0.9,
            converged=True,
            payload={"design_size": 16},
        )
        d = outcome.to_dict()
        assert d["generation"] == 3
        assert d["best_score"] == 0.9
        assert d["converged"] is True
        assert d["design_size"] == 16
        assert "best_score_so_far" not in d
        assert "num_evaluations" not in d


class TestTunerLifecycleConfig:
    def test_coerces_strategy_string(self):
        cfg = TunerLifecycleConfig(strategy="lhs")
        assert cfg.strategy is TuningStrategy.LHS

    def test_rejects_zero_workers(self):
        with pytest.raises(TunerConfigError, match="num_parallel_workers"):
            TunerLifecycleConfig(strategy=TuningStrategy.LHS, num_parallel_workers=0)

    def test_rejects_zero_restart_interval(self):
        with pytest.raises(TunerConfigError, match="adaptive_restart_interval"):
            TunerLifecycleConfig(
                strategy=TuningStrategy.LHS, adaptive_restart_interval=0
            )

    def test_defaults(self):
        cfg = TunerLifecycleConfig(strategy=TuningStrategy.PBT)
        assert cfg.knob_tier == "minimal"
        assert cfg.knob_source == "expert"
        assert cfg.use_docker is True
        assert cfg.random_seed == 42

    def test_strategy_agnostic_defaults(self):
        cfg = TunerLifecycleConfig(strategy=TuningStrategy.PBT)
        assert cfg.tuning_mode is TuningMode.OFFLINE
        assert cfg.adaptive_restart_interval == 10
        assert cfg.force_recreate_instances is False
        assert cfg.probe_disk is True
        assert cfg.worker_ram is None
        assert cfg.worker_cpus is None
        assert cfg.worker_disk_read_bps is None
        assert cfg.worker_disk_write_bps is None
        assert cfg.worker_disk_read_iops is None
        assert cfg.worker_disk_write_iops is None
        assert cfg.scoring_policy is None
        assert cfg.scoring_policy_version is None
        assert cfg.metric_reference_version is None

    def test_coerces_tuning_mode_string(self):
        cfg = TunerLifecycleConfig(strategy=TuningStrategy.PBT, tuning_mode="adaptive")
        assert cfg.tuning_mode is TuningMode.ADAPTIVE
