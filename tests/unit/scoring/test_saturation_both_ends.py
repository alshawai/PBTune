"""Ticket #169 (B7/B8): normalizer must report BOTH saturated ends.

Regression: ``QuantileUtilityNormalizer.detect_metric_saturation`` selected the
upper OR lower bound with an ``elif`` and returned a single string per metric.
A metric clamped at BOTH ends (dominant elite pinned at utility 1.0, crippled
worker pinned at utility 0.0 on the same metric) therefore reported only the
upper end, so expanding it silently discarded the lower-end saturation.

These tests pin the corrected contract at the normalizer seam
(normalization.py:171): a both-ends-saturated metric reports both bounds.
"""

import numpy as np

from src.utils.scoring.normalization import QuantileUtilityNormalizer
from src.utils.metrics import PerformanceMetrics


def _fit_throughput_normalizer() -> QuantileUtilityNormalizer:
    normalizer = QuantileUtilityNormalizer(lower_quantile=0.05, upper_quantile=0.95)
    history = [PerformanceMetrics(throughput=v) for v in np.linspace(100.0, 200.0, 50)]
    normalizer.fit(history, metric_whitelist=["throughput"])
    return normalizer


def test_detect_metric_saturation_reports_both_ends_when_both_clamped():
    """Two workers clamped at utility 1.0 and two clamped at utility 0.0 on the
    SAME metric must be reported as saturated at BOTH ends, not just the upper."""
    normalizer = _fit_throughput_normalizer()

    # HIGHER_IS_BETTER throughput: values far above q_high -> utility 1.0 (upper),
    # values far below q_low -> utility 0.0 (lower).
    metrics_list = [
        PerformanceMetrics(throughput=1000.0),
        PerformanceMetrics(throughput=1000.0),
        PerformanceMetrics(throughput=1.0),
        PerformanceMetrics(throughput=1.0),
    ]

    saturated = normalizer.detect_metric_saturation(
        metrics_list, min_saturated_workers=2
    )

    assert "throughput" in saturated
    bounds = saturated["throughput"]
    # Contract: a per-metric collection of saturated bounds, not a single string.
    assert not isinstance(bounds, str), (
        "both-ends saturation must not collapse to a single-string bound; "
        f"got {bounds!r}"
    )
    assert set(bounds) == {"upper", "lower"}, (
        f"metric saturated at both ends must report both bounds; got {bounds!r}"
    )


def test_detect_metric_saturation_single_end_reports_only_that_end():
    """A metric clamped at just the upper end still reports only the upper end
    (no phantom lower-end saturation introduced by the contract change)."""
    normalizer = _fit_throughput_normalizer()

    metrics_list = [
        PerformanceMetrics(throughput=1000.0),
        PerformanceMetrics(throughput=1000.0),
        PerformanceMetrics(throughput=150.0),  # in-band
    ]

    saturated = normalizer.detect_metric_saturation(
        metrics_list, min_saturated_workers=2
    )

    assert "throughput" in saturated
    assert set(saturated["throughput"]) == {"upper"}


def test_detect_metric_saturation_below_quorum_reports_nothing():
    """A single clamped worker (below the quorum of 2) reports no saturation."""
    normalizer = _fit_throughput_normalizer()

    metrics_list = [
        PerformanceMetrics(throughput=1000.0),
        PerformanceMetrics(throughput=150.0),
        PerformanceMetrics(throughput=1.0),
    ]

    saturated = normalizer.detect_metric_saturation(
        metrics_list, min_saturated_workers=2
    )

    assert saturated == {}
