"""Tests for src.tuners.utils.output_paths."""

from pathlib import Path

import pytest

from src.tuners.utils.output_paths import resolve_tuner_output_root
from src.tuners.utils.types import TuningStrategy


class TestResolveTunerOutputRoot:
    def test_sysbench_workload_as_single_segment(self):
        path = resolve_tuner_output_root(
            "results",
            strategy=TuningStrategy.LHS,
            workload="oltp_read_write",
            knob_tier="core",
        )
        assert path == Path("results/sessions/oltp_read_write/lhs/core")

    def test_olap_workload(self):
        path = resolve_tuner_output_root(
            "results",
            strategy=TuningStrategy.PBT,
            workload="olap",
            knob_tier="minimal",
        )
        assert path == Path("results/sessions/olap/pbt/minimal")

    def test_data_driven_tier_gets_scalpel_suffix(self):
        path = resolve_tuner_output_root(
            "results",
            strategy=TuningStrategy.BO,
            workload="olap",
            knob_tier="standard",
            knob_source="data_driven",
        )
        assert path == Path("results/sessions/olap/bo/standard@scalpel-v1")

    def test_strategy_string_coerced(self):
        path = resolve_tuner_output_root(
            "results",
            strategy="lhs",
            workload="olap",
            knob_tier="core",
        )
        assert "lhs" in str(path)
        assert "sessions" in str(path)

    @pytest.mark.parametrize(
        "strategy,segment",
        [
            (TuningStrategy.PBT, "pbt"),
            (TuningStrategy.BO, "bo"),
            (TuningStrategy.LHS, "lhs"),
        ],
    )
    def test_strategy_segment(self, strategy, segment):
        path = resolve_tuner_output_root(
            "results",
            strategy=strategy,
            workload="olap",
            knob_tier="core",
        )
        assert f"/sessions/olap/{segment}/core" in str(path)

    def test_ablation_subpath(self):
        path = resolve_tuner_output_root(
            "results",
            strategy=TuningStrategy.PBT,
            workload="oltp_read_write",
            knob_tier="extensive",
            ablation_variable="population_size",
            ablation_value="4",
        )
        assert path == Path(
            "results/sessions/oltp_read_write/pbt/extensive/ablations/population_size/4"
        )

    def test_ablation_without_value_ignored(self):
        path = resolve_tuner_output_root(
            "results",
            strategy=TuningStrategy.PBT,
            workload="oltp_read_write",
            knob_tier="extensive",
            ablation_variable="population_size",
            ablation_value=None,
        )
        assert "ablations" not in str(path)

    def test_sessions_prefix_always_present(self):
        path = resolve_tuner_output_root(
            "results",
            strategy=TuningStrategy.PBT,
            workload="mixed",
            knob_tier="core",
        )
        assert path == Path("results/sessions/mixed/pbt/core")
