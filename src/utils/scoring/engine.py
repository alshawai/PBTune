"""Unified scoring engine for policy-driven composite scores."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Dict, Optional, Tuple, TYPE_CHECKING
from logging import Logger

from src.utils.logger import get_logger, get_color_context
from src.utils.scoring.constants import METRIC_DIRECTIONALITY
from src.utils.scoring.contracts import MetricSnapshot, ScoreBreakdown

from src.utils.scoring.normalization import QuantileUtilityNormalizer
from src.utils.scoring.policies import (
    ScoringPolicySpec,
    resolve_fixed_v1_weights,
    resolve_policy,
)

LOGGER = get_logger("ScoringEngine")
COLORS = get_color_context()

if TYPE_CHECKING:
    from src.utils.metrics import PerformanceMetrics


@dataclass
class ScoringContext:
    """Cached context needed to compute scores."""

    policy_id: str
    workload_type: str
    latency_metric: str
    features: dict[str, float]
    weight_overrides: dict[str, float]

    def key(self) -> Tuple[str, str, str, Tuple[Tuple[str, float], ...], Tuple[Tuple[str, float], ...]]:
        """Return a hashable key for cache comparisons."""
        return (
            self.policy_id,
            self.workload_type,
            self.latency_metric,
            tuple(sorted(self.features.items())),
            tuple(sorted(self.weight_overrides.items())),
        )


class ScoringEngine:
    """Compute ScoreBreakdown outputs using cached policy weights."""

    def __init__(
        self,
        *,
        policy_id: str,
        workload_type: str,
        latency_metric: str,
        features: Optional[Dict[str, float]] = None,
        normalizer: Optional[QuantileUtilityNormalizer] = None,
        fatal_error_threshold: float = 0.05,
        weight_overrides: Optional[Dict[str, float]] = None,
    ) -> None:
        self._policy_id = policy_id
        self._workload_type = workload_type
        self._latency_metric = latency_metric
        self._features = features or {}
        self._weight_overrides = weight_overrides or {}
        self._normalizer = normalizer
        self._fatal_error_threshold = fatal_error_threshold

        self._policy: ScoringPolicySpec = resolve_policy(policy_id)
        self._weights: Dict[str, float] = {}
        self._metrics: list[str] = []
        self._context_key: Optional[Tuple[Tuple[str, float], ...] | Tuple] = None

        self._refresh_cached_state()

    def update_context(
        self,
        *,
        policy_id: Optional[str] = None,
        workload_type: Optional[str] = None,
        latency_metric: Optional[str] = None,
        features: Optional[Dict[str, float]] = None,
        weight_overrides: Optional[Dict[str, float]] = None,
    ) -> None:
        """Update scoring context and refresh cached weights if needed."""
        if policy_id is not None:
            self._policy_id = policy_id
        if workload_type is not None:
            self._workload_type = workload_type
        if latency_metric is not None:
            self._latency_metric = latency_metric
        if features is not None:
            self._features = dict(features)
        if weight_overrides is not None:
            self._weight_overrides = dict(weight_overrides)

        self._refresh_cached_state()

    def set_normalizer(self, normalizer: Optional[QuantileUtilityNormalizer]) -> None:
        """Replace the active normalizer."""
        self._normalizer = normalizer

    def compute_breakdown(
        self, metrics: PerformanceMetrics, worker_logger: Optional[Logger] = None
    ) -> ScoreBreakdown:
        """Compute the full ScoreBreakdown for one metrics snapshot."""
        logger = worker_logger or LOGGER
        gate = self._compute_reliability_gate(metrics, logger)
        if gate == 0.0:
            return ScoreBreakdown(
                final_score=0.0,
                policy=self._policy.policy_id,
                policy_version=self._policy.version,
                reliability_gate=0.0,
                components=[],
                metadata={
                    "reason": "reliability_gate",
                    "failure_type": metrics.failure_type,
                },
            )

        utilities = self._score_utilities(metrics, logger)
        raw_values = metrics.to_dict()

        components: list[MetricSnapshot] = []
        missing_utilities: list[str] = []
        total = 0.0

        for metric in self._metrics:
            weight = self._weights.get(metric, 0.0)
            if weight == 0.0:
                continue

            if metric in utilities:
                util = float(utilities[metric])
            else:
                util = 0.5
                missing_utilities.append(metric)

            raw_value = float(raw_values.get(metric, 0.0) or 0.0)
            contribution = weight * util * gate
            total += contribution

            components.append(
                MetricSnapshot(
                    metric_id=metric,
                    raw_value=raw_value,
                    normalized_value=util,
                    weight=weight,
                    weighted_contribution=contribution * 100.0,
                    directionality=METRIC_DIRECTIONALITY.get(
                        metric, "lower_is_better"
                    ),
                )
            )

        breakdown = ScoreBreakdown(
            final_score=total * 100.0,
            policy=self._policy.policy_id,
            policy_version=self._policy.version,
            reliability_gate=gate,
            components=components,
            metadata={
                "weights": dict(self._weights),
                "missing_utilities": missing_utilities,
            },
        )

        logger.info(
            " %s➤ Score computed: total=%.3f%% (gate=%.2f, components=%d)%s",
            COLORS.bold,
            breakdown.final_score,
            gate,
            len(components),
            COLORS.reset
        )
        return breakdown

    def _refresh_cached_state(self) -> None:
        context = ScoringContext(
            policy_id=self._policy_id,
            workload_type=self._workload_type,
            latency_metric=self._latency_metric,
            features=self._features,
            weight_overrides=self._weight_overrides,
        )
        context_key = context.key()
        if context_key == self._context_key:
            return

        self._context_key = context_key
        self._policy = resolve_policy(self._policy_id)

        if not self._policy.is_dynamic:
            weights, metrics = resolve_fixed_v1_weights(
                self._policy,
                workload_type=self._workload_type,
                latency_metric=self._latency_metric,
                weight_overrides=self._weight_overrides,
            )
            self._weights = weights
            self._metrics = metrics
            return

        if self._policy.weight_model is None:
            LOGGER.warning(
                "No weight model available for dynamic policy %s",
                self._policy.policy_id,
            )
            self._weights = {}
            self._metrics = list(self._policy.metrics)
            return

        self._weights = self._policy.weight_model.compute_weights(self._features)
        self._metrics = list(self._policy.metrics)

        if self._weight_overrides:
            for key, value in self._weight_overrides.items():
                self._weights[key] = value
                if key not in self._metrics:
                    self._metrics.append(key)

    def _compute_reliability_gate(
        self, metrics: PerformanceMetrics, logger
    ) -> float:
        if metrics.failure_type is not None:
            logger.warning(
                "  %sReliability gate = 0.0 (failure_type=%s)%s",
                COLORS.italic,
                metrics.failure_type,
                COLORS.reset
            )
            return 0.0

        if metrics.error_rate >= self._fatal_error_threshold:
            logger.warning(
                "  %sReliability gate = 0.0 (error_rate=%.4f >= threshold=%.4f)%s",
                COLORS.italic,
                metrics.error_rate,
                self._fatal_error_threshold,
                COLORS.reset
            )
            return 0.0

        if metrics.error_rate > 0:
            gate = 1.0 - (metrics.error_rate / self._fatal_error_threshold)
            logger.debug(
                "  %sReliability gate = %.4f (error_rate=%.4f, linear decay)%s",
                COLORS.italic,
                gate,
                metrics.error_rate,
                COLORS.reset
            )
            return gate

        return 1.0

    def _score_utilities(
        self, metrics: PerformanceMetrics, worker_logger: Optional[Logger] = None
    ) -> Dict[str, float]:
        if self._normalizer is None:
            return {}

        return self._normalizer.score_metrics(
            metrics,
            metric_whitelist=self._metrics,
            worker_logger=worker_logger,
        )
