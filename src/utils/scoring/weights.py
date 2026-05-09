"""
Feature-Driven Weight Model
===========================

Implements dynamic weighting for the composite performance score based on
workload features. Uses a floor-constrained softmax to guarantee minimum
importance for all metrics while dynamically shifting emphasis.
"""

import numpy as np
from typing import Dict, List


class FeatureDrivenWeightModel:
    """
    Computes dynamic metric weights from workload features.

    Features (f) are multiplied by a coefficient matrix (M) and added to
    base weights (b). The result is passed through a floor-constrained
    softmax to ensure every metric retains a minimum weight (alpha) while
    still summing to 1.0.
    """

    def __init__(
        self,
        metrics: List[str],
        base_weights: Dict[str, float],
        floors: Dict[str, float],
        coefficient_matrix: Dict[str, Dict[str, float]],
        temperature: float = 1.0,
    ):
        """
        Parameters
        ----------
        metrics : List[str]
            List of metric names to be weighted.
        base_weights : Dict[str, float]
            Base unnormalized logit score for each metric.
        floors : Dict[str, float]
            Minimum weight guaranteed for each metric (sum must be < 1.0).
        coefficient_matrix : Dict[str, Dict[str, float]]
            Mapping of metric -> {feature_name: coefficient}.
        temperature : float
            Softmax temperature.
        """
        self.metrics = metrics
        self.base_weights = base_weights
        self.floors = floors
        self.coefficient_matrix = coefficient_matrix
        self.temperature = temperature

        # Validate floors
        total_floor = sum(self.floors.get(m, 0.0) for m in self.metrics)
        if total_floor >= 1.0:
            raise ValueError(f"Sum of weight floors must be < 1.0, got {total_floor}")

    def compute_weights(self, features: Dict[str, float]) -> Dict[str, float]:
        """
        Compute final normalized weights given workload features.

        Steps:
        1. Calculate raw logits: w_i = b_i + sum_j(M_ij * f_j)
        2. Softmax: S_i = exp(w_i / t) / sum(exp(w_k / t))
        3. Floor constraint: W_i = alpha_i + (1 - sum(alpha)) * S_i

        Note: ``working_set_millions`` is passed through ``log1p()`` before
        multiplication to prevent softmax domination at large scale factors.
        Raw values grow linearly (0.02 → 25 → 100), which would collapse the
        softmax; log1p compresses them to (0.02 → 3.26 → 4.62).
        """
        logits = []
        for metric in self.metrics:
            logit = self.base_weights.get(metric, 0.0)
            coeffs = self.coefficient_matrix.get(metric, {})
            for feat_name, feat_val in features.items():
                # Saturate working_set_millions logarithmically to keep it
                # from dominating the softmax at large benchmarks (>1M rows).
                val = (
                    float(np.log1p(feat_val))
                    if feat_name == "working_set_millions"
                    else feat_val
                )
                logit += coeffs.get(feat_name, 0.0) * val
            logits.append(logit)

        # Softmax with temperature (numerically stable)
        logits_arr = np.array(logits) / self.temperature
        max_logit: float = float(np.max(logits_arr))
        exp_logits = np.exp(logits_arr - max_logit)
        softmax = exp_logits / np.sum(exp_logits)

        # Floor constraint
        total_floor = sum(self.floors.get(m, 0.0) for m in self.metrics)
        remaining_mass = 1.0 - total_floor

        final_weights = {}
        for i, metric in enumerate(self.metrics):
            alpha = self.floors.get(metric, 0.0)
            final_weights[metric] = float(alpha + remaining_mass * softmax[i])

        return final_weights
