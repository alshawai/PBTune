"""
Unit tests for the evaluate_tuning module.

Coverage:
    - loader.py:     load_tuning_session() — happy path + error cases
    - statistics.py: compute_comparison_statistics() — correctness of
                     Wilcoxon, bootstrap CI, Holm correction, Cohen's d
    - types.py:      Dataclass field validation
    - runner.py:     _metrics_to_score(), _extract_pg_major(),
                     _serialise_result() — isolated from I/O
    - __main__.py:   CLI argument parsing and validation

No Docker, PostgreSQL, or filesystem I/O beyond tmp_path fixtures.
"""

from __future__ import annotations

import json
import copy
import sys
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import MagicMock, patch

import pytest

from src.evaluation.exceptions import (
    DockerEnvironmentError,
    EvaluationError,
    TuningSessionLoadError,
)
from src.evaluation.loader import (
    load_tuning_session,
)
from src.evaluation.runner import (
    ComparisonRunner,
    _extract_pg_major,
    _metrics_to_score,
)
from src.evaluation.statistics import (
    _bootstrap_ci_median,
    _paired_cohens_d,
    _stat_summary,
    _wilcoxon_p,
    compute_comparison_statistics,
)
from src.evaluation.types import (
    ComparisonConfig,
    ComparisonResult,
    ComparisonStatistics,
    PerformanceMetrics,
    RunResult,
    TuningSessionData,
    WorkerResources,
)
from src.utils.rescoring import rescore_metrics_globally


# ===========================================================================
# loader.py tests
# ===========================================================================

class TestLoadTuningSession:
    """Tests for load_tuning_session()."""

    def test_happy_path(self, sample_session_file: Path) -> None:
        """Valid session file loads without error."""
        data = load_tuning_session(sample_session_file)

        assert data.benchmark == "tpch"
        assert data.workload_type == "olap"
        assert data.best_score == pytest.approx(72.3)
        assert "shared_buffers" in data.best_knobs
        assert data.worker_resources.cpu_cores == 1
        assert data.worker_resources.ram_bytes == 1_641_393_356
        assert data.worker_resources.disk_type == "SSD"

    def test_missing_file_raises(self, tmp_path: Path) -> None:
        """Non-existent file raises TuningSessionLoadError."""
        with pytest.raises(TuningSessionLoadError, match="not found"):
            load_tuning_session(tmp_path / "nonexistent.json")

    def test_invalid_json_raises(self, tmp_path: Path) -> None:
        """Malformed JSON raises TuningSessionLoadError."""
        bad = tmp_path / "bad.json"
        bad.write_text("{not valid json", encoding="utf-8")
        with pytest.raises(TuningSessionLoadError, match="Failed to parse"):
            load_tuning_session(bad)

    def test_missing_best_configuration_raises(self, tmp_path: Path) -> None:
        """Missing best_configuration key raises TuningSessionLoadError."""
        data = {
            "tuning_session": {"benchmark": "tpch"},
            "worker_resources": {"ram_bytes": 1000, "cpu_cores": 1, "disk_type": "SSD"},
            # best_configuration intentionally omitted
        }
        p = tmp_path / "missing_best.json"
        p.write_text(json.dumps(data), encoding="utf-8")
        with pytest.raises(TuningSessionLoadError, match="best_configuration"):
            load_tuning_session(p)

    def test_missing_worker_resources_raises(self, tmp_path: Path) -> None:
        """Missing worker_resources key raises TuningSessionLoadError."""
        data = {
            "tuning_session": {"benchmark": "tpch"},
            "best_configuration": {
                "knobs": {"shared_buffers": "128MB"},
                "score": 50.0,
            },
            # worker_resources intentionally omitted
        }
        p = tmp_path / "missing_wr.json"
        p.write_text(json.dumps(data), encoding="utf-8")
        with pytest.raises(TuningSessionLoadError, match="worker_resources"):
            load_tuning_session(p)

    def test_negative_cpu_cores_raises(self, tmp_path: Path, sample_session_file: Path) -> None:
        """Negative cpu_cores raises TuningSessionLoadError."""
        with open(sample_session_file, encoding="utf-8") as f:
            data = json.load(f)
        data["worker_resources"]["cpu_cores"] = -1
        p = tmp_path / "bad_cpu.json"
        p.write_text(json.dumps(data), encoding="utf-8")
        with pytest.raises(TuningSessionLoadError, match="cpu_cores"):
            load_tuning_session(p)

    def test_empty_knobs_raises(self, tmp_path: Path, sample_session_file: Path) -> None:
        """Empty knobs dict raises TuningSessionLoadError."""
        with open(sample_session_file, encoding="utf-8") as f:
            data = json.load(f)
        data["best_configuration"]["knobs"] = {}
        p = tmp_path / "empty_knobs.json"
        p.write_text(json.dumps(data), encoding="utf-8")
        with pytest.raises(TuningSessionLoadError, match="non-empty"):
            load_tuning_session(p)

    @pytest.mark.parametrize("path_segment,expected", [
        ("olap/pbt_runs", "tpch"),
        ("oltp/pbt_runs", "sysbench"),
        ("tpch/results", "tpch"),
    ])
    def test_benchmark_inferred_from_path(
        self,
        tmp_path: Path,
        sample_session_file: Path,
        path_segment: str,
        expected: str,
    ) -> None:
        """Benchmark type inferred from file path when not in metadata."""
        with open(sample_session_file, encoding="utf-8") as f:
            data = json.load(f)
        # Remove benchmark key from tuning_session
        data["tuning_session"].pop("benchmark_name", None)
        if "workload_type" in data["tuning_session"]:
            del data["tuning_session"]["workload_type"]
        # Place file under a path that contains the segment
        nested = tmp_path / path_segment
        nested.mkdir(parents=True, exist_ok=True)
        p = nested / "pbt_results_test.json"
        p.write_text(json.dumps(data), encoding="utf-8")

        result = load_tuning_session(p)
        assert result.benchmark == expected

    def test_runtime_metadata_is_normalized_for_evaluation(
        self,
        tmp_path: Path,
        sample_session_file: Path,
    ) -> None:
        """Legacy timing keys should be normalized into canonical evaluation keys."""
        with open(sample_session_file, encoding="utf-8") as f:
            data = json.load(f)

        data["tuning_session"]["evaluation_duration"] = 45
        data["tuning_session"]["warmup_duration"] = 12
        data["tuning_session"]["warmup_passes"] = 2
        p = tmp_path / "normalized.json"
        p.write_text(json.dumps(data), encoding="utf-8")

        result = load_tuning_session(p)
        assert result.tuning_config["sysbench_duration_seconds"] == 45
        assert result.tuning_config["sysbench_warmup_seconds"] == 12
        assert result.tuning_config["tpch_warmup_passes"] == 2


