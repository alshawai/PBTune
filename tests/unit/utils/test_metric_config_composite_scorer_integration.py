"""Integration-style tests for MetricConfig and CompositeScorer wiring."""

from __future__ import annotations

import pytest

from src.utils.metrics import MetricConfig, PerformanceMetrics


def _metric(
    latency_p95: float, throughput: float, error_rate: float = 0.0
) -> PerformanceMetrics:
    return PerformanceMetrics(
        latency_p95=latency_p95,
        latency_p99=latency_p95 * 1.2,
        throughput=throughput,
        memory_utilization=0.4,
        error_rate=error_rate,
        latency_variance=max(0.0, latency_p95 / 10.0),
        throughput_variance=max(0.0, throughput / 100.0),
        tail_amplification=1.2,
        memory_pressure=0.2,
        scan_efficiency=0.8,
        buffer_miss_rate=0.1,
    )


def test_compute_score_respects_failure_gate() -> None:
    """Failure-tagged metrics should always produce a zero score."""
    config = MetricConfig.for_oltp()
    config.scoring_policy = "feature_driven_v2"

    baseline = [
        _metric(latency_p95=20 + i, throughput=400 + (i * 10)) for i in range(15)
    ]
    config.update_ranges(baseline)

    crashed = _metric(latency_p95=10, throughput=800)
    crashed.failure_type = "EXECUTION_CRASH"

    score = config.compute_score(crashed).final_score
    assert score == pytest.approx(0.0)


def test_feature_driven_policy_responds_to_workload_features() -> None:
    """Different feature vectors should yield different scores for same metrics."""
    metrics = _metric(latency_p95=40.0, throughput=900.0)
    baseline = [
        _metric(latency_p95=25 + i, throughput=600 + (i * 5)) for i in range(20)
    ]

    read_heavy = MetricConfig.for_oltp()
    read_heavy.scoring_policy = "feature_driven_v2"
    read_heavy.workload_features = {
        "read_ratio": 0.95,
        "write_ratio": 0.05,
        "olap_complexity": 0.2,
        "complexity": 0.2,
    }
    read_heavy.update_ranges(baseline)

    write_heavy = MetricConfig.for_oltp()
    write_heavy.scoring_policy = "feature_driven_v2"
    write_heavy.workload_features = {
        "read_ratio": 0.10,
        "write_ratio": 0.90,
        "olap_complexity": 0.6,
        "complexity": 0.6,
    }
    write_heavy.update_ranges(baseline)

    read_score = read_heavy.compute_score(metrics).final_score
    write_score = write_heavy.compute_score(metrics).final_score

    assert read_score > 0.0
    assert write_score > 0.0
    assert read_score != write_score
