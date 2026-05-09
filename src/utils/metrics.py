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
   returning a bounded score in ``[0, 100]``.

The compatibility policy ``fixed_v1`` remains available for historical session
loading and incremental migration, while ``feature_driven_v2`` is the
policy-aware path that aligns tuning, post-hoc rescoring, and evaluation.
"""

from __future__ import annotations
from dataclasses import dataclass, field
from typing import Any, Dict, Optional, List
import logging
from enum import Enum
import numpy as np

from src.utils.scoring.constants import (
    DEFAULT_METRIC_REFERENCE_VERSION,
    DEFAULT_SCORING_POLICY,
    DEFAULT_SCORING_POLICY_VERSION,
)

logger = logging.getLogger("Metrics")


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

    error_rate: float = 0.0

    memory_utilization: float = 0.0
    memory_pressure: float = 0.0

    io_read_mb: float = 0.0
    io_write_mb: float = 0.0

    cache_hit_ratio: float = 0.0
    buffer_miss_rate: float = 0.0
    scan_efficiency: float = 0.0

    rows_examined: int = 0
    rows_returned: int = 0

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
            "error_rate": self.error_rate,
            "memory_utilization": self.memory_utilization,
            "memory_pressure": self.memory_pressure,
            "io_read_mb": self.io_read_mb,
            "io_write_mb": self.io_write_mb,
            "cache_hit_ratio": self.cache_hit_ratio,
            "buffer_miss_rate": self.buffer_miss_rate,
            "scan_efficiency": self.scan_efficiency,
            "rows_examined": self.rows_examined,
            "rows_returned": self.rows_returned,
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
    latency_min : float
        Expected minimum latency (ms) - best case performance
    latency_max : float
        Expected maximum latency (ms) - worst acceptable performance
    throughput_min : float
        Expected minimum throughput (TPS) - worst acceptable performance
    throughput_max : float
        Expected maximum throughput (TPS) - best case performance
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

    # Reference ranges for min-max normalization (ADAPTIVE - updated from observed data)
    latency_min: float = 1.0
    latency_max: float = 1000.0
    throughput_min: float = 1.0
    throughput_max: float = 10000.0
    scoring_policy: str = DEFAULT_SCORING_POLICY
    scoring_policy_version: str = DEFAULT_SCORING_POLICY_VERSION
    metric_reference_version: str = DEFAULT_METRIC_REFERENCE_VERSION
    workload_features: dict[str, float] = field(default_factory=dict)
    normalization_metadata: dict[str, Any] = field(default_factory=dict)

    _ranges_initialized: bool = field(default=False, init=False, repr=False)
    _normalizer: Any = field(default=None, init=False, repr=False)

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
            latency_min=OLTP_METRIC_CONFIG.latency_min,
            latency_max=OLTP_METRIC_CONFIG.latency_max,
            throughput_min=OLTP_METRIC_CONFIG.throughput_min,
            throughput_max=OLTP_METRIC_CONFIG.throughput_max,
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
            latency_min=OLAP_METRIC_CONFIG.latency_min,
            latency_max=OLAP_METRIC_CONFIG.latency_max,
            throughput_min=OLAP_METRIC_CONFIG.throughput_min,
            throughput_max=OLAP_METRIC_CONFIG.throughput_max,
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
            latency_min=MIXED_METRIC_CONFIG.latency_min,
            latency_max=MIXED_METRIC_CONFIG.latency_max,
            throughput_min=MIXED_METRIC_CONFIG.throughput_min,
            throughput_max=MIXED_METRIC_CONFIG.throughput_max,
            scoring_policy=MIXED_METRIC_CONFIG.scoring_policy,
            scoring_policy_version=MIXED_METRIC_CONFIG.scoring_policy_version,
            metric_reference_version=MIXED_METRIC_CONFIG.metric_reference_version,
            workload_features=dict(MIXED_METRIC_CONFIG.workload_features),
            normalization_metadata=dict(MIXED_METRIC_CONFIG.normalization_metadata),
        )

    def get_normalization_metadata(self) -> dict[str, Any]:
        """Build normalization metadata for persistence and compatibility checks."""
        metadata: dict[str, Any] = {
            "normalizer": "adaptive_minmax",
            "metric_reference_version": self.metric_reference_version,
            "latency_metric": self.latency_metric,
            "latency_min": self.latency_min,
            "latency_max": self.latency_max,
            "throughput_min": self.throughput_min,
            "throughput_max": self.throughput_max,
            "ranges_initialized": self._ranges_initialized,
        }
        metadata.update(self.normalization_metadata)
        return metadata

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

        This implements OtterTune's adaptive scaling approach: instead of using
        hardcoded benchmark values, we compute min/max from actual measurements
        on used hardware. This ensures fair normalization regardless of system specs.

        Parameters
        ----------
        historical_metrics : List[PerformanceMetrics]
            Past performance measurements to compute ranges from
        padding_factor : float
            Padding to add beyond observed min/max (default 20%)
            Allows room for continued improvement as PBT finds better configs

        Notes
        -----
        Uses 5th/95th percentiles instead of absolute min/max to be robust
        to outliers. Adds padding to allow room for future improvements.
        Applies IQR-based outlier filtering before percentile computation.
        """
        from src.utils.scoring.outlier_filtering import iqr_filter

        if len(historical_metrics) < 3:
            logger.warning(
                "Only %d metrics available. "
                "Need at least 3 for reliable range estimation. Skipping update.",
                len(historical_metrics),
            )
            return

        latencies = [
            getattr(m, f"latency_{self.latency_metric}")
            for m in historical_metrics
            if getattr(m, f"latency_{self.latency_metric}") > 0
        ]

        throughputs = [m.throughput for m in historical_metrics if m.throughput > 0]
        if len(latencies) < 3 or len(throughputs) < 3:
            logger.warning(
                "Insufficient valid metrics (latency=%d, "
                "throughput=%d). Need at least 3 each.",
                len(latencies),
                len(throughputs),
            )
            return

        latencies_arr = np.array(latencies)
        throughputs_arr = np.array(throughputs)

        latencies_filtered, lat_filter_meta = iqr_filter(latencies_arr)
        throughputs_filtered, thr_filter_meta = iqr_filter(throughputs_arr)

        if lat_filter_meta["n_removed"] > 0:
            logger.info(
                "IQR filter removed %d/%d latency outliers (bounds: [%.1f, %.1f] ms)",
                lat_filter_meta["n_removed"],
                lat_filter_meta["original_size"],
                lat_filter_meta["lower_bound"],
                lat_filter_meta["upper_bound"],
            )

        if thr_filter_meta["n_removed"] > 0:
            logger.info(
                "IQR filter removed %d/%d throughput outliers (bounds: [%.1f, %.1f] TPS)",
                thr_filter_meta["n_removed"],
                thr_filter_meta["original_size"],
                thr_filter_meta["lower_bound"],
                thr_filter_meta["upper_bound"],
            )

        lat_p05 = np.percentile(latencies_filtered, 5)
        lat_p95 = np.percentile(latencies_filtered, 95)
        thr_p05: float = float(np.percentile(throughputs_filtered, 5))
        thr_p95: float = float(np.percentile(throughputs_filtered, 95))

        lat_range = lat_p95 - lat_p05
        thr_range = thr_p95 - thr_p05

        self.latency_min = float(max(0.1, lat_p05 - padding_factor * lat_range))
        self.latency_max = float(lat_p95 + padding_factor * lat_range)
        self.throughput_min = float(max(0.1, thr_p05 - padding_factor * thr_range))
        self.throughput_max = float(thr_p95 + padding_factor * thr_range)

        # Delegate to robust utility normalizer
        if self._normalizer is None:
            from src.utils.scoring.normalization import QuantileUtilityNormalizer

            self._normalizer = QuantileUtilityNormalizer()

        # Restrict calibration to the metrics actually consumed by the active
        # scoring policy. This prevents logging-only PerformanceMetrics fields
        # (total_queries, io_read_mb, etc.) from producing zero-anchored noise.
        from src.utils.scoring.policies import POLICIES, FIXED_V1_POLICY

        _active_policy = POLICIES.get(self.scoring_policy, FIXED_V1_POLICY)
        self._normalizer.fit(
            historical_metrics, metric_whitelist=_active_policy.metrics
        )

        # Sync robust anchors back to legacy fields for compatibility wrappers
        lat_metric = f"latency_{self.latency_metric}"
        if lat_metric in self._normalizer.anchors:
            _, q_low, q_high = self._normalizer.anchors[lat_metric]
            self.latency_min = float(q_low)
            self.latency_max = float(q_high)

        if "throughput" in self._normalizer.anchors:
            _, q_low, q_high = self._normalizer.anchors["throughput"]
            self.throughput_min = float(q_low)
            self.throughput_max = float(q_high)

        self._ranges_initialized = True
        logger.info(
            "Updated normalization ranges from %d observations (after IQR filtering):\n"
            "  Latency (%s): [%.2f, %.2f] ms\n"
            "  Throughput: [%.2f, %.2f] TPS\n"
            "  (using 5th/95th percentiles via robust QuantileUtilityNormalizer)",
            len(historical_metrics),
            self.latency_metric,
            self.latency_min,
            self.latency_max,
            self.throughput_min,
            self.throughput_max,
        )

    def detect_saturation(
        self, metrics: PerformanceMetrics, saturation_threshold: float = 0.95
    ) -> Dict[str, bool]:
        """
        Detect if metrics are saturating (hitting normalization ceiling).

        Saturation occurs when the NORMALIZED component (after min-max scaling)
        approaches 1.0, indicating the metric has hit or exceeded the range bounds.

        Parameters
        ----------
        metrics : PerformanceMetrics
            Performance measurements to check
        saturation_threshold : float
            Normalized score threshold for saturation (default: 0.95)
            Component scores >= this indicate saturation

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
            # Delegate saturation detection to normalizer drift
            needs_recalibration = self._normalizer.needs_recalibration()
            lat_metric = f"latency_{self.latency_metric}"

            # Estimate per-component saturation from out-of-support rates.
            saturation["latency"] = (
                self._normalizer.out_of_support_rate(lat_metric)
                > self._normalizer.drift_threshold
            )
            saturation["throughput"] = (
                self._normalizer.out_of_support_rate("throughput")
                > self._normalizer.drift_threshold
            )

            saturation["any"] = needs_recalibration
        else:
            # Legacy min-max thresholding
            latency = getattr(metrics, f"latency_{self.latency_metric}")
            if latency > 0:
                latency_clamped = np.clip(latency, self.latency_min, self.latency_max)
                latency_normalized = (self.latency_max - latency_clamped) / (
                    self.latency_max - self.latency_min
                )
                if latency_normalized >= saturation_threshold:
                    saturation["latency"] = True

            if metrics.throughput > 0:
                throughput_clamped = np.clip(
                    metrics.throughput, self.throughput_min, self.throughput_max
                )
                throughput_normalized = (throughput_clamped - self.throughput_min) / (
                    self.throughput_max - self.throughput_min
                )
                if throughput_normalized >= saturation_threshold:
                    saturation["throughput"] = True

            saturation["any"] = saturation["latency"] or saturation["throughput"]

        return saturation

    def expand_ranges_for_metrics(
        self, metrics_list: List[PerformanceMetrics], expansion_factor: float = 0.5
    ) -> bool:
        """
        Expand normalization ranges to accommodate metrics that exceed current bounds.

        Uses 5th/95th percentiles for robustness, then expands beyond those to
        provide headroom for continued improvement.

        Parameters
        ----------
        metrics_list : List[PerformanceMetrics]
            Current generation's metrics that triggered expansion
        expansion_factor : float
            How much to expand beyond observed values (default: 50%)

        Returns
        -------
        bool
            True if ranges were expanded, False if no expansion needed
        """
        if not metrics_list:
            return False

        if self._normalizer is not None:
            # Update history
            for m in metrics_list:
                self._normalizer.update(m)

            # PRIMARY: Per-metric saturation detection
            # Require at least 2 workers (or half the population for larger ones)
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
                        logger.info(
                            "Expanded %s anchor (%s bound saturated by ≥%d workers)",
                            metric_name,
                            bound,
                            min_saturated,
                        )
                        expanded = True

                # If we expanded, sync primary anchors back to MetricConfig
                if expanded:
                    old_lat_min, old_lat_max = self.latency_min, self.latency_max
                    old_thr_min, old_thr_max = self.throughput_min, self.throughput_max

                    if lat_metric in self._normalizer.anchors:
                        _, q_low, q_high = self._normalizer.anchors[lat_metric]
                        self.latency_min, self.latency_max = float(q_low), float(q_high)

                    if "throughput" in self._normalizer.anchors:
                        _, q_low, q_high = self._normalizer.anchors["throughput"]
                        self.throughput_min, self.throughput_max = (
                            float(q_low),
                            float(q_high),
                        )

                    logger.info(
                        "⚡ Expanded normalization ranges via saturation detection:\n"
                        "  Latency (%s): [%.2f, %.2f] → [%.2f, %.2f] ms\n"
                        "  Throughput: [%.2f, %.2f] → [%.2f, %.2f] TPS",
                        self.latency_metric,
                        old_lat_min,
                        old_lat_max,
                        self.latency_min,
                        self.latency_max,
                        old_thr_min,
                        old_thr_max,
                        self.throughput_min,
                        self.throughput_max,
                    )
                return expanded

            # SECONDARY: Time-gated full recalibration (gradual drift safety net)
            if not self._normalizer.needs_recalibration():
                return False

            # Build a history-aware dataset to prevent calibration collapse.
            fit_dataset = self._normalizer.build_recalibration_dataset(
                metrics_list,
                latency_metric_name=lat_metric,
            )
            from src.utils.scoring.policies import POLICIES, FIXED_V1_POLICY

            _active_policy = POLICIES.get(self.scoring_policy, FIXED_V1_POLICY)
            self._normalizer.fit(fit_dataset, metric_whitelist=_active_policy.metrics)

            old_lat_min, old_lat_max = self.latency_min, self.latency_max
            old_thr_min, old_thr_max = self.throughput_min, self.throughput_max

            if lat_metric in self._normalizer.anchors:
                _, q_low, q_high = self._normalizer.anchors[lat_metric]
                if self.latency_min != float(q_low) or self.latency_max != float(
                    q_high
                ):
                    self.latency_min = float(q_low)
                    self.latency_max = float(q_high)
                    expanded = True

            if "throughput" in self._normalizer.anchors:
                _, q_low, q_high = self._normalizer.anchors["throughput"]
                if self.throughput_min != float(q_low) or self.throughput_max != float(
                    q_high
                ):
                    self.throughput_min = float(q_low)
                    self.throughput_max = float(q_high)
                    expanded = True

            if expanded:
                logger.info(
                    "⚡ Expanded normalization ranges via normalizer recalibration:\n"
                    "  Latency (%s): [%.2f, %.2f] → [%.2f, %.2f] ms\n"
                    "  Throughput: [%.2f, %.2f] → [%.2f, %.2f] TPS",
                    self.latency_metric,
                    old_lat_min,
                    old_lat_max,
                    self.latency_min,
                    self.latency_max,
                    old_thr_min,
                    old_thr_max,
                    self.throughput_min,
                    self.throughput_max,
                )
            return expanded

        # Legacy min-max fallback logic
        latencies = [
            getattr(m, f"latency_{self.latency_metric}")
            for m in metrics_list
            if getattr(m, f"latency_{self.latency_metric}") > 0
        ]
        throughputs = [m.throughput for m in metrics_list if m.throughput > 0]

        if not latencies or not throughputs:
            return False

        expanded = False
        old_lat_min, old_lat_max = self.latency_min, self.latency_max
        old_thr_min, old_thr_max = self.throughput_min, self.throughput_max

        # Use percentiles for robustness (if enough samples)
        if len(latencies) >= 3:
            lat_p05 = float(np.percentile(latencies, 5))
            lat_p95 = float(np.percentile(latencies, 95))
        else:
            lat_p05 = float(min(latencies))
            lat_p95 = float(max(latencies))

        if len(throughputs) >= 3:
            thr_p05 = float(np.percentile(throughputs, 5))
            thr_p95 = float(np.percentile(throughputs, 95))
        else:
            thr_p05 = float(min(throughputs))
            thr_p95 = float(max(throughputs))

        # Expand latency range if best performance exceeds current bounds
        if lat_p05 < self.latency_min:
            lat_range = self.latency_max - self.latency_min
            new_min = lat_p05 - (expansion_factor * lat_range)
            self.latency_min = float(max(0.1, new_min))
            expanded = True

        if lat_p95 > self.latency_max:
            lat_range = self.latency_max - self.latency_min
            new_max = lat_p95 + (expansion_factor * lat_range)
            self.latency_max = float(new_max)
            expanded = True

        # Expand throughput range if best performance exceeds current bounds
        if thr_p05 < self.throughput_min:
            thr_range = self.throughput_max - self.throughput_min
            new_min = thr_p05 - (expansion_factor * thr_range)
            self.throughput_min = float(max(0.1, new_min))
            expanded = True

        if thr_p95 > self.throughput_max:
            thr_range = self.throughput_max - self.throughput_min
            new_max = thr_p95 + (expansion_factor * thr_range)
            self.throughput_max = float(new_max)
            expanded = True

        if expanded:
            logger.info(
                "⚡ Expanded normalization ranges due to saturation:\n"
                "  Latency (%s): [%.2f, %.2f] → [%.2f, %.2f] ms\n"
                "  Throughput: [%.2f, %.2f] → [%.2f, %.2f] TPS\n"
                "  (expansion: %.0f%% of range for headroom)",
                self.latency_metric,
                old_lat_min,
                old_lat_max,
                self.latency_min,
                self.latency_max,
                old_thr_min,
                old_thr_max,
                self.throughput_min,
                self.throughput_max,
                expansion_factor * 100,
            )

        return expanded

    def compute_score(self, metrics: PerformanceMetrics) -> float:
        """
        Compute composite performance score using active policy and normalizer.

        Higher score = better performance (for PBT maximization)
        """
        components = self.compute_detailed_scores(metrics)
        return components.get("total", 0.0)

    def compute_detailed_scores(self, metrics: PerformanceMetrics) -> Dict[str, float]:
        """
        Compute individual score components using the active CompositeScorer.

        Returns a dictionary showing contribution of each component.
        """
        from src.utils.scoring.scorer import CompositeScorer
        from src.utils.scoring.policies import POLICIES, FIXED_V1_POLICY

        policy = POLICIES.get(self.scoring_policy, FIXED_V1_POLICY)

        weight_overrides = {}
        if policy.policy_id == "fixed_v1":
            weight_overrides = {
                f"latency_{self.latency_metric}": self.weight_latency,
                "throughput": self.weight_throughput,
                "memory_utilization": self.weight_memory,
                "error_rate": self.weight_error,
            }

        scorer = CompositeScorer(
            policy=policy,
            normalizer=getattr(self, "_normalizer", None),
            workload_type=self.workload_type.value.lower(),
            features=self.workload_features,
            weight_overrides=weight_overrides,
        )

        # Calculate legacy fallback utilities if normalizer is not active
        fallback_utilities = {}
        latency = getattr(metrics, f"latency_{self.latency_metric}")
        if latency > 0:
            latency_clamped = np.clip(latency, self.latency_min, self.latency_max)
            fallback_utilities[f"latency_{self.latency_metric}"] = float(
                (self.latency_max - latency_clamped)
                / (self.latency_max - self.latency_min)
            )
        else:
            fallback_utilities[f"latency_{self.latency_metric}"] = 0.0

        if metrics.throughput > 0:
            thr_clamped = np.clip(
                metrics.throughput, self.throughput_min, self.throughput_max
            )
            fallback_utilities["throughput"] = float(
                (thr_clamped - self.throughput_min)
                / (self.throughput_max - self.throughput_min)
            )
        else:
            fallback_utilities["throughput"] = 0.0

        fallback_utilities["memory_utilization"] = float(
            1.0 - np.clip(metrics.memory_utilization, 0.0, 1.0)
        )
        fallback_utilities["error_rate"] = float(
            1.0 - np.clip(metrics.error_rate, 0.0, 1.0)
        )

        # Inject fallbacks if scorer needs them
        components, total_score = scorer.compute_detailed_score(
            metrics, fallback_utilities=fallback_utilities
        )

        # Rescale total to [0, 100] for legacy compatibility
        final_score = max(0.0, total_score * 100.0)

        if self.normalize_by_baseline and self.baseline_metrics is not None:
            baseline_comps = self.compute_detailed_scores(self.baseline_metrics)
            baseline_total = baseline_comps.get("total", 0.0)
            if baseline_total > 0:
                final_score = (final_score / baseline_total) * 100.0

        # Format output dictionary mapping all components to their 100-scaled values
        output = {"total": final_score}
        for k, v in components.items():
            output[k] = v * 100.0

        # Ensure minimum legacy keys are present for downstream plotters
        output["latency"] = output.get(f"latency_{self.latency_metric}", 0.0)
        output["throughput"] = output.get("throughput", 0.0)
        output["memory"] = output.get("memory_utilization", 0.0)
        output["error"] = output.get("error_rate", 0.0)

        return output


# Priorities: Low latency, High throughput
OLTP_METRIC_CONFIG = MetricConfig(
    workload_type=WorkloadType.OLTP,
    weight_latency=0.50,  # Primary: Fast response
    weight_throughput=0.40,  # Primary: High TPS
    weight_memory=0.05,  # Minor: Memory headroom
    weight_error=0.05,  # Minor: Error penalty
    latency_metric="p95",  # SLA-critical metric
    latency_min=10.0,  # Fallback: 10ms
    latency_max=200.0,  # Fallback: 200ms
    throughput_min=10.0,  # Fallback: 10 TPS
    throughput_max=1000.0,  # Fallback: 1000 TPS
)

# Priorities: Outlier latency (P99) and Total Execution Time
OLAP_METRIC_CONFIG = MetricConfig(
    workload_type=WorkloadType.OLAP,
    weight_latency=0.55,  # Primary: Worst-case query (P99) must be bounded
    weight_throughput=0.30,  # Secondary: Total throughput (QphH)
    weight_memory=0.10,  # Regularization: Safe memory allocation
    weight_error=0.05,  # Necessity: Heavy penalty for fatal parameters
    latency_metric="p99",  # Academic Standard for analytical optimization
    latency_min=100.0,  # Fallback: 100ms
    latency_max=20000.0,  # Fallback: 20s
    throughput_min=10,  # Fallback: 10 QphH
    throughput_max=1000.0,  # Fallback: 1000 QphH
)

# Balanced approach for hybrid workloads
MIXED_METRIC_CONFIG = MetricConfig(
    workload_type=WorkloadType.MIXED,
    weight_latency=0.40,
    weight_throughput=0.35,
    weight_memory=0.15,
    weight_error=0.10,
    latency_metric="p95",
    latency_min=100.0,  # Fallback: 100ms
    latency_max=20000.0,  # Fallback: 20s
    throughput_min=10,  # Fallback: 10 TPS
    throughput_max=1000.0,  # Fallback: 1000 TPS
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
        "latency_min": base_config.latency_min,
        "latency_max": base_config.latency_max,
        "throughput_min": base_config.throughput_min,
        "throughput_max": base_config.throughput_max,
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
