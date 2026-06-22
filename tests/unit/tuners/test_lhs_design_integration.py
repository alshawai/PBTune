"""Integration test for LHSDesignTuner.run() with in-memory fakes.

Exercises the full lifecycle — design sweep, batch evaluation, best-tracking,
session serialization — without Docker or a live PostgreSQL by injecting fake
knob-space, orchestrator, environment, and instances. The point is to verify
the *driver* logic in ``step``/``run`` and the schema-compatible session JSON,
not the real benchmark path.
"""

import json
from types import SimpleNamespace

import pytest

from src.tuners.lhs_design import LHSDesignTuner
from src.tuners.utils.types import TunerLifecycleConfig, TuningStrategy
from src.utils.metrics import PerformanceMetrics
from src.utils.types import STANDARD_BENCHMARK_CONFIG, clone_benchmark_config


class _FakeKnobSpace:
    """Minimal knob space: identity fractions, fixed deterministic design."""

    def __init__(self, design):
        self._design = design
        self.knobs = {"work_mem": object(), "shared_buffers": object()}
        self.worker_resources = None

    def __len__(self):
        return len(self.knobs)

    def resolve_hardware_ranges(self, _resources):
        pass

    def get_default_config(self):
        return {"work_mem": 0.0, "shared_buffers": 0.0}

    def sample_diverse_configs(self, num_samples, seed=None):
        return [dict(c) for c in self._design[:num_samples]]

    def config_to_fractions(self, config):
        return dict(config)


class _FakeScorer:
    def compute_breakdown(self, _metrics):
        return SimpleNamespace(to_dict=lambda: {"composite": 1.0})


class _FakeOrchestrator:
    """Returns a deterministic score keyed on the config's work_mem value."""

    def __init__(self):
        self.scorer = _FakeScorer()

    def evaluate_worker(self, worker, **_kwargs):
        score = float(worker.knob_config.get("work_mem", 0.0))
        metrics = PerformanceMetrics(throughput=score * 10.0)
        timing = SimpleNamespace(to_dict=lambda **kw: {"records": []})
        return metrics, score, False, {}, timing


class _FakeEnv:
    def __init__(self, num):
        self._instances = [SimpleNamespace(port=5440 + i) for i in range(num)]
        self.pg_server_version = "16.0"
        self.stopped = False
        self.cleaned = False

    def setup_instances(self, num_workers, **_kw):
        return self._instances[:num_workers]

    def verify_instances(self):
        pass

    def get_db_config(self, worker_id):
        return SimpleNamespace(port=5440 + worker_id)

    def stop_all(self):
        self.stopped = True

    def cleanup(self, remove_data=False):
        self.cleaned = True


class _FakeLHSTuner(LHSDesignTuner):
    """LHSDesignTuner with setup() replaced by in-memory fakes."""

    def __init__(self, *args, design, **kwargs):
        super().__init__(*args, **kwargs)
        self._injected_design = design

    def collect_metric_history(self):
        """Opt out of the global post-hoc recalibration pass.

        The fake orchestrator's score (== ``work_mem``) is a stub, not the
        output of the real scoring engine, so feeding its metrics through the
        real recalibration rubric would reorder the best against an unrelated
        scale. These tests exercise the run()/step() driver + serialization;
        the recalibration seam has dedicated coverage in
        ``TestRecalibrationWiring`` (unit) and ``TestRecalibrationRun`` below.
        """
        return []

    def setup(self):
        space = _FakeKnobSpace(self._injected_design)
        self.knob_space = space
        self.full_knob_space = space
        self.worker_resources = SimpleNamespace(
            ram_bytes=2048,
            cpu_cores=2,
            disk_type="SSD",
            disk_read_bps=0,
            disk_write_bps=0,
            disk_read_iops=0,
            disk_write_iops=0,
            disk_class="ssd",
        )
        self._benchmark_name = "tpch"
        from src.utils.metrics import WorkloadType

        self._workload_type = WorkloadType.OLAP
        self.metric_config = SimpleNamespace(
            get_scoring_metadata=lambda: {
                "scoring_policy": "fixed_v1",
                "scoring_policy_version": "1.0",
                "metric_reference_version": "v1",
                "workload_features": {},
                "normalization_metadata": {},
            }
        )
        self.orchestrator = _FakeOrchestrator()
        self.env = _FakeEnv(self.lifecycle.num_parallel_workers)
        self._instances = self.env.setup_instances(
            self.lifecycle.num_parallel_workers
        )
        # Empty dict mirrors BaseTuner's default; log_system_info() tolerates
        # it via .get(..., {}) so the Commit C system-info block renders here
        # without a real hardware probe.
        self.system_info = {}
        self.session_environment = SimpleNamespace(to_dict=lambda: {"docker": False})

        # Build the design directly (skip real LHS sampling).
        self.design = [dict(c) for c in self._injected_design[: self.design_size]]


def _make(tmp_path, design, workers):
    lifecycle = TunerLifecycleConfig(
        strategy=TuningStrategy.LHS,
        knob_tier="minimal",
        num_parallel_workers=workers,
    )
    return _FakeLHSTuner(
        lifecycle,
        benchmark="tpch",
        benchmark_config=clone_benchmark_config(STANDARD_BENCHMARK_CONFIG),
        design_size=len(design),
        timestamp="20260619_1200",
        output_root=tmp_path,
        design=design,
    )


# Design where work_mem ascends so the best score is the last point.
_DESIGN = [
    {"work_mem": 0.1, "shared_buffers": 0.5},
    {"work_mem": 0.2, "shared_buffers": 0.5},
    {"work_mem": 0.3, "shared_buffers": 0.5},
    {"work_mem": 0.9, "shared_buffers": 0.5},
    {"work_mem": 0.4, "shared_buffers": 0.5},
]