# ===========================================================================
# statistics.py tests
# ===========================================================================

class TestStatisticalPrimitives:
    """Tests for individual statistical functions."""

    def test_stat_summary_basic(self) -> None:
        """_stat_summary computes correct mean, std, median, IQR."""
        values = [1.0, 2.0, 3.0, 4.0, 5.0]
        s = _stat_summary(values)
        assert s.mean == pytest.approx(3.0)
        assert s.median == pytest.approx(3.0)
        assert s.iqr_lower == pytest.approx(2.0)
        assert s.iqr_upper == pytest.approx(4.0)
        assert s.values == values

    def test_stat_summary_single_value(self) -> None:
        """Single-element list produces std=0 without errors."""
        s = _stat_summary([42.0])
        assert s.mean == pytest.approx(42.0)
        assert s.std == pytest.approx(0.0)

    def test_wilcoxon_all_same_direction(self) -> None:
        """All differences in same direction → significant at α=0.05."""
        import numpy as np
        # All 5 differences positive → minimum p-value for N=5 Wilcoxon
        diffs = np.array([5.0, 3.0, 7.0, 4.0, 6.0])
        p = _wilcoxon_p(diffs)
        assert p < 0.1   # Should be clearly significant for N=5

    def test_wilcoxon_all_zero(self) -> None:
        """All-zero differences → p=1.0 (no effect)."""
        import numpy as np
        diffs = np.zeros(5)
        p = _wilcoxon_p(diffs)
        assert p == pytest.approx(1.0)

    def test_wilcoxon_mixed_directions(self) -> None:
        """Mixed direction differences → high p-value (not significant)."""
        import numpy as np
        diffs = np.array([5.0, -5.0, 3.0, -3.0, 1.0])
        p = _wilcoxon_p(diffs)
        assert p > 0.2  # Not significant

    def test_bootstrap_ci_contains_median(self) -> None:
        """Bootstrap 95% CI should contain the true sample median."""
        import numpy as np
        rng = np.random.default_rng(0)
        diffs = rng.normal(10.0, 1.0, size=5)
        lo, hi = _bootstrap_ci_median(diffs, n_bootstrap=1000)
        assert lo < np.median(diffs) < hi

    def test_bootstrap_ci_width_reasonable(self) -> None:
        """CI should be non-zero width for non-constant differences."""
        import numpy as np
        diffs = np.array([8.0, 10.0, 9.0, 11.0, 7.0])
        lo, hi = _bootstrap_ci_median(diffs, n_bootstrap=1000)
        assert hi > lo

    def test_cohens_d_large_effect(self) -> None:
        """Large consistent improvement → |d| > 0.8."""
        import numpy as np
        # All differences large and consistent
        diffs = np.array([20.0, 22.0, 19.0, 21.0, 23.0])
        d = _paired_cohens_d(diffs)
        assert abs(d) > 0.8

    def test_cohens_d_zero_effect(self) -> None:
        """All-zero differences → d=0.0."""
        import numpy as np
        diffs = np.zeros(5)
        d = _paired_cohens_d(diffs)
        assert d == pytest.approx(0.0)


