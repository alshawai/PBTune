"""Tests for the LHS-design CLI argument parsing and tuner construction."""

from unittest import mock

import pytest

from src.tuners.lhs_design import LHSDesignTuner
from src.tuners.lhs_design import cli as lhs_design_cli
from src.tuners.lhs_design.cli import build_tuner, parse_args
from src.tuners.utils.exceptions import TunerConfigError
from src.tuners.utils.types import TunerLifecycleConfig, TuningStrategy
from src.utils.types import TuningMode


class TestParseArgs:
    def test_defaults(self):
        args = parse_args([])
        assert args.tier == "minimal"
        assert args.benchmark == "sysbench"
        assert args.config == "standard"
        # --design-size / --parallel-workers default to None so "unset" is
        # distinguishable from an explicit value; the profile supplies the
        # concrete default during tuner construction.
        assert args.design_size is None
        assert args.parallel_workers is None

    def test_design_size_default_resolves_via_profile(self):
        # Unset --design-size resolves to the standard-profile value (32).
        tuner = build_tuner(parse_args([]))
        assert tuner.design_size == 32

    def test_parallel_workers_default_resolves_via_profile(self):
        # Unset --parallel-workers resolves to the standard-profile value (4).
        tuner = build_tuner(parse_args([]))
        assert tuner.lifecycle.num_parallel_workers == 4

    def test_overrides(self):
        args = parse_args(
            ["--tier", "core", "--design-size", "64", "--parallel-workers", "4"]
        )
        assert args.tier == "core"
        assert args.design_size == 64
        assert args.parallel_workers == 4


class TestSharedFlagsParse:
    """The shared groups from src.tuners.cli are registered on the parser."""

    def test_resource_and_mode_flags_present(self):
        args = parse_args(
            [
                "--worker-ram",
                "2G",
                "--worker-cpus",
                "3",
                "--no-probe-disk",
                "--tuning-mode",
                "online",
            ]
        )
        assert args.worker_ram == "2G"
        assert args.worker_cpus == 3
        assert args.probe_disk is False
        assert args.tuning_mode == "online"

    def test_probe_disk_defaults_true(self):
        args = parse_args([])
        assert args.probe_disk is True

    def test_scoring_provenance_flags(self):
        args = parse_args(
            [
                "--scoring-policy",
                "feature_driven_v2",
                "--scoring-policy-version",
                "v2.1",
                "--metric-reference-version",
                "v1.0",
            ]
        )
        assert args.scoring_policy == "feature_driven_v2"
        assert args.scoring_policy_version == "v2.1"
        assert args.metric_reference_version == "v1.0"

    def test_disk_io_and_recreate_flags(self):
        args = parse_args(
            [
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
        assert "lhs" in parts
        assert "sessions" in parts
        assert "oltp_read_write" in parts
        assert parts.endswith("core")

    def test_tpch_workload_type(self):
        args = parse_args(["--benchmark", "tpch"])
        tuner = build_tuner(args)
        assert "olap" in str(tuner.output_root)


class TestSharedFlagsThreadIntoLifecycle:
    """Shared flags are materialized onto the lifecycle / benchmark config."""

    def test_tuning_mode_threads_into_lifecycle_and_benchmark(self):
        args = parse_args(["--tuning-mode", "online"])
        tuner = build_tuner(args)
        assert tuner.lifecycle.tuning_mode is TuningMode.ONLINE
        assert tuner.benchmark_config.tuning_mode is TuningMode.ONLINE

    def test_tuning_mode_defaults_to_offline(self):
        tuner = build_tuner(parse_args([]))
        assert tuner.lifecycle.tuning_mode is TuningMode.OFFLINE

    def test_worker_resources_thread_into_lifecycle(self):
        args = parse_args(
            [
                "--worker-ram",
                "2G",
                "--worker-cpus",
                "3",
                "--worker-disk-read-bps",
                "100",
                "--no-probe-disk",
            ]
        )
        lc = build_tuner(args).lifecycle
        assert lc.worker_ram == "2G"
        assert lc.worker_cpus == 3
        assert lc.worker_disk_read_bps == 100
        assert lc.probe_disk is False

    def test_scoring_provenance_threads_into_lifecycle(self):
        args = parse_args(
            [
                "--scoring-policy",
                "feature_driven_v2",
                "--scoring-policy-version",
                "v2.1",
                "--metric-reference-version",
                "v1.0",
            ]
        )
        lc = build_tuner(args).lifecycle
        assert lc.scoring_policy == "feature_driven_v2"
        assert lc.scoring_policy_version == "v2.1"
        assert lc.metric_reference_version == "v1.0"

    def test_force_recreate_and_cleanup_thread_into_lifecycle(self):
        args = parse_args(["--force-recreate-instances", "--cleanup-instances"])
        lc = build_tuner(args).lifecycle
        assert lc.force_recreate_instances is True
        assert lc.cleanup_instances is True

    def test_force_recreate_baseline_threads_into_lifecycle(self):
        args = parse_args(["--force-recreate-baseline"])
        assert build_tuner(args).lifecycle.force_recreate_baseline is True


class TestSnapshotFlags:
    """--enable/--disable-snapshots and --snapshot-restore-interval surface."""

    def test_snapshots_default_unset(self):
        args = parse_args([])
        assert args.enable_snapshots is None
        assert args.snapshot_restore_interval is None

    def test_enable_snapshots_parses_true(self):
        assert parse_args(["--enable-snapshots"]).enable_snapshots is True

    def test_disable_snapshots_parses_false(self):
        assert parse_args(["--disable-snapshots"]).enable_snapshots is False

    def test_default_enables_snapshots_on_lifecycle(self):
        assert build_tuner(parse_args([])).lifecycle.enable_snapshots is True

    def test_disable_snapshots_threads_onto_lifecycle(self):
        lc = build_tuner(parse_args(["--disable-snapshots"])).lifecycle
        assert lc.enable_snapshots is False

    def test_interval_override_threads_onto_lifecycle(self):
        lc = build_tuner(parse_args(["--snapshot-restore-interval", "3"])).lifecycle
        assert lc.snapshot_restore_interval == 3

    @pytest.mark.parametrize(
        "profile,expected",
        [("rapid", 10), ("standard", 5), ("thorough", 1), ("research", 1)],
    )
    def test_interval_default_resolves_via_profile(self, profile, expected):
        lc = build_tuner(parse_args(["--config", profile])).lifecycle
        assert lc.snapshot_restore_interval == expected

    def test_interval_below_one_raises(self):
        with pytest.raises(TunerConfigError):
            TunerLifecycleConfig(
                strategy=TuningStrategy.LHS, snapshot_restore_interval=0
            )


class TestHtmlLogParity:
    """main() attaches an HTML file handler under the run's logs/ subdir."""

    def test_main_attaches_html_handler(self):
        # The shared helper resolves the path and calls add_html_file_logging;
        # patch it at the point of use (src.tuners.cli) rather than in this
        # module, which no longer imports it directly.
        with mock.patch(
            "src.tuners.cli.add_html_file_logging"
        ) as add_html, mock.patch.object(
            LHSDesignTuner, "run", return_value=None
        ):
            lhs_design_cli.main(["--benchmark", "tpch", "--design-size", "4"])
        add_html.assert_called_once()
        log_path = add_html.call_args.kwargs["output_file"]
        # Strategy-agnostic stem: session_{ts}.html (the strategy is encoded
        # in the run's path, matching traces/trace_*.json and best_*.json).
        assert log_path.name.startswith("session_")
        assert log_path.suffix == ".html"
        # Nested under the run's logs/ directory alongside traces/.
        assert log_path.parent.name == "logs"
