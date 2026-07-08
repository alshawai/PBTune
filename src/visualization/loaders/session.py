"""
Loader for Population-Based Training session JSONs.
"""

import json
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Optional

import numpy as np

from src.utils.logger import get_logger
from src.utils.metrics import PerformanceMetrics, MetricConfig
from src.utils.scoring import create_scoring_engine
from src.tuners.utils.calibration import rescore_metrics_globally
from src.visualization.exceptions import DataLoadError, InvalidSchemaError

LOGGER = get_logger("SessionLoader")


@dataclass
class SessionTrace:
    """Parsed tuning session history."""

    generations: np.ndarray  # [1, 2, ..., G]
    best_scores: np.ndarray  # Running best per generation
    mean_scores: np.ndarray  # Population mean per generation
    std_scores: np.ndarray  # Population std per generation
    worker_scores: list[np.ndarray]  # Per-worker score arrays
    worker_configs: list[list[dict]]  # Per-generation, per-worker config dicts
    wall_clock_seconds: np.ndarray  # Cumulative time elapsed per generation
    generation_elapsed_seconds: np.ndarray  # Time spent evaluating this generation
    exploit_events: list[dict]  # [{gen, source, target, ...}]
    metadata: dict  # system_info, workload, tier, etc.
    metric_config: MetricConfig  # The normalization config used for scoring


# Recognised raw-metric keys that bypass composite scoring.
RAW_METRIC_KEYS = {"latency_p95", "latency_p99", "throughput"}


def _extract_raw_value(pm: PerformanceMetrics, metric_key: str) -> float:
    """Pull a single raw field from a PerformanceMetrics dataclass."""
    return float(getattr(pm, metric_key, 0.0))


