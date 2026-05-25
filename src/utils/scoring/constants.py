"""Scoring constants and metric registry for scoring policies."""

from typing import Final

DEFAULT_SCORING_POLICY: Final[str] = "feature_driven_v2"
DEFAULT_SCORING_POLICY_VERSION: Final[str] = "2.0"
DEFAULT_METRIC_REFERENCE_VERSION: Final[str] = "v2"

METRIC_DIRECTIONALITY: Final[dict[str, str]] = {
    "latency_p50": "lower_is_better",
    "latency_p95": "lower_is_better",
    "latency_p99": "lower_is_better",
    "throughput": "higher_is_better",
    "memory_utilization": "lower_is_better",
    "error_rate": "lower_is_better",
    "cache_hit_ratio": "higher_is_better",
    "tail_amplification": "lower_is_better",
    "scan_efficiency": "higher_is_better",
    "latency_variance": "lower_is_better",
    "memory_pressure": "lower_is_better",
    "buffer_miss_rate": "lower_is_better",
}

REQUIRED_METRIC_IDS: Final[tuple[str, ...]] = (
    "throughput",
    "error_rate",
)

OPTIONAL_METRIC_IDS: Final[tuple[str, ...]] = (
    "latency_p50",
    "latency_p95",
    "latency_p99",
    "memory_utilization",
    "cache_hit_ratio",
)
