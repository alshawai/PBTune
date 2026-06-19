"""Tests for the LHS-design CLI argument parsing and tuner construction."""

from src.tuners.lhs_design import LHSDesignTuner
from src.tuners.lhs_design_cli import build_tuner, parse_args
from src.tuners.utils.types import TuningStrategy


class TestParseArgs:
    def test_defaults(self):
        args = parse_args([])
        assert args.tier == "minimal"
        assert args.benchmark == "sysbench"
        assert args.design_size == 32
        assert args.parallel_workers == 1

    def test_overrides(self):
        args = parse_args(
            ["--tier", "core", "--design-size", "64", "--parallel-workers", "4"]
        )
        assert args.tier == "core"
        assert args.design_size == 64
        assert args.parallel_workers == 4


class TestBuildTuner:
    def test_builds_lhs_tuner(self):
        args = parse_args(
            ["--benchmark", "tpch", "--design-size", "8", "--no-docker"]
        )
        tuner = build_tuner(args)
        assert isinstance(tuner, LHSDesignTuner)
        assert tuner.strategy is TuningStrategy.LHS
        assert tuner.design_size == 8
        assert tuner.lifecycle.use_docker is False

    def test_output_root_reflects_strategy_and_benchmark(self):
        args = parse_args(
            [
                "--benchmark",
                "sysbench",
                "--sysbench-workload",
                "oltp_read_write",
                "--tier",
                "core",
            ]
        )
        tuner = build_tuner(args)
        parts = str(tuner.output_root)
        assert "lhs_runs" in parts
        assert "oltp_read_write" in parts
        assert parts.endswith("core")

    def test_tpch_workload_type(self):
        args = parse_args(["--benchmark", "tpch"])
        tuner = build_tuner(args)
        assert "olap" in str(tuner.output_root)
