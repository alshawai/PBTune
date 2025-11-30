"""
Performance Metrics Module
==========================

This module defines performance metrics collection and composite scoring
for database workload evaluation. The scoring function is WORKLOAD-DEPENDENT,
as OLTP and OLAP workloads have fundamentally different optimization goals.

Key Concepts:
-------------
1. PerformanceMetrics: Raw measurements (latency, throughput, resources)
2. MetricConfig: Workload-specific weights and objectives
3. compute_score(): Composite score computation

Workload Types:
--------------
- OLTP (TPC-C, SYSBENCH): Prioritizes low latency and high throughput
- OLAP (TPC-H): Prioritizes query execution time and resource efficiency
- MIXED: Balanced approach

Mathematical Formulation:
------------------------
OLTP Score:
    score = w1 * (1 / latency_p95) + w2 * throughput + w3 * (1 - cpu_util)
    
OLAP Score:
    score = w1 * (1 / query_time) + w2 * (1 - mem_util) + w3 * (1 - cpu_util)

Higher score = better performance (we maximize this in PBT)
"""

from dataclasses import dataclass
from typing import Dict, Optional
from enum import Enum
import numpy as np


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
    throughput : float
        Transactions/queries per second
    total_queries : int
        Total number of queries executed
    total_time : float
        Total execution time in seconds
    error_rate : float
        Fraction of failed queries (0.0 to 1.0)
    cpu_utilization : float
        Average CPU utilization (0.0 to 1.0)
    memory_utilization : float
        Average memory utilization (0.0 to 1.0)
    io_read_mb : float
        Total MB read from disk
    io_write_mb : float
        Total MB written to disk
    cache_hit_ratio : float
        Buffer cache hit ratio (0.0 to 1.0)
    """

    latency_p50: float = 0.0
    latency_p95: float = 0.0
    latency_p99: float = 0.0

    throughput: float = 0.0  # Queries/second or TPS
    total_queries: int = 0
    total_time: float = 0.0

    error_rate: float = 0.0

    cpu_utilization: float = 0.0
    memory_utilization: float = 0.0

    io_read_mb: float = 0.0
    io_write_mb: float = 0.0

    cache_hit_ratio: float = 0.0

    def to_dict(self) -> Dict[str, float]:
        """Convert metrics to dictionary"""
        return {
            "latency_p50": self.latency_p50,
            "latency_p95": self.latency_p95,
            "latency_p99": self.latency_p99,
            "throughput": self.throughput,
            "total_queries": float(self.total_queries),
            "total_time": self.total_time,
            "error_rate": self.error_rate,
            "cpu_utilization": self.cpu_utilization,
            "memory_utilization": self.memory_utilization,
            "io_read_mb": self.io_read_mb,
            "io_write_mb": self.io_write_mb,
            "cache_hit_ratio": self.cache_hit_ratio,
        }

    def __repr__(self) -> str:
        """Human-readable representation"""
        return (
            f"PerformanceMetrics(\n"
            f"  Latency: p50={self.latency_p50:.2f}ms, "
            f"p95={self.latency_p95:.2f}ms, p99={self.latency_p99:.2f}ms\n"
            f"  Throughput: {self.throughput:.2f} TPS/QPS\n"
            f"  Queries: {self.total_queries} in {self.total_time:.2f}s\n"
            f"  Errors: {self.error_rate*100:.2f}%\n"
            f"  CPU: {self.cpu_utilization*100:.1f}%, "
            f"Memory: {self.memory_utilization*100:.1f}%\n"
            f"  Cache Hit: {self.cache_hit_ratio*100:.1f}%\n"
            f")"
        )


@dataclass
class MetricConfig:
    """
    Configuration for workload-specific metric computation.
    
    This defines how to compute a composite performance score
    from raw metrics, which varies by workload type.
    
    Attributes
    ----------
    workload_type : WorkloadType
        Type of workload (OLTP, OLAP, MIXED)
    weight_latency : float
        Weight for latency component
    weight_throughput : float
        Weight for throughput component
    weight_cpu : float
        Weight for CPU utilization component
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
    """

    workload_type: WorkloadType
    weight_latency: float = 0.5
    weight_throughput: float = 0.3
    weight_cpu: float = 0.1
    weight_memory: float = 0.05
    weight_error: float = 0.05
    latency_metric: str = "p95"  # 'p50', 'p95', or 'p99'
    normalize_by_baseline: bool = False
    baseline_metrics: Optional[PerformanceMetrics] = None

    def __post_init__(self):
        """Validate configuration"""
        total_weight = (
            self.weight_latency +
            self.weight_throughput +
            self.weight_cpu +
            self.weight_memory +
            self.weight_error
        )
        if not np.isclose(total_weight, 1.0, atol=0.01):
            raise ValueError(
                f"Weights must sum to 1.0, got {total_weight:.3f}. "
                f"Adjust weights: latency={self.weight_latency}, "
                f"throughput={self.weight_throughput}, "
                f"cpu={self.weight_cpu}, memory={self.weight_memory}, "
                f"error={self.weight_error}"
            )

        if self.latency_metric not in ["p50", "p95", "p99"]:
            raise ValueError(
                f"latency_metric must be 'p50', 'p95', or 'p99', "
                f"got '{self.latency_metric}'"
            )

    @staticmethod
    def for_oltp() -> 'MetricConfig':
        """Create OLTP-optimized metric configuration."""
        return OLTP_METRIC_CONFIG

    @staticmethod
    def for_olap() -> 'MetricConfig':
        """Create OLAP-optimized metric configuration."""
        return OLAP_METRIC_CONFIG

    @staticmethod
    def for_mixed() -> 'MetricConfig':
        """Create mixed workload metric configuration."""
        return MIXED_METRIC_CONFIG

    def compute_score(self, metrics: PerformanceMetrics) -> float:
        """
        Compute composite performance score.
        
        Higher score = better performance (for PBT maximization)
        
        Parameters
        ----------
        metrics : PerformanceMetrics
            Raw performance measurements
            
        Returns
        -------
        float
            Composite performance score (higher is better)
            
        Notes
        -----
        The score formula varies by workload type:
        
        OLTP (low latency + high throughput):
            score = w1 * (1/latency) + w2 * throughput - w3 * resources - w4 * errors
            
        OLAP (fast queries + efficient resources):
            score = w1 * (1/latency) + w2 * (1 - cpu) + w3 * (1 - mem) - w4 * errors
        
        We use reciprocal of latency so that lower latency → higher score.
        We subtract resource utilization so that efficiency → higher score.
        """
        score = 0.0

        latency = getattr(metrics, f"latency_{self.latency_metric}")
        if latency > 0:
            # Convert ms to seconds, take reciprocal, and add small epsilon
            latency_score = 1000.0 / (latency + 1e-6)
            score += self.weight_latency * latency_score

        if metrics.throughput > 0:
            # Normalize to a reasonable scale (so it doesn't dominate the score)
            throughput_score = min(metrics.throughput / 100.0, 1.0)
            score += self.weight_throughput * throughput_score

        cpu_score = 1.0 - metrics.cpu_utilization
        score += self.weight_cpu * cpu_score

        memory_score = 1.0 - metrics.memory_utilization
        score += self.weight_memory * memory_score

        error_penalty = 1.0 - metrics.error_rate
        score += self.weight_error * error_penalty

        if self.normalize_by_baseline and self.baseline_metrics is not None:
            baseline_score = self.compute_score(self.baseline_metrics)
            if baseline_score > 0:
                score = score / baseline_score

        return score

    def compute_detailed_scores(
        self,
        metrics: PerformanceMetrics
    ) -> Dict[str, float]:
        """
        Compute individual score components for analysis.
        
        Returns a dictionary showing contribution of each component.
        
        Parameters
        ----------
        metrics : PerformanceMetrics
            Raw performance measurements
            
        Returns
        -------
        Dict[str, float]
            Dictionary with score components and total
        """
        components = {}

        latency = getattr(metrics, f"latency_{self.latency_metric}")
        if latency > 0:
            latency_score = 1000.0 / (latency + 1e-6)
            components["latency"] = self.weight_latency * latency_score
        else:
            components["latency"] = 0.0

        if metrics.throughput > 0:
            throughput_score = min(metrics.throughput / 100.0, 1.0)
            components["throughput"] = self.weight_throughput * throughput_score
        else:
            components["throughput"] = 0.0

        components["cpu"] = self.weight_cpu * (1.0 - metrics.cpu_utilization)
        components["memory"] = self.weight_memory * (1.0 - metrics.memory_utilization)
        components["error"] = self.weight_error * (1.0 - metrics.error_rate)

        components["total"] = sum(components.values())

        return components


# Priorities: Low latency, High throughput
OLTP_METRIC_CONFIG = MetricConfig(
    workload_type=WorkloadType.OLTP,
    weight_latency=0.45,      # Primary: Fast response
    weight_throughput=0.35,   # Primary: High TPS
    weight_cpu=0.10,          # Secondary: Don't bottleneck
    weight_memory=0.05,       # Minor: Memory headroom
    weight_error=0.05,        # Minor: Error penalty
    latency_metric="p95",     # SLA-critical metric
)

# Priorities: Query execution time, Resource efficiency
OLAP_METRIC_CONFIG = MetricConfig(
    workload_type=WorkloadType.OLAP,
    weight_latency=0.50,      # Primary: Fast query completion
    weight_throughput=0.15,   # Minor: Queries per hour
    weight_cpu=0.15,          # Important: CPU efficiency
    weight_memory=0.15,       # Important: Memory efficiency
    weight_error=0.05,        # Minor: Error penalty
    latency_metric="p50",     # Median query time
)

# Balanced approach for hybrid workloads
MIXED_METRIC_CONFIG = MetricConfig(
    workload_type=WorkloadType.MIXED,
    weight_latency=0.40,
    weight_throughput=0.25,
    weight_cpu=0.15,
    weight_memory=0.10,
    weight_error=0.10,
    latency_metric="p95",
)


def create_metric_config(
    workload_type: str,
    **custom_weights
) -> MetricConfig:
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
        base_config = OLTP_METRIC_CONFIG
    elif workload_type_lower == "olap":
        base_config = OLAP_METRIC_CONFIG
    elif workload_type_lower == "mixed":
        base_config = MIXED_METRIC_CONFIG
    else:
        raise ValueError(
            f"Unknown workload_type: {workload_type}. "
            f"Must be 'oltp', 'olap', or 'mixed'"
        )

    if custom_weights:
        config_dict = {
            "workload_type": base_config.workload_type,
            "weight_latency": custom_weights.get("weight_latency", base_config.weight_latency),
            "weight_throughput": custom_weights.get(
                "weight_throughput",
                base_config.weight_throughput
                ),
            "weight_cpu": custom_weights.get("weight_cpu", base_config.weight_cpu),
            "weight_memory": custom_weights.get("weight_memory", base_config.weight_memory),
            "weight_error": custom_weights.get("weight_error", base_config.weight_error),
            "latency_metric": custom_weights.get("latency_metric", base_config.latency_metric),
        }
        return MetricConfig(**config_dict)

    return base_config