class TestComputeComparisonStatistics:
    """Integration tests for compute_comparison_statistics()."""

    def test_clear_improvement_detected(
        self,
        default_runs: list[RunResult],
        tuned_runs: list[RunResult],
    ) -> None:
        """Clear improvement in all metrics → positive overall improvement."""
        stats = compute_comparison_statistics(default_runs, tuned_runs, benchmark="sysbench")
        assert stats.overall_improvement_pct > 0.0

    def test_primary_alpha_correct(
        self,
        default_runs: list[RunResult],
        tuned_runs: list[RunResult],
    ) -> None:
        """Primary endpoint is tested at alpha=0.05 without family correction."""
        stats = compute_comparison_statistics(default_runs, tuned_runs, benchmark="sysbench")
        assert stats.alpha == pytest.approx(0.05)

    def test_returns_primary_plus_three_secondary_metrics(
        self,
        default_runs: list[RunResult],
        tuned_runs: list[RunResult],
    ) -> None:
        """Statistics include score + benchmark latency + throughput + memory."""
        stats = compute_comparison_statistics(default_runs, tuned_runs, benchmark="sysbench")
        metric_names = {mc.metric_name for mc in stats.metrics}
        assert metric_names == {"score", "latency_p95", "throughput", "memory_utilization"}

    def test_latency_higher_is_better_flag(
        self,
        default_runs: list[RunResult],
        tuned_runs: list[RunResult],
    ) -> None:
        """latency_p95 should have higher_is_better=False."""
        stats = compute_comparison_statistics(default_runs, tuned_runs, benchmark="sysbench")
        latency_mc = next(m for m in stats.metrics if m.metric_name == "latency_p95")
        assert latency_mc.higher_is_better is False

    def test_memory_utilization_treated_as_lower_is_better(self, make_run_result) -> None:
        """Memory utilization should be penalized when tuned uses more memory."""
        default_runs = [
            make_run_result(
                "default",
                i,
                score=50.0,
                memory_utilization=0.10,
            )
            for i in range(1, 6)
        ]
        tuned_runs = [
            make_run_result(
                "tuned",
                i,
                score=50.0,
                memory_utilization=0.20,
            )
            for i in range(1, 6)
        ]

        stats = compute_comparison_statistics(default_runs, tuned_runs, benchmark="sysbench")
        memory_mc = next(m for m in stats.metrics if m.metric_name == "memory_utilization")

        assert memory_mc.higher_is_better is False
        assert memory_mc.improvement_pct < 0.0

    def test_tpch_uses_latency_p99_endpoint(
        self,
        default_runs: list[RunResult],
        tuned_runs: list[RunResult],
    ) -> None:
        """TPC-H statistical endpoint uses latency_p99 instead of p95."""
        stats = compute_comparison_statistics(default_runs, tuned_runs, benchmark="tpch")
        metric_names = {mc.metric_name for mc in stats.metrics}
        assert "latency_p99" in metric_names
        assert "latency_p95" not in metric_names

    def test_ci_is_ordered(
        self,
        default_runs: list[RunResult],
        tuned_runs: list[RunResult],
    ) -> None:
        """Bootstrap CI lower bound < upper bound for all metrics."""
        stats = compute_comparison_statistics(default_runs, tuned_runs, benchmark="sysbench")
        for mc in stats.metrics:
            lo, hi = mc.improvement_ci
            assert lo <= hi, f"CI inverted for {mc.metric_name}: [{lo}, {hi}]"

    def test_empty_runs_raises(self) -> None:
        """Empty run list raises ValueError."""
        with pytest.raises(ValueError, match="non-empty"):
            compute_comparison_statistics([], [], benchmark="sysbench")

    def test_mismatched_lengths_raise(
        self,
        default_runs: list[RunResult],
        tuned_runs: list[RunResult],
        make_run_result,
    ) -> None:
        """Mismatched lengths should fail to protect pair integrity."""
        extra = default_runs + [make_run_result("default", 6)]
        with pytest.raises(ValueError, match="equal length"):
            compute_comparison_statistics(extra, tuned_runs, benchmark="sysbench")

    def test_no_improvement_not_significant(self, make_run_result) -> None:
        """When both configs produce identical scores → not significant."""
        same_default = [make_run_result("default", i, score=50.0) for i in range(1, 6)]
        same_tuned = [make_run_result("tuned", i, score=50.0) for i in range(1, 6)]
        stats = compute_comparison_statistics(same_default, same_tuned, benchmark="sysbench")
        score_mc = next(m for m in stats.metrics if m.metric_name == "score")
        assert not score_mc.significant

    def test_mismatched_pair_keys_raise(
        self,
        default_runs: list[RunResult],
        tuned_runs: list[RunResult],
    ) -> None:
        """Pair IDs and seeds must align exactly before paired statistics."""
        tuned_runs[0].pair_seed = 9999

        with pytest.raises(ValueError, match="not aligned"):
            compute_comparison_statistics(default_runs, tuned_runs, benchmark="sysbench")

    def test_statistics_metadata_includes_power_warning_for_n5(
        self,
        default_runs: list[RunResult],
        tuned_runs: list[RunResult],
    ) -> None:
        stats = compute_comparison_statistics(default_runs, tuned_runs, benchmark="sysbench")

        assert stats.n_pairs == 5
        assert stats.correction_method == "holm_secondary"
        assert stats.secondary_correction_method == "holm"
        assert stats.primary_endpoint == "score"
        assert set(stats.secondary_endpoints) == {
            "latency_p95",
            "throughput",
            "memory_utilization",
        }
        assert stats.power_warning is not None
        assert "0.0625" in stats.power_warning


