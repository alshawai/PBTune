"""
Scoring Policies
================

Defines the weight models and frozen policy versions for composite scoring.

Policies:
- `fixed_v1`: Legacy static weights based on workload type (OLTP/OLAP/MIXED).
- `feature_driven_v2`: Dynamic weights based on workload features and a
  coefficient matrix, evaluating variance, tail amplification, and DB stats.
"""

from dataclasses import dataclass
from typing import Dict, List, Optional
from src.utils.scoring.weights import FeatureDrivenWeightModel

POLICY_VERSION_FIXED_V1 = "1.0.0"
POLICY_VERSION_FEATURE_DRIVEN_V2 = "2.0.0"


@dataclass
class ScoringPolicySpec:
    """Specification for a scoring policy."""

    policy_id: str
    version: str
    metrics: List[str]
    is_dynamic: bool

    # For fixed_v1
    fixed_weights: Optional[Dict[str, Dict[str, float]]] = None

    # For feature_driven_v2
    weight_model: Optional[FeatureDrivenWeightModel] = None


# ---------------------------------------------------------------------------
# Policy: fixed_v1
# ---------------------------------------------------------------------------
# Legacy compatibility policy matching exact weights from before migration.
# Missing metrics default to weight 0.0.

FIXED_V1_METRICS = [
    "latency_p50",
    "latency_p95",
    "latency_p99",
    "throughput",
    "memory_utilization",
    "error_rate",
]

FIXED_V1_WEIGHTS = {
    "oltp": {
        "latency_p50": 0.0,
        "latency_p95": 0.5,
        "latency_p99": 0.0,
        "throughput": 0.3,
        "memory_utilization": 0.05,
        "error_rate": 0.15,
    },
    "olap": {
        "latency_p50": 0.0,
        "latency_p95": 0.8,
        "latency_p99": 0.0,
        "throughput": 0.0,
        "memory_utilization": 0.05,
        "error_rate": 0.15,
    },
    "mixed": {
        "latency_p50": 0.0,
        "latency_p95": 0.4,
        "latency_p99": 0.0,
        "throughput": 0.4,
        "memory_utilization": 0.05,
        "error_rate": 0.15,
    },
}

FIXED_V1_POLICY = ScoringPolicySpec(
    policy_id="fixed_v1",
    version=POLICY_VERSION_FIXED_V1,
    metrics=FIXED_V1_METRICS,
    is_dynamic=False,
    fixed_weights=FIXED_V1_WEIGHTS,
)


# ---------------------------------------------------------------------------
# Policy: feature_driven_v2
# ---------------------------------------------------------------------------
# V2 dynamic policy uses the floor-constrained softmax weight model.
# Incorporates new metrics: tail amplification, variance, and DB internals.

V2_METRICS = [
    "latency_p95",
    "latency_p99",
    "latency_variance",
    "tail_amplification",
    "throughput",
    "throughput_variance",
    "error_rate",
    "memory_pressure",
    "scan_efficiency",
    "buffer_miss_rate",
]

# Alpha values for floor-constrained softmax.
# Reduced total from 0.55 → 0.30 to allow more dynamic adaptation.
V2_FLOORS = {
    "latency_p95": 0.10,  # Minimum guarantee for primary OLTP SLA metric
    "throughput": 0.10,  # Minimum guarantee for secondary OLTP metric
    "error_rate": 0.05,  # Safety floor
    "latency_p99": 0.05,  # Tail latency floor — OtterTune default objective
}

# Base logits (before features apply).
# Positive = higher base importance; negative = suppressed unless features activate.
# Literature basis: CDBTune (60% latency / 40% throughput for OLTP), OtterTune (P99 primary).
V2_BASE_WEIGHTS = {
    "latency_p95": 1.2,  # Primary OLTP SLA metric
    "latency_p99": 1.0,  # OtterTune default target; tail latency
    "throughput": 1.2,  # Secondary OLTP metric
    "error_rate": 0.3,  # Safety signal
    "latency_variance": 0.0,  # Diagnostic; activated by write_ratio/tail_sensitivity
    "tail_amplification": -0.5,  # Diagnostic; suppressed for OLTP, activated by olap_complexity
    "throughput_variance": 0.0,  # Stability diagnostic
    "memory_pressure": -0.3,  # Resource; low for small OLTP, grows with working set
    "scan_efficiency": -1.0,  # Irrelevant for OLTP indexed lookups; activated by olap_complexity
    "buffer_miss_rate": -0.3,  # Resource; grows with working set via log-saturated feature
}

# Feature coefficient matrix (M).
# w_i = base_i + sum_j(M_ij * f_j)  — passed through floor-constrained softmax.
#
# Design principles:
#   - OLTP: latency/throughput dominate; resource metrics grow with working_set_millions
#   - OLAP: scan_efficiency/tail_amplification dominate via olap_complexity and join_intensity
#   - working_set_millions is passed through log1p() in compute_weights() to prevent
#     softmax domination at large scale factors (>1M rows)
#   - read_ratio removed from scan_efficiency/buffer_miss_rate: OLTP index lookups
#     have constant scan efficiency regardless of read ratio; buffer misses depend on
#     working set size, not the read/write split
V2_COEFFICIENTS = {
    "latency_p95": {"write_ratio": 0.8, "concurrency_pressure": 0.6},
    "latency_p99": {
        "tail_latency_sensitivity": 1.2,
        "write_ratio": 0.4,
        "concurrency_pressure": 0.3,
    },
    "latency_variance": {
        "write_ratio": 1.2,
        "olap_complexity": 0.5,
        "tail_latency_sensitivity": 0.8,
    },
    "tail_amplification": {"olap_complexity": 1.5, "tail_latency_sensitivity": 1.0},
    "throughput": {"concurrency_pressure": -0.4, "write_ratio": -0.3},
    "throughput_variance": {"write_ratio": 1.0, "concurrency_pressure": 0.8},
    "error_rate": {"concurrency_pressure": 0.3},
    "memory_pressure": {"working_set_millions": 0.3, "concurrency_pressure": 0.5},
    "scan_efficiency": {"olap_complexity": 2.5, "join_intensity": 2.0},
    "buffer_miss_rate": {"working_set_millions": 0.3, "olap_complexity": 0.5},
}

V2_WEIGHT_MODEL = FeatureDrivenWeightModel(
    metrics=V2_METRICS,
    base_weights=V2_BASE_WEIGHTS,
    floors=V2_FLOORS,
    coefficient_matrix=V2_COEFFICIENTS,
    temperature=1.0,
)

FEATURE_DRIVEN_V2_POLICY = ScoringPolicySpec(
    policy_id="feature_driven_v2",
    version=POLICY_VERSION_FEATURE_DRIVEN_V2,
    metrics=V2_METRICS,
    is_dynamic=True,
    weight_model=V2_WEIGHT_MODEL,
)

# Global Registry
POLICIES = {
    "fixed_v1": FIXED_V1_POLICY,
    "feature_driven_v2": FEATURE_DRIVEN_V2_POLICY,
}
