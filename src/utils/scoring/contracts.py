"""Typed contracts for feature-driven scoring metadata and breakdowns."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

from src.utils.scoring.constants import (
    DEFAULT_SCORING_POLICY,
    DEFAULT_SCORING_POLICY_VERSION,
    DEFAULT_METRIC_REFERENCE_VERSION,
)


@dataclass
class WorkloadFeatures:
    """Feature vector and extraction metadata for a workload."""

    features: dict[str, float] = field(default_factory=dict)
    source: str = "static"
    version: str = "1.0"

    def to_dict(self) -> dict[str, Any]:
        """Convert workload feature state into a serializable dictionary."""
        return {
            "features": dict(self.features),
            "source": self.source,
            "version": self.version,
        }


@dataclass
class MetricSnapshot:
    """Per-metric contribution snapshot used to explain a composite score."""

    metric_id: str
    raw_value: float
    normalized_value: float
    weight: float
    weighted_contribution: float
    directionality: str

    def to_dict(self) -> dict[str, Any]:
        """Convert metric snapshot into a serializable dictionary."""
        return {
            "metric_id": self.metric_id,
            "raw_value": self.raw_value,
            "normalized_value": self.normalized_value,
            "weight": self.weight,
            "weighted_contribution": self.weighted_contribution,
            "directionality": self.directionality,
        }


@dataclass
class ScoreBreakdown:
    """Detailed representation of a score and its component contributions."""

    final_score: float
    policy: str = DEFAULT_SCORING_POLICY
    policy_version: str = DEFAULT_SCORING_POLICY_VERSION
    reliability_gate: float = 1.0
    components: list[MetricSnapshot] = field(default_factory=list)
    metadata: dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        """Convert score breakdown into a serializable dictionary."""
        return {
            "final_score": self.final_score,
            "policy": self.policy,
            "policy_version": self.policy_version,
            "reliability_gate": self.reliability_gate,
            "components": [component.to_dict() for component in self.components],
            "metadata": dict(self.metadata),
        }


@dataclass
class NormalizationState:
    """Normalization state exported for reproducible rescoring."""

    normalizer: str = "quantile_utility"
    metric_reference_version: str = DEFAULT_METRIC_REFERENCE_VERSION
    ranges: dict[str, dict[str, float]] = field(default_factory=dict)
    metadata: dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        """Convert normalization state into a serializable dictionary."""
        return {
            "normalizer": self.normalizer,
            "metric_reference_version": self.metric_reference_version,
            "ranges": {metric: dict(bounds) for metric, bounds in self.ranges.items()},
            "metadata": dict(self.metadata),
        }


def score_breakdown_from_dict(payload: dict[str, Any]) -> ScoreBreakdown:
    """Parse a ScoreBreakdown from serialized metadata (legacy-safe)."""
    if not payload:
        return ScoreBreakdown(final_score=0.0)

    if "final_score" in payload:
        components = []
        for item in payload.get("components", []):
            if not isinstance(item, dict):
                continue
            components.append(
                MetricSnapshot(
                    metric_id=str(item.get("metric_id", "")),
                    raw_value=float(item.get("raw_value", 0.0) or 0.0),
                    normalized_value=float(item.get("normalized_value", 0.0) or 0.0),
                    weight=float(item.get("weight", 0.0) or 0.0),
                    weighted_contribution=float(
                        item.get("weighted_contribution", 0.0) or 0.0
                    ),
                    directionality=str(item.get("directionality", "lower_is_better")),
                )
            )
        return ScoreBreakdown(
            final_score=float(payload.get("final_score", 0.0) or 0.0),
            policy=str(payload.get("policy", DEFAULT_SCORING_POLICY)),
            policy_version=str(
                payload.get("policy_version", DEFAULT_SCORING_POLICY_VERSION)
            ),
            reliability_gate=float(payload.get("reliability_gate", 1.0) or 1.0),
            components=components,
            metadata=dict(payload.get("metadata", {})),
        )

    legacy_score = float(payload.get("total", payload.get("score", 0.0)) or 0.0)
    return ScoreBreakdown(
        final_score=legacy_score,
        policy=str(payload.get("policy", DEFAULT_SCORING_POLICY)),
        policy_version=str(
            payload.get("policy_version", DEFAULT_SCORING_POLICY_VERSION)
        ),
        reliability_gate=float(payload.get("reliability_gate", 1.0) or 1.0),
        components=[],
        metadata={"legacy_payload": dict(payload)},
    )