# ===========================================================================
# runner.py helper tests
# ===========================================================================

class TestRunnerHelpers:
    """Tests for standalone helper functions in runner.py."""

    @pytest.mark.parametrize("pg_str,expected", [
        ("PostgreSQL 16.2", "16"),
        ("PostgreSQL 18.3", "18"),
        ("PostgreSQL 14.0 (Ubuntu)", "14"),
        ("unknown", "16"),
        ("", "16"),
    ])
    def test_extract_pg_major(self, pg_str: str, expected: str) -> None:
        assert _extract_pg_major(pg_str) == expected

    def test_metrics_to_score_sysbench_high_tps(self) -> None:
        """High TPS, low latency → score approaches 100."""
        snap = PerformanceMetrics(
            latency_p50=50.0, latency_p95=80.0, latency_p99=100.0,
            throughput=2000.0, error_rate=0.0, memory_utilization=0.4,
            total_queries=120_000, total_time=60.0,
        )
        score = _metrics_to_score(snap, "sysbench")
        assert 0.0 < score <= 100.0
        assert score > 50.0  # High TPS should give a good score

    def test_metrics_to_score_sysbench_zero_tps(self) -> None:
        """Zero TPS → score = 0."""
        snap = PerformanceMetrics(
            latency_p50=0.0, latency_p95=0.0, latency_p99=0.0,
            throughput=0.0, error_rate=1.0, memory_utilization=0.0,
            total_queries=0, total_time=60.0,
        )
        score = _metrics_to_score(snap, "sysbench")
        assert score == pytest.approx(0.0)

    def test_metrics_to_score_tpch(self) -> None:
        """TPC-H score capped at 100, non-negative."""
        snap = PerformanceMetrics(
            latency_p50=5000.0, latency_p95=8000.0, latency_p99=10000.0,
            throughput=0.5, error_rate=0.0, memory_utilization=0.8,
            total_queries=22, total_time=3600.0,
        )
        score = _metrics_to_score(snap, "tpch")
        assert 0.0 <= score <= 100.0


