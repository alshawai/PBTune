"""Composite scorer for policy-driven performance scoring."""

from __future__ import annotations

from logging import Logger
from typing import Dict, Optional, TYPE_CHECKING

from src.utils.logger import get_logger, get_color_context
from src.utils.logger.helpers import (
    log_feature_weight_table,
    log_weight_snapshot_table,
)
from src.utils.scoring.constants import METRIC_DIRECTIONALITY
from src.utils.scoring.contracts import MetricSnapshot, ScoreBreakdown
from src.utils.scoring.normalization import QuantileUtilityNormalizer
from src.utils.scoring.policies import resolve_fixed_v1_weights, resolve_policy

LOGGER = get_logger("CompositeScorer")
COLORS = get_color_context()

if TYPE_CHECKING:
    from src.utils.metrics import PerformanceMetrics


class CompositeScorer:
    """Compute composite score and detailed breakdowns for a metrics snapshot."""

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
        self._normalizer = normalizer
        self._fatal_error_threshold = fatal_error_threshold
        self._weight_overrides = weight_overrides or {}

        self._policy = resolve_policy(policy_id)
        self._weights: Dict[str, float] = {}
        self._metrics: list[str] = []
        self._context_key: tuple | None = None
        self._logged_initial_weights = False
        self._last_logged_weights: Dict[str, float] | None = None
        self._log_weights_next_generation = True

        self._resolve_weights(log_initial=False)

    def update_context(
        self,
        *,
        policy_id: Optional[str] = None,
        workload_type: Optional[str] = None,
        latency_metric: Optional[str] = None,
        features: Optional[Dict[str, float]] = None,
        weight_overrides: Optional[Dict[str, float]] = None,
        update_reason: Optional[str] = None,
    ) -> bool:
        """Update scorer context and recompute weights if anything changed."""
        new_policy_id = policy_id if policy_id is not None else self._policy_id
        new_workload_type = (
            workload_type if workload_type is not None else self._workload_type
        )
        new_latency_metric = (
            latency_metric if latency_metric is not None else self._latency_metric
        )
        new_features = dict(features) if features is not None else self._features
        new_overrides = (
            dict(weight_overrides) if weight_overrides is not None else self._weight_overrides
        )

        new_key = self._make_context_key(
            new_policy_id,
            new_workload_type,
            new_latency_metric,
            new_features,
            new_overrides,
        )
        if new_key == self._context_key:
            return False

        self._policy_id = new_policy_id
        self._workload_type = new_workload_type
        self._latency_metric = new_latency_metric
        self._features = new_features
        self._weight_overrides = new_overrides
        self._policy = resolve_policy(self._policy_id)

        self._resolve_weights(log_initial=False)

        if update_reason == "feature_refinement":
            LOGGER.info(
                "%s➤ Weights are updated due to feature refinement.%s",
                COLORS.bold,
                COLORS.reset,
            )

        return True

    def compute_breakdown(
        self, metrics: PerformanceMetrics, worker_logger: Optional[Logger] = None
    ) -> ScoreBreakdown:
        """Compute a full ScoreBreakdown for a metrics snapshot."""
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
                    directionality=METRIC_DIRECTIONALITY.get(metric, "lower_is_better"),
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
            COLORS.reset,
        )
        return breakdown

    def schedule_log_next_generation(self) -> None:
        self._log_weights_next_generation = True

    def log_generation_weights(self, *, generation: int) -> None:
        """Log weight snapshot table at generation 0 or after updates."""
        if generation != 0 and not self._log_weights_next_generation:
            return

        current = dict(self._weights)
        if generation == 0:
            deltas: Dict[str, float] = {}
        else:
            previous = self._last_logged_weights or {}
            deltas = {
                metric: current.get(metric, 0.0) - previous.get(metric, 0.0)
                for metric in current
            }

        if generation == 0:
            log_feature_weight_table(
                LOGGER,
                self._features,
                current,
                generation=generation,
            )
        else:
            log_weight_snapshot_table(
                LOGGER,
                current,
                deltas,
                generation=generation,
            )

        self._last_logged_weights = current
        self._log_weights_next_generation = False

    def _resolve_weights(self, *, log_initial: bool) -> None:
        if not self._policy.is_dynamic:
            weights, metrics = resolve_fixed_v1_weights(
                self._policy,
                workload_type=self._workload_type,
                latency_metric=self._latency_metric,
                weight_overrides=self._weight_overrides,
            )
            self._weights = weights
            self._metrics = metrics
            self._context_key = self._make_context_key(
                self._policy_id,
                self._workload_type,
                self._latency_metric,
                self._features,
                self._weight_overrides,
            )
            self._log_initial_weights(log_initial)
            return

        if self._policy.weight_model is None:
            LOGGER.warning(
                "No weight model available for dynamic policy %s",
                self._policy.policy_id,
            )
            self._weights = {}
            self._metrics = list(self._policy.metrics)
            self._context_key = self._make_context_key(
                self._policy_id,
                self._workload_type,
                self._latency_metric,
                self._features,
                self._weight_overrides,
            )
            self._log_initial_weights(log_initial)
            return

        self._weights = self._policy.weight_model.compute_weights(
            self._features,
            log_weights=False,
        )
        self._metrics = list(self._policy.metrics)

        if self._weight_overrides:
            for key, value in self._weight_overrides.items():
                self._weights[key] = value
                if key not in self._metrics:
                    self._metrics.append(key)

        self._context_key = self._make_context_key(
            self._policy_id,
            self._workload_type,
            self._latency_metric,
            self._features,
            self._weight_overrides,
        )
        self._log_initial_weights(log_initial)

    def _make_context_key(
        self,
        policy_id: str,
        workload_type: str,
        latency_metric: str,
        features: Dict[str, float],
        weight_overrides: Dict[str, float],
    ) -> tuple:
        return (
            policy_id,
            workload_type,
            latency_metric,
            tuple(sorted(features.items())),
            tuple(sorted(weight_overrides.items())),
        )

    def _log_initial_weights(self, log_initial: bool) -> None:
        if not log_initial or self._logged_initial_weights:
            return

        LOGGER.debug(
            "  %s➤ Computed Weights: %s%s",
            COLORS.italic,
            ", ".join(f"{m} >> {w:.4f}" for m, w in self._weights.items()),
            COLORS.reset,
        )
        self._logged_initial_weights = True


    def _compute_reliability_gate(
        self, metrics: PerformanceMetrics, logger: Logger
    ) -> float:
        if metrics.failure_type is not None:
            logger.warning(
                "  %sReliability gate = 0.0 (failure_type=%s)%s",
                COLORS.italic,
                metrics.failure_type,
                COLORS.reset,
            )
            return 0.0

        if metrics.error_rate >= self._fatal_error_threshold:
            logger.warning(
                "  %sReliability gate = 0.0 (error_rate=%.4f >= threshold=%.4f)%s",
                COLORS.italic,
                metrics.error_rate,
                self._fatal_error_threshold,
                COLORS.reset,
            )
            return 0.0

        if metrics.error_rate > 0:
            gate = 1.0 - (metrics.error_rate / self._fatal_error_threshold)
            logger.debug(
                "  %sReliability gate = %.4f (error_rate=%.4f, linear decay)%s",
                COLORS.italic,
                gate,
                metrics.error_rate,
                COLORS.reset,
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
