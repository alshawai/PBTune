"""Shared global rescoring utilities.

This module is the single source of truth for post-hoc global rescoring logic
used across evaluation and analysis packages.
"""

from __future__ import annotations

from typing import Any

from src.utils.logger import get_logger
from src.utils.metrics import MetricConfig, PerformanceMetrics, create_metric_config
from src.utils.scoring import create_scoring_engine


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
    scoring_policy: str | None = None,
    scoring_policy_version: str | None = None,
    metric_reference_version: str | None = None,
    workload_features: dict[str, float] | None = None,
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
    scores = [engine.compute_breakdown(m).final_score for m in metrics]
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

    return metric_config, scores, metadata