class TestRescoring:
    """Tests for deterministic post-hoc global rescoring."""

    def test_rescoring_uses_workload_latency_endpoint(
        self,
        default_runs: list[RunResult],
        tuned_runs: list[RunResult],
    ) -> None:
        all_runs = sorted(
            [*default_runs, *tuned_runs],
            key=lambda r: (r.run_number, r.order_in_pair, r.config_type),
        )
        _, scores, metadata = rescore_metrics_globally(
            [r.metrics for r in all_runs],
            benchmark="tpch",
        )
        for run, score in zip(all_runs, scores, strict=True):
            run.score = score

        assert metadata["mode"] == "global_posthoc"
        assert metadata["latency_metric"] == "p99"
        assert metadata["benchmark"] == "tpch"
        assert metadata["n_observations"] == 10

    def test_rescoring_is_deterministic_for_same_inputs(
        self,
        default_runs: list[RunResult],
        tuned_runs: list[RunResult],
    ) -> None:
        default_runs_a = copy.deepcopy(default_runs)
        tuned_runs_a = copy.deepcopy(tuned_runs)
        default_runs_b = copy.deepcopy(default_runs)
        tuned_runs_b = copy.deepcopy(tuned_runs)

        all_runs_a = sorted(
            [*default_runs_a, *tuned_runs_a],
            key=lambda r: (r.run_number, r.order_in_pair, r.config_type),
        )
        all_runs_b = sorted(
            [*default_runs_b, *tuned_runs_b],
            key=lambda r: (r.run_number, r.order_in_pair, r.config_type),
        )

        _, scores_a, _ = rescore_metrics_globally(
            [r.metrics for r in all_runs_a],
            benchmark="sysbench",
        )
        _, scores_b, _ = rescore_metrics_globally(
            [r.metrics for r in all_runs_b],
            benchmark="sysbench",
        )
        for run, score in zip(all_runs_a, scores_a, strict=True):
            run.score = score
        for run, score in zip(all_runs_b, scores_b, strict=True):
            run.score = score

        scores_a = [r.score for r in [*default_runs_a, *tuned_runs_a]]
        scores_b = [r.score for r in [*default_runs_b, *tuned_runs_b]]
        assert scores_a == pytest.approx(scores_b)


class TestBenchmarkParameterResolution:
    """Tests for CLI > session > default benchmark parameter precedence."""

    def _make_session(self, tuning_config: dict[str, object]) -> TuningSessionData:
        return TuningSessionData(
            best_knobs={},
            best_score=10.0,
            worker_resources=WorkerResources(ram_bytes=1_000_000_000, cpu_cores=1, disk_type="SSD"),
            system_info={},
            tuning_config=tuning_config,
            benchmark="sysbench",
            workload_type="oltp",
            session_id="s1",
        )

    def test_session_values_used_when_cli_omits(
        self,
    ) -> None:
        config = ComparisonConfig(
            tuning_session_path=Path("results/oltp/pbt_runs/core/tuning_sessions/session.json"),
            benchmark="sysbench",
        )
        runner = ComparisonRunner(config)
        session = self._make_session(
            {
                "sysbench_duration_seconds": 75,
                "sysbench_warmup_seconds": 9,
                "sysbench_tables": 22,
                "sysbench_table_size": 123456,
                "tpch_warmup_passes": 2,
                "scale_factor": 3.0,
            }
        )

        resolved = runner._resolve_effective_benchmark_params(session, benchmark="sysbench")

        assert resolved["sysbench_duration"] == 75
        assert resolved["sysbench_warmup_seconds"] == 9
        assert resolved["sysbench_tables"] == 22
        assert resolved["sysbench_table_size"] == 123456
        assert resolved["tpch_warmup_passes"] == 2
        assert resolved["scale_factor"] == pytest.approx(3.0)

    def test_cli_values_override_session(
        self,
    ) -> None:
        config = ComparisonConfig(
            tuning_session_path=Path("results/oltp/pbt_runs/core/tuning_sessions/session.json"),
            benchmark="sysbench",
            sysbench_duration=99,
            sysbench_warmup_seconds=14,
            sysbench_tables=11,
            sysbench_table_size=222222,
            tpch_warmup_passes=4,
            scale_factor=7.0,
        )
        runner = ComparisonRunner(config)
        session = self._make_session(
            {
                "sysbench_duration_seconds": 10,
                "sysbench_warmup_seconds": 3,
                "sysbench_tables": 2,
                "sysbench_table_size": 1000,
                "tpch_warmup_passes": 1,
                "scale_factor": 1.0,
            }
        )

        resolved = runner._resolve_effective_benchmark_params(session, benchmark="sysbench")

        assert resolved["sysbench_duration"] == 99
        assert resolved["sysbench_warmup_seconds"] == 14
        assert resolved["sysbench_tables"] == 11
        assert resolved["sysbench_table_size"] == 222222
        assert resolved["tpch_warmup_passes"] == 4
        assert resolved["scale_factor"] == pytest.approx(7.0)


