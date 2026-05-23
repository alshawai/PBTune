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

    # Get old anchor values from normalizer
    lat_metric = f"latency_{config.latency_metric}"
    if config._normalizer and lat_metric in config._normalizer.anchors:
        _, old_lat_low, old_lat_high = config._normalizer.anchors[lat_metric]
    else:
        old_lat_low, old_lat_high = None, None

    if config._normalizer and "throughput" in config._normalizer.anchors:
        _, old_thr_low, old_thr_high = config._normalizer.anchors["throughput"]
    else:
        old_thr_low, old_thr_high = None, None

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
    # Check that normalizer anchors were expanded
    if old_lat_high is not None:
        _, new_lat_low, new_lat_high = config._normalizer.anchors.get(lat_metric, (1, old_lat_low, old_lat_high))
        assert new_lat_high > old_lat_high

    if old_thr_high is not None:
        _, new_thr_low, new_thr_high = config._normalizer.anchors.get("throughput", (1, old_thr_low, old_thr_high))
        assert new_thr_high > old_thr_high

    # Lower bounds should remain valid and non-negative.
    if old_lat_low is not None:
        _, new_lat_low, _ = config._normalizer.anchors.get(lat_metric, (1, old_lat_low, old_lat_high))
        assert new_lat_low >= 0.0

    if old_thr_low is not None:
        _, new_thr_low, _ = config._normalizer.anchors.get("throughput", (1, old_thr_low, old_thr_high))
        assert new_thr_low >= 0.0


def test_expand_ranges_for_metrics_recalibrates_from_saturation() -> None:
    """Multiple saturated workers should trigger immediate per-metric anchor expansion."""
    config = MetricConfig.for_oltp()

    baseline = [
        _build_metric(latency_p95=10.0 + i, throughput=100.0 + i) for i in range(20)
    ]
    config.update_ranges(baseline)

    # Get old anchor values from normalizer
    lat_metric = f"latency_{config.latency_metric}"
    if config._normalizer and lat_metric in config._normalizer.anchors:
        _, old_lat_low, old_lat_high = config._normalizer.anchors[lat_metric]
    else:
        old_lat_low, old_lat_high = None, None

    if config._normalizer and "throughput" in config._normalizer.anchors:
        _, old_thr_low, old_thr_high = config._normalizer.anchors["throughput"]
    else:
        old_thr_low, old_thr_high = None, None

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
    if old_lat_high is not None:
        _, new_lat_low, new_lat_high = config._normalizer.anchors.get(lat_metric, (1, old_lat_low, old_lat_high))
        assert new_lat_high > old_lat_high

    # Throughput was not saturated (all 110 within [100, 119]), so it should NOT be expanded
    if old_thr_high is not None:
        _, new_thr_low, new_thr_high = config._normalizer.anchors.get("throughput", (1, old_thr_low, old_thr_high))
        assert new_thr_high == old_thr_high
        assert new_thr_low == old_thr_low
