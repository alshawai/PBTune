"""
Post-hoc global score recalibration — the canonical home for the rescoring math.

Strategies that evaluate many configurations under per-config (local)
normalization benefit from a final pass that recomputes every score against a
single globally calibrated normalization range. This module owns that math
outright: the low-level :func:`rescore_metrics_globally` engine plus the
tuner-facing :func:`maybe_recalibrate_scores` adapter that decides *whether*
recalibration is worthwhile and returns the rescored payload (including full
per-config :class:`ScoreBreakdown` objects) in a strategy-agnostic shape.

Two public surfaces sit on top of one private core:
  - :func:`rescore_metrics_globally` returns flat ``(metric_config, scores,
    metadata)`` — the long-standing contract every downstream consumer
    (evaluation, analysis, visualization) already unpacks.
  - :func:`maybe_recalibrate_scores` returns a :class:`RecalibrationResult`
    that additionally carries the full per-config :class:`ScoreBreakdown`
    objects, so a tuner can serialize an already-rescored session with
    accurate ``score_breakdown`` fields.

Import direction (interim, see ADR-006)
---------------------------------------
This relocation (was ``src/utils/rescoring.py``) is part of unifying the
tuning strategies under ``src/tuners/``. Until PBT and BO are themselves
refactored into this package, several downstream consumers (evaluation,
analysis, visualization) import this module — i.e. ``src/...`` depends on
``src/tuners/...``. That direction is intentional but temporary. Exit
criterion: once PBT/BO run through :class:`~src.tuners.base.BaseTuner`, every
session serializes already-recalibrated, so those consumers drop their own
rescoring pass and the back-import disappears.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional, Tuple

from src.utils.logger import get_logger
from src.utils.metrics import (
    MetricConfig,
    PerformanceMetrics,
    create_metric_config,
)
from src.utils.scoring import create_scoring_engine
from src.utils.scoring.contracts import ScoreBreakdown

# Global range calibration is meaningless with too few observations; the
# rescoring engine itself uses a floor of 3 valid latency/throughput samples.
MIN_OBSERVATIONS_FOR_RECALIBRATION = 3


def workload_for_benchmark(benchmark: str) -> str:
    """Map benchmark names to metric workload configuration keys."""
    if benchmark == "tpch":
        return "olap"
    if benchmark == "sysbench":
        return "oltp"
    return "mixed"


def _count_valid_observations(
    metrics: List[PerformanceMetrics],
    latency_attr: str,
) -> Tuple[int, int]:
    """Count valid latency and throughput observations for range calibration."""
    valid_latency = sum(1 for m in metrics if getattr(m, latency_attr) > 0.0)
    valid_throughput = sum(1 for m in metrics if m.throughput > 0.0)
    return valid_latency, valid_throughput


def _rescore_with_breakdowns(
    metrics: List[PerformanceMetrics],
    *,
    benchmark: Optional[str] = None,
    workload: Optional[str] = None,
    padding_factor: float = 0.0,
    scoring_policy: Optional[str] = None,
    scoring_policy_version: Optional[str] = None,
    metric_reference_version: Optional[str] = None,
    workload_features: Optional[Dict[str, float]] = None,
) -> Tuple[MetricConfig, List[ScoreBreakdown], Dict[str, Any]]:
    """
    Shared core: calibrate one global range and recompute every score.

    Returns the full per-config :class:`ScoreBreakdown` objects. The two
    public wrappers project from these: :func:`rescore_metrics_globally`
    keeps only ``b.final_score``, while :func:`maybe_recalibrate_scores`
    retains the breakdowns for serialization.

    Raises:
        ValueError: If neither workload nor benchmark is provided.
    """
    logger = get_logger("Rescoring")

    if workload is None:
        if benchmark is None:
            raise ValueError("Either 'workload' or 'benchmark' must be provided.")
        workload = workload_for_benchmark(benchmark)

    logger.info(
        "Rescoring %d metrics globally (workload=%s, benchmark=%s, policy=%s)",
        len(metrics),
        workload,
        benchmark,
        scoring_policy or "default",
    )

    metric_config = create_metric_config(
        workload,
        scoring_policy=scoring_policy,
        scoring_policy_version=scoring_policy_version,
        metric_reference_version=metric_reference_version,
        workload_features=workload_features,
    )
    latency_metric_name = metric_config.latency_metric
    latency_attr = f"latency_{latency_metric_name}"

    valid_latency, valid_throughput = _count_valid_observations(metrics, latency_attr)
    ranges_calibrated = valid_latency >= 3 and valid_throughput >= 3

    logger.debug(
        "Observation counts: latency=%d, throughput=%d (calibration_threshold=3)",
        valid_latency,
        valid_throughput,
    )

    if ranges_calibrated:
        logger.info(
            "Calibrating normalization ranges from %d observations (padding=%.2f)",
            len(metrics),
            padding_factor,
        )
        metric_config.update_ranges(metrics, padding_factor=padding_factor)
    else:
        logger.warning(
            "Insufficient observations for calibration (latency=%d, throughput=%d). "
            "Using default ranges.",
            valid_latency,
            valid_throughput,
        )

    engine = create_scoring_engine(metric_config)
    breakdowns = [engine.compute_breakdown(m) for m in metrics]
    scores = [b.final_score for b in breakdowns]
    logger.info(
        "Rescoring complete: %d scores computed (mean=%.4f, min=%.4f, max=%.4f)",
        len(scores),
        sum(scores) / len(scores) if scores else 0.0,
        min(scores) if scores else 0.0,
        max(scores) if scores else 0.0,
    )

    metadata = {
        "mode": "global_posthoc",
        "workload": workload,
        "benchmark": benchmark,
        "padding_factor": padding_factor,
        "latency_metric": latency_metric_name,
        "ranges_calibrated": ranges_calibrated,
        "n_observations": len(metrics),
        "n_valid_latency": valid_latency,
        "n_valid_throughput": valid_throughput,
        "calibration_sample_counts": {
            "latency": valid_latency,
            "throughput": valid_throughput,
        },
        "normalizer_type": "QuantileUtilityNormalizer",
        "metric_reference_version": metric_config.metric_reference_version,
        "scoring_policy": metric_config.scoring_policy,
        "scoring_policy_version": metric_config.scoring_policy_version,
        "workload_features": metric_config.workload_features,
    }

    return metric_config, breakdowns, metadata


def rescore_metrics_globally(
    metrics: List[PerformanceMetrics],
    *,
    benchmark: Optional[str] = None,
    workload: Optional[str] = None,
    padding_factor: float = 0.0,
    scoring_policy: Optional[str] = None,
    scoring_policy_version: Optional[str] = None,
    metric_reference_version: Optional[str] = None,
    workload_features: Optional[Dict[str, float]] = None,
) -> Tuple[MetricConfig, List[float], Dict[str, Any]]:
    """
    Recompute scores using one globally calibrated normalization range.

    Args:
        metrics: Flat metric observations to rescore.
        benchmark: Optional benchmark identifier (sysbench/tpch/mixed).
        workload: Optional workload identifier (oltp/olap/mixed).
        padding_factor: Extra range padding factor for normalization.

    Returns:
        Tuple of ``(calibrated_metric_config, scores, metadata)`` where
        ``scores`` is the per-observation list of final composite scores.
        Callers needing the full :class:`ScoreBreakdown` per observation
        should use :func:`maybe_recalibrate_scores` instead.

    Raises:
        ValueError: If neither workload nor benchmark is provided.
    """
    metric_config, breakdowns, metadata = _rescore_with_breakdowns(
        metrics,
        benchmark=benchmark,
        workload=workload,
        padding_factor=padding_factor,
        scoring_policy=scoring_policy,
        scoring_policy_version=scoring_policy_version,
        metric_reference_version=metric_reference_version,
        workload_features=workload_features,
    )
    return metric_config, [b.final_score for b in breakdowns], metadata


@dataclass
class RecalibrationResult:
    """
    Outcome of a global recalibration pass.

    Attributes
    ----------
    applied
        Whether recalibration actually ran (False when below the floor).
    metric_config
        The globally calibrated ``MetricConfig`` (None when skipped).
    scores
        Per-observation rescored composite values (empty when skipped).
    breakdowns
        Per-observation full :class:`ScoreBreakdown` objects, aligned with
        ``scores`` (empty when skipped). Serialization uses these for accurate
        ``score_breakdown`` + scoring-derived fields.
    metadata
        Normalization metadata emitted by the rescoring engine.
    """

    applied: bool = False
    metric_config: Optional[MetricConfig] = None
    scores: List[float] = field(default_factory=list)
    breakdowns: List[ScoreBreakdown] = field(default_factory=list)
    metadata: Dict[str, Any] = field(default_factory=dict)


def maybe_recalibrate_scores(
    metrics: List[PerformanceMetrics],
    *,
    benchmark: Optional[str] = None,
    workload: Optional[str] = None,
    scoring_policy: Optional[str] = None,
    scoring_policy_version: Optional[str] = None,
    metric_reference_version: Optional[str] = None,
    workload_features: Optional[Dict[str, float]] = None,
) -> RecalibrationResult:
    """
    Recalibrate scores globally if there are enough observations.

    Returns an unapplied :class:`RecalibrationResult` (rather than raising)
    when fewer than :data:`MIN_OBSERVATIONS_FOR_RECALIBRATION` metrics are
    available, so callers can fall back to local scores transparently. When
    applied, the result carries both flat ``scores`` and the full per-config
    ``breakdowns`` for serialization.
    """
    if len(metrics) < MIN_OBSERVATIONS_FOR_RECALIBRATION:
        return RecalibrationResult(applied=False)

    metric_config, breakdowns, metadata = _rescore_with_breakdowns(
        metrics,
        benchmark=benchmark,
        workload=workload,
        scoring_policy=scoring_policy,
        scoring_policy_version=scoring_policy_version,
        metric_reference_version=metric_reference_version,
        workload_features=workload_features,
    )
    return RecalibrationResult(
        applied=True,
        metric_config=metric_config,
        scores=[b.final_score for b in breakdowns],
        breakdowns=list(breakdowns),
        metadata=dict(metadata),
    )
