"""Ticket #169 (B7/B8): saturation quorum is 2 and both saturated ends expand.

Two defects, both exercised at the public ``MetricConfig.expand_ranges_for_metrics``
seam (metrics.py:432):

1. The quorum was ``max(2, len(metrics_list) // 2)`` — half the population. But
   TWO clamped workers already produce identical utility on a metric, so two
   genuinely different configs tie in the composite score. Two is the minimum
   count at which a ranking tie can exist, so the quorum must be a fixed 2.
2. A metric saturated at BOTH ends must have BOTH anchors expanded; expanding
   one end must never silently discard saturation at the other.
"""

from __future__ import annotations

import numpy as np

from src.utils.metrics import MetricConfig, PerformanceMetrics


def _fit_config(latency_lo: float, latency_hi: float) -> MetricConfig:
    config = MetricConfig.for_oltp()
    baseline = [
        PerformanceMetrics(latency_p95=v, throughput=110.0)
        for v in np.linspace(latency_lo, latency_hi, 21)
    ]
    config.update_ranges(baseline)
    return config


def test_saturation_quorum_is_fixed_two() -> None:
    """The quorum is a named constant equal to 2 (spec value)."""
    from src.utils.metrics import SATURATION_QUORUM

    assert SATURATION_QUORUM == 2


def test_two_saturated_workers_trigger_expansion_below_half_population() -> None:
    """With 8 workers, only 2 clamped at a bound. The old half-population quorum
    (max(2, 8//2) = 4) would ignore them; the fixed quorum of 2 must expand."""
    config = _fit_config(15.0, 35.0)
    lat_metric = f"latency_{config.latency_metric}"
    _, _old_low, old_high = config._normalizer.anchors[lat_metric]

    # 2 workers clamp the LOWER-utility (very high latency) bound; 6 in-band.
    metrics_list = [
        PerformanceMetrics(latency_p95=500.0, throughput=110.0),
        PerformanceMetrics(latency_p95=500.0, throughput=110.0),
    ] + [PerformanceMetrics(latency_p95=25.0, throughput=110.0) for _ in range(6)]

    expanded = config.expand_ranges_for_metrics(metrics_list)

    assert expanded is True, (
        "2 clamped workers out of 8 must trigger expansion at quorum=2 "
        "(they already tie in the composite score)"
    )
    _, _new_low, new_high = config._normalizer.anchors[lat_metric]
    assert new_high > old_high


def test_both_ends_saturated_relieves_both_ends() -> None:
    """A metric clamped at BOTH ends must have BOTH anchors expanded so the
    previously-clamped extreme values become discriminative again.

    Pre-fix, detection picked the upper end via ``elif`` and only that end was
    relieved: the high-latency worker stayed pinned at utility 0.0.
    """
    config = _fit_config(15.0, 35.0)

    low_extreme = 13.0  # below q_low  -> LOWER_IS_BETTER utility 1.0 (upper)
    high_extreme = 40.0  # above q_high -> LOWER_IS_BETTER utility 0.0 (lower)

    # Confirm both extremes start fully clamped (the both-ends saturation shape).
    assert config._normalizer.score_metric("latency_p95", low_extreme) == 1.0
    assert config._normalizer.score_metric("latency_p95", high_extreme) == 0.0

    metrics_list = [
        PerformanceMetrics(latency_p95=low_extreme, throughput=110.0),
        PerformanceMetrics(latency_p95=low_extreme, throughput=110.0),
        PerformanceMetrics(latency_p95=high_extreme, throughput=110.0),
        PerformanceMetrics(latency_p95=high_extreme, throughput=110.0),
    ]

    expanded = config.expand_ranges_for_metrics(metrics_list)
    assert expanded is True

    util_low = config._normalizer.score_metric("latency_p95", low_extreme)
    util_high = config._normalizer.score_metric("latency_p95", high_extreme)

    assert 0.0 < util_low < 1.0, (
        f"low-latency (upper-utility) end must be relieved; utility={util_low}"
    )
    assert 0.0 < util_high < 1.0, (
        f"high-latency (lower-utility) end must be relieved, not silently "
        f"discarded; utility={util_high}"
    )
