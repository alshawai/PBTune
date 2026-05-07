"""Regression tests for MetricConfig normalizer recalibration behavior."""

from __future__ import annotations

from src.utils.metrics import MetricConfig, PerformanceMetrics


def _build_metric(latency_p95: float, throughput: float) -> PerformanceMetrics:
    return PerformanceMetrics(latency_p95=latency_p95, throughput=throughput)


def test_expand_ranges_for_metrics_recalibrates_from_out_of_support_drift() -> None:
    """Out-of-support drift should trigger recalibration and range expansion."""
    config = MetricConfig.for_oltp()

    baseline = [
        _build_metric(latency_p95=10.0 + i, throughput=100.0 + i) for i in range(20)
    ]
    config.update_ranges(baseline)

    old_latency_min = config.latency_min
    old_latency_max = config.latency_max
    old_throughput_min = config.throughput_min
    old_throughput_max = config.throughput_max

    # Force normalizer to allow drift detection with fewer samples
    if config._normalizer is not None:
        config._normalizer.min_samples_for_drift = 10

    # Push drift with enough out-of-support samples to exceed normalizer threshold.
    outliers = [
        _build_metric(latency_p95=500.0 + i, throughput=2500.0 + (i * 10.0))
        for i in range(12)
    ]

    expanded = config.expand_ranges_for_metrics(outliers, expansion_factor=0.25)

    assert expanded is True
    assert config.latency_max > old_latency_max
    assert config.throughput_max > old_throughput_max
    # Lower bounds should remain valid and non-negative.
    assert config.latency_min >= 0.0
    assert config.throughput_min >= 0.0
    assert config.latency_min <= config.latency_max
    assert config.throughput_min <= config.throughput_max

    # Ensure range movement happened after recalibration.
    assert (
        config.latency_min != old_latency_min
        or config.latency_max != old_latency_max
        or config.throughput_min != old_throughput_min
        or config.throughput_max != old_throughput_max
    )


def test_expand_ranges_for_metrics_recalibrates_from_saturation() -> None:
    """Multiple saturated workers should trigger immediate per-metric anchor expansion."""
    config = MetricConfig.for_oltp()

    baseline = [
        _build_metric(latency_p95=10.0 + i, throughput=100.0 + i) for i in range(20)
    ]
    config.update_ranges(baseline)

    old_latency_max = config.latency_max
    old_throughput_min = config.throughput_min
    old_throughput_max = config.throughput_max

    # 4 workers total
    # 2 workers saturate the upper bound of latency (they are way above the historical max)
    # The normalizer drift threshold is 40 samples, so this won't trigger drift natively.
    outliers = [
        _build_metric(latency_p95=500.0, throughput=110.0),
        _build_metric(latency_p95=500.0, throughput=110.0),
        _build_metric(latency_p95=15.0, throughput=110.0),
        _build_metric(latency_p95=15.0, throughput=110.0),
    ]

    expanded = config.expand_ranges_for_metrics(outliers)

    assert expanded is True
    # Latency should be expanded due to saturation
    assert config.latency_max > old_latency_max

    # Throughput was not saturated (all 110 within [100, 119]), so it should NOT be expanded
    assert config.throughput_min == old_throughput_min
    assert config.throughput_max == old_throughput_max
