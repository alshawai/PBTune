"""Shared global rescoring utilities.

This module is the single source of truth for post-hoc global rescoring logic
used across evaluation and analysis packages.
"""

from __future__ import annotations

from typing import Any

from src.utils.metrics import MetricConfig, PerformanceMetrics, create_metric_config


def workload_for_benchmark(benchmark: str) -> str:
    """Map benchmark names to metric workload configuration keys."""
    if benchmark == "tpch":
        return "olap"
    if benchmark == "sysbench":
        return "oltp"
    return "mixed"


def _count_valid_observations(
    metrics: list[PerformanceMetrics],
    latency_attr: str,
) -> tuple[int, int]:
    """Count valid latency and throughput observations for range calibration."""
    valid_latency = sum(1 for m in metrics if getattr(m, latency_attr) > 0.0)
    valid_throughput = sum(1 for m in metrics if m.throughput > 0.0)
    return valid_latency, valid_throughput


def rescore_metrics_globally(
    metrics: list[PerformanceMetrics],
    *,
    benchmark: str | None = None,
    workload: str | None = None,
    padding_factor: float = 0.0,
) -> tuple[MetricConfig, list[float], dict[str, Any]]:
    """
    Recompute scores using one globally calibrated normalization range.

    Args:
        metrics: Flat metric observations to rescore.
        benchmark: Optional benchmark identifier (sysbench/tpch/mixed).
        workload: Optional workload identifier (oltp/olap/mixed).
        padding_factor: Extra range padding factor for normalization.

    Returns:
        Tuple of (calibrated_metric_config, rescored_values, metadata).

    Raises:
        ValueError: If neither workload nor benchmark is provided.
    """
    if workload is None:
        if benchmark is None:
            raise ValueError("Either 'workload' or 'benchmark' must be provided.")
        workload = workload_for_benchmark(benchmark)

    metric_config = create_metric_config(workload)
    latency_metric_name = metric_config.latency_metric
    latency_attr = f"latency_{latency_metric_name}"

    valid_latency, valid_throughput = _count_valid_observations(metrics, latency_attr)
    ranges_calibrated = valid_latency >= 3 and valid_throughput >= 3

    if ranges_calibrated:
        metric_config.update_ranges(metrics, padding_factor=padding_factor)

    scores = [metric_config.compute_score(m) for m in metrics]
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
        "latency_min": metric_config.latency_min,
        "latency_max": metric_config.latency_max,
        "throughput_min": metric_config.throughput_min,
        "throughput_max": metric_config.throughput_max,
    }

    return metric_config, scores, metadata
