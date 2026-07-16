"""
Workload Feature Refinement
==========================

:class:`WorkloadFeatureRefiner` blends runtime observations into the static
workload-feature vector and pushes the resulting weight updates into the scoring
engine. It is a small stateful collaborator owning:

- ``_static_feature_priors`` — the static baseline captured on first refinement,
  used as the soft-minimum damping floor
- ``_pending_feature_deltas`` — feature deltas accumulated across a generation,
  flushed into the scorer by :meth:`maybe_update_weights`

The refiner mutates ``metric_config.workload_features`` in place (the scoring
engine reads that same dict) and reaches the scorer lazily through the
``scorer_provider`` callable, so the orchestrator's lazily-created scoring engine
is honored. ``WorkloadOrchestrator`` owns one refiner and keeps thin delegating
methods over it.
"""

from __future__ import annotations

from typing import Any, Callable, Dict, List, Optional

from src.utils.metrics import PerformanceMetrics
from src.utils.logger import get_logger, get_color_context

LOGGER = get_logger("WorkloadOrchestrator")
COLORS = get_color_context()


class WorkloadFeatureRefiner:
    """Refine workload features from runtime metrics and update scoring weights."""

    def __init__(
        self,
        metric_config: Any,
        scorer_provider: Callable[[], Any],
    ) -> None:
        """
        Parameters
        ----------
        metric_config : MetricConfig
            Metric configuration whose ``workload_features`` dict is refined in
            place; the scoring engine reads the same dict.
        scorer_provider : Callable[[], Any]
            Zero-arg callable returning the active scoring engine. Called lazily
            so the orchestrator's deferred scorer construction is respected.
        """
        self._metric_config = metric_config
        self._scorer_provider = scorer_provider
        self._static_feature_priors: Optional[Dict[str, float]] = None
        self._pending_feature_deltas: Dict[str, float] = {}

    def _refine_workload_features(
        self,
        metrics: PerformanceMetrics,
    ) -> Dict[str, tuple[float, float]]:
        """Refine static workload features with runtime observations using EMA blending.

        Blends observed runtime metrics into the static feature vector to capture
        dynamic workload characteristics. Uses exponential moving average with
        alpha=0.7 to keep static features dominant while allowing runtime correction.
        Refined features are damped with a 15% soft minimum retention floor of the
        original static prior to prevent prior erasure.

        Refinement rules (bounded to [0, 1]):
        - High throughput CV -> increase concurrency_pressure (concurrency pressure signal = CV / 0.20)
        - High tail amplification (p99/p50) -> increase tail_latency_sensitivity (sensitivity signal = tail_amp / 10.0)
        """
        if not self._metric_config.workload_features:
            LOGGER.debug(" ➤ No workload features to refine")
            return {}

        features = self._metric_config.workload_features

        # Cache static feature priors on first call to establish the damping baseline
        if self._static_feature_priors is None:
            self._static_feature_priors = dict(features)

        alpha = 0.7  # EMA blending factor: keep static features dominant
        refinements = {}

        # 1. Throughput Coefficient of Variation (CV) -> concurrency pressure
        if (
            hasattr(metrics, "throughput_variance")
            and metrics.throughput_variance is not None
            and hasattr(metrics, "throughput")
            and metrics.throughput is not None
        ):
            if metrics.throughput > 0:
                # metrics.throughput_variance holds stddev (np.std)
                throughput_cv = metrics.throughput_variance / metrics.throughput
                throughput_variance_signal = min(1.0, throughput_cv / 0.20)
            else:
                throughput_variance_signal = 0.0

            if "concurrency_pressure" in features:
                old_val = features["concurrency_pressure"]
                refined_val = (
                    alpha * features["concurrency_pressure"]
                    + (1 - alpha) * throughput_variance_signal
                )
                # Apply 15% soft minimum floor based on the original static prior
                floor = 0.15 * self._static_feature_priors.get(
                    "concurrency_pressure", 0.0
                )
                features["concurrency_pressure"] = max(floor, min(1.0, refined_val))

                refinements["concurrency_pressure"] = (
                    old_val,
                    features["concurrency_pressure"],
                )

        # 2. Tail Latency Amplification (p99/p50) -> tail latency sensitivity
        if (
            hasattr(metrics, "latency_p99")
            and metrics.latency_p99 is not None
            and hasattr(metrics, "latency_p50")
            and metrics.latency_p50 is not None
        ):
            if metrics.latency_p50 > 0:
                tail_amp = metrics.latency_p99 / metrics.latency_p50
                tail_sensitivity_signal = min(1.0, tail_amp / 10.0)
            else:
                tail_sensitivity_signal = 0.0

            if "tail_latency_sensitivity" in features:
                old_val = features["tail_latency_sensitivity"]
                refined_val = (
                    alpha * features["tail_latency_sensitivity"]
                    + (1 - alpha) * tail_sensitivity_signal
                )
                # Apply 15% soft minimum floor based on the original static prior
                floor = 0.15 * self._static_feature_priors.get(
                    "tail_latency_sensitivity", 0.0
                )
                features["tail_latency_sensitivity"] = max(floor, min(1.0, refined_val))

                refinements["tail_latency_sensitivity"] = (
                    old_val,
                    features["tail_latency_sensitivity"],
                )

        return refinements

    def refine_from_generation(self, workers: List[Any]) -> bool:
        """Refine workload features using aggregated metrics from all workers in a generation.

        This generation-level refinement aggregates metrics from all workers before
        refining features once, ensuring that all workers in a generation use the same
        features and thus the same weights. This prevents race conditions that occur
        when feature refinement is performed per-worker during parallel evaluation.

        Parameters
        ----------
        workers : List[BaseWorker]
            List of all workers in the current generation
        """
        logger = get_logger("BenchmarkExecutor")

        if not workers:
            logger.debug(" No workers to aggregate for feature refinement")
            return False

        # Aggregate metrics from all healthy workers
        health_metrics = [
            w.metrics
            for w in workers
            if w.metrics is not None and w.metrics.failure_type is None
        ]
        if not health_metrics:
            logger.debug(" No valid metrics to aggregate for feature refinement")
            return False

        LOGGER.debug(
            " Aggregating metrics from %s%d%s healthy workers...",
            COLORS.bold,
            len(health_metrics),
            COLORS.reset,
        )
        aggregated_metrics = PerformanceMetrics()

        # Average numeric metrics
        aggregated_metrics.latency_p50 = sum(
            m.latency_p50 for m in health_metrics
        ) / len(health_metrics)
        aggregated_metrics.latency_p95 = sum(
            m.latency_p95 for m in health_metrics
        ) / len(health_metrics)
        aggregated_metrics.latency_p99 = sum(
            m.latency_p99 for m in health_metrics
        ) / len(health_metrics)
        aggregated_metrics.latency_variance = sum(
            m.latency_variance for m in health_metrics
        ) / len(health_metrics)
        aggregated_metrics.throughput = sum(m.throughput for m in health_metrics) / len(
            health_metrics
        )
        aggregated_metrics.throughput_variance = sum(
            m.throughput_variance for m in health_metrics
        ) / len(health_metrics)
        aggregated_metrics.buffer_miss_rate = sum(
            m.buffer_miss_rate for m in health_metrics
        ) / len(health_metrics)
        aggregated_metrics.scan_efficiency = sum(
            m.scan_efficiency for m in health_metrics
        ) / len(health_metrics)

        logger.debug(
            " ➤ Aggregated metrics from %s%d%s workers for generation-level feature refinement.",
            COLORS.bold,
            len(health_metrics),
            COLORS.reset,
        )

        logger.debug(" Refining features using aggregated metrics...")
        refinements = self._refine_workload_features(aggregated_metrics)
        if refinements:
            for feature, (old, new) in refinements.items():
                self._pending_feature_deltas[feature] = (
                    self._pending_feature_deltas.get(feature, 0.0) + (new - old)
                )
        return bool(refinements)

    def maybe_update_weights(
        self,
        generation: int,
        *,
        force: bool = False,
        log_every: int = 5,
    ) -> bool:
        if not self._pending_feature_deltas and not force:
            return False

        should_update = force or (log_every > 0 and (generation + 1) % log_every == 0)
        if not should_update:
            return False

        if self._pending_feature_deltas:
            delta_line = ", ".join(
                f"{feature} {delta:+.4f}"
                for feature, delta in self._pending_feature_deltas.items()
            )
            LOGGER.info(
                "%sΔ features (accumulated):%s %s%s%s",
                COLORS.bold,
                COLORS.reset,
                COLORS.italic,
                delta_line,
                COLORS.reset,
            )

        updated = self._scorer_provider().update_context(
            features=self._metric_config.workload_features,
            update_reason="feature_refinement",
        )
        if updated or force:
            self._scorer_provider().schedule_log_next_generation()

        self._pending_feature_deltas.clear()
        return updated
