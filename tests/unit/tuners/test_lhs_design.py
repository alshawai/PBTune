"""Unit tests for LHSDesignTuner batch math and header wiring (DB-free)."""

import math

import pytest

from src.tuners.lhs_design import LHSDesignTuner
from src.tuners.utils.types import TunerLifecycleConfig, TuningStrategy
from src.utils.types import STANDARD_BENCHMARK_CONFIG, clone_benchmark_config


def _make_tuner(design_size=8, workers=2, tmp_path=None):
    lifecycle = TunerLifecycleConfig(
        strategy=TuningStrategy.LHS,
        knob_tier="minimal",
        num_parallel_workers=workers,
    )
    return LHSDesignTuner(
        lifecycle,
        benchmark="tpch",
        benchmark_config=clone_benchmark_config(STANDARD_BENCHMARK_CONFIG),
        design_size=design_size,
        timestamp="20260619_1200",
        output_root=tmp_path,
    )


class TestConstruction:
    def test_strategy_forced_to_lhs(self, tmp_path):
        lifecycle = TunerLifecycleConfig(strategy=TuningStrategy.PBT)
        tuner = LHSDesignTuner(
            lifecycle,
            benchmark="tpch",
            benchmark_config=clone_benchmark_config(STANDARD_BENCHMARK_CONFIG),
            design_size=4,
            timestamp="t",
            output_root=tmp_path,
        )
        assert tuner.strategy is TuningStrategy.LHS

    def test_rejects_zero_design(self, tmp_path):
        lifecycle = TunerLifecycleConfig(strategy=TuningStrategy.LHS)
        with pytest.raises(ValueError, match="design_size"):
            LHSDesignTuner(
                lifecycle,
                benchmark="tpch",
                benchmark_config=clone_benchmark_config(STANDARD_BENCHMARK_CONFIG),
                design_size=0,
                timestamp="t",
                output_root=tmp_path,
            )


class TestBatchMath:
    @pytest.mark.parametrize(
        "design_size,workers,expected",
        [
            (8, 2, 4),
            (5, 2, 3),
            (1, 1, 1),
            (10, 4, 3),
            (4, 8, 1),  # more workers than design -> single batch
        ],
    )
    def test_max_generations(self, design_size, workers, expected, tmp_path):
        tuner = _make_tuner(design_size, workers, tmp_path)
        assert tuner.max_generations == expected
        assert tuner.max_generations == max(1, math.ceil(design_size / workers))

    def test_should_stop_after_design_covered(self, tmp_path):
        from src.tuners.utils.types import GenerationOutcome

        tuner = _make_tuner(design_size=5, workers=2, tmp_path=tmp_path)
        tuner.design = list(range(5))  # pretend 5 design points
        # batch 0 covers 2, batch 1 covers 4, batch 2 covers 6 >= 5 -> stop
        assert tuner.should_stop(GenerationOutcome(index=0)) is False
        assert tuner.should_stop(GenerationOutcome(index=1)) is False
        assert tuner.should_stop(GenerationOutcome(index=2)) is True


class TestHeaderProperties:
    def test_benchmark_and_workload_default(self, tmp_path):
        tuner = _make_tuner(tmp_path=tmp_path)
        # Before setup, benchmark_name is unknown and num_knobs is 0.
        assert tuner.benchmark_name == "unknown"
        assert tuner.num_knobs == 0

    def test_best_config_fractions_empty_without_space(self, tmp_path):
        tuner = _make_tuner(tmp_path=tmp_path)
        assert tuner.best_config_fractions({"x": 1}) == {}
