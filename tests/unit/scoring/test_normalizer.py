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


def test_expand_anchor_lower_is_better_upper_utility_saturation_lowers_q_low():
    """
    LOWER_IS_BETTER metric saturated at utility=1 (workers clustered at q_low):
    expansion must lower q_low so values stop being clamped to the floor.

    Regression: previously the expansion raised q_high on "upper" utility
    saturation, which left LOWER_IS_BETTER metrics pinned at utility=1.0
    indefinitely (latency_p95 in production logs).
    """
    normalizer = QuantileUtilityNormalizer(lower_quantile=0.05, upper_quantile=0.95)
    history = [PerformanceMetrics(latency_p95=v) for v in np.linspace(4.0, 10.0, 50)]
    normalizer.fit(history)

    _, old_low, old_high = normalizer.anchors["latency_p95"]
    saturating = [PerformanceMetrics(latency_p95=4.2) for _ in range(4)]
    for m in saturating:
        normalizer.update(m)

    expanded = normalizer.expand_metric_anchor("latency_p95", "upper")
    assert expanded
    _, new_low, new_high = normalizer.anchors["latency_p95"]

    assert new_low < old_low, (
        f"q_low must drop to relieve LOWER_IS_BETTER upper-utility saturation; "
        f"old_low={old_low:.4f}, new_low={new_low:.4f}"
    )
    # And the previously-saturated value should now produce non-1 utility.
    util = normalizer.score_metric("latency_p95", 4.2)
    assert 0.0 < util < 1.0, f"utility still saturated after expansion: {util}"


def test_expand_anchor_lower_is_better_lower_utility_saturation_raises_q_high():
    """LOWER_IS_BETTER metric saturated at utility=0 (workers at q_high)
    must have q_high raised, not q_low lowered."""
    normalizer = QuantileUtilityNormalizer(lower_quantile=0.05, upper_quantile=0.95)
    history = [
        PerformanceMetrics(throughput_variance=v) for v in np.linspace(10.0, 100.0, 50)
    ]
    normalizer.fit(history)

    _, old_low, old_high = normalizer.anchors["throughput_variance"]
    for _ in range(4):
        normalizer.update(PerformanceMetrics(throughput_variance=120.0))

    expanded = normalizer.expand_metric_anchor("throughput_variance", "lower")
    assert expanded
    _, new_low, new_high = normalizer.anchors["throughput_variance"]

    assert new_high > old_high, (
        f"q_high must rise to relieve LOWER_IS_BETTER lower-utility saturation; "
        f"old_high={old_high:.4f}, new_high={new_high:.4f}"
    )


def test_expand_anchor_higher_is_better_upper_utility_saturation_raises_q_high():
    """HIGHER_IS_BETTER metric saturated at utility=1 (workers at q_high)
    must have q_high raised — direction-aware mapping is a no-op here."""
    normalizer = QuantileUtilityNormalizer(lower_quantile=0.05, upper_quantile=0.95)
    history = [PerformanceMetrics(throughput=v) for v in np.linspace(100.0, 2000.0, 50)]
    normalizer.fit(history)

    _, old_low, old_high = normalizer.anchors["throughput"]
    for _ in range(4):
        normalizer.update(PerformanceMetrics(throughput=2200.0))

    expanded = normalizer.expand_metric_anchor("throughput", "upper")
    assert expanded
    _, new_low, new_high = normalizer.anchors["throughput"]

    assert new_high > old_high, (
        f"HIGHER_IS_BETTER upper-utility saturation should raise q_high; "
        f"old_high={old_high:.4f}, new_high={new_high:.4f}"
    )
