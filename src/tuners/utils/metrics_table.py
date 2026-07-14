"""Shared worker-metric row formatting for the per-round metrics table.

Every strategy logs the same end-of-round "Worker Metrics" table, and it must
look identical across PBT, LHS, and BO: composite score rendered as a
percentage, latencies/throughput carrying their units, 0–1 ratio metrics shown
as percentages, floats fixed to two decimals, and a *stable* row order so the
operational (second) block reads the same everywhere. This module owns that
single canonical projection.

Previously PBT hand-built this dict inside ``population.py`` (so its table was
the "good" one) while LHS/BO fed the raw :meth:`PerformanceMetrics.to_dict`
(bare 6-decimal floats, no units, no Score, insertion-order rows, and a leaked
``failure_type`` row). Routing all three through :func:`build_worker_metric_row`
makes the display uniform by construction.
"""

from __future__ import annotations

from typing import Any, Dict, Optional

from src.utils.metrics import PerformanceMetrics


def build_worker_metric_row(
    metrics: PerformanceMetrics,
    score: Optional[float],
) -> Dict[str, Any]:
    """Project one worker's metrics into a display-ready, ordered row.

    The key order defines the table's row order: the composite ``score`` first
    (rendered as ``NN.NN%``), then the scoring metrics with units, then the
    operational block (queries, time, IO, rows, cache). ``failure_type`` and
    other raw-only fields are intentionally excluded from the table.

    Parameters
    ----------
    metrics
        The worker's measured performance metrics.
    score
        The worker's composite score on the 0–100 scale, or ``None`` for a
        failed evaluation (rendered as ``n/a``).
    """
    return {
        "score": f"{score:.2f}%" if score is not None else "n/a",
        "latency_p95": f"{metrics.latency_p95:.2f}{metrics.latency_unit}",
        "latency_p99": f"{metrics.latency_p99:.2f}{metrics.latency_unit}",
        "latency_variance": f"{metrics.latency_variance:.2f}{metrics.latency_unit}",
        "tail_amplification": f"{metrics.tail_amplification:.2f}",
        "throughput": f"{metrics.throughput:.1f} {metrics.throughput_unit}",
        "throughput_variance": (
            f"{metrics.throughput_variance:.2f} {metrics.throughput_unit}"
        ),
        "error_rate": f"{metrics.error_rate * 100.0:.2f}%",
        "memory_pressure": f"{metrics.memory_pressure * 100.0:.2f}%",
        "buffer_miss_rate": f"{metrics.buffer_miss_rate * 100.0:.2f}%",
        "scan_efficiency": f"{metrics.scan_efficiency * 100.0:.1f}%",
        "total_queries": metrics.total_queries,
        "total_time": f"{metrics.total_time:.2f}s",
        "io_read_mb": f"{metrics.io_read_mb:.2f} MB",
        "io_write_mb": f"{metrics.io_write_mb:.2f} MB",
        "rows_examined": metrics.rows_examined,
        "rows_returned": metrics.rows_returned,
        "cache_hit_ratio": f"{metrics.cache_hit_ratio * 100.0:.1f}%",
        "memory_utilization": f"{metrics.memory_utilization * 100.0:.2f}%",
    }
