"""CLI argument parsing and tuner construction for the BO tuner.

Mirrors ``tests/unit/tuners/lhs_design/test_lhs_design_cli.py``: verifies the
BO argument surface parses, that ``build_tuner`` wires a ``BOTuner`` with the
right strategy/lifecycle, that the output root is strategy/tier scoped, and
that the top-level router dispatches the ``bo`` token here. No Docker, no
PostgreSQL, no SMAC3 — construction only.
"""

from __future__ import annotations

from src.tuners.bo.cli import build_lifecycle_config, build_tuner, parse_args
from src.tuners.bo.config import BOConfig
from src.tuners.bo.tuner import BOTuner
from src.tuners.utils.types import TuningStrategy


class TestParseArgs:
    def test_defaults(self):
        args = parse_args(["--tier", "minimal"])
        assert args.tier == "minimal"
        assert args.config == "standard"
        # "unset" sentinels so the preset / PBT session supplies the concrete
        # value during BOConfig.from_args resolution.
        assert args.iterations is None
        assert args.bo_surrogate is None
        assert args.cotenancy_degree is None

    def test_probe_disk_defaults_true(self):
        args = parse_args(["--tier", "minimal"])
        assert args.probe_disk is True

    def test_no_probe_disk(self):
        args = parse_args(["--tier", "minimal", "--no-probe-disk"])
        assert args.probe_disk is False

    def test_bo_specific_flags(self):
        args = parse_args(
            [
                "--tier",
                "core",
                "--bo-surrogate",
                "gp",
                "--iterations",
                "20",
                "--range-update-interval",
                "8",
                "--cotenancy-degree",
                "3",
            ]
        )
        assert args.tier == "core"
        assert args.bo_surrogate == "gp"
        assert args.iterations == 20
        assert args.range_update_interval == 8
        assert args.cotenancy_degree == 3

    def test_disk_io_flags(self):
        args = parse_args(
            [
                "--tier",
                "minimal",
                "--worker-disk-read-bps",
                "100",
                "--worker-disk-write-bps",
                "200",
                "--worker-disk-read-iops",
                "300",
                "--worker-disk-write-iops",
                "400",
                "--force-recreate-instances",
            ]
        )
        assert args.worker_disk_read_bps == 100
        assert args.worker_disk_write_bps == 200
        assert args.worker_disk_read_iops == 300
        assert args.worker_disk_write_iops == 400
        assert args.force_recreate_instances is True


class TestBuildLifecycleConfig:
    def test_cotenancy_degree_drives_num_parallel_workers(self):
        config = BOConfig(
            n_iterations=10,
            knob_tier="minimal",
            cotenancy_degree=4,
        )
        lifecycle = build_lifecycle_config(config)
        assert lifecycle.strategy is TuningStrategy.BO
        assert lifecycle.num_parallel_workers == 4

    def test_degree_one_yields_single_worker(self):
        config = BOConfig(n_iterations=10, knob_tier="minimal", cotenancy_degree=1)
        lifecycle = build_lifecycle_config(config)
        assert lifecycle.num_parallel_workers == 1

    def test_disable_early_stopping_flows_to_lifecycle(self):
        config = BOConfig(
            n_iterations=10,
            knob_tier="minimal",
            early_stopping_enabled=False,
        )
        lifecycle = build_lifecycle_config(config)
        assert lifecycle.disable_early_stopping is True


class TestBuildTuner:
    def test_builds_bo_tuner(self):
        args = parse_args(["--tier", "minimal", "--no-docker", "--iterations", "5"])
        tuner = build_tuner(args)
        assert isinstance(tuner, BOTuner)
        assert tuner.strategy is TuningStrategy.BO
        assert tuner.lifecycle.use_docker is False
        assert tuner.bo_config.n_iterations == 5

    def test_num_instances_matches_cotenancy_degree(self):
        args = parse_args(["--tier", "minimal", "--cotenancy-degree", "3"])
        tuner = build_tuner(args)
        assert tuner.num_instances == 3
        assert tuner.seeded_config_count == 1

    def test_output_root_reflects_strategy_and_tier(self):
        args = parse_args(["--tier", "core", "--benchmark", "sysbench"])
        tuner = build_tuner(args)
        parts = tuner.output_root.parts
        assert "bo" in parts
        assert "core" in parts


class TestRouterDispatch:
    def test_bo_token_registered(self):
        from src.tuners.__main__ import STRATEGY_MAINS
        from src.tuners.bo.cli import main as bo_main

        assert STRATEGY_MAINS["bo"] is bo_main

    def test_missing_tier_and_session_returns_error(self):
        from src.tuners.bo.cli import main

        # Neither --tier nor --pbt-session → usage error (exit code 2),
        # short-circuits before any environment bring-up.
        assert main([]) == 2
