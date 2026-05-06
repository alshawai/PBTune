"""
Metric Instrumentation Module
=============================

This module provides utilities for computing derived metrics from raw performance measurements.

Derived Metrics:
- latency_variance: Statistical variance of latency distribution
- tail_latency_amplification: Ratio of p99 to p50 latency (tail latency multiplier)
- scan_efficiency: Derived metric indicating cache effectiveness

These metrics are computed from existing raw metrics and added to the PerformanceMetrics
during evaluation to provide deeper insight into database performance characteristics.
"""

from __future__ import annotations
from dataclasses import dataclass
from typing import Dict
import logging

from src.utils.metrics import PerformanceMetrics, WorkloadType

logger = logging.getLogger(__name__)


@dataclass
class DerivedMetrics:
    """
    Computed metrics derived from raw PerformanceMetrics.

    These metrics provide additional insights into performance characteristics
    that may not be directly available from standard monitoring.

    Attributes
    ----------
    tail_latency_amplification : float
        Ratio of p99 to p50 latency. Values > 1.0 indicate latency amplification
        in the tail. Typical range [1.0, 10.0+] depending on workload.
        - 1.0-1.5: Consistent latency, well-behaved
        - 1.5-3.0: Moderate tail amplification
        - 3.0+: Significant tail issues requiring attention

    scan_efficiency : float
        Cache efficiency metric in [0.0, 1.0].
        - 1.0: All queries served from cache (perfect case)
        - 0.8-0.9: Very good cache hit ratio
        - 0.5-0.8: Moderate cache effectiveness
        - 0.0-0.5: Poor cache efficiency, high disk IO

    latency_variance : float
        Standard deviation of latency distribution (ms).
        Higher values indicate less predictable latency.
        Typically correlates with tail_latency_amplification.
    """

    tail_latency_amplification: float = 1.0
    scan_efficiency: float = 1.0
    latency_variance: float = 0.0


class MetricInstrumentationEngine:
    """Engine for computing and enriching derived metrics."""

    @staticmethod
    def calculate_tail_amplification(latency_p50: float, latency_p99: float) -> float:
        """Calculate tail latency amplification: ratio of p99 to p50."""
        if latency_p50 <= 0:
            return 0.0
        return latency_p99 / latency_p50

    @staticmethod
    def calculate_scan_efficiency(cache_hit_ratio: float) -> float:
        """Calculate scan efficiency from cache hit ratio."""
        if cache_hit_ratio >= 0.99:
            return 1.0
        return max(0.0, cache_hit_ratio)

    @staticmethod
    def compute_derived_metrics(
        metrics: PerformanceMetrics,
    ) -> DerivedMetrics:
        """
        Compute derived metrics from raw performance measurements.

        Parameters
        ----------
        metrics : PerformanceMetrics
            Raw performance metrics from workload execution

        Returns
        -------
        DerivedMetrics
            Computed derived metrics
        """
        return DerivedMetrics(
            tail_latency_amplification=MetricInstrumentationEngine.calculate_tail_amplification(
                metrics.latency_p50, metrics.latency_p99
            ),
            scan_efficiency=MetricInstrumentationEngine.calculate_scan_efficiency(
                metrics.cache_hit_ratio
            ),
            latency_variance=metrics.latency_variance,
        )

    @staticmethod
    def enrich_metrics_dict(
        metrics_dict: Dict[str, float],
        metrics: PerformanceMetrics,
    ) -> Dict[str, float]:
        """
        Add derived metrics to a metrics dictionary.

        Parameters
        ----------
        metrics_dict : dict
            Dictionary of raw metrics to enrich
        metrics : PerformanceMetrics
            Source metrics object

        Returns
        -------
        dict
            Dictionary with derived metrics added
        """
        derived = MetricInstrumentationEngine.compute_derived_metrics(metrics)

        metrics_dict["tail_latency_amplification"] = derived.tail_latency_amplification
        metrics_dict["scan_efficiency"] = derived.scan_efficiency
        metrics_dict["latency_variance"] = derived.latency_variance

        return metrics_dict

    @staticmethod
    def format_derived_metrics(
        derived: DerivedMetrics,
    ) -> str:
        """
        Format derived metrics for logging/display.

        Parameters
        ----------
        derived : DerivedMetrics
            Computed derived metrics

        Returns
        -------
        str
            Formatted string representation
        """
        return (
            f"DerivedMetrics(\n"
            f"  Tail Latency Amplification: {derived.tail_latency_amplification:.2f}x\n"
            f"  Scan Efficiency: {derived.scan_efficiency * 100:.1f}%\n"
            f"  Latency Variance (stddev): {derived.latency_variance:.2f}ms\n"
            f")"
        )

    @staticmethod
    def log_metrics_summary(
        metrics: PerformanceMetrics,
        workload_type: WorkloadType = WorkloadType.OLTP,
    ) -> None:
        """
        Log a complete summary of raw and derived metrics.

        Parameters
        ----------
        metrics : PerformanceMetrics
            Raw performance metrics
        workload_type : WorkloadType
            Type of workload for context-specific messaging
        """
        derived = MetricInstrumentationEngine.compute_derived_metrics(metrics)

        logger.info(
            "Performance Summary (%s):\n"
            "  Raw Metrics:\n"
            "    Latency: p50=%.2fms, p95=%.2fms, p99=%.2fms\n"
            "    Throughput: %.2f TPS\n"
            "    Cache Hit: %.1f%%, Memory: %.1f%%\n"
            "  Derived Metrics:\n"
            "    Tail Amplification: %.2fx\n"
            "    Scan Efficiency: %.1f%%\n"
            "    Latency Variance: %.2fms",
            workload_type.value,
            metrics.latency_p50,
            metrics.latency_p95,
            metrics.latency_p99,
            metrics.throughput,
            metrics.cache_hit_ratio * 100,
            metrics.memory_utilization * 100,
            derived.tail_latency_amplification,
            derived.scan_efficiency * 100,
            derived.latency_variance,
        )
