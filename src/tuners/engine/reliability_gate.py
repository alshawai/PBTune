"""
Reliability Gate for Workload Evaluation
========================================

Post-execution classification of an evaluation result. The gate runs *after*
workload execution succeeds but *before* scoring, inspecting the raw metrics and
assigning a ``failure_type`` when the run is degraded enough to distrust.

This is pure logic — it reads ``PerformanceMetrics`` and a logger, mutates the
metrics in place, and touches no orchestrator state. The thresholds live here as
module constants (single source of truth); ``WorkloadOrchestrator`` re-exports
them as class attributes for backwards-compatible overriding in tests/subclasses.
"""

from __future__ import annotations

import logging

from src.utils.metrics import PerformanceMetrics

# Thresholds for failure classification. Kept as module-level constants so they
# are easy to reference from a single source of truth.
HIGH_ERROR_RATE_THRESHOLD: float = 0.50
NEAR_ZERO_THROUGHPUT_THRESHOLD: float = 0.1
DEGRADED_ERROR_RATE_THRESHOLD: float = 0.10


def apply_reliability_gate(
    metrics: PerformanceMetrics,
    worker_logger: logging.Logger,
    *,
    high_error_rate_threshold: float = HIGH_ERROR_RATE_THRESHOLD,
    near_zero_throughput_threshold: float = NEAR_ZERO_THROUGHPUT_THRESHOLD,
    degraded_error_rate_threshold: float = DEGRADED_ERROR_RATE_THRESHOLD,
) -> None:
    """
    Classify the evaluation result and set ``failure_type`` if degraded.

    The gate runs *after* workload execution succeeds but *before* scoring.
    It inspects the raw metrics and assigns one of:

    * ``HIGH_ERROR_RATE`` — more than 50 % of queries failed.
    * ``NEAR_ZERO_THROUGHPUT`` — throughput is effectively zero, meaning
      the workload produced no useful work despite not crashing.
    * ``DEGRADED`` — error rate above 10 % but below the crash threshold,
      indicating partial failure that still produced some useful data.

    If the evaluation is healthy, ``failure_type`` remains ``None``.
    Only the first matching classification is applied (most severe first).

    Thresholds are injectable so callers can override the module defaults
    (e.g. ``WorkloadOrchestrator`` forwards its class-level attributes).
    """
    if metrics.failure_type is not None:
        # Already classified (e.g. EXECUTION_CRASH from the outer handler)
        return

    if metrics.error_rate >= high_error_rate_threshold:
        metrics.failure_type = "HIGH_ERROR_RATE"
        worker_logger.warning(
            " ➤ Reliability gate: error_rate=%.2f exceeds threshold %.2f — "
            "marking as HIGH_ERROR_RATE",
            metrics.error_rate,
            high_error_rate_threshold,
        )
        return

    if metrics.throughput <= near_zero_throughput_threshold:
        metrics.failure_type = "NEAR_ZERO_THROUGHPUT"
        worker_logger.warning(
            " ➤ Reliability gate: throughput=%.4f at or below threshold %.4f — "
            "marking as NEAR_ZERO_THROUGHPUT",
            metrics.throughput,
            near_zero_throughput_threshold,
        )
        return

    if metrics.error_rate >= degraded_error_rate_threshold:
        metrics.failure_type = "DEGRADED"
        worker_logger.warning(
            " ➤ Reliability gate: error_rate=%.2f exceeds degraded threshold "
            "%.2f — marking as DEGRADED",
            metrics.error_rate,
            degraded_error_rate_threshold,
        )
        return
