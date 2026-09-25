"""Ticket #170 (bug B9): asymmetric, direction-aware normalizer anchoring.

The pre-fix ``QuantileUtilityNormalizer.fit`` trimmed BOTH tails (a symmetric
IQR filter, then p05/p95 quantiles) before anchoring. That discarded the single
best (elite) observation, so the elite sat permanently *outside* the anchor
range and clamped at maximum utility. The scorer then went blind to any further
improvement past that clamped point (reference session: throughput elite=1791
while the fitted upper anchor stopped at 1342.62, so roughly the top of the
objective weight was pinned).

The fix trims only the BAD tail and anchors the GOOD end at/beyond the best
non-failed observation with headroom, direction-aware:

* HIGHER_IS_BETTER: good end = the (numerically high) ``q_high`` anchor, which
  must strictly exceed the best (max) observation.
* LOWER_IS_BETTER / ZERO_IS_BEST: good end = the (numerically low) ``q_low``
  anchor, which must strictly undercut the best (min) observation.

These tests are written red-first against the pre-fix ``fit`` and turn green
only once anchoring is asymmetric.
"""

import numpy as np

from src.analysis.pbt_invariants import SessionTrace, check_normalizer_support
from src.utils.metrics import PerformanceMetrics
from src.utils.scoring.normalization import (
    MetricDirection,
    QuantileUtilityNormalizer,
)


# --------------------------------------------------------------------------
# Helpers
# --------------------------------------------------------------------------


def _fit(metrics, whitelist, **kw) -> QuantileUtilityNormalizer:
    normalizer = QuantileUtilityNormalizer(
        lower_quantile=0.05, upper_quantile=0.95, **kw
    )
    normalizer.fit(metrics, metric_whitelist=whitelist)
    return normalizer


def _build_trace(
    ranges: dict[str, tuple[int, float, float]],
    metrics: list[PerformanceMetrics],
) -> SessionTrace:
    """Wrap a fitted anchor set + its observations as a minimal session trace.

    Shaped exactly like the persisted schema the invariant library reads:
    ``tuning_session.scoring.normalization_metadata.ranges`` for the anchors,
    and per-generation ``worker_scores[*].metrics`` for the observed support.
    """
    payload_ranges = {
        name: {"direction": float(d), "low": float(lo), "high": float(hi)}
        for name, (d, lo, hi) in ranges.items()
    }
    worker_scores = [
        {"worker_id": i, "score": 0.0, "metrics": m.to_dict()}
        for i, m in enumerate(metrics)
    ]
    payload = {
        "tuning_session": {
            "scoring": {"normalization_metadata": {"ranges": payload_ranges}}
        },
        "history": [{"generation": 0, "worker_scores": worker_scores}],
    }
    return SessionTrace.from_dict(payload)


# --------------------------------------------------------------------------
# Criterion 3 + 5: the lone breakout elite is never clamped at max utility
# --------------------------------------------------------------------------


def test_higher_is_better_breakout_elite_not_clamped_at_max_utility():
    """A lone throughput breakout (bulk in [795, 1680], elite at 1791) must
    score strictly below 1.0 and the good-end anchor must cover it."""
    bulk = np.linspace(795.0, 1680.0, 48).tolist()
    metrics = [PerformanceMetrics(throughput=v) for v in bulk + [1791.0]]
    normalizer = _fit(metrics, ["throughput"])

    direction, q_low, q_high = normalizer.anchors["throughput"]
    assert direction == MetricDirection.HIGHER_IS_BETTER
    # Criterion 2: good-end anchor strictly beyond the best observation.
    assert q_high > 1791.0, f"good-end anchor {q_high} does not cover elite 1791"

    # Criterion 3: the best worker is not pinned at maximum utility.
    util_elite = normalizer.score_metric("throughput", 1791.0)
    assert util_elite < 1.0, f"elite clamped at max utility: {util_elite}"

    # Further improvement stays visible: the elite outscores the next-best, and
    # a hypothetical improvement beyond it still rises rather than flat-lining.
    assert util_elite > normalizer.score_metric("throughput", 1680.0)


def test_lower_is_better_breakout_elite_not_clamped_at_max_utility():
    """A lone latency breakout (bulk in [50, 120], elite at 12) must score
    strictly below 1.0 and the good-end (low) anchor must undercut it."""
    bulk = np.linspace(50.0, 120.0, 48).tolist()
    metrics = [PerformanceMetrics(latency_p95=v) for v in bulk + [12.0]]
    normalizer = _fit(metrics, ["latency_p95"])

    direction, q_low, q_high = normalizer.anchors["latency_p95"]
    assert direction == MetricDirection.LOWER_IS_BETTER
    # Criterion 2: good-end (low) anchor strictly below the best observation.
    assert q_low < 12.0, f"good-end anchor {q_low} does not undercut best 12"

    # Criterion 3: the best (lowest-latency) worker is not pinned at utility 1.
    util_elite = normalizer.score_metric("latency_p95", 12.0)
    assert util_elite < 1.0, f"elite clamped at max utility: {util_elite}"

    # The best latency still outscores the bulk (monotone, not flat-clamped).
    assert util_elite > normalizer.score_metric("latency_p95", 50.0)


# --------------------------------------------------------------------------
# Criterion 1 + 4: good tail preserved, bad tail still robustly trimmed
# --------------------------------------------------------------------------


