"""Tests for src.tuners.utils.output_paths."""

from pathlib import Path

import pytest

from src.tuners.utils.output_paths import resolve_tuner_output_root
from src.tuners.utils.types import TuningStrategy


class TestResolveTunerOutputRoot:
    def test_sysbench_inserts_workload_segment(self):
        path = resolve_tuner_output_root(
            "results",
            strategy=TuningStrategy.LHS,
            workload_type="oltp",
            benchmark="sysbench",
            sysbench_workload="oltp_read_write",
            knob_tier="core",
        )
        assert path == Path("results/oltp/oltp_read_write/lhs_runs/core")

    def test_tpch_omits_workload_segment(self):
        path = resolve_tuner_output_root(
            "results",
            strategy=TuningStrategy.PBT,
            workload_type="olap",
            benchmark="tpch",
            sysbench_workload="ignored",
            knob_tier="minimal",
        )
        assert path == Path("results/olap/pbt_runs/minimal")

    def test_data_driven_tier_gets_scalpel_suffix(self):
        path = resolve_tuner_output_root(
            "results",
            strategy=TuningStrategy.BO,
            workload_type="olap",
            benchmark="tpch",
            sysbench_workload="ignored",
            knob_tier="standard",
            knob_source="data_driven",
        )
        assert path == Path("results/olap/bo_runs/standard@scalpel-v1")

    def test_strategy_string_coerced(self):
        path = resolve_tuner_output_root(
            "results",
            strategy="lhs",
            workload_type="olap",
            benchmark="tpch",
            sysbench_workload="x",
            knob_tier="core",
        )
        assert "lhs_runs" in str(path)

    @pytest.mark.parametrize(
        "strategy,segment",
        [
            (TuningStrategy.PBT, "pbt_runs"),
            (TuningStrategy.BO, "bo_runs"),
            (TuningStrategy.LHS, "lhs_runs"),
        ],
    )
    def test_runs_segment_per_strategy(self, strategy, segment):
        path = resolve_tuner_output_root(
            "results",
            strategy=strategy,
            workload_type="olap",
            benchmark="tpch",
            sysbench_workload="x",
            knob_tier="core",
        )
        assert segment in str(path)
