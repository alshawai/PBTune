"""
Loader for BO baseline traces.
"""

import json
from dataclasses import dataclass
from pathlib import Path
from typing import Optional

import numpy as np
from dateutil import parser as dateutil_parser

from src.utils.logger import get_logger
from src.utils.metrics import PerformanceMetrics, MetricConfig
from src.utils.rescoring import rescore_metrics_globally
from src.visualization.exceptions import DataLoadError, InvalidSchemaError
from src.visualization.loaders.session import RAW_METRIC_KEYS, _extract_raw_value

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


def _valid_metric_dict(metrics_dict: dict | None) -> dict | None:
    if not metrics_dict:
        return None

    valid_keys = PerformanceMetrics.__dataclass_fields__.keys()
    filtered = {k: v for k, v in metrics_dict.items() if k in valid_keys}
    return filtered or None


def _extract_step_metrics(step: dict) -> dict | None:
    direct_metrics = _valid_metric_dict(step.get("metrics"))
    if direct_metrics:
        return direct_metrics

    worker_scores = step.get("worker_scores") or []
    if not worker_scores:
        return None

    scored_workers = [
        worker
        for worker in worker_scores
        if worker.get("metrics") and worker.get("score") is not None
    ]
    if scored_workers:
        best_worker = max(scored_workers, key=lambda worker: worker["score"])
        return _valid_metric_dict(best_worker.get("metrics"))

    for worker in worker_scores:
        worker_metrics = _valid_metric_dict(worker.get("metrics"))
        if worker_metrics:
            return worker_metrics

    return None


def _extract_stored_step_score(step: dict) -> float:
    if "score" in step:
        return step["score"]

    worker_scores = step.get("worker_scores") or []
    scored_workers = [
        worker["score"]
        for worker in worker_scores
        if worker.get("score") is not None
    ]
    if scored_workers:
        return max(scored_workers)

    if "mean_score" in step:
        return step["mean_score"]
    if "best_score" in step:
        return step["best_score"]
    return 0.0


def load_bo_trace(
    path: Path | str,
    metric_config: Optional[MetricConfig] = None,
    metric_key: Optional[str] = None,
) -> BOTrace:
    """
    Load a Bayesian Optimization baseline trace from JSON.
    Optionally accepts a MetricConfig (from a PBT session) to rescore the BO
    trace using the exact same global anchors, ensuring direct comparability.

    Parameters
    ----------
    metric_key : str | None
        If set to a raw metric name (``latency_p95``, ``latency_p99``,
        ``throughput``), ``best_scores`` will contain that raw value
        instead of the composite score.
    """
    path = Path(path)
    if not path.exists():
        raise DataLoadError(f"Baseline trace not found: {path}")

    try:
        with open(path, "r", encoding="utf-8") as f:
            data = json.load(f)
    except json.JSONDecodeError as e:
        raise DataLoadError(f"Failed to parse JSON in {path}: {e}") from e

    # BO JSON saves iterations in evaluation_history (old) or generation_history (standardized)
    if "evaluation_history" in data:
        history = data["evaluation_history"]
    elif "generation_history" in data:
        history = data["generation_history"]
    else:
        raise InvalidSchemaError(f"Missing 'evaluation_history' or 'generation_history' in {path}")
    n_evals = len(history)

    evaluations = np.arange(1, n_evals + 1)
    wall_clock_seconds = np.zeros(n_evals)
    scores = np.zeros(n_evals)

    # Optional global rescoring
    all_metrics: list[PerformanceMetrics] = []
    has_metrics = False

    metric_indices: list[int | None] = []

    for step in history:
        metrics_dict = _extract_step_metrics(step)
        if metrics_dict:
            has_metrics = True
            try:
                metric_indices.append(len(all_metrics))
                all_metrics.append(PerformanceMetrics(**metrics_dict))
            except Exception:
                metric_indices.append(None)
        else:
            metric_indices.append(None)

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

        use_raw = metric_key is not None and metric_key in RAW_METRIC_KEYS
        if use_raw:
            new_scores = [_extract_raw_value(m, metric_key) for m in all_metrics]
        else:
            new_scores = [metric_config.compute_score_value(m) for m in all_metrics]
    else:
        new_scores = None

    start_time = None
    for i, step in enumerate(history):
        ts_str = step.get("timestamp")
        if ts_str:
            try:
                current_time = dateutil_parser.isoparse(ts_str)
                if start_time is None:
                    # Give it a tiny offset based on the duration of the first iteration
                    import datetime

                    start_time = current_time - datetime.timedelta(
                        seconds=step.get("wall_clock_seconds", 20.0)
                    )
                wall_clock_seconds[i] = (current_time - start_time).total_seconds()
            except Exception:
                wall_clock_seconds[i] = step.get("wall_clock_seconds", 0.0)
        else:
            wall_clock_seconds[i] = step.get("wall_clock_seconds", 0.0)
        metric_idx = metric_indices[i]
        if new_scores is not None and metric_idx is not None:
            scores[i] = new_scores[metric_idx]
        else:
            scores[i] = _extract_stored_step_score(step)

    # Convert raw scores into a running best array
    # Lower is better for latency metrics; higher is better otherwise
    use_raw = metric_key is not None and metric_key in RAW_METRIC_KEYS
    if use_raw and metric_key.startswith("latency"):
        best_scores = np.minimum.accumulate(scores)
    else:
        best_scores = np.maximum.accumulate(scores)

    method_name = data.get("optimizer_backend", "bo_smac")
    if method_name.startswith("smac"):
        method_name = "bo_smac"

    best_config_metrics = None
    if "best_configuration" in data and "metrics" in data["best_configuration"]:
        metrics_dict = data["best_configuration"]["metrics"]
        valid_keys = PerformanceMetrics.__dataclass_fields__.keys()
        filtered = {k: v for k, v in metrics_dict.items() if k in valid_keys}
        try:
            best_config_metrics = PerformanceMetrics(**filtered)
        except Exception:
            pass

    metadata = {
        "file_name": path.name,
        "n_evaluations": n_evals,
        "total_time_seconds": wall_clock_seconds[-1] if n_evals > 0 else 0.0,
        "best_config_metrics": best_config_metrics,
    }

    return BOTrace(
        evaluations=evaluations,
        wall_clock_seconds=wall_clock_seconds,
        best_scores=best_scores,
        method_name=method_name,
        metadata=metadata,
        metric_config=metric_config,
    )
