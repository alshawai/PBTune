"""
Performance Metrics Module
==========================

This module defines the canonical performance-metric container and the
scoring compatibility layer used by tuning, rescoring, and evaluation.

The current scoring model is policy-driven:

1. ``PerformanceMetrics`` stores the raw measurements collected from a run.
2. ``MetricConfig`` preserves legacy workload-specific defaults while carrying
   scoring-policy metadata and normalization state.
3. ``compute_score()`` delegates to the shared scoring-v2 stack, which applies
   workload features, robust normalization, and a reliability gate before
   returning a ``ScoreBreakdown`` with a bounded score in ``[0, 100]``.

The compatibility policy ``fixed_v1`` remains available for historical session
loading and incremental migration, while ``feature_driven_v2`` is the
policy-aware path that aligns tuning, post-hoc rescoring, and evaluation.
"""

from __future__ import annotations
from dataclasses import dataclass, field
from typing import Any, Dict, Optional, List
from enum import Enum
from logging import Logger
import numpy as np


from src.utils.logger import get_logger, get_color_context

LOGGER = get_logger("Metrics")
COLORS = get_color_context()


class WorkloadType(Enum):
    """Type of database workload"""

    OLTP = "oltp"
    OLAP = "olap"
    MIXED = "mixed"


@dataclass
class PerformanceMetrics:
    """
    Raw performance measurements from workload execution.

    This class captures ALL relevant metrics, then MetricConfig
    determines which ones to use and how to weight them.

    Attributes
    ----------
    latency_p50 : float
        Median latency in milliseconds
    latency_p95 : float
        95th percentile latency in milliseconds
    latency_p99 : float
        99th percentile latency in milliseconds
    latency_stddev : float
        Standard deviation of latency (milliseconds)
    throughput : float
        Transactions/queries per second
    total_queries : int
        Total number of queries executed
    total_time : float
        Total execution time in seconds
    error_rate : float
        Fraction of failed queries (0.0 to 1.0)
    memory_utilization : float
        Average memory utilization (0.0 to 1.0)
    io_read_mb : float
        Total MB read from disk
    io_write_mb : float
        Total MB written to disk
    cache_hit_ratio : float
        Buffer cache hit ratio (0.0 to 1.0)
    failure_type : Optional[str]
        Optional failure classification for degraded/crashed evaluations.
        None means healthy evaluation.
    """

    latency_p50: float = 0.0
    latency_p95: float = 0.0
    latency_p99: float = 0.0
    latency_unit: str = "ms"
    latency_variance: float = 0.0
    tail_amplification: float = 0.0

    throughput: float = 0.0
    throughput_unit: str = "TPS"
    throughput_variance: float = 0.0

    total_queries: int = 0
    total_time: float = 0.0

    memory_utilization: float = 0.0
    memory_pressure: float = 0.0

    io_read_mb: float = 0.0
    io_write_mb: float = 0.0

    cache_hit_ratio: float = 0.0
    buffer_miss_rate: float = 0.0

    scan_efficiency: float = 0.0
    rows_examined: int = 0
    rows_returned: int = 0

    error_rate: float = 0.0
    failure_type: Optional[str] = None

    def to_dict(self) -> Dict[str, float | str | int | None]:
        """Convert metrics to dictionary"""
        return {
            "latency_p50": self.latency_p50,
            "latency_p95": self.latency_p95,
            "latency_p99": self.latency_p99,
            "latency_variance": self.latency_variance,
            "tail_amplification": self.tail_amplification,
            "latency_unit": self.latency_unit,
            "throughput": self.throughput,
            "throughput_variance": self.throughput_variance,
            "throughput_unit": self.throughput_unit,
            "total_queries": self.total_queries,
            "total_time": self.total_time,
            "memory_utilization": self.memory_utilization,
            "memory_pressure": self.memory_pressure,
            "io_read_mb": self.io_read_mb,
            "io_write_mb": self.io_write_mb,
            "cache_hit_ratio": self.cache_hit_ratio,
            "buffer_miss_rate": self.buffer_miss_rate,
            "scan_efficiency": self.scan_efficiency,
            "rows_examined": self.rows_examined,
            "rows_returned": self.rows_returned,
            "error_rate": self.error_rate,
            "failure_type": self.failure_type,
        }

    def __repr__(self) -> str:
        """Human-readable representation"""
        return (
            f"PerformanceMetrics(\n"
            f"  Latency: p50={self.latency_p50:.2f}{self.latency_unit}, "
            f"p95={self.latency_p95:.2f}{self.latency_unit}, "
            f"p99={self.latency_p99:.2f}{self.latency_unit}, "
            f"var={self.latency_variance:.2f}, tail_amp={self.tail_amplification:.2f}\n"
            f"  Throughput: {self.throughput:.2f} {self.throughput_unit}, var={self.throughput_variance:.2f}\n"
            f"  Queries: {self.total_queries} in {self.total_time:.2f}s "
            f"(rows examined: {self.rows_examined}, returned: {self.rows_returned})\n"
            f"  Errors: {self.error_rate * 100:.2f}%\n"
            f"  Memory: util={self.memory_utilization * 100:.1f}%, pressure={self.memory_pressure:.2f}\n"
            f"  Cache Hit: {self.cache_hit_ratio * 100:.1f}%, "
            f"buffer miss rate={self.buffer_miss_rate:.4f}, scan efficiency={self.scan_efficiency:.2f}\n"
            f"  Failure Type: {self.failure_type}\n"
            f")"
        )


