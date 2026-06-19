"""Post-hoc global score recalibration for tuning strategies.

Strategies that evaluate many configurations under per-config (local)
normalization benefit from a final pass that recomputes every score against a
single globally calibrated normalization range. ``src/utils/rescoring`` is
already the shared source of truth for that math; this module is a thin
tuner-facing adapter that decides *whether* recalibration is worthwhile and
returns the rescored payload in a strategy-agnostic shape.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional

from src.utils.metrics import MetricConfig, PerformanceMetrics
from src.utils.rescoring import rescore_metrics_globally

# Global range calibration is meaningless with too few observations; the
# rescoring engine itself uses a floor of 3 valid latency/throughput samples.
MIN_OBSERVATIONS_FOR_RECALIBRATION = 3


@dataclass
class RecalibrationResult:
    """Outcome of a global recalibration pass.

    Attributes
    ----------
    applied
        Whether recalibration actually ran (False when below the floor).
    metric_config
        The globally calibrated ``MetricConfig`` (None when skipped).
    rescored_values
        Per-observation rescored composite values (empty when skipped).
    metadata
        Normalization metadata emitted by the rescoring engine.
    """

    applied: bool = False
    metric_config: Optional[MetricConfig] = None
    rescored_values: List[float] = field(default_factory=list)
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
    """Recalibrate scores globally if there are enough observations.

    Returns an unapplied :class:`RecalibrationResult` (rather than raising)
    when fewer than :data:`MIN_OBSERVATIONS_FOR_RECALIBRATION` metrics are
    available, so callers can fall back to local scores transparently.
    """
    if len(metrics) < MIN_OBSERVATIONS_FOR_RECALIBRATION:
        return RecalibrationResult(applied=False)

    metric_config, rescored_values, metadata = rescore_metrics_globally(
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
        rescored_values=list(rescored_values),
        metadata=dict(metadata),
    )
