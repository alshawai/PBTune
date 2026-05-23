"""
Unit tests for the QuantileUtilityNormalizer.
"""

import numpy as np

from src.utils.scoring.normalization import QuantileUtilityNormalizer
from src.utils.metrics import PerformanceMetrics


def test_normalizer_initialization():
    """Test default initialization."""
    normalizer = QuantileUtilityNormalizer()
    assert not normalizer.is_calibrated
    assert normalizer.lower_quantile == 0.05
    assert normalizer.upper_quantile == 0.95
    assert normalizer.calibration_window == 100


def test_normalizer_fit():
    """Test fitting the normalizer with observations."""
    normalizer = QuantileUtilityNormalizer(lower_quantile=0.0, upper_quantile=1.0)

    # Create metrics with simple uniform distribution
    metrics_list = [
        PerformanceMetrics(throughput=0.0, latency_p95=10.0),
        PerformanceMetrics(throughput=2.5, latency_p95=20.0),
        PerformanceMetrics(throughput=5.0, latency_p95=30.0),
        PerformanceMetrics(throughput=7.5, latency_p95=40.0),
        PerformanceMetrics(throughput=10.0, latency_p95=50.0),
    ]

    normalizer.fit(metrics_list)

    assert normalizer.is_calibrated

    # throughput is 'higher_is_better' (heuristic: contains "throughput")
    # 0 -> 0.0 utility, 10 -> 1.0 utility
    assert np.isclose(normalizer.score_metric("throughput", 0.0), 0.0)
    assert np.isclose(normalizer.score_metric("throughput", 10.0), 1.0)
    assert np.isclose(normalizer.score_metric("throughput", 5.0), 0.5)

    # latency is 'lower_is_better' (heuristic: contains "latency")
    # 10 -> 1.0 utility (best), 50 -> 0.0 utility (worst)
    assert np.isclose(normalizer.score_metric("latency_p95", 10.0), 1.0)
    assert np.isclose(normalizer.score_metric("latency_p95", 50.0), 0.0)
    assert np.isclose(normalizer.score_metric("latency_p95", 30.0), 0.5)


def test_normalizer_clipping():
    """Test utilities are clipped to [0, 1]."""
    normalizer = QuantileUtilityNormalizer(lower_quantile=0.0, upper_quantile=1.0)
    metrics_list = [
        PerformanceMetrics(throughput=0.0),
        PerformanceMetrics(throughput=10.0),
    ]
    normalizer.fit(metrics_list)

    assert np.isclose(normalizer.score_metric("throughput", -5.0), 0.0)
    assert np.isclose(normalizer.score_metric("throughput", 15.0), 1.0)


def test_normalizer_uncalibrated_metric():
    """Test scoring an unknown metric returns neutral utility."""
    normalizer = QuantileUtilityNormalizer()
    metrics_list = [
        PerformanceMetrics(throughput=0.0),
        PerformanceMetrics(throughput=10.0),
    ]
    normalizer.fit(metrics_list)

    # Unknown metric should return neutral 0.5
    assert normalizer.score_metric("unknown_metric", 5.0) == 0.5


def test_normalizer_update_and_drift():
    """Test update tracking and drift detection."""
    normalizer = QuantileUtilityNormalizer(
        lower_quantile=0.05,
        upper_quantile=0.95,
        drift_threshold=0.2,
        min_samples_for_drift=10,
    )

    # Initial fit with values 0-10
    metrics_list = [PerformanceMetrics(throughput=float(i)) for i in range(11)]
    normalizer.fit(metrics_list)

    # Update with in-range value
    normalizer.update(PerformanceMetrics(throughput=5.0))
    assert not normalizer.needs_recalibration()

    # Update with many out-of-range values to trigger drift
    # Need at least 10 samples before drift detection kicks in
    for _ in range(15):
        normalizer.update(PerformanceMetrics(throughput=100.0))

    # Should detect drift (>20% out of support)
    assert normalizer.needs_recalibration()


def test_normalizer_state_serialization():
    """Test exporting and importing state."""
    normalizer = QuantileUtilityNormalizer()
    metrics_list = [
        PerformanceMetrics(throughput=0.0),
        PerformanceMetrics(throughput=10.0),
    ]
    normalizer.fit(metrics_list)

    state = normalizer.export_state()
    assert state["is_calibrated"]
    assert "throughput" in state["anchors"]

    # Create new normalizer and load state
    new_normalizer = QuantileUtilityNormalizer()
    new_normalizer.import_state(state)

    assert new_normalizer.is_calibrated
    assert np.isclose(new_normalizer.score_metric("throughput", 5.0), 0.5)


def test_normalizer_score_vector():
    """Test scoring an entire metrics object."""
    normalizer = QuantileUtilityNormalizer(lower_quantile=0.0, upper_quantile=1.0)
    metrics_list = [
        PerformanceMetrics(throughput=0.0, latency_p95=10.0, error_rate=0.0),
        PerformanceMetrics(throughput=10.0, latency_p95=50.0, error_rate=0.1),
    ]
    normalizer.fit(metrics_list)

    metrics = PerformanceMetrics(throughput=5.0, latency_p95=30.0, error_rate=0.05)
    scores = normalizer.score_metrics(metrics)

    assert "throughput" in scores
    assert "latency_p95" in scores
    assert "error_rate" in scores
    assert np.isclose(scores["throughput"], 0.5)
    assert np.isclose(scores["latency_p95"], 0.5)
