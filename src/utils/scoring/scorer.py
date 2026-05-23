"""
Composite Scorer
================

Orchestrates the computation of the final PBT reward signal by combining
the active scoring policy (weights), utility normalization, and reliability gating.
"""

from typing import Dict, Optional, Tuple

from src.utils.logger import get_logger, get_color_context
from src.utils.metrics import PerformanceMetrics
from src.utils.scoring.policies import ScoringPolicySpec
from src.utils.scoring.normalization import QuantileUtilityNormalizer

LOGGER = get_logger("Scorer")
COLORS = get_color_context()


class CompositeScorer:
    """
    Computes final bounded score by applying weights and reliability gating.

    Score = G * sum(W_i * U_i)
    where:
      - G is the reliability gate [0, 1]
      - W_i is the dynamic/static weight for metric i
      - U_i is the normalized utility [0, 1] for metric i
    """

    def __init__(
        self,
        policy: ScoringPolicySpec,
        normalizer: Optional[QuantileUtilityNormalizer] = None,
        workload_type: str = "oltp",
        features: Optional[Dict[str, float]] = None,
        fatal_error_threshold: float = 0.05,
        weight_overrides: Optional[Dict[str, float]] = None,
    ):
        """
        Parameters
        ----------
        policy : ScoringPolicySpec
            The active scoring policy.
        normalizer : Optional[QuantileUtilityNormalizer]
            The normalizer to compute U_i. If None, it expects pre-normalized values
            or uses a fallback (mostly for compatibility paths).
        workload_type : str
            Workload identifier (oltp, olap, mixed) for static weight lookup.
        features : Optional[Dict[str, float]]
            Workload features for dynamic weighting.
        fatal_error_threshold : float
            Error rate above which the score is penalized to 0.
        weight_overrides : Optional[Dict[str, float]]
            Overrides for static weights (used for backward compatibility).
        """
        self.policy = policy
        self.normalizer = normalizer
        self.workload_type = workload_type
        self.features = features or {}
        self.fatal_error_threshold = fatal_error_threshold
        self.weight_overrides = weight_overrides or {}

        # Compute active weights upfront
        self.weights = self._compute_active_weights()

    def _compute_active_weights(self) -> Dict[str, float]:
        """Resolve metric weights based on policy rules."""
        if not self.policy.is_dynamic:
            if not self.policy.fixed_weights:
                weights = {
                    m: 1.0 / len(self.policy.metrics) for m in self.policy.metrics
                }
                LOGGER.debug(
                    "Using uniform weights (policy=%s): %s",
                    self.policy.policy_id,
                    {k: f"{v:.4f}" for k, v in weights.items()},
                )
            else:
                weights = self.policy.fixed_weights.get(self.workload_type, {}).copy()
                LOGGER.debug(
                    "Using fixed weights (policy=%s, workload=%s): %s",
                    self.policy.policy_id,
                    self.workload_type,
                    {k: f"{v:.4f}" for k, v in weights.items()},
                )

            # Apply legacy overrides
            if self.weight_overrides:
                LOGGER.debug(
                    "Applying weight overrides: %s",
                    {k: f"{v:.4f}" for k, v in self.weight_overrides.items()},
                )
                for k, v in self.weight_overrides.items():
                    weights[k] = v

            return weights

        if self.policy.weight_model:
            weights = self.policy.weight_model.compute_weights(self.features)
            LOGGER.debug(
                "Using dynamic weights (policy=%s, model=%s): %s",
                self.policy.policy_id,
                self.policy.weight_model.__class__.__name__,
                {k: f"{v:.4f}" for k, v in weights.items()},
            )
            return weights

        LOGGER.warning(
            "No weight model available for dynamic policy %s", self.policy.policy_id
        )
        return {}

    def _compute_reliability_gate(self, metrics: PerformanceMetrics) -> float:
        """
        Compute reliability gate G in [0, 1].

        If evaluation failed entirely, G = 0.
        If error rate is too high, G decays to 0.
        """
        if metrics.failure_type is not None:
            LOGGER.warning(
                "Reliability gate = 0.0 (failure_type=%s)", metrics.failure_type
            )
            return 0.0

        if metrics.error_rate >= self.fatal_error_threshold:
            LOGGER.warning(
                "Reliability gate = 0.0 (error_rate=%.4f >= threshold=%.4f)",
                metrics.error_rate,
                self.fatal_error_threshold,
            )
            return 0.0

        if metrics.error_rate > 0:
            gate = 1.0 - (metrics.error_rate / self.fatal_error_threshold)
            LOGGER.debug(
                "Reliability gate = %.4f (error_rate=%.4f, linear decay)",
                gate,
                metrics.error_rate,
            )
            return gate

        LOGGER.debug("Reliability gate = 1.0 (no errors)")
        return 1.0

    def compute_detailed_score(
        self,
        metrics: PerformanceMetrics,
        fallback_utilities: Optional[Dict[str, float]] = None,
    ) -> Tuple[Dict[str, float], float]:
        """
        Compute score and return breakdown of components.

        Returns
        -------
        Tuple[Dict[str, float], float]
            (component_scores, total_score)
        """
        gate = self._compute_reliability_gate(metrics)
        if gate == 0.0:
            LOGGER.warning("Score computation aborted: reliability gate = 0.0")
            return {}, 0.0

        # Score utilities
        if self.normalizer is not None and self.normalizer.is_calibrated:
            utilities = self.normalizer.score_metrics(metrics)
            LOGGER.debug(
                "Utilities from normalizer: %s",
                {k: f"{v:.4f}" for k, v in utilities.items()},
            )
        else:
            utilities = fallback_utilities or {}
            if self.normalizer is None:
                LOGGER.debug("Normalizer not available, using fallback utilities")
            else:
                LOGGER.debug(
                    "Normalizer uncalibrated (samples=%d), using fallback utilities",
                    self.normalizer.total_samples_since_calibration,
                )
            if utilities:
                LOGGER.debug(
                    "Fallback utilities: %s",
                    {k: f"{v:.4f}" for k, v in utilities.items()},
                )

        components = {}
        total_score = 0.0
        missing_metrics = []

        for metric, weight in self.weights.items():
            if weight == 0.0:
                continue

            if metric in utilities:
                u = utilities[metric]
            else:
                u = 0.5
                missing_metrics.append(metric)

            contribution = weight * u * gate
            components[metric] = contribution
            total_score += contribution

        if missing_metrics:
            # Only log missing utilities if the normalizer is actively calibrated.
            # During Generation 0 (uncalibrated), the system intentionally falls back
            # to a subset of legacy metrics, making this warning noisy and expected.
            is_calibrated = (
                self.normalizer is not None and self.normalizer.is_calibrated
            )
            if is_calibrated:
                LOGGER.debug(
                    "Missing utilities for %d metrics (using default 0.5): %s",
                    len(missing_metrics),
                    missing_metrics,
                )

        LOGGER.debug(
            "Score computation complete: total=%.4f (gate=%.4f, %d components)",
            total_score,
            gate,
            len(components),
        )

        return components, total_score