class TestDockerPrerequisites:
    """Tests for Docker preflight checks in ComparisonRunner."""

    @staticmethod
    def _fake_docker_module() -> tuple[SimpleNamespace, SimpleNamespace]:
        image_not_found = type("ImageNotFound", (Exception,), {})
        api_error = type("APIError", (Exception,), {})
        docker_exception = type("DockerException", (Exception,), {})
        errors = SimpleNamespace(
            ImageNotFound=image_not_found,
            APIError=api_error,
            DockerException=docker_exception,
        )
        client = MagicMock()
        client.ping.return_value = None
        module = SimpleNamespace(from_env=MagicMock(return_value=client), errors=errors)
        return module, client

    def test_preflight_noop_when_docker_disabled(self, sample_session_file: Path) -> None:
        config = ComparisonConfig(
            tuning_session_path=sample_session_file,
            benchmark="sysbench",
            use_docker=False,
        )
        runner = ComparisonRunner(config)

        runner._validate_docker_prerequisites()

    def test_preflight_raises_with_build_hint_when_image_missing(
        self,
        sample_session_file: Path,
    ) -> None:
        config = ComparisonConfig(
            tuning_session_path=sample_session_file,
            benchmark="sysbench",
            use_docker=True,
            docker_image="pbt-eval",
        )
        runner = ComparisonRunner(config)

        fake_docker, fake_client = self._fake_docker_module()
        fake_client.images.get.side_effect = fake_docker.errors.ImageNotFound()
        fake_client.images.pull.side_effect = fake_docker.errors.ImageNotFound()

        with patch.dict(sys.modules, {"docker": fake_docker}):
            with pytest.raises(DockerEnvironmentError, match="docker build -f docker/eval\\.Dockerfile"):
                runner._validate_docker_prerequisites()

        fake_client.close.assert_called_once()

    def test_preflight_accepts_image_after_successful_pull(
        self,
        sample_session_file: Path,
    ) -> None:
        config = ComparisonConfig(
            tuning_session_path=sample_session_file,
            benchmark="sysbench",
            use_docker=True,
            docker_image="example/eval:latest",
        )
        runner = ComparisonRunner(config)

        fake_docker, fake_client = self._fake_docker_module()
        fake_client.images.get.side_effect = fake_docker.errors.ImageNotFound()
        fake_client.images.pull.return_value = object()

        with patch.dict(sys.modules, {"docker": fake_docker}):
            runner._validate_docker_prerequisites()

        fake_client.images.pull.assert_called_once_with("example/eval:latest")
        fake_client.close.assert_called_once()


class TestOutputPathResolution:
    """Tests for ComparisonRunner output directory contract."""

    @staticmethod
    def _build_result(
        config: ComparisonConfig,
        workload_type: str,
        tuning_config: dict[str, object] | None = None,
    ) -> ComparisonResult:
        session = TuningSessionData(
            best_knobs={},
            best_score=0.0,
            worker_resources=WorkerResources(
                ram_bytes=1_000_000_000,
                cpu_cores=1,
                disk_type="SSD",
            ),
            system_info={},
            tuning_config=tuning_config or {},
            benchmark="sysbench",
            workload_type=workload_type,
            session_id="20260411_120000",
        )
        stats = ComparisonStatistics(
            metrics=[],
            significant_metrics=[],
            overall_improvement_pct=0.0,
            overall_improvement_ci=(0.0, 0.0),
        )
        return ComparisonResult(
            default_runs=[],
            tuned_runs=[],
            tuned_knobs={},
            statistics=stats,
            config=config,
            session_data=session,
            timestamp="20260411_120000",
        )

    def test_default_output_dir_uses_workload_type(self) -> None:
        config = ComparisonConfig(
            tuning_session_path=Path("results/olap/pbt_runs/extensive/tuning_sessions/session.json"),
            benchmark="sysbench",
            output_dir=None,
        )
        runner = ComparisonRunner(config)
        result = self._build_result(config, workload_type="olap")

        assert runner._resolve_output_dir(result) == (
            Path("results") / "olap" / "comparisons" / "extensive"
        )

    def test_metadata_tier_preferred_over_path_tier(self) -> None:
        config = ComparisonConfig(
            tuning_session_path=Path("results/olap/pbt_runs/extensive/tuning_sessions/session.json"),
            benchmark="sysbench",
            output_dir=None,
        )
        runner = ComparisonRunner(config)
        result = self._build_result(
            config,
            workload_type="olap",
            tuning_config={"knob_tier": "core"},
        )

        assert runner._resolve_output_dir(result) == (
            Path("results") / "olap" / "comparisons" / "core"
        )

    def test_metadata_tier_field_supported(self) -> None:
        config = ComparisonConfig(
            tuning_session_path=Path("results/olap/pbt_runs/extensive/tuning_sessions/session.json"),
            benchmark="sysbench",
            output_dir=None,
        )
        runner = ComparisonRunner(config)
        result = self._build_result(
            config,
            workload_type="olap",
            tuning_config={"tier": "minimal"},
        )

        assert runner._resolve_output_dir(result) == (
            Path("results") / "olap" / "comparisons" / "minimal"
        )

    def test_unknown_workload_falls_back_to_mixed(self) -> None:
        config = ComparisonConfig(
            tuning_session_path=Path("results/unknown/session.json"),
            benchmark="sysbench",
            output_dir=None,
        )
        runner = ComparisonRunner(config)
        result = self._build_result(config, workload_type="custom")

        assert runner._resolve_output_dir(result) == (
            Path("results") / "mixed" / "comparisons" / "unknown"
        )

    def test_custom_output_dir_is_used_as_is(self, tmp_path: Path) -> None:
        custom_output = tmp_path / "custom-comparisons"
        config = ComparisonConfig(
            tuning_session_path=Path("results/oltp/session.json"),
            benchmark="sysbench",
            output_dir=custom_output,
        )
        runner = ComparisonRunner(config)
        result = self._build_result(config, workload_type="oltp")

        assert runner._resolve_output_dir(result) == custom_output

    def test_log_output_path_uses_logs_subdir(self) -> None:
        config = ComparisonConfig(
            tuning_session_path=Path("results/oltp/session.json"),
            benchmark="sysbench",
            output_dir=Path("results/oltp/comparisons/core"),
        )
        runner = ComparisonRunner(config)

        log_path = runner._resolve_log_output_path(config.output_dir, "20260412_090000")

        assert log_path == (
            Path("results/oltp/comparisons/core") / "logs" / "evaluation_20260412_090000.html"
        )


