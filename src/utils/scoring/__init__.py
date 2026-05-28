"""Scoring contracts and constants used by tuning, evaluation, and analysis."""

from typing import TYPE_CHECKING

from src.utils.scoring.constants import (
    DEFAULT_METRIC_REFERENCE_VERSION,
    DEFAULT_SCORING_POLICY,
    DEFAULT_SCORING_POLICY_VERSION,
    METRIC_DIRECTIONALITY,
    OPTIONAL_METRIC_IDS,
    REQUIRED_METRIC_IDS,
)
from src.utils.scoring.contracts import (
    MetricSnapshot,
    NormalizationState,
    ScoreBreakdown,
    WorkloadFeatures,
    score_breakdown_from_dict,
)
from src.utils.scoring.workload_features import (
    TemplateWorkloadMetadata,
    WorkloadFeatureExtractor,
)
from src.utils.scoring.scorer import CompositeScorer

if TYPE_CHECKING:
    from src.utils.metrics import MetricConfig

__all__ = [
    "DEFAULT_METRIC_REFERENCE_VERSION",
    "DEFAULT_SCORING_POLICY",
    "DEFAULT_SCORING_POLICY_VERSION",
    "METRIC_DIRECTIONALITY",
    "OPTIONAL_METRIC_IDS",
    "REQUIRED_METRIC_IDS",
    "MetricSnapshot",
    "NormalizationState",
    "CompositeScorer",
    "ScoreBreakdown",
    "score_breakdown_from_dict",
    "WorkloadFeatures",
    "TemplateWorkloadMetadata",
    "WorkloadFeatureExtractor",
    "create_scoring_engine",
]


def create_scoring_engine(metric_config: "MetricConfig") -> CompositeScorer:
    """
    Factory function to create and configure a CompositeScorer from MetricConfig.

    Parameters
    ----------
    metric_config : MetricConfig
        The metric configuration containing scoring policy, workload type, and features.

    Returns
    -------
    CompositeScorer
        A configured CompositeScorer instance ready to compute score breakdowns.
    """
    normalizer = None
    if getattr(metric_config, "_normalizer", None) is None:
        from src.utils.scoring.normalization import QuantileUtilityNormalizer

        normalizer = QuantileUtilityNormalizer()
    else:
        normalizer = metric_config._normalizer

    weight_overrides = {}
    if getattr(metric_config, "scoring_policy", None) == "fixed_v1":
        weight_overrides = {
            f"latency_{getattr(metric_config, 'latency_metric', 'p95')}": getattr(
                metric_config, "weight_latency", 0.5
            ),
            "throughput": getattr(metric_config, "weight_throughput", 0.3),
            "memory_utilization": getattr(metric_config, "weight_memory", 0.05),
            "error_rate": getattr(metric_config, "weight_error", 0.15),
        }

    engine = CompositeScorer(
        policy_id=getattr(metric_config, "scoring_policy", "fixed_v1"),
        workload_type=getattr(
            getattr(
                metric_config,
                "workload_type",
                type("obj", (object,), {"value": "oltp"})(),
            ),
            "value",
            "oltp",
        ).lower(),
        latency_metric=getattr(metric_config, "latency_metric", "p95"),
        features=getattr(metric_config, "workload_features", {}),
        normalizer=normalizer,
        weight_overrides=weight_overrides,
    )

    return engine