from src.utils.scoring.constants import (
    DEFAULT_METRIC_REFERENCE_VERSION,
    DEFAULT_SCORING_POLICY,
    DEFAULT_SCORING_POLICY_VERSION,
)
from src.utils.scoring.contracts import NormalizationState, ScoreBreakdown


@dataclass
class MetricConfig:
    """
    Configuration for workload-specific metric computation.

    This configuration preserves the legacy workload defaults, but the active
    score path now flows through the shared scoring-v2 policy stack. That keeps
    the final score aligned across tuning, rescoring, and evaluation while
    maintaining compatibility with historical sessions.

    Attributes
    ----------
    workload_type : WorkloadType
        Type of workload (OLTP, OLAP, MIXED)
    weight_latency : float
        Weight for latency component
    weight_throughput : float
        Weight for throughput component
    weight_memory : float
        Weight for memory utilization component
    weight_error : float
        Weight for error rate penalty
    latency_metric : str
        Which latency percentile to use ('p50', 'p95', 'p99')
    normalize_by_baseline : bool
        Whether to normalize scores relative to a baseline config
    baseline_metrics : Optional[PerformanceMetrics]
        Baseline metrics for normalization
    scoring_policy : str
        Scoring policy identifier for serialization and compatibility checks.
    scoring_policy_version : str
        Version of the active scoring policy.
    metric_reference_version : str
        Version of the metric reference schema used during scoring.
    workload_features : dict[str, float]
        Optional workload feature vector used by policy-aware scoring.
    normalization_metadata : dict[str, Any]
        Optional extra metadata describing normalization/calibration state.
    """

    workload_type: WorkloadType
    weight_latency: float = 0.5
    weight_throughput: float = 0.3
    weight_memory: float = 0.05
    weight_error: float = 0.05
    latency_metric: str = "p95"  # 'p50', 'p95', or 'p99'
    normalize_by_baseline: bool = False
    baseline_metrics: Optional[PerformanceMetrics] = None

    scoring_policy: str = DEFAULT_SCORING_POLICY
    scoring_policy_version: str = DEFAULT_SCORING_POLICY_VERSION
    metric_reference_version: str = DEFAULT_METRIC_REFERENCE_VERSION
    workload_features: dict[str, float] = field(default_factory=dict)
    normalization_metadata: dict[str, Any] = field(default_factory=dict)

    _normalizer: Any = field(default=None, init=False, repr=False)
    _scoring_engine: Any = field(default=None, init=False, repr=False)

    def __post_init__(self):
        """Validate configuration"""
        total_weight = (
            self.weight_latency
            + self.weight_throughput
            + self.weight_memory
            + self.weight_error
        )
        if not np.isclose(total_weight, 1.0, atol=0.01):
            raise ValueError(
                f"Weights must sum to 1.0, got {total_weight:.3f}. "
                f"Adjust weights: latency={self.weight_latency}, "
                f"throughput={self.weight_throughput}, "
                f"memory={self.weight_memory}, error={self.weight_error}"
            )

        if self.latency_metric not in ["p50", "p95", "p99"]:
            raise ValueError(
                f"latency_metric must be 'p50', 'p95', or 'p99', "
                f"got '{self.latency_metric}'"
            )

    @staticmethod
    def for_oltp() -> "MetricConfig":
        """Create OLTP-optimized metric configuration."""
        return MetricConfig(
            workload_type=OLTP_METRIC_CONFIG.workload_type,
            weight_latency=OLTP_METRIC_CONFIG.weight_latency,
            weight_throughput=OLTP_METRIC_CONFIG.weight_throughput,
            weight_memory=OLTP_METRIC_CONFIG.weight_memory,
            weight_error=OLTP_METRIC_CONFIG.weight_error,
            latency_metric=OLTP_METRIC_CONFIG.latency_metric,
            normalize_by_baseline=OLTP_METRIC_CONFIG.normalize_by_baseline,
            baseline_metrics=OLTP_METRIC_CONFIG.baseline_metrics,
            scoring_policy=OLTP_METRIC_CONFIG.scoring_policy,
            scoring_policy_version=OLTP_METRIC_CONFIG.scoring_policy_version,
            metric_reference_version=OLTP_METRIC_CONFIG.metric_reference_version,
            workload_features=dict(OLTP_METRIC_CONFIG.workload_features),
            normalization_metadata=dict(OLTP_METRIC_CONFIG.normalization_metadata),
        )

    @staticmethod
    def for_olap() -> "MetricConfig":
        """Create OLAP-optimized metric configuration."""
        return MetricConfig(
            workload_type=OLAP_METRIC_CONFIG.workload_type,
            weight_latency=OLAP_METRIC_CONFIG.weight_latency,
            weight_throughput=OLAP_METRIC_CONFIG.weight_throughput,
            weight_memory=OLAP_METRIC_CONFIG.weight_memory,
            weight_error=OLAP_METRIC_CONFIG.weight_error,
            latency_metric=OLAP_METRIC_CONFIG.latency_metric,
            normalize_by_baseline=OLAP_METRIC_CONFIG.normalize_by_baseline,
            baseline_metrics=OLAP_METRIC_CONFIG.baseline_metrics,
            scoring_policy=OLAP_METRIC_CONFIG.scoring_policy,
            scoring_policy_version=OLAP_METRIC_CONFIG.scoring_policy_version,
            metric_reference_version=OLAP_METRIC_CONFIG.metric_reference_version,
            workload_features=dict(OLAP_METRIC_CONFIG.workload_features),
            normalization_metadata=dict(OLAP_METRIC_CONFIG.normalization_metadata),
        )

    @staticmethod
    def for_mixed() -> "MetricConfig":
        """Create mixed workload metric configuration."""
        return MetricConfig(
            workload_type=MIXED_METRIC_CONFIG.workload_type,
            weight_latency=MIXED_METRIC_CONFIG.weight_latency,
            weight_throughput=MIXED_METRIC_CONFIG.weight_throughput,
            weight_memory=MIXED_METRIC_CONFIG.weight_memory,
            weight_error=MIXED_METRIC_CONFIG.weight_error,
            latency_metric=MIXED_METRIC_CONFIG.latency_metric,
            normalize_by_baseline=MIXED_METRIC_CONFIG.normalize_by_baseline,
            baseline_metrics=MIXED_METRIC_CONFIG.baseline_metrics,
            scoring_policy=MIXED_METRIC_CONFIG.scoring_policy,
            scoring_policy_version=MIXED_METRIC_CONFIG.scoring_policy_version,
            metric_reference_version=MIXED_METRIC_CONFIG.metric_reference_version,
            workload_features=dict(MIXED_METRIC_CONFIG.workload_features),
            normalization_metadata=dict(MIXED_METRIC_CONFIG.normalization_metadata),
        )

    def get_normalization_metadata(self) -> dict[str, Any]:
        """Build normalization metadata for persistence and compatibility checks."""
        state = self.get_normalization_state().to_dict()

        metadata: dict[str, Any] = {
            **state,
            "latency_metric": self.latency_metric,
        }
        metadata.update(self.normalization_metadata)
        return metadata

    def get_normalization_state(self) -> NormalizationState:
        """Return the structured NormalizationState for serialization."""
        normalizer_name = "quantile_utility"
        ranges: dict[str, dict[str, float]] = {}

        if self._normalizer is not None:
            state = self._normalizer.export_state()
            for metric_name, anchor in state.get("anchors", {}).items():
                ranges[metric_name] = {
                    "low": float(anchor["low"]),
                    "high": float(anchor["high"]),
                    "direction": float(anchor["direction"]),
                }

        return NormalizationState(
            normalizer=normalizer_name,
            metric_reference_version=self.metric_reference_version,
            ranges=ranges,
            metadata={
                "is_calibrated": bool(
                    getattr(self._normalizer, "is_calibrated", False)
                ),
            },
        )

    def get_scoring_metadata(self) -> dict[str, Any]:
        """Build scoring metadata for tuning/evaluation serialization."""
        return {
            "scoring_policy": self.scoring_policy,
            "scoring_policy_version": self.scoring_policy_version,
            "metric_reference_version": self.metric_reference_version,
            "workload_features": dict(self.workload_features),
            "normalization_metadata": self.get_normalization_metadata(),
        }

    def update_ranges(
        self, historical_metrics: List[PerformanceMetrics], padding_factor: float = 0.2
    ) -> None:
        """
        Update normalization ranges based on observed performance data.

        Delegates to QuantileUtilityNormalizer.fit() for robust quantile-anchored
        normalization. This replaces the legacy min-max approach with a more robust
        quantile-based method that is resistant to outliers.

        Parameters
        ----------
        historical_metrics : List[PerformanceMetrics]
            Past performance measurements to compute ranges from
        padding_factor : float
            Deprecated parameter (kept for API compatibility). The normalizer
            uses quantile-based anchoring which is inherently robust.
        """
        if len(historical_metrics) < 3:
            LOGGER.warning(
                " ➤ Only %d metrics available. "
                "Need at least 3 for reliable range estimation. Skipping update.",
                len(historical_metrics),
            )
            return

        normalizer = self._ensure_normalizer()
        from src.utils.scoring.policies import POLICIES, FIXED_V1_POLICY

        _active_policy = POLICIES.get(self.scoring_policy, FIXED_V1_POLICY)
        normalizer.fit(historical_metrics, metric_whitelist=_active_policy.metrics)

    def detect_saturation(
        self, metrics: PerformanceMetrics, saturation_threshold: float = 0.95
    ) -> Dict[str, bool]:
        """
        Detect if metrics are saturating (hitting normalization ceiling).

        Delegates to QuantileUtilityNormalizer.needs_recalibration() and
        out_of_support_rate() for drift-based saturation detection.

        Parameters
        ----------
        metrics : PerformanceMetrics
            Performance measurements to check
        saturation_threshold : float
            Deprecated parameter (kept for API compatibility).

        Returns
        -------
        Dict[str, bool]
            Dictionary indicating which metrics are saturated:
            - 'latency': True if latency component is saturated
            - 'throughput': True if throughput component is saturated
            - 'any': True if any component is saturated
        """
        saturation = {"latency": False, "throughput": False, "any": False}

        if self._normalizer is not None and self._normalizer.is_calibrated:
            needs_recalibration = self._normalizer.needs_recalibration()
            lat_metric = f"latency_{self.latency_metric}"

            saturation["latency"] = (
                self._normalizer.out_of_support_rate(lat_metric)
                > self._normalizer.drift_threshold
            )
            saturation["throughput"] = (
                self._normalizer.out_of_support_rate("throughput")
                > self._normalizer.drift_threshold
            )
            saturation["any"] = needs_recalibration

        return saturation

    def expand_ranges_for_metrics(
        self, metrics_list: List[PerformanceMetrics], expansion_factor: float = 0.5
    ) -> bool:
        """
        Expand normalization ranges to accommodate metrics that exceed current bounds.

        Delegates to QuantileUtilityNormalizer for saturation detection and anchor
        expansion. Uses per-metric saturation detection first, then falls back to
        time-gated full recalibration if drift is detected.

        Parameters
        ----------
        metrics_list : List[PerformanceMetrics]
            Current generation's metrics that triggered expansion
        expansion_factor : float
            Deprecated parameter (kept for API compatibility).

        Returns
        -------
        bool
            True if ranges were expanded, False if no expansion needed
        """
        if not metrics_list:
            return False

        if self._normalizer is not None:
            for m in metrics_list:
                self._normalizer.update(m)

            min_saturated = max(2, len(metrics_list) // 2)
            saturated = self._normalizer.detect_metric_saturation(
                metrics_list,
                min_saturated_workers=min_saturated,
            )

            expanded = False
            lat_metric = f"latency_{self.latency_metric}"

            if saturated:
                for metric_name, bound in saturated.items():
                    if self._normalizer.expand_metric_anchor(metric_name, bound):
                        LOGGER.debug(
                            "  %sExpanded %s anchor (%s bound saturated by ≥%d workers)%s",
                            COLORS.italic,
                            metric_name,
                            bound,
                            min_saturated,
                            COLORS.reset,
                        )
                        expanded = True
                return expanded

            if not self._normalizer.needs_recalibration():
                return False

            fit_dataset = self._normalizer.build_recalibration_dataset(
                metrics_list,
                latency_metric_name=lat_metric,
            )
            from src.utils.scoring.policies import POLICIES, FIXED_V1_POLICY

            _active_policy = POLICIES.get(self.scoring_policy, FIXED_V1_POLICY)
            self._normalizer.fit(fit_dataset, metric_whitelist=_active_policy.metrics)

            LOGGER.info(" ➤ Expanded normalization ranges via normalizer recalibration")
            return True

        return False

    def compute_score(
        self, metrics: PerformanceMetrics, worker_logger: Optional[Logger] = None
    ) -> ScoreBreakdown:
        """Compute a ScoreBreakdown using the unified scoring engine."""
        from src.utils.scoring.engine import ScoringEngine

        normalizer = self._ensure_normalizer()

        engine = self._scoring_engine
        if engine is None:
            engine = ScoringEngine(
                policy_id=self.scoring_policy,
                workload_type=self.workload_type.value.lower(),
                latency_metric=self.latency_metric,
                features=self.workload_features,
                normalizer=normalizer,
                weight_overrides=self._resolve_fixed_v1_overrides(),
            )
            self._scoring_engine = engine
        else:
            engine.set_normalizer(normalizer)
            engine.update_context(
                policy_id=self.scoring_policy,
                workload_type=self.workload_type.value.lower(),
                latency_metric=self.latency_metric,
                features=self.workload_features,
                weight_overrides=self._resolve_fixed_v1_overrides(),
            )

        breakdown = engine.compute_breakdown(metrics, worker_logger=worker_logger)

        if self.normalize_by_baseline and self.baseline_metrics is not None:
            baseline = engine.compute_breakdown(
                self.baseline_metrics, worker_logger=worker_logger
            )
            if baseline.final_score > 0:
                baseline_scaled = (breakdown.final_score / baseline.final_score) * 100.0
                breakdown = ScoreBreakdown(
                    final_score=baseline_scaled,
                    policy=breakdown.policy,
                    policy_version=breakdown.policy_version,
                    reliability_gate=breakdown.reliability_gate,
                    components=breakdown.components,
                    metadata={
                        **breakdown.metadata,
                        "baseline_score": baseline.final_score,
                        "baseline_normalized": True,
                    },
                )

        return breakdown

    def compute_score_value(
        self, metrics: PerformanceMetrics, worker_logger: Optional[Logger] = None
    ) -> float:
        """Return the legacy scalar score value from ScoreBreakdown."""
        return self.compute_score(metrics, worker_logger=worker_logger).final_score

    def _ensure_normalizer(self):
        if self._normalizer is None:
            from src.utils.scoring.normalization import QuantileUtilityNormalizer

            self._normalizer = QuantileUtilityNormalizer()
        return self._normalizer

    def _resolve_fixed_v1_overrides(self) -> dict[str, float]:
        if self.scoring_policy != "fixed_v1":
            return {}

        return {
            f"latency_{self.latency_metric}": self.weight_latency,
            "throughput": self.weight_throughput,
            "memory_utilization": self.weight_memory,
            "error_rate": self.weight_error,
        }


# Priorities: Low latency, High throughput
OLTP_METRIC_CONFIG = MetricConfig(
    workload_type=WorkloadType.OLTP,
    weight_latency=0.50,
    weight_throughput=0.40,
    weight_memory=0.05,
    weight_error=0.05,
    latency_metric="p95",
)

OLAP_METRIC_CONFIG = MetricConfig(
    workload_type=WorkloadType.OLAP,
    weight_latency=0.55,
    weight_throughput=0.30,
    weight_memory=0.10,
    weight_error=0.05,
    latency_metric="p99",
)

MIXED_METRIC_CONFIG = MetricConfig(
    workload_type=WorkloadType.MIXED,
    weight_latency=0.40,
    weight_throughput=0.35,
    weight_memory=0.15,
    weight_error=0.10,
    latency_metric="p95",
)


def create_metric_config(workload_type: str, **custom_weights) -> MetricConfig:
    """
    Factory function to create metric configuration.

    Parameters
    ----------
    workload_type : str
        'oltp', 'olap', or 'mixed'
    **custom_weights
        Override default weights (e.g., weight_latency=0.6)

    Returns
    -------
    MetricConfig
        Configured metric computer

    Examples
    --------
    >>> # Use default OLTP config
    >>> config = create_metric_config('oltp')

    >>> # Custom OLTP with more emphasis on throughput
    >>> config = create_metric_config('oltp', weight_latency=0.3, weight_throughput=0.5)
    """
    workload_type_lower = workload_type.lower()

    if workload_type_lower == "oltp":
        base_config = MetricConfig.for_oltp()
    elif workload_type_lower == "olap":
        base_config = MetricConfig.for_olap()
    elif workload_type_lower == "mixed":
        base_config = MetricConfig.for_mixed()
    else:
        raise ValueError(
            f"Unknown workload_type: {workload_type}. "
            f"Must be 'oltp', 'olap', or 'mixed'"
        )

    config_dict = {
        "workload_type": base_config.workload_type,
        "weight_latency": custom_weights.get(
            "weight_latency", base_config.weight_latency
        ),
        "weight_throughput": custom_weights.get(
            "weight_throughput",
            base_config.weight_throughput,
        ),
        "weight_memory": custom_weights.get("weight_memory", base_config.weight_memory),
        "weight_error": custom_weights.get("weight_error", base_config.weight_error),
        "latency_metric": custom_weights.get(
            "latency_metric", base_config.latency_metric
        ),
        "normalize_by_baseline": base_config.normalize_by_baseline,
        "baseline_metrics": base_config.baseline_metrics,
        "scoring_policy": custom_weights.get(
            "scoring_policy", base_config.scoring_policy
        ),
        "scoring_policy_version": custom_weights.get(
            "scoring_policy_version", base_config.scoring_policy_version
        ),
        "metric_reference_version": custom_weights.get(
            "metric_reference_version", base_config.metric_reference_version
        ),
        "workload_features": dict(
            custom_weights.get("workload_features", base_config.workload_features) or {}
        ),
        "normalization_metadata": dict(
            custom_weights.get(
                "normalization_metadata", base_config.normalization_metadata
            )
            or {}
        ),
    }
    return MetricConfig(**config_dict)
