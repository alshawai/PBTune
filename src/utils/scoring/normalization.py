"""
Robust Utility Normalization
============================

Implements the QuantileUtilityNormalizer for converting raw performance metrics
into a monotonic [0, 1] utility scale using robust quantile anchoring.

This replaces the brittle min/max normalization which was highly susceptible
to single-point outliers (e.g. a single query timeout permanently compressing
all future reward variance).
"""

from typing import Dict, List, Any, Tuple
import numpy as np

from src.utils.logger import get_logger
from src.utils.metrics import PerformanceMetrics


class MetricDirection:
    """Constants defining the optimization direction of metrics."""

    HIGHER_IS_BETTER = 1
    LOWER_IS_BETTER = -1
    ZERO_IS_BEST = 0


class QuantileUtilityNormalizer:
    """
    Robust normalizer that maps raw metrics to [0, 1] utility scores.

    Instead of min/max, it uses robust quantiles (e.g., p05 and p95) as anchors.
    Metrics outside the anchors are smoothly clamped to [0, 1].
    """

    def __init__(
        self,
        lower_quantile: float = 0.05,
        upper_quantile: float = 0.95,
        calibration_window: int = 100,
        drift_threshold: float = 0.2,
        min_samples_for_drift: int = 40,
    ):
        """
        Parameters
        ----------
        lower_quantile : float
            Lower anchor percentile (default: 0.05 / 5th percentile)
        upper_quantile : float
            Upper anchor percentile (default: 0.95 / 95th percentile)
        calibration_window : int
            Number of recent samples to keep for recalibration
        drift_threshold : float
            Fraction of out-of-support samples that triggers recalibration
        """
        self.lower_quantile = lower_quantile
        self.upper_quantile = upper_quantile
        self.calibration_window = calibration_window
        self.drift_threshold = drift_threshold
        self.min_samples_for_drift = min_samples_for_drift

        self.NATURALLY_BOUNDED_METRICS = {
            "error_rate",
            "memory_pressure",
            "buffer_miss_rate",
            "scan_efficiency",
        }

        # Fallback anchors for uncalibrated metrics (before fit() is called)
        self.FALLBACK_ANCHORS = {
            "latency_p95": (MetricDirection.LOWER_IS_BETTER, 5.0, 2000.0),
            "latency_p99": (MetricDirection.LOWER_IS_BETTER, 5.0, 3000.0),
            "latency_variance": (MetricDirection.LOWER_IS_BETTER, 0.0, 500.0),
            "tail_amplification": (MetricDirection.LOWER_IS_BETTER, 1.0, 10.0),
            "throughput": (MetricDirection.HIGHER_IS_BETTER, 10.0, 5000.0),
            "throughput_variance": (MetricDirection.LOWER_IS_BETTER, 0.0, 100.0),
        }

        # metric_name -> (direction, anchor_low, anchor_high)
        self.anchors: Dict[str, Tuple[int, float, float]] = {}

        # Keep recent history for recalibration
        self._history: Dict[str, List[float]] = {}
        self._out_of_support_counts: Dict[str, int] = {}
        self._total_samples_since_calibration: int = 0
        self._is_calibrated = False

        # Track drift events for monitoring
        self._drift_events: List[Dict[str, Any]] = []
        self._last_drift_check_sample_count: int = 0

    @property
    def is_calibrated(self) -> bool:
        """Return whether the normalizer has been calibrated at least once."""
        return self._is_calibrated

    @property
    def total_samples_since_calibration(self) -> int:
        """Return number of samples processed since the last fit() call."""
        return self._total_samples_since_calibration

    def out_of_support_rate(self, metric_name: str) -> float:
        """Return out-of-support rate for one metric since last calibration."""
        if self._total_samples_since_calibration <= 0:
            return 0.0
        count = self._out_of_support_counts.get(metric_name, 0)
        return count / float(self._total_samples_since_calibration)

    def build_recalibration_dataset(
        self,
        recent_metrics: List[PerformanceMetrics],
        *,
        latency_metric_name: str,
    ) -> List[PerformanceMetrics]:
        """Build a history-aware dataset for robust recalibration.

        Combines current generation metrics with retained history for the active
        latency metric and throughput so recalibration does not overfit on a small
        single-generation sample.
        """
        dataset: List[PerformanceMetrics] = list(recent_metrics)
        all_keys = list(self._history.keys())
        if not all_keys:
            return dataset

        max_len = max(len(self._history[k]) for k in all_keys)

        for idx in range(max_len):
            metric = PerformanceMetrics()
            for key in all_keys:
                hist = self._history[key]
                if idx < len(hist):
                    if hasattr(metric, key):
                        setattr(metric, key, float(hist[idx]))
            dataset.append(metric)

        return dataset

    def detect_metric_saturation(
        self,
        metrics_list: List[PerformanceMetrics],
        saturation_epsilon: float = 0.01,
        min_saturated_workers: int = 2,
    ) -> Dict[str, str]:
        """Detect per-metric saturation across multiple workers.
        
        Returns dict of metric_name -> "upper" | "lower" for saturated metrics.
        A metric is saturated when >= min_saturated_workers hit the same bound.
        """
        if not self._is_calibrated:
            return {}

        saturated: Dict[str, str] = {}
        
        for metric_name in self.anchors:
            upper_count = 0
            lower_count = 0
            
            for m in metrics_list:
                raw_dict = m.to_dict()
                val = raw_dict.get(metric_name)
                if val is None or not isinstance(val, (int, float)):
                    continue
                if metric_name in self.NATURALLY_BOUNDED_METRICS:
                    continue
                
                utility = self.score_metric(metric_name, float(val))
                if utility >= 1.0 - saturation_epsilon:
                    upper_count += 1
                elif utility <= saturation_epsilon:
                    lower_count += 1
            
            if upper_count >= min_saturated_workers:
                saturated[metric_name] = "upper"
            elif lower_count >= min_saturated_workers:
                saturated[metric_name] = "lower"
        
        return saturated

    def expand_metric_anchor(self, metric_name: str, bound: str) -> bool:
        """Expand a single metric's anchor on the saturated bound.
        
        Uses the history buffer to recompute the anchor with a wider percentile,
        expanding only the saturated side.
        """
        if metric_name not in self._history or metric_name not in self.anchors:
            return False
        
        direction, old_low, old_high = self.anchors[metric_name]
        
        # We need the NEVER_ZERO_METRICS defined in fit() logic
        NEVER_ZERO_METRICS = {
            "latency_p99",
            "latency_p95",
            "latency_p50",
            "latency_variance",
            "tail_amplification",
        }
        
        values = [v for v in self._history[metric_name] if v > 0.0 or metric_name not in NEVER_ZERO_METRICS]
        
        if len(values) < 2:
            return False
        
        import numpy as np
        arr = np.array(values)
        new_low = float(np.percentile(arr, self.lower_quantile * 100))
        new_high = float(np.percentile(arr, self.upper_quantile * 100))
        
        # Apply 20% expansion headroom on the saturated side
        rng = new_high - new_low
        if rng == 0:
            rng = max(abs(new_high), 1e-6) * 0.2
        
        if bound == "upper":
            new_high = new_high + rng * 0.2
        elif bound == "lower":
            new_low = max(0.0, new_low - rng * 0.2)
        
        if new_low == new_high:
            return False
        
        self.anchors[metric_name] = (direction, new_low, new_high)
        return True

    def _get_metric_direction(self, metric_name: str) -> int:
        """Heuristic for metric direction based on name."""
        # Check specific patterns FIRST (before broad matches like "throughput")
        if any(
            x in metric_name
            for x in [
                "variance",
                "miss_rate",
                "amplification",
                "pressure",
                "latency",
                "error",
            ]
        ):
            return MetricDirection.LOWER_IS_BETTER
        if any(x in metric_name for x in ["throughput", "hit_ratio", "efficiency"]):
            return MetricDirection.HIGHER_IS_BETTER
        return MetricDirection.LOWER_IS_BETTER  # default safe assumption

    def fit(
        self,
        metrics_list: List[PerformanceMetrics],
        metric_whitelist: Optional[List[str]] = None,
    ) -> None:
        """
        Calibrate normalizer anchors using a historical list of metrics.

        Parameters
        ----------
        metrics_list : List[PerformanceMetrics]
            Observations to calibrate from.
        metric_whitelist : Optional[List[str]]
            When provided, only calibrate anchors for these metric keys.
            All other fields from ``PerformanceMetrics.to_dict()`` are
            ignored. This prevents logging-only fields (e.g. ``total_queries``,
            ``io_read_mb``) from producing zero-anchored noise in the
            normalizer and the "Missing utilities" debug messages in the scorer.
        """
        from src.utils.scoring.outlier_filtering import iqr_filter

        logger = get_logger(__name__)

        if not metrics_list:
            logger.debug("fit() called with empty metrics list, skipping")
            return

        logger.info("Calibrating normalizer anchors from %d observations", len(metrics_list))

        # Extract flat dictionary of values
        series = {}
        for metric in metrics_list:
            raw_dict = metric.to_dict()
            for key, val in raw_dict.items():
                if isinstance(val, (int, float)) and not isinstance(val, bool):
                    if metric_whitelist and key not in metric_whitelist:
                        continue
                    if key not in series:
                        series[key] = []
                    series[key].append(float(val))

        # Metrics that physically cannot be zero (extraction artifacts)
        NEVER_ZERO_METRICS = {
            "latency_p99",
            "latency_p95",
            "latency_p50",
            "latency_variance",
            "tail_amplification",
        }

        for key, values in series.items():
            if key in self.NATURALLY_BOUNDED_METRICS:
                continue

            if key in NEVER_ZERO_METRICS:
                values = [v for v in values if v > 0.0]

            if not values:
                continue

            direction = self._get_metric_direction(key)
            arr = np.array(values)

            arr_filtered, filter_meta = iqr_filter(arr, k=2.5)
            if len(arr_filtered) >= 3:
                arr = arr_filtered
                if filter_meta["n_removed"] > 0:
                    logger.debug(
                        "IQR filter for %s: removed %d/%d outliers (bounds: [%.4f, %.4f])",
                        key,
                        filter_meta["n_removed"],
                        filter_meta["original_size"],
                        filter_meta["lower_bound"],
                        filter_meta["upper_bound"],
                    )

            # For latency/error, if all are 0, make it slightly non-zero to avoid div by zero
            q_low = float(np.percentile(arr, self.lower_quantile * 100))
            q_high = float(np.percentile(arr, self.upper_quantile * 100))

            if q_low == q_high:
                if q_low == 0.0:
                    q_high = 1e-6
                else:
                    q_low = q_low * 0.9
                    q_high = q_high * 1.1

            self.anchors[key] = (direction, q_low, q_high)
            self._history[key] = list(values[-self.calibration_window :])
            self._out_of_support_counts[key] = 0

            logger.debug(
                "Calibrated anchor for %s: direction=%d, low=%.4f, high=%.4f",
                key,
                direction,
                q_low,
                q_high,
            )

        self._total_samples_since_calibration = 0
        self._is_calibrated = True
        logger.info("Normalizer calibration complete: %d metrics anchored", len(self.anchors))

    def update(self, metrics: PerformanceMetrics) -> None:
        """
        Update rolling history and track out-of-support occurrences for drift detection.
        """
        if not self._is_calibrated:
            self.fit([metrics])
            return

        raw_dict = metrics.to_dict()
        self._total_samples_since_calibration += 1

        for key, val in raw_dict.items():
            if isinstance(val, (int, float)) and not isinstance(val, bool):
                val = float(val)
                if key in self.NATURALLY_BOUNDED_METRICS:
                    continue

                if key not in self._history:
                    self._history[key] = []

                self._history[key].append(val)
                # Keep history size bounded
                if len(self._history[key]) > self.calibration_window:
                    self._history[key].pop(0)

                # Check out of support
                if key in self.anchors:
                    _, q_low, q_high = self.anchors[key]
                    if val < q_low or val > q_high:
                        self._out_of_support_counts[key] = (
                            self._out_of_support_counts.get(key, 0) + 1
                        )

    def needs_recalibration(self) -> bool:
        """
        Detect drift by checking if the out-of-support rate exceeds the threshold.
        """
        logger = get_logger(__name__)

        if not self._is_calibrated or self._total_samples_since_calibration < self.min_samples_for_drift:
            return False

        drifted_metrics = []
        for metric_name, count in self._out_of_support_counts.items():
            rate = count / float(self._total_samples_since_calibration)
            if rate > self.drift_threshold:
                drifted_metrics.append(metric_name)
                logger.warning(
                    "Drift detected in %s: out_of_support_rate=%.4f (threshold=%.4f)",
                    metric_name,
                    rate,
                    self.drift_threshold,
                )

        if drifted_metrics:
            logger.info(
                "Normalizer recalibration needed: %d metrics drifted after %d samples",
                len(drifted_metrics),
                self._total_samples_since_calibration,
            )
            return True

        return False

    def record_drift_event(self, metric_name: str, out_of_support_rate: float) -> None:
        """
        Record a drift event when out-of-support rate exceeds threshold.

        Parameters
        ----------
        metric_name : str
            Name of the metric that drifted
        out_of_support_rate : float
            Fraction of samples outside support since last calibration
        """
        event = {
            "sample_count": self._total_samples_since_calibration,
            "metric": metric_name,
            "out_of_support_rate": out_of_support_rate,
            "threshold": self.drift_threshold,
        }
        self._drift_events.append(event)

    def get_drift_events(self) -> List[Dict[str, Any]]:
        """Return list of recorded drift events."""
        return list(self._drift_events)

    def clear_drift_events(self) -> None:
        """Clear the drift event history."""
        self._drift_events.clear()

    def score_metric(self, metric_name: str, value: float) -> float:
        """
        Map a raw metric value to a utility score [0, 1].

        Uses calibrated anchors if available, falls back to sensible defaults
        for common metrics before calibration is complete.
        """
        if metric_name in self.anchors:
            direction, q_low, q_high = self.anchors[metric_name]
        elif metric_name in self.FALLBACK_ANCHORS:
            direction, q_low, q_high = self.FALLBACK_ANCHORS[metric_name]
        else:
            return 0.5  # Neutral utility if unknown

        # Clamp value to anchors
        val_clamped = max(q_low, min(q_high, value))

        # Map to [0, 1]
        rng = q_high - q_low
        if rng == 0:
            return 1.0 if direction == MetricDirection.HIGHER_IS_BETTER else 0.0

        normalized = (val_clamped - q_low) / rng

        if direction == MetricDirection.HIGHER_IS_BETTER:
            return normalized
        elif direction == MetricDirection.LOWER_IS_BETTER:
            return 1.0 - normalized
        else:  # ZERO_IS_BEST (e.g. deviation from target)
            return 1.0 - normalized

    def score_vector(self, metrics: PerformanceMetrics) -> Dict[str, float]:
        """
        Score only metrics that have calibrated anchors.
        """
        logger = get_logger(__name__)
        raw_dict = metrics.to_dict()
        scores = {}
        for key, val in raw_dict.items():
            if isinstance(val, (int, float)) and not isinstance(val, bool):
                if key in self.NATURALLY_BOUNDED_METRICS:
                    direction = self._get_metric_direction(key)
                    clamped = max(0.0, min(1.0, float(val)))
                    scores[key] = clamped if direction == MetricDirection.HIGHER_IS_BETTER else 1.0 - clamped
                elif key in self.anchors:  # Only score calibrated metrics
                    scores[key] = self.score_metric(key, float(val))

        logger.debug(
            "Scored metrics vector (%d calibrated): %s",
            len(scores),
            {k: f"{v:.4f}" for k, v in scores.items()},
        )
        return scores

    def export_state(self) -> Dict[str, Any]:
        """Export normalizer state for serialization."""
        return {
            "anchors": {
                key: {"direction": d, "low": l, "high": h}
                for key, (d, l, h) in self.anchors.items()
            },
            "is_calibrated": self._is_calibrated,
            "total_samples": self._total_samples_since_calibration,
        }

    def import_state(self, state: Dict[str, Any]) -> None:
        """Import normalizer state from serialization."""
        self.anchors = {}
        for key, anchor_dict in state.get("anchors", {}).items():
            self.anchors[key] = (
                anchor_dict["direction"],
                anchor_dict["low"],
                anchor_dict["high"],
            )
        self._is_calibrated = state.get("is_calibrated", False)
        self._total_samples_since_calibration = state.get("total_samples", 0)
        self._history = {}
        self._out_of_support_counts = {}

    def get_drift_events(self) -> List[Dict[str, Any]]:
        """
        Return list of drift events detected since normalizer creation.

        Each event contains:
        - sample_count: Number of samples when drift was detected
        - metrics_drifted: List of metric names that triggered drift
        - out_of_support_rates: Dict of metric -> out-of-support rate
        """
        return list(self._drift_events)

    def record_drift_event(self, drifted_metrics: List[str]) -> None:
        """
        Record a drift event for monitoring and analysis.

        Parameters
        ----------
        drifted_metrics : List[str]
            List of metric names that exceeded drift threshold
        """
        event = {
            "sample_count": self._total_samples_since_calibration,
            "metrics_drifted": drifted_metrics,
            "out_of_support_rates": {
                metric: self.out_of_support_rate(metric) for metric in drifted_metrics
            },
        }
        self._drift_events.append(event)