class TestTunedKnobResolution:
    """Tests for converting serialized tuned knob fractions to absolute values."""

    def test_hardware_relative_knobs_are_resolved_from_fractions(self) -> None:
        config = ComparisonConfig(
            tuning_session_path=Path("results/oltp/pbt_runs/core/tuning_sessions/session.json"),
            benchmark="sysbench",
        )
        runner = ComparisonRunner(config)

        session = TuningSessionData(
            best_knobs={
                "shared_buffers": 0.25,
                "max_worker_processes": 0.75,
                "random_page_cost": 1.2,
            },
            best_score=50.0,
            worker_resources=WorkerResources(
                ram_bytes=2_147_483_648,
                cpu_cores=8,
                disk_type="SSD",
            ),
            system_info={},
            tuning_config={"tier": "core"},
            benchmark="sysbench",
            workload_type="oltp",
            session_id="20260412_090000",
        )

        resolved = runner._resolve_tuned_knobs(session)

        assert isinstance(resolved["shared_buffers"], int)
        assert resolved["shared_buffers"] >= 16
        assert isinstance(resolved["max_worker_processes"], int)
        assert resolved["max_worker_processes"] >= 1
        assert resolved["random_page_cost"] == pytest.approx(1.2)


# ===========================================================================
# CLI (__main__.py) tests
# ===========================================================================

