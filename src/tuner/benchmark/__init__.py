"""
Workload Orchestration and Performance Metrics
==============================================

This package handles:
- Workload definition and execution
- Performance metrics collection
- Composite score computation (workload-dependent)
- Restart policy and tuning-mode logic
"""

from src.utils.metrics import (
    PerformanceMetrics,
    MetricConfig,
    WorkloadType,
    OLTP_METRIC_CONFIG,
    OLAP_METRIC_CONFIG,
)

from src.tuner.benchmark.restart_policy import TuningMode, should_restart

__all__ = [
    "PerformanceMetrics",
    "MetricConfig",
    "WorkloadType",
    "OLTP_METRIC_CONFIG",
    "OLAP_METRIC_CONFIG",
    "TuningMode",
    "should_restart",
]