def test_good_tail_is_never_discarded_from_the_anchor_fit():
    """The good-end anchor must cover the observed maximum for a HIGHER metric
    and the observed minimum for a LOWER metric — no matter how extreme the
    single best observation is relative to the bulk."""
    hi_metrics = [
        PerformanceMetrics(throughput=v)
        for v in np.linspace(500.0, 900.0, 40).tolist() + [5000.0]
    ]
    hi = _fit(hi_metrics, ["throughput"])
    _, _, q_high = hi.anchors["throughput"]
    assert q_high > 5000.0, "good (high) tail was discarded from the fit"

    lo_metrics = [
        PerformanceMetrics(latency_p95=v)
        for v in np.linspace(80.0, 200.0, 40).tolist() + [3.0]
    ]
    lo = _fit(lo_metrics, ["latency_p95"])
    _, q_low, _ = lo.anchors["latency_p95"]
    assert q_low < 3.0, "good (low) tail was discarded from the fit"


def test_bad_tail_outlier_does_not_distort_the_range():
    """A pathological-but-not-failed low throughput reading must be robustly
    trimmed off the BAD (low) end rather than dragging the anchor down to it."""
    bulk = np.linspace(800.0, 1000.0, 40).tolist()
    metrics = [PerformanceMetrics(throughput=v) for v in bulk + [5.0]]
    normalizer = _fit(metrics, ["throughput"])

    _, q_low, q_high = normalizer.anchors["throughput"]
    # The bad-end anchor sits with the bulk, not at the pathological 5.0.
    assert q_low > 700.0, f"bad-tail outlier distorted q_low to {q_low}"
    # Yet the good end still covers the true maximum.
    assert q_high > 1000.0
    # The pathological reading clamps to the floor (utility 0), not the range.
    assert normalizer.score_metric("throughput", 5.0) == 0.0


def test_bad_tail_outlier_does_not_distort_the_range_lower_is_better():
    """The LOWER-direction analogue: a pathological-but-not-failed HIGH latency
    reading must be robustly trimmed off the BAD (high) end rather than dragging
    the anchor up to it. Exercises the ``arr <= upper_bound`` bad-tail branch,
    which the HIGHER-only test above never reaches."""
    bulk = np.linspace(50.0, 120.0, 40).tolist()
    metrics = [PerformanceMetrics(latency_p95=v) for v in bulk + [5000.0]]
    normalizer = _fit(metrics, ["latency_p95"])

    _, q_low, q_high = normalizer.anchors["latency_p95"]
    # The bad-end (high) anchor sits with the bulk, not at the pathological 5000.
    assert q_high < 200.0, f"bad-tail outlier distorted q_high to {q_high}"
    # Yet the good end still undercuts the true minimum.
    assert q_low < 50.0
    # The pathological reading clamps to the floor (utility 0), not the range.
    assert normalizer.score_metric("latency_p95", 5000.0) == 0.0


def test_zero_is_best_anchors_like_a_good_low_metric():
    """``ZERO_IS_BEST`` is fit-unreachable today (``_get_metric_direction`` only
    ever returns HIGHER/LOWER), so drive the anchor helper directly to lock the
    contract: it must anchor like LOWER_IS_BETTER — good-end (low) anchor below
    the best (min) with headroom, bad end at the robust high quantile — so a
    future ZERO_IS_BEST metric cannot silently fall into the wrong branch."""
    normalizer = QuantileUtilityNormalizer(lower_quantile=0.05, upper_quantile=0.95)
    values = np.linspace(2.0, 40.0, 40).tolist()  # best (min) = 2.0
    q_low, q_high = normalizer._fit_metric_anchor(
        values, MetricDirection.ZERO_IS_BEST
    )
    # Good end (low) undercuts the best observation; bad end stays above it.
    assert q_low < min(values), "ZERO_IS_BEST good-end (low) anchor must undercut the min"
    assert q_high > q_low


# --------------------------------------------------------------------------
# Criterion 6: the check_normalizer_support harness invariant turns green
# --------------------------------------------------------------------------


def test_check_normalizer_support_holds_on_corrected_anchors():
    """Fit both directions on data with a tied best cluster (30% of workers),
    then assert the anchor-support invariant HOLDS on the corrected anchors and
    that the best cluster is no longer clamped. A symmetric quantile anchor (the
    pre-fix approach) is shown to violate the same invariant on the same data.
    """
    tps = [2000.0] * 15 + np.linspace(200.0, 1500.0, 35).tolist()
    lats = [10.0] * 15 + np.linspace(50.0, 200.0, 35).tolist()
    metrics = [
        PerformanceMetrics(throughput=t, latency_p95=lat)
        for t, lat in zip(tps, lats, strict=True)
    ]

    # A symmetric quantile anchor pins the tied best cluster outside the range:
    # 30% of workers clamp, breaching the 25% support ceiling.
    buggy_ranges = {
        "throughput": (
            MetricDirection.HIGHER_IS_BETTER,
            float(np.percentile(tps, 5)),
            float(np.percentile(tps, 95)),
        ),
        "latency_p95": (
            MetricDirection.LOWER_IS_BETTER,
            float(np.percentile(lats, 5)),
            float(np.percentile(lats, 95)),
        ),
    }
    assert not check_normalizer_support(_build_trace(buggy_ranges, metrics)).holds

    # The corrected asymmetric fit covers the good end, so clamping falls well
    # under the ceiling and the invariant holds.
    normalizer = _fit(metrics, ["throughput", "latency_p95"])
    finding = check_normalizer_support(_build_trace(normalizer.anchors, metrics))
    assert finding.holds, finding.summary

    rows = {r["metric"]: r for r in finding.observed["metrics"]}
    # The good end carries zero clamped observations by construction.
    assert rows["throughput"]["clamped_high"] == 0.0
    assert rows["latency_p95"]["clamped_low"] == 0.0