class TestCLI:
    """Tests for the CLI argument parser (main() with mocked runner)."""

    def test_missing_session_exits_nonzero(self) -> None:
        """Calling without --session should exit with error via SystemExit."""
        from src.evaluation.__main__ import main
        with pytest.raises(SystemExit) as exc_info:
            main(["--repetitions", "5"])   # Missing --session
        assert exc_info.value.code != 0

    def test_repetitions_less_than_2_exits_nonzero(self, tmp_path: Path) -> None:
        """--repetitions 1 should fail validation."""
        fake = tmp_path / "s.json"
        fake.write_text("{}", encoding="utf-8")
        from src.evaluation.__main__ import main
        # argparse.error() calls sys.exit(2) which pytest catches as SystemExit
        with pytest.raises(SystemExit) as exc_info:
            main(["--session", str(fake), "--repetitions", "1"])
        assert exc_info.value.code != 0

    def test_successful_run_returns_zero(
        self,
        sample_session_file: Path,
    ) -> None:
        """With a mocked ComparisonRunner, CLI should return 0."""
        mock_result = MagicMock()
        with patch(
            "src.evaluation.__main__.ComparisonRunner"
        ) as MockRunner:
            MockRunner.return_value.run.return_value = mock_result
            from src.evaluation.__main__ import main
            rc = main([
                "--session", str(sample_session_file),
                "--repetitions", "5",
                "--no-docker",
            ])
        assert rc == 0

    def test_evaluation_error_returns_one(
        self,
        sample_session_file: Path,
    ) -> None:
        """EvaluationError from runner → exit code 1."""
        with patch(
            "src.evaluation.__main__.ComparisonRunner"
        ) as MockRunner:
            MockRunner.return_value.run.side_effect = EvaluationError("fail")
            from src.evaluation.__main__ import main
            rc = main([
                "--session", str(sample_session_file),
                "--no-docker",
            ])
        assert rc == 1

    def test_no_docker_flag_sets_use_docker_false(
        self,
        sample_session_file: Path,
    ) -> None:
        """--no-docker flag sets use_docker=False in ComparisonConfig."""
        captured_config = {}
        with patch(
            "src.evaluation.__main__.ComparisonRunner"
        ) as MockRunner:
            def capture(config):
                captured_config["use_docker"] = config.use_docker
                m = MagicMock()
                m.run.return_value = MagicMock()
                return m
            MockRunner.side_effect = capture
            from src.evaluation.__main__ import main
            main(["--session", str(sample_session_file), "--no-docker"])

        assert captured_config.get("use_docker") is False

    def test_legacy_warmup_flag_rejected(self, sample_session_file: Path) -> None:
        """Generic --warmup flag is removed; benchmark-specific flags must be used."""
        from src.evaluation.__main__ import main

        with pytest.raises(SystemExit) as exc_info:
            main([
                "--session", str(sample_session_file),
                "--warmup", "30",
            ])
        assert exc_info.value.code != 0

    def test_seed_and_sysbench_flags_propagate_to_config(
        self,
        sample_session_file: Path,
    ) -> None:
        """CLI benchmark-specific flags should map to ComparisonConfig fields."""
        captured_config: dict[str, object] = {}

        with patch("src.evaluation.__main__.ComparisonRunner") as MockRunner:
            def capture(config):
                captured_config["pair_seed"] = config.pair_seed
                captured_config["sysbench_duration"] = config.sysbench_duration
                captured_config["sysbench_warmup_seconds"] = config.sysbench_warmup_seconds
                captured_config["sysbench_tables"] = config.sysbench_tables
                captured_config["sysbench_table_size"] = config.sysbench_table_size
                m = MagicMock()
                m.run.return_value = MagicMock()
                return m

            MockRunner.side_effect = capture
            from src.evaluation.__main__ import main

            main([
                "--session", str(sample_session_file),
                "--seed", "777",
                "--sysbench-duration", "75",
                "--sysbench-warmup-seconds", "12",
                "--sysbench-tables", "16",
                "--sysbench-table-size", "200000",
            ])

        assert captured_config["pair_seed"] == 777
        assert captured_config["sysbench_duration"] == 75
        assert captured_config["sysbench_warmup_seconds"] == 12
        assert captured_config["sysbench_tables"] == 16
        assert captured_config["sysbench_table_size"] == 200000

    def test_cli_defaults_are_owned_by_python_entrypoint(
        self,
        sample_session_file: Path,
    ) -> None:
        """Default values should come from python CLI contract, not wrapper logic."""
        captured_config: dict[str, object] = {}

        with patch("src.evaluation.__main__.ComparisonRunner") as MockRunner:
            def capture(config):
                captured_config["repetitions"] = config.repetitions
                captured_config["benchmark"] = config.benchmark
                captured_config["use_docker"] = config.use_docker
                captured_config["output_dir"] = config.output_dir
                captured_config["sysbench_duration"] = config.sysbench_duration
                captured_config["sysbench_warmup_seconds"] = config.sysbench_warmup_seconds
                captured_config["tpch_warmup_passes"] = config.tpch_warmup_passes
                captured_config["pair_seed"] = config.pair_seed
                m = MagicMock()
                m.run.return_value = MagicMock()
                return m

            MockRunner.side_effect = capture
            from src.evaluation.__main__ import main

            main(["--session", str(sample_session_file)])

        assert captured_config["repetitions"] == 5
        assert captured_config["benchmark"] is None
        assert captured_config["use_docker"] is True
        assert captured_config["output_dir"] is None
        assert captured_config["sysbench_duration"] is None
        assert captured_config["sysbench_warmup_seconds"] is None
        assert captured_config["tpch_warmup_passes"] is None
        assert captured_config["pair_seed"] == 50_000
