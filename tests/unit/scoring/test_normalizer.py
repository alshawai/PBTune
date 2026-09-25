"""
Unit tests for the QuantileUtilityNormalizer.
"""

import numpy as np

from src.utils.scoring.normalization import (
    MetricDirection,
    OLAP_FALLBACK_ANCHORS,
    OLTP_FALLBACK_ANCHORS,
    QuantileUtilityNormalizer,
)
from src.utils.metrics import PerformanceMetrics


def test_normalizer_initialization():
    """Test default initialization."""
    normalizer = QuantileUtilityNormalizer()
    assert not normalizer.is_calibrated
    assert normalizer.lower_quantile == 0.05
    assert normalizer.upper_quantile == 0.95
    assert normalizer.calibration_window == 100


def test_normalizer_fit():
    """Test fitting the normalizer with observations.

    Ticket #170 (bug B9): anchoring is now asymmetric and direction-aware. The
    BAD end still anchors at the observed extreme (min for a HIGHER metric, max
    for a LOWER one), but the GOOD end is placed strictly *beyond* the best
    observation with headroom, so the best worker is no longer pinned at utility
    1.0 and further improvement stays visible. The assertions below that used to
    read ``best -> 1.0`` and ``midpoint -> 0.5`` encoded the old symmetric-both-
    ends mapping (and thereby the bug); they are replaced with the corrected
    contract (good end covers the best; scoring is monotone).
    """
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

    # throughput is 'higher_is_better' (heuristic: contains "throughput").
    # Bad (low) end still anchors at the worst observation: 0 -> 0.0 utility.
    assert np.isclose(normalizer.score_metric("throughput", 0.0), 0.0)
    # Good (high) anchor now strictly exceeds the best observation, so the best
    # worker scores just under 1.0 rather than being clamped at it (B9 fix).
    _, _, tp_high = normalizer.anchors["throughput"]
    assert tp_high > 10.0
    assert normalizer.score_metric("throughput", 10.0) < 1.0
    # Scoring stays monotone increasing across the observed range.
    assert (
        normalizer.score_metric("throughput", 0.0)
        < normalizer.score_metric("throughput", 5.0)
        < normalizer.score_metric("throughput", 10.0)
    )

    # latency is 'lower_is_better' (heuristic: contains "latency").
    # Bad (high) end still anchors at the worst observation: 50 -> 0.0 utility.
    assert np.isclose(normalizer.score_metric("latency_p95", 50.0), 0.0)
    # Good (low) anchor now strictly undercuts the best observation.
    _, lat_low, _ = normalizer.anchors["latency_p95"]
    assert lat_low < 10.0
    assert normalizer.score_metric("latency_p95", 10.0) < 1.0
    # Monotone decreasing utility as latency rises.
    assert (
        normalizer.score_metric("latency_p95", 10.0)
        > normalizer.score_metric("latency_p95", 30.0)
        > normalizer.score_metric("latency_p95", 50.0)
    )


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
    # Round-trip fidelity: the imported normalizer must score identically to the
    # original across the range. (Previously this asserted a fixed 0.5 midpoint,
    # which encoded the old symmetric-both-ends mapping; ticket #170 makes
    # anchoring asymmetric, so the midpoint value shifts. Comparing the two
    # normalizers directly is a stronger, mapping-agnostic round-trip check.)
    for value in (0.0, 2.5, 5.0, 7.5, 10.0):
        assert np.isclose(
            new_normalizer.score_metric("throughput", value),
            normalizer.score_metric("throughput", value),
        )


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
    # Ticket #170: asymmetric anchoring places the good-end anchor just beyond
    # the best observation, so a mid-range reading lands just *below* 0.5 rather
    # than exactly on it. Assert that tight "discriminating interior, pulled
    # slightly below midpoint" band — meaningfully stronger than a bare 0<u<1,
    # which would pass for almost any monotone mapping and catch no drift.
    assert 0.4 < scores["throughput"] < 0.5
    assert 0.4 < scores["latency_p95"] < 0.5


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


def test_fallback_anchors_default_to_oltp_and_are_unchanged():
    """The default (OLTP) fallback anchor set is byte-identical to the historical
    values — the OLAP addition must not perturb OLTP scoring."""
    normalizer = QuantileUtilityNormalizer()
    assert normalizer.workload_type == "oltp"
    assert normalizer.FALLBACK_ANCHORS == OLTP_FALLBACK_ANCHORS
    # Historical OLTP latency ceiling (millisecond-scale) unchanged.
    assert normalizer.FALLBACK_ANCHORS["latency_p99"] == (
        MetricDirection.LOWER_IS_BETTER,
        5.0,
        3000.0,
    )


def test_olap_fallback_discriminates_second_scale_latency():
    """OLAP fallback anchors keep second-scale TPC-H latencies discriminative
    instead of clamping them all to zero (the pre-fix bug).

    Under the OLTP fallback, q_high=3000ms means any real TPC-H latency (seconds)
    clamps to utility 0, so the uncalibrated scorer is blind to query time. The
    OLAP anchor set (selected via workload_type) spans the observed sf=1 range.
    """
    olap = QuantileUtilityNormalizer(workload_type="olap")
    assert olap.workload_type == "olap"
    assert olap.FALLBACK_ANCHORS == OLAP_FALLBACK_ANCHORS
    assert not olap.is_calibrated  # these are the *fallback*, not fitted anchors

    # Two configs 3x apart in p99 latency, both inside the OLAP anchor band.
    fast = olap.score_metric("latency_p99", 3000.0)
    slow = olap.score_metric("latency_p99", 9000.0)
    assert fast > slow > 0.0  # discriminative, not clamped to zero

    # The identical values under the OLTP fallback both clamp to zero.
    oltp = QuantileUtilityNormalizer()
    assert oltp.score_metric("latency_p99", 3000.0) == 0.0
    assert oltp.score_metric("latency_p99", 9000.0) == 0.0
