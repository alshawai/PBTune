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

from dataclasses import dataclass, field
from typing import Dict, Optional, List
import logging
from enum import Enum
import numpy as np

logger = logging.getLogger(__name__)


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

    throughput: float = 0.0
    throughput_unit: str = "TPS"

    total_queries: int = 0
    total_time: float = 0.0

    error_rate: float = 0.0

    memory_utilization: float = 0.0

    io_read_mb: float = 0.0
    io_write_mb: float = 0.0

    cache_hit_ratio: float = 0.0
    failure_type: Optional[str] = None

    def to_dict(self) -> Dict[str, float | str | None]:
        """Convert metrics to dictionary"""
        return {
            "latency_p50": self.latency_p50,
            "latency_p95": self.latency_p95,
            "latency_p99": self.latency_p99,
            "latency_unit": self.latency_unit,
            "throughput": self.throughput,
            "throughput_unit": self.throughput_unit,
            "total_queries": float(self.total_queries),
            "total_time": self.total_time,
            "error_rate": self.error_rate,
            "memory_utilization": self.memory_utilization,
            "io_read_mb": self.io_read_mb,
            "io_write_mb": self.io_write_mb,
            "cache_hit_ratio": self.cache_hit_ratio,
            "failure_type": self.failure_type,
        }

    def __repr__(self) -> str:
        """Human-readable representation"""
        return (
            f"PerformanceMetrics(\n"
            f"  Latency: p50={self.latency_p50:.2f}{self.latency_unit}, "
            f"p95={self.latency_p95:.2f}{self.latency_unit}, "
            f"p99={self.latency_p99:.2f}{self.latency_unit}\n"
            f"  Throughput: {self.throughput:.2f} {self.throughput_unit}\n"
            f"  Queries: {self.total_queries} in {self.total_time:.2f}s\n"
            f"  Errors: {self.error_rate*100:.2f}%\n"
            f"Memory: {self.memory_utilization*100:.1f}%\n"
            f"  Cache Hit: {self.cache_hit_ratio*100:.1f}%\n"
            f"  Failure Type: {self.failure_type}\n"
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

    _ranges_initialized: bool = field(default=False, init=False, repr=False)

    def __post_init__(self):
        """Validate configuration"""
        total_weight = (
            self.weight_latency +
            self.weight_throughput +
            self.weight_memory +
            self.weight_error
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

    def update_ranges(
        self,
        historical_metrics: List[PerformanceMetrics],
        padding_factor: float = 0.2
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
        """
        if len(historical_metrics) < 3:
            logger.warning(
                "Only %d metrics available. "
                "Need at least 3 for reliable range estimation. Skipping update.",
                len(historical_metrics)
            )
            return

        latencies = [
            getattr(m, f"latency_{self.latency_metric}")
            for m in historical_metrics
            if getattr(m, f"latency_{self.latency_metric}") > 0
        ]

        throughputs = [
            m.throughput for m in historical_metrics
            if m.throughput > 0
        ]
        if len(latencies) < 3 or len(throughputs) < 3:
            logger.warning(
                "Insufficient valid metrics (latency=%d, "
                "throughput=%d). Need at least 3 each.",
                len(latencies), len(throughputs)
            )
            return

        lat_p05 = np.percentile(latencies, 5)
        lat_p95 = np.percentile(latencies, 95)
        thr_p05 = np.percentile(throughputs, 5)
        thr_p95 = np.percentile(throughputs, 95)

        lat_range = lat_p95 - lat_p05
        thr_range = thr_p95 - thr_p05

        self.latency_min = float(max(0.1, lat_p05 - padding_factor * lat_range))
        self.latency_max = float(lat_p95 + padding_factor * lat_range)
        self.throughput_min = float(max(0.1, thr_p05 - padding_factor * thr_range))
        self.throughput_max = float(thr_p95 + padding_factor * thr_range)

        self._ranges_initialized = True
        logger.info(
            "Updated normalization ranges from %d observations:\n"
            "  Latency (%s): [%.2f, %.2f] ms\n"
            "  Throughput: [%.2f, %.2f] TPS\n"
            "  (using 5th/95th percentiles + %.0f%% padding)",
            len(historical_metrics), self.latency_metric,
            self.latency_min, self.latency_max,
            self.throughput_min, self.throughput_max,
            padding_factor * 100
        )

    def detect_saturation(
        self,
        metrics: PerformanceMetrics,
        saturation_threshold: float = 0.95
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
        saturation = {'latency': False, 'throughput': False, 'any': False}

        # Check latency saturation by computing its normalized value
        latency = getattr(metrics, f"latency_{self.latency_metric}")
        if latency > 0:
            # Clamp and normalize (same as in compute_score)
            latency_clamped = np.clip(latency, self.latency_min, self.latency_max)
            latency_normalized = (
                (self.latency_max - latency_clamped) /
                (self.latency_max - self.latency_min)
            )
            if latency_normalized >= saturation_threshold:
                saturation['latency'] = True

        # Check throughput saturation by computing its normalized value
        if metrics.throughput > 0:
            throughput_clamped = np.clip(
                metrics.throughput,
                self.throughput_min,
                self.throughput_max
            )
            throughput_normalized = (
                (throughput_clamped - self.throughput_min) /
                (self.throughput_max - self.throughput_min)
            )
            if throughput_normalized >= saturation_threshold:
                saturation['throughput'] = True

        saturation['any'] = saturation['latency'] or saturation['throughput']

        return saturation

    def expand_ranges_for_metrics(
        self,
        metrics_list: List[PerformanceMetrics],
        expansion_factor: float = 0.5
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

        latencies = [
            getattr(m, f"latency_{self.latency_metric}")
            for m in metrics_list
            if getattr(m, f"latency_{self.latency_metric}") > 0
        ]
        throughputs = [
            m.throughput for m in metrics_list
            if m.throughput > 0
        ]

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
                old_lat_min, old_lat_max, self.latency_min, self.latency_max,
                old_thr_min, old_thr_max, self.throughput_min, self.throughput_max,
                expansion_factor * 100
            )

        return expanded

    def compute_score(self, metrics: PerformanceMetrics) -> float:
        """
        Compute composite performance score using min-max normalization.
        
        Higher score = better performance (for PBT maximization)
        
        This approach ensures:
        1. All components are normalized to [0, 1] range
        2. Scores are comparable across different system configurations
        
        Parameters
        ----------
        metrics : PerformanceMetrics
            Raw performance measurements
            
        Returns
        -------
        float
            Composite performance score in range [0, 1] (higher is better)
            
        Notes
        -----
        Min-Max Normalization Formula:
        
        For "lower is better" metrics (latency):
            normalized = (max - value) / (max - min)
            → Low latency gets score close to 1.0
        
        For "higher is better" metrics (throughput):
            normalized = (value - min) / (max - min)
            → High throughput gets score close to 1.0
        
        For "lower is better" metrics already in [0,1] (utilization, error):
            normalized = 1 - value
        
        Final score = Σ(weight_i * normalized_component_i)
        """
        # Force score to 0.0 for dead workers (those that failed workload execution)
        if metrics.failure_type is not None:
            return 0.0

        score = 0.0

        latency = getattr(metrics, f"latency_{self.latency_metric}")
        latency_normalized = 0.0
        if latency > 0:
            latency_clamped = np.clip(latency, self.latency_min, self.latency_max)
            latency_normalized = (
                (self.latency_max - latency_clamped) /
                (self.latency_max - self.latency_min)
            )
            score += self.weight_latency * latency_normalized

        throughput_normalized = 0.0
        if metrics.throughput > 0:
            throughput_clamped = np.clip(
                metrics.throughput,
                self.throughput_min,
                self.throughput_max
            )
            throughput_normalized = (
                (throughput_clamped - self.throughput_min) /
                (self.throughput_max - self.throughput_min)
            )
            score += self.weight_throughput * throughput_normalized

        memory_utilization_clamped = np.clip(metrics.memory_utilization, 0.0, 1.0)
        memory_score = 1.0 - memory_utilization_clamped
        score += self.weight_memory * memory_score

        error_rate_clamped = np.clip(metrics.error_rate, 0.0, 1.0)
        error_score = 1.0 - error_rate_clamped
        score += self.weight_error * error_score

        score = max(0.0, score)

        if self.normalize_by_baseline and self.baseline_metrics is not None:
            baseline_score = self.compute_score(self.baseline_metrics)
            if baseline_score > 0:
                score = score / baseline_score

        return score * 100.0

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
            Dictionary with normalized score components and total
        """
        components = {}

        latency = getattr(metrics, f"latency_{self.latency_metric}")
        if latency > 0:
            latency_clamped = np.clip(latency, self.latency_min, self.latency_max)
            latency_normalized = (
                (self.latency_max - latency_clamped) /
                (self.latency_max - self.latency_min)
            )
            components["latency"] = self.weight_latency * latency_normalized
            components["latency_raw"] = latency
            components["latency_normalized"] = latency_normalized
        else:
            components["latency"] = 0.0
            components["latency_raw"] = 0.0
            components["latency_normalized"] = 0.0

        if metrics.throughput > 0:
            throughput_clamped = np.clip(
                metrics.throughput,
                self.throughput_min,
                self.throughput_max
            )
            throughput_normalized = (
                (throughput_clamped - self.throughput_min) /
                (self.throughput_max - self.throughput_min)
            )
            components["throughput"] = self.weight_throughput * throughput_normalized
            components["throughput_raw"] = metrics.throughput
            components["throughput_normalized"] = throughput_normalized
        else:
            components["throughput"] = 0.0
            components["throughput_raw"] = 0.0
            components["throughput_normalized"] = 0.0

        memory_utilization_clamped = np.clip(metrics.memory_utilization, 0.0, 1.0)
        memory_normalized = 1.0 - memory_utilization_clamped
        components["memory"] = self.weight_memory * memory_normalized
        components["memory_raw"] = metrics.memory_utilization
        components["memory_normalized"] = memory_normalized

        error_rate_clamped = np.clip(metrics.error_rate, 0.0, 1.0)
        error_normalized = 1.0 - error_rate_clamped
        components["error"] = self.weight_error * error_normalized
        components["error_raw"] = metrics.error_rate
        components["error_normalized"] = error_normalized

        components["total"] = (
            components["latency"] +
            components["throughput"] +
            components["memory"] +
            components["error"]
        ) * 100.0

        return components


# Priorities: Low latency, High throughput
OLTP_METRIC_CONFIG = MetricConfig(
    workload_type=WorkloadType.OLTP,
    weight_latency=0.50,      # Primary: Fast response
    weight_throughput=0.40,   # Primary: High TPS
    weight_memory=0.05,       # Minor: Memory headroom
    weight_error=0.05,        # Minor: Error penalty
    latency_metric="p95",     # SLA-critical metric
    latency_min=10.0,         # Fallback: 10ms
    latency_max=200.0,        # Fallback: 200ms
    throughput_min=10.0,      # Fallback: 10 TPS
    throughput_max=1000.0,    # Fallback: 1000 TPS
)

# Priorities: Outlier latency (P99) and Total Execution Time
OLAP_METRIC_CONFIG = MetricConfig(
    workload_type=WorkloadType.OLAP,
    weight_latency=0.55,      # Primary: Worst-case query (P99) must be bounded
    weight_throughput=0.30,   # Secondary: Total throughput (QphH)
    weight_memory=0.10,       # Regularization: Safe memory allocation
    weight_error=0.05,        # Necessity: Heavy penalty for fatal parameters
    latency_metric="p99",     # Academic Standard for analytical optimization
    latency_min=100.0,        # Fallback: 100ms
    latency_max=20000.0,      # Fallback: 20s
    throughput_min=10,        # Fallback: 10 QphH
    throughput_max=1000.0,    # Fallback: 1000 QphH
)

# Balanced approach for hybrid workloads
MIXED_METRIC_CONFIG = MetricConfig(
    workload_type=WorkloadType.MIXED,
    weight_latency=0.40,
    weight_throughput=0.35,
    weight_memory=0.15,
    weight_error=0.10,
    latency_metric="p95",
    latency_min=100.0,       # Fallback: 100ms
    latency_max=20000.0,     # Fallback: 20s
    throughput_min=10,       # Fallback: 10 TPS
    throughput_max=1000.0,   # Fallback: 1000 TPS
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
            "weight_memory": custom_weights.get("weight_memory", base_config.weight_memory),
            "weight_error": custom_weights.get("weight_error", base_config.weight_error),
            "latency_metric": custom_weights.get("latency_metric", base_config.latency_metric),
        }
        return MetricConfig(**config_dict)

    return base_config
