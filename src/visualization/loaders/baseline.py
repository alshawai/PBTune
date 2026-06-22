"""
Loader for BO baseline traces.
"""

import json
from dataclasses import dataclass
from pathlib import Path
from typing import Optional

import numpy as np

from src.utils.logger import get_logger
from src.utils.metrics import PerformanceMetrics, MetricConfig
from src.utils.scoring import create_scoring_engine
from src.tuners.utils.calibration import rescore_metrics_globally
from src.visualization.exceptions import DataLoadError, InvalidSchemaError

LOGGER = get_logger("BaselineLoader")


@dataclass
class BOTrace:
    """Parsed BO baseline tuning history."""

    evaluations: np.ndarray  # [1, 2, ..., N]
    wall_clock_seconds: np.ndarray  # Cumulative wall-clock time
    best_scores: np.ndarray  # Running best score
    method_name: str  # "smac3", "openbox", etc.
    metadata: dict
    metric_config: Optional[MetricConfig]  # The normalization config used (if any)


def load_bo_trace(
    path: Path | str, metric_config: Optional[MetricConfig] = None
) -> BOTrace:
    """
    Load a Bayesian Optimization baseline trace from JSON.
    Optionally accepts a MetricConfig (from a PBT session) to rescore the BO
    trace using the exact same global anchors, ensuring direct comparability.
    """
    path = Path(path)
    if not path.exists():
        raise DataLoadError(f"Baseline trace not found: {path}")

    try:
        with open(path, "r", encoding="utf-8") as f:
            data = json.load(f)
    except json.JSONDecodeError as e:
        raise DataLoadError(f"Failed to parse JSON in {path}: {e}") from e

    # BO JSON saves iterations in evaluation_history
    if "evaluation_history" not in data:
        raise InvalidSchemaError(f"Missing 'evaluation_history' in {path}")

    history = data["evaluation_history"]
    n_evals = len(history)

    evaluations = np.arange(1, n_evals + 1)
    wall_clock_seconds = np.zeros(n_evals)
    scores = np.zeros(n_evals)

    # Optional global rescoring
    all_metrics: list[PerformanceMetrics] = []
    has_metrics = False

    for step in history:
        metrics_dict = step.get("metrics")
        if metrics_dict:
            has_metrics = True
            valid_keys = PerformanceMetrics.__dataclass_fields__.keys()
            filtered = {k: v for k, v in metrics_dict.items() if k in valid_keys}
            try:
                all_metrics.append(PerformanceMetrics(**filtered))
            except Exception:
                # Append a dummy if parsing fails, to keep index aligned
                all_metrics.append(PerformanceMetrics())
        else:
            all_metrics.append(PerformanceMetrics())

    if has_metrics:
        # Either use provided metric config or compute a new one
        if metric_config is None:
            tuning_session = data.get("tuning_session", {})
            workload = tuning_session.get("workload_type", "oltp")
            benchmark = tuning_session.get("benchmark_name")
            scoring_policy = data.get(
                "scoring_policy", tuning_session.get("scoring_policy")
            )
            policy_version = data.get(
                "scoring_policy_version", tuning_session.get("scoring_policy_version")
            )
            metric_ref = data.get(
                "metric_reference_version",
                tuning_session.get("metric_reference_version"),
            )

            metric_config, _, _ = rescore_metrics_globally(
                metrics=all_metrics,
                workload=workload,
                benchmark=benchmark,
                scoring_policy=scoring_policy,
                scoring_policy_version=policy_version,
                metric_reference_version=metric_ref,
            )

        engine = create_scoring_engine(metric_config)
        new_scores = [engine.compute_breakdown(m).final_score for m in all_metrics]
    else:
        new_scores = None

    for i, step in enumerate(history):
        wall_clock_seconds[i] = step.get("wall_clock_seconds", 0.0)
        if new_scores:
            scores[i] = new_scores[i]
        else:
            scores[i] = step.get("score", 0.0)

    # Convert raw scores into a running best array
    # Ensures a monotonically increasing curve
    best_scores = np.maximum.accumulate(scores)

    method_name = data.get("optimizer_backend", "bo_smac")
    if method_name.startswith("smac"):
        method_name = "bo_smac"

    metadata = {
        "file_name": path.name,
        "n_evaluations": n_evals,
        "total_time_seconds": wall_clock_seconds[-1] if n_evals > 0 else 0.0,
    }

    return BOTrace(
        evaluations=evaluations,
        wall_clock_seconds=wall_clock_seconds,
        best_scores=best_scores,
        method_name=method_name,
        metadata=metadata,
        metric_config=metric_config,
    )