def load_session(
    path: Path | str,
    metric_config: Optional[MetricConfig] = None,
    metric_key: Optional[str] = None,
) -> SessionTrace:
    """
    Load a PBT session JSON file, optionally rescore it with a provided metric_config
    (or compute a new global range if none is provided), and parse it into arrays for plotting.

    Parameters
    ----------
    metric_key : str | None
        If set to a raw metric name (``latency_p95``, ``latency_p99``,
        ``throughput``), the returned ``best_scores`` / ``mean_scores`` /
        ``std_scores`` / ``worker_scores`` arrays will contain that raw value
        instead of the composite score.  ``None`` (default) keeps the
        existing composite-score behaviour.
    """
    path = Path(path)
    if not path.exists():
        raise DataLoadError(f"Session file not found: {path}")

    try:
        with open(path, "r", encoding="utf-8") as f:
            data = json.load(f)
    except json.JSONDecodeError as e:
        raise DataLoadError(f"Failed to parse JSON in {path}: {e}") from e

    # Need at least generation_history
    if "generation_history" not in data:
        raise InvalidSchemaError(f"Missing 'generation_history' in {path}")

    history = data["generation_history"]
    n_gens = len(history)

    # 1. Gather all raw metrics for rescoring
    all_metrics: list[PerformanceMetrics] = []

    # We also keep track of exactly where each metric came from to apply scores back easily
    # mapping: (generation_idx, worker_id) -> metric_idx in all_metrics
    metric_map: dict[tuple[int, str], int] = {}

    for i, gen in enumerate(history):
        for ws in gen.get("worker_scores", []):
            wid = ws.get("worker_id")
            metrics_dict = ws.get("metrics")
            if wid and metrics_dict:
                # Filter metrics_dict to only valid PerformanceMetrics fields
                valid_keys = PerformanceMetrics.__dataclass_fields__.keys()
                filtered = {k: v for k, v in metrics_dict.items() if k in valid_keys}
                try:
                    pm = PerformanceMetrics(**filtered)
                    metric_map[(i, wid)] = len(all_metrics)
                    all_metrics.append(pm)
                except Exception as e:
                    LOGGER.debug(
                        "Skipping malformed metric in gen %d for %s: %s", i, wid, e
                    )

    tuning_session = data.get("tuning_session", {})
    workload = tuning_session.get("workload_type", "oltp")
    benchmark = tuning_session.get("benchmark_name")
    scoring_policy = data.get("scoring_policy", tuning_session.get("scoring_policy"))
    scoring_policy_version = data.get(
        "scoring_policy_version", tuning_session.get("scoring_policy_version")
    )
    metric_ref = data.get(
        "metric_reference_version", tuning_session.get("metric_reference_version")
    )

    # 2. Rescore Globally
    if metric_config is None:
        if all_metrics:
            metric_config, _, _ = rescore_metrics_globally(
                metrics=all_metrics,
                workload=workload,
                benchmark=benchmark,
                scoring_policy=scoring_policy,
                scoring_policy_version=scoring_policy_version,
                metric_reference_version=metric_ref,
            )
        else:
            from src.utils.metrics import create_metric_config

            metric_config = create_metric_config(workload)

    # Compute new scores for all metrics using the config
    use_raw = metric_key is not None and metric_key in RAW_METRIC_KEYS
    if use_raw:
        new_scores = [_extract_raw_value(m, metric_key) for m in all_metrics]
    elif hasattr(metric_config, "compute_score_value"):
        new_scores = [metric_config.compute_score_value(m) for m in all_metrics]
    else:
        engine = create_scoring_engine(metric_config)
        new_scores = [engine.compute_breakdown(m).final_score for m in all_metrics]

    # Initialize arrays
    generations = np.arange(1, n_gens + 1)
    best_scores = np.zeros(n_gens)
    mean_scores = np.zeros(n_gens)
    std_scores = np.zeros(n_gens)
    wall_clock_seconds = np.zeros(n_gens)
    generation_elapsed_seconds = np.zeros(n_gens)
    worker_configs: list[list[dict]] = [[] for _ in range(n_gens)]

    # Collect all unique worker IDs to track individual traces
    worker_ids = set()
    for gen in history:
        for ws in gen.get("worker_scores", []):
            if "worker_id" in ws:
                worker_ids.add(ws["worker_id"])

    sorted_worker_ids = sorted(list(worker_ids))
    worker_scores = [np.zeros(n_gens) for _ in sorted_worker_ids]
    worker_id_to_idx = {wid: idx for idx, wid in enumerate(sorted_worker_ids)}

    exploit_events = []

    # 3. Apply rescored values back to the generations
    for i, gen in enumerate(history):
        gen_scores = []
        for ws in gen.get("worker_scores", []):
            wid = ws.get("worker_id")
            if wid in worker_id_to_idx:
                idx = metric_map.get((i, wid))
                if idx is not None:
                    score = new_scores[idx]
                    worker_scores[worker_id_to_idx[wid]][i] = score
                    gen_scores.append(score)

        if gen_scores:
            if use_raw and metric_key.startswith("latency"):
                best_scores[i] = min(gen_scores)  # lower latency is better
            else:
                best_scores[i] = max(gen_scores)
            mean_scores[i] = np.mean(gen_scores)
            std_scores[i] = np.std(gen_scores)

        wall_clock_seconds[i] = gen.get("wall_clock_seconds", 0.0)
        generation_elapsed_seconds[i] = gen.get("generation_elapsed_seconds", 0.0)

        for wc in gen.get("worker_configs", []):
            worker_configs[i].append(wc)

        # Extract exploit events
        for op in gen.get("operations", []):
            if op.get("type") == "exploit":
                event_data = op.copy()
                event_data["generation"] = i + 1
                exploit_events.append(event_data)

    # Enforce monotonically increasing best scores (lower-is-better for latency)
    if use_raw and metric_key.startswith("latency"):
        running_best = np.minimum.accumulate(best_scores)
    else:
        running_best = np.maximum.accumulate(best_scores)

    # Extract best configuration metrics
    best_config_metrics = None
    if "best_configuration" in data and "metrics" in data["best_configuration"]:
        metrics_dict = data["best_configuration"]["metrics"]
        valid_keys = PerformanceMetrics.__dataclass_fields__.keys()
        filtered = {k: v for k, v in metrics_dict.items() if k in valid_keys}
        try:
            best_config_metrics = PerformanceMetrics(**filtered)
        except Exception:
            pass

    # Compile metadata
    metadata = {
        "file_name": path.name,
        "n_workers": len(sorted_worker_ids),
        "tuning_session": tuning_session,
        "best_config_metrics": best_config_metrics,
        "metric_key": metric_key,
    }

    return SessionTrace(
        generations=generations,
        best_scores=running_best,
        mean_scores=mean_scores,
        std_scores=std_scores,
        worker_scores=worker_scores,
        worker_configs=worker_configs,
        wall_clock_seconds=wall_clock_seconds,
        generation_elapsed_seconds=generation_elapsed_seconds,
        exploit_events=exploit_events,
        metadata=metadata,
        metric_config=metric_config,
    )


