"""Unit tests for the canonical rescoring home, src.tuners.utils.calibration.

This is the relocated coverage for the former ``src/utils/rescoring.py``
module (ADR-006; the file was lifted into the unified tuners package). Two
public surfaces sit on one private core:

  - :func:`rescore_metrics_globally` — the long-standing flat
    ``(metric_config, scores, metadata)`` contract every downstream consumer
    (evaluation, analysis, visualization) unpacks.
  - :func:`maybe_recalibrate_scores` — the tuner-facing adapter returning a
    :class:`RecalibrationResult` that additionally carries the full per-config
    :class:`ScoreBreakdown` objects for pre-rescored serialization.

These run DB-free: the scoring engine + normalizer are pure functions of the
supplied :class:`PerformanceMetrics`.
"""

from __future__ import annotations

import pytest

from src.tuners.utils.calibration import (
    MIN_OBSERVATIONS_FOR_RECALIBRATION,
    RecalibrationResult,
    maybe_recalibrate_scores,
    rescore_metrics_globally,
    workload_for_benchmark,
)
from src.utils.metrics import MetricConfig, PerformanceMetrics
from src.utils.scoring.contracts import ScoreBreakdown


def _metrics(n: int) -> list[PerformanceMetrics]:
    """n observations with valid (positive) latency + throughput."""
    return [
        PerformanceMetrics(latency_p95=10.0 + i, throughput=100.0 + i * 5.0)
        for i in range(n)
    ]


class TestWorkloadForBenchmark:
    def test_known_benchmarks(self) -> None:
        assert workload_for_benchmark("tpch") == "olap"
        assert workload_for_benchmark("sysbench") == "oltp"

    def test_unknown_benchmark_defaults_to_mixed(self) -> None:
        assert workload_for_benchmark("custom") == "mixed"
        assert workload_for_benchmark("") == "mixed"


class TestRescoreMetricsGlobally:
    def test_flat_contract_preserved(self) -> None:
        """The long-standing surface returns ``(config, List[float], meta)``."""
        metric_config, scores, metadata = rescore_metrics_globally(
            _metrics(5), benchmark="sysbench"
        )
        assert isinstance(metric_config, MetricConfig)
        assert isinstance(scores, list)
        assert all(isinstance(s, float) for s in scores)
        assert len(scores) == 5
        assert metadata["mode"] == "global_posthoc"
        assert metadata["workload"] == "oltp"
        assert metadata["ranges_calibrated"] is True

    def test_workload_overrides_benchmark_mapping(self) -> None:
        _, _, metadata = rescore_metrics_globally(_metrics(4), workload="olap")
        assert metadata["workload"] == "olap"

    def test_below_calibration_floor_uses_default_ranges(self) -> None:
        """Two observations cannot calibrate; metadata flags it, no raise."""
        _, scores, metadata = rescore_metrics_globally(
            _metrics(2), benchmark="sysbench"
        )
        assert len(scores) == 2
        assert metadata["ranges_calibrated"] is False
        assert metadata["n_valid_latency"] == 2

    def test_requires_workload_or_benchmark(self) -> None:
        with pytest.raises(ValueError):
            rescore_metrics_globally(_metrics(5))


class TestMaybeRecalibrate:
    def test_below_floor_skips(self) -> None:
        # Fewer than the floor → unapplied, no exception, no DB/scoring needed.
        result = maybe_recalibrate_scores([], benchmark="sysbench")
        assert isinstance(result, RecalibrationResult)
        assert result.applied is False
        assert result.metric_config is None
        assert result.scores == []
        assert result.breakdowns == []
        assert result.metadata == {}

    def test_floor_constant(self) -> None:
        assert MIN_OBSERVATIONS_FOR_RECALIBRATION == 3

    def test_applied_returns_aligned_scores_and_breakdowns(self) -> None:
        """An applied pass carries flat scores AND per-config breakdowns,
        positionally aligned (breakdowns[i].final_score == scores[i])."""
        result = maybe_recalibrate_scores(_metrics(5), benchmark="sysbench")
        assert result.applied is True
        assert isinstance(result.metric_config, MetricConfig)
        assert len(result.scores) == 5
        assert len(result.breakdowns) == 5
        assert all(isinstance(b, ScoreBreakdown) for b in result.breakdowns)
        assert [b.final_score for b in result.breakdowns] == result.scores
        assert result.metadata["ranges_calibrated"] is True

    def test_applied_matches_flat_engine(self) -> None:
        """The adapter and the flat engine agree on the scores for the same
        observations + provenance."""
        metrics = _metrics(6)
        _, flat_scores, _ = rescore_metrics_globally(metrics, benchmark="sysbench")
        result = maybe_recalibrate_scores(metrics, benchmark="sysbench")
        assert result.scores == flat_scores

    def test_exactly_at_floor_applies(self) -> None:
        result = maybe_recalibrate_scores(
            _metrics(MIN_OBSERVATIONS_FOR_RECALIBRATION), benchmark="sysbench"
        )
        assert result.applied is True
        assert len(result.breakdowns) == MIN_OBSERVATIONS_FOR_RECALIBRATION
