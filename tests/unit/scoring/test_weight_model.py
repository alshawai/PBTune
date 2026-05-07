"""
Unit tests for the feature-driven weight model and policy registry.
"""

import pytest
import numpy as np

from src.utils.scoring.weights import FeatureDrivenWeightModel
from src.utils.scoring.policies import (
    ScoringPolicySpec,
    POLICIES,
)


def test_weight_model_initialization():
    """Test weight model initializes with proper constraints."""
    metrics = ["throughput", "latency_p95"]
    base_weights = {"throughput": 0.5, "latency_p95": 0.5}
    floors = {"throughput": 0.1, "latency_p95": 0.1}
    coefficient_matrix = {
        "throughput": {"read_ratio": 0.1},
        "latency_p95": {"read_ratio": -0.1},
    }

    model = FeatureDrivenWeightModel(
        metrics=metrics,
        base_weights=base_weights,
        floors=floors,
        coefficient_matrix=coefficient_matrix,
    )

    assert model.metrics == metrics
    assert model.base_weights == base_weights
    assert model.floors == floors
    assert model.coefficient_matrix == coefficient_matrix


def test_weight_model_compute_weights_default():
    """Test weight model returns normalized weights when no features match."""
    metrics = ["throughput", "latency_p95"]
    base_weights = {"throughput": 0.5, "latency_p95": 0.5}
    floors = {"throughput": 0.1, "latency_p95": 0.1}
    coefficient_matrix = {
        "throughput": {"read_ratio": 0.2},
        "latency_p95": {"read_ratio": -0.2},
    }

    model = FeatureDrivenWeightModel(
        metrics=metrics,
        base_weights=base_weights,
        floors=floors,
        coefficient_matrix=coefficient_matrix,
    )

    # Empty features should yield weights respecting floors
    weights = model.compute_weights({})
    assert np.isclose(sum(weights.values()), 1.0)
    assert weights["throughput"] >= floors["throughput"]
    assert weights["latency_p95"] >= floors["latency_p95"]


def test_weight_model_compute_weights_with_features():
    """Test weight model shifts weights based on feature values."""
    metrics = ["throughput", "latency_p95", "memory_utilization"]
    base_weights = {
        "throughput": 0.4,
        "latency_p95": 0.4,
        "memory_utilization": 0.2,
    }
    floors = {
        "throughput": 0.1,
        "latency_p95": 0.1,
        "memory_utilization": 0.05,
    }
    coefficient_matrix = {
        "throughput": {"read_ratio": 0.2},
        "latency_p95": {"read_ratio": -0.1},
        "memory_utilization": {},
    }

    model = FeatureDrivenWeightModel(
        metrics=metrics,
        base_weights=base_weights,
        floors=floors,
        coefficient_matrix=coefficient_matrix,
    )

    # With high read ratio
    w1 = model.compute_weights({"read_ratio": 1.0})
    assert np.isclose(sum(w1.values()), 1.0)
    assert w1["throughput"] >= floors["throughput"]
    assert w1["latency_p95"] >= floors["latency_p95"]
    assert w1["memory_utilization"] >= floors["memory_utilization"]


def test_weight_model_floor_constraint():
    """Test floor constraint prevents weights from going below minimum."""
    metrics = ["metric_a", "metric_b"]
    base_weights = {"metric_a": 0.5, "metric_b": 0.5}
    floors = {"metric_a": 0.3, "metric_b": 0.3}
    coefficient_matrix = {
        "metric_a": {"feature": 1.0},
        "metric_b": {"feature": -1.0},
    }

    model = FeatureDrivenWeightModel(
        metrics=metrics,
        base_weights=base_weights,
        floors=floors,
        coefficient_matrix=coefficient_matrix,
    )

    w = model.compute_weights({"feature": 1.0})

    # Both weights should respect their floors
    assert w["metric_a"] >= floors["metric_a"]
    assert w["metric_b"] >= floors["metric_b"]
    assert np.isclose(sum(w.values()), 1.0)


def test_weight_model_invalid_floors():
    """Test that invalid floor constraints raise ValueError."""
    metrics = ["metric_a", "metric_b"]
    base_weights = {"metric_a": 0.5, "metric_b": 0.5}
    floors = {"metric_a": 0.6, "metric_b": 0.6}  # Sum > 1.0
    coefficient_matrix = {}

    with pytest.raises(ValueError, match="Sum of weight floors must be < 1.0"):
        FeatureDrivenWeightModel(
            metrics=metrics,
            base_weights=base_weights,
            floors=floors,
            coefficient_matrix=coefficient_matrix,
        )


def test_policy_registry():
    """Test scoring policy registry contains expected policies."""
    assert "fixed_v1" in POLICIES
    assert "feature_driven_v2" in POLICIES

    # Verify feature_driven_v2 policy structure
    policy = POLICIES["feature_driven_v2"]
    assert isinstance(policy, ScoringPolicySpec)
    assert policy.policy_id == "feature_driven_v2"
    assert policy.is_dynamic is True
    assert policy.weight_model is not None

    # Verify fixed_v1 policy structure
    fixed_policy = POLICIES["fixed_v1"]
    assert fixed_policy.policy_id == "fixed_v1"
    assert fixed_policy.is_dynamic is False
    assert fixed_policy.fixed_weights is not None