def load_sessions(
    directory: Path | str,
    metric_key: Optional[str] = None,
) -> list[SessionTrace]:
    """
    Load all PBT session JSON files in a directory, computing a single super-global
    normalization range across ALL files to ensure completely consistent scoring.
    """
    dir_path = Path(directory)
    if not dir_path.exists() or not dir_path.is_dir():
        raise DataLoadError(f"Directory not found: {directory}")

    json_files = sorted(dir_path.glob("pbt_results_*.json"), key=lambda p: p.name)
    if not json_files:
        LOGGER.warning("No PBT result files found in %s", directory)
        return []

    # Pass 1: Gather ALL metrics from ALL files to form a super-global range
    super_global_metrics = []
    shared_metadata: dict[str, Any] = {}

    for f in json_files:
        try:
            with open(f, "r", encoding="utf-8") as file_obj:
                data = json.load(file_obj)

            if not shared_metadata:
                # Grab metadata from first file to guide rescoring policy
                ts = data.get("tuning_session", {})
                shared_metadata["workload"] = ts.get("workload_type", "oltp")
                shared_metadata["benchmark"] = ts.get("benchmark_name")
                shared_metadata["scoring_policy"] = data.get(
                    "scoring_policy", ts.get("scoring_policy")
                )
                shared_metadata["policy_version"] = data.get(
                    "scoring_policy_version", ts.get("scoring_policy_version")
                )
                shared_metadata["metric_ref"] = data.get(
                    "metric_reference_version", ts.get("metric_reference_version")
                )

            for gen in data.get("generation_history", []):
                for ws in gen.get("worker_scores", []):
                    metrics_dict = ws.get("metrics")
                    if metrics_dict:
                        valid_keys = PerformanceMetrics.__dataclass_fields__.keys()
                        filtered = {
                            k: v for k, v in metrics_dict.items() if k in valid_keys
                        }
                        try:
                            super_global_metrics.append(PerformanceMetrics(**filtered))
                        except Exception:
                            pass
        except Exception as e:
            LOGGER.debug(
                "Failed to read metrics from %s during super-global pass: %s", f, e
            )

    # Compute super-global metric config
    metric_config = None
    if super_global_metrics:
        metric_config, _, _ = rescore_metrics_globally(
            metrics=super_global_metrics,
            workload=shared_metadata.get("workload", "oltp"),
            benchmark=shared_metadata.get("benchmark"),
            scoring_policy=shared_metadata.get("scoring_policy"),
            scoring_policy_version=shared_metadata.get("policy_version"),
            metric_reference_version=shared_metadata.get("metric_ref"),
        )
        LOGGER.info(
            "Computed super-global rescoring ranges from %d observations across %d files.",
            len(super_global_metrics),
            len(json_files),
        )

    # Pass 2: Load each session individually, injecting the super-global metric config
    sessions = []
    for f in json_files:
        try:
            sessions.append(load_session(f, metric_config=metric_config, metric_key=metric_key))
        except Exception as e:
            LOGGER.error("Failed to load session %s: %s", f.name, e)

    return sessions