class TestLHSDesignRun:
    def test_run_evaluates_whole_design(self, tmp_path):
        tuner = _make(tmp_path, _DESIGN, workers=2)
        results = tuner.run()

        # All 5 design points recorded across batches.
        assert len(tuner.design_records) == 5
        indices = sorted(r["design_index"] for r in tuner.design_records)
        assert indices == [0, 1, 2, 3, 4]

    def test_best_config_is_highest_score(self, tmp_path):
        tuner = _make(tmp_path, _DESIGN, workers=2)
        results = tuner.run()
        best = results["best_configuration"]
        assert best["score"] == pytest.approx(0.9)
        assert best["knobs"]["work_mem"] == pytest.approx(0.9)

    def test_session_header_schema(self, tmp_path):
        tuner = _make(tmp_path, _DESIGN, workers=2)
        results = tuner.run()
        session = results["tuning_session"]
        assert session["tuning_strategy"] == "lhs"
        assert session["benchmark_name"] == "tpch"
        assert session["workload_type"] == "olap"
        assert session["design_size"] == 5
        assert session["num_knobs"] == 2
        assert session["timing_schema_version"] == "1.1"

    def test_session_written_to_disk(self, tmp_path):
        tuner = _make(tmp_path, _DESIGN, workers=2)
        tuner.run()
        session_file = (
            tmp_path / "tuning_sessions" / "lhs_results_20260619_1200.json"
        )
        assert session_file.exists()
        loaded = json.loads(session_file.read_text())
        assert loaded["tuning_session"]["tuning_strategy"] == "lhs"
        assert len(loaded["design_records"]) == 5

    def test_teardown_stops_env(self, tmp_path):
        tuner = _make(tmp_path, _DESIGN, workers=2)
        tuner.run()
        assert tuner.env.stopped is True

    def test_single_worker_serial_path(self, tmp_path):
        tuner = _make(tmp_path, _DESIGN, workers=1)
        results = tuner.run()
        assert len(tuner.design_records) == 5
        assert results["best_configuration"]["score"] == pytest.approx(0.9)

    def test_design_records_carry_fractions_and_metrics(self, tmp_path):
        tuner = _make(tmp_path, _DESIGN, workers=2)
        tuner.run()
        rec = tuner.design_records[0]
        assert "config" in rec and "work_mem" in rec["config"]
        assert "metrics" in rec and rec["metrics"]["throughput"] is not None


class _RealMetricOrchestrator:
    """Emits real PerformanceMetrics (valid latency + throughput) so the
    global recalibration pass can rescore them through the real engine."""

    def __init__(self):
        self.scorer = _FakeScorer()

    def evaluate_worker(self, worker, **_kwargs):
        wm = float(worker.knob_config.get("work_mem", 0.0))
        # Lower latency + higher throughput as work_mem rises.
        metrics = PerformanceMetrics(
            latency_p95=100.0 - wm * 50.0,
            throughput=100.0 + wm * 100.0,
        )
        timing = SimpleNamespace(to_dict=lambda **kw: {"records": []})
        return metrics, wm, False, {}, timing


class _RecalLHSTuner(_FakeLHSTuner):
    """Fake tuner that KEEPS the real recalibration pass enabled."""

    def collect_metric_history(self):
        # Re-enable the real history surface (parent fake disabled it).
        return LHSDesignTuner.collect_metric_history(self)

    def setup(self):
        super().setup()
        # Swap in a real MetricConfig + real-metric orchestrator so the
        # post-hoc recalibration genuinely calibrates and rescores.
        from src.utils.metrics import create_metric_config

        self.metric_config = create_metric_config("olap")
        self.orchestrator = _RealMetricOrchestrator()


class TestRecalibrationRun:
    """The run() lifecycle applies global post-hoc recalibration before
    serialization, and the rescored values are internally consistent."""

    def _make_recal(self, tmp_path, design, workers):
        lifecycle = TunerLifecycleConfig(
            strategy=TuningStrategy.LHS,
            knob_tier="minimal",
            num_parallel_workers=workers,
        )
        return _RecalLHSTuner(
            lifecycle,
            benchmark="tpch",
            benchmark_config=clone_benchmark_config(STANDARD_BENCHMARK_CONFIG),
            design_size=len(design),
            timestamp="20260619_1200",
            output_root=tmp_path,
            design=design,
        )

    def test_recalibration_applied_and_serialized(self, tmp_path):
        tuner = self._make_recal(tmp_path, _DESIGN, workers=2)
        results = tuner.run()

        # The session records that recalibration ran.
        assert results["recalibration"]["applied"] is True
        assert "metadata" in results["recalibration"]
        assert tuner.recalibration.applied is True
        assert len(tuner.recalibration.breakdowns) == 5

        # Every design record carries a rescored breakdown dict.
        for rec in tuner.design_records:
            assert isinstance(rec["score_breakdown"], dict)

        # best_configuration is internally consistent with the rescored
        # records: its score equals the max record score.
        best_record_score = max(r["score"] for r in tuner.design_records)
        assert results["best_configuration"]["score"] == pytest.approx(
            best_record_score
        )

    def test_serialized_session_carries_rescored_scores(self, tmp_path):
        tuner = self._make_recal(tmp_path, _DESIGN, workers=2)
        tuner.run()
        session_file = (
            tmp_path / "tuning_sessions" / "lhs_results_20260619_1200.json"
        )
        loaded = json.loads(session_file.read_text())
        assert loaded["recalibration"]["applied"] is True
        # Each serialized design record has a non-null rescored breakdown.
        assert all(
            r["score_breakdown"] is not None for r in loaded["design_records"]
        )

