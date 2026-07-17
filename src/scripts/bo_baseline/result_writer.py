"""Result serialization for Bayesian Optimization baseline runner."""

import json
from datetime import datetime
from pathlib import Path
from typing import Any, Dict, List, Optional

from src.knobs.knob_space import KnobSpace
from src.tuners.utils.output_paths import resolve_tuner_output_root
from src.tuners.utils.types import TuningStrategy
from src.utils.hardware_info import WorkerResources
from src.utils.session_clock import format_session_id
from src.utils.timing import TimingRecorder
from src.utils.types import BenchmarkConfig, SessionEnvironment
from src.scripts.bo_baseline.config import BOConfig
from src.utils.logger import get_logger
from src.utils.metrics import MetricConfig
import numpy as np

LOGGER = get_logger("ResultWriter")


def convert_numpy_types(obj: Any) -> Any:
    """Recursively convert numpy types to Python native types for JSON serialization."""
    if isinstance(obj, np.bool_):
        return bool(obj)
    elif isinstance(obj, (np.integer, np.int64, np.int32, np.int16, np.int8)):
        return int(obj)
    elif isinstance(obj, (np.floating, np.float64, np.float32, np.float16)):
        return float(obj)
    elif isinstance(obj, np.ndarray):
        return obj.tolist()
    elif isinstance(obj, dict):
        return {key: convert_numpy_types(value) for key, value in obj.items()}
    elif isinstance(obj, (list, tuple)):
        return [convert_numpy_types(item) for item in obj]
    elif hasattr(obj, "to_dict") and callable(obj.to_dict):
        return convert_numpy_types(obj.to_dict())
    else:
        return obj


def resolve_bo_output_root(
    output_dir: Path,
    benchmark_config: BenchmarkConfig,
    knob_tier: str,
    knob_source: str = "expert",
) -> Path:
    """Resolve the base BO output directory under results.

    Thin wrapper over the unified :func:`resolve_tuner_output_root` that
    derives the single ``workload`` key from the benchmark config.
    """
    if benchmark_config.benchmark == "sysbench":
        workload = benchmark_config.sysbench_workload
    elif benchmark_config.benchmark == "tpch":
        workload = "olap"
    else:
        workload = benchmark_config.workload_type

    return resolve_tuner_output_root(
        output_dir,
        strategy=TuningStrategy.BO,
        workload=workload,
        knob_tier=knob_tier,
        knob_source=knob_source,
    )


def write_bo_results(
    knob_space: KnobSpace,
    config: BOConfig,
    worker_resources: WorkerResources,
    system_info: Dict[str, Any],
    iteration_log: List[Dict],
    total_time: float,
    output_dir: Path,
    metric_config: MetricConfig,
    bo_surrogate: str = "gp",
    early_stopped: bool = False,
    stale_counter: int = 0,
    session_environment: Optional[SessionEnvironment] = None,
    tuning_time_seconds: Optional[float] = None,
    bootstrap_timing: Optional[TimingRecorder] = None,
    bo_timing: Optional[TimingRecorder] = None,
    run_timestamp: Optional[str] = None,
    requested_iterations: Optional[int] = None,
    requested_pilot_size: Optional[int] = None,
    actual_pilot_size: Optional[int] = None,
    cotenancy: Optional[Dict[str, Any]] = None,
) -> Dict[str, Any]:
    """
    Serialize Bayesian Optimization results in PBT-compatible JSON format.

    Parameters
    ----------
    knob_space : KnobSpace
        The knob space used for tuning
    config : BOConfig
        The BO configuration
    worker_resources : WorkerResources
        Hardware resources of the worker
    system_info : Dict[str, Any]
        System information snapshot
    iteration_log : List[Dict]
        Log of all iterations with metrics and configs
    total_time : float
        Total tuning time in seconds (wall clock including bootstrap)
    output_dir : Path
        Output directory for results
    metric_config : MetricConfig
        The metric configuration for scoring policy metadata
    bo_surrogate : str
        Surrogate model type (gp or rf)
    early_stopped : bool
        Whether the run terminated via early stopping
    stale_counter : int
        Number of consecutive non-improving iterations at termination
    session_environment : Optional[SessionEnvironment]
        Canonical environment snapshot for the run.
    tuning_time_seconds : Optional[float]
        Time inside the ask/tell loop (excludes bootstrap). When ``None`` the
        function falls back to ``total_time`` to remain backwards compatible
        with callers that have not yet been upgraded.
    bootstrap_timing : Optional[TimingRecorder]
        Bootstrap-phase recorder. Serialized as ``bootstrap_breakdown`` when
        provided.
    bo_timing : Optional[TimingRecorder]
        BO control-loop recorder collecting facade.ask / facade.tell spans.
        Mirrors PBT's per-generation ``generation_timing`` — its records are
        merged into the session-level ``timing_summary`` so cross-iteration
        BO overhead is visible alongside per-evaluation work.
    run_timestamp : Optional[str]
        Canonical session timestamp string. When omitted, falls back to
        :func:`format_session_id` so existing tests that pass none still work.
    requested_iterations : Optional[int]
        The user-requested total iteration budget (``config.n_iterations``).
        Serialized as ``tuning_session.requested_iterations`` so analysis can
        detect runs that were truncated relative to intent.
    requested_pilot_size : Optional[int]
        The user-requested bootstrap pilot size
        (``min(range_update_interval, n_iterations)``). Serialized as
        ``tuning_session.requested_pilot_size``.
    actual_pilot_size : Optional[int]
        The number of pilot configurations actually generated and evaluated.
        With the backfill sampler this should match ``requested_pilot_size``;
        the field is explicit so future regressions surface immediately.

    Returns
    -------
    Dict[str, Any]
        The serialized results dictionary
    """
    # Find best configuration from iteration log
    best_iteration = None
    best_score = -float("inf")

    for iteration in iteration_log:
        score = iteration.get("score", 0.0)
        if score > best_score:
            best_score = score
            best_iteration = iteration

    if best_iteration is None:
        LOGGER.warning("No valid iterations found in log")
        best_iteration = {
            "config": {},
            "metrics": {},
            "score": 0.0,
            "score_breakdown": None,
        }

    # Build generation history from iteration log
    generation_history = []
    best_score_so_far = -float("inf")
    bo_overhead_total = 0.0
    # Aggregate per-iteration timing into a single recorder so the top-level
    # ``timing_summary`` matches the PBT schema: mean/std/n/min/max/total per
    # component across the whole session.
    session_timing = TimingRecorder()

    for i, iteration in enumerate(iteration_log):
        score = iteration.get("score", 0.0)
        if score > best_score_so_far:
            best_score_so_far = score

        # Use the REAL bracketed BO overhead (ask + tell time) recorded by the
        # runner. The previous ``wall_clock_seconds * 0.05`` placeholder was a
        # fabricated proxy — see docs/research/timing-instrumentation-plan.md
        # Phase 2D.
        bo_overhead = iteration.get("bo_overhead_seconds", 0.0)
        bo_overhead_total += bo_overhead

        # ``generation_elapsed_seconds`` is the wall-clock cost of producing
        # this generation's observation: the per-evaluation work
        # (``wall_clock_seconds`` — apply, run, measure, score) plus the
        # BO control-loop overhead bracketed around it (ask + tell + drift +
        # repair + relabel). Visualizers consume this as
        # "time spent evaluating this generation"; emitting only the BO
        # overhead would under-report it by 200-1000x.
        wall_clock = iteration.get("wall_clock_seconds", 0.0)
        generation_elapsed = wall_clock + bo_overhead

        iteration_timing = iteration.get("timing")
        if iteration_timing and isinstance(iteration_timing, dict):
            for rec in iteration_timing.get("records", []) or []:
                session_timing.add(
                    rec.get("component", "unknown"),
                    float(rec.get("seconds", 0.0)),
                    **(rec.get("metadata") or {}),
                )

        worker_score_entry = {
            "worker_id": 0,
            "score": score,
            "metrics": iteration.get("metrics", {}),
            "score_breakdown": convert_numpy_types(
                iteration.get("score_breakdown")
            ),
        }
        if iteration_timing is not None:
            worker_score_entry["timing"] = iteration_timing

        generation_entry = {
            "generation": i,
            "best_score": best_score_so_far,
            "mean_score": score,
            "std_score": 0.0,
            "num_exploited": 0,
            "best_worker_id": 0,
            "converged": False,
            "restart_count": 1 if iteration.get("restarted", False) else 0,
            "timestamp": datetime.fromtimestamp(
                iteration.get("timestamp", 0.0)
            ).isoformat(),
            "wall_clock_seconds": iteration.get("wall_clock_seconds", 0.0),
            "generation_elapsed_seconds": generation_elapsed,
            "phase": iteration.get("phase", "bo"),
            "bo_overhead_seconds": bo_overhead,
            "worker_scores": [worker_score_entry],
            "worker_configs": [
                {
                    "worker_id": 0,
                    "config": convert_numpy_types(
                        knob_space.config_to_fractions(iteration.get("config", {}))
                    ),
                }
            ],
        }
        # Per-generation `timing` block is reserved for whole-generation work
        # not attributable to any single worker (in PBT: `evolve`). BO has no
        # such per-gen work — every eval component is per-worker — so we do
        # NOT mirror `iteration_timing` here. The per-worker copy above is the
        # single source of truth; mirroring would double-count when the
        # analysis script aggregates both layers.
        generation_history.append(generation_entry)

    # Fold BO control-loop spans (facade.ask / facade.tell across the run)
    # into the session aggregate so ``timing_summary`` has the same shape as
    # PBT's: per-component mean/std/n/min/max/total covering every layer of
    # work that contributed to wall clock.
    if bo_timing is not None:
        session_timing.merge(bo_timing)

    # Fold bootstrap-phase spans (instance setup, knob pruning, pilot
    # generation, ConfigSpace/SMAC build, default-config seeding) into the
    # session aggregate. ``bootstrap_breakdown`` (below) keeps a separate
    # canonical view used by timing_breakdown.py — but other consumers that
    # read ``timing_summary`` directly would otherwise see zero pre-tuning
    # work. timing_breakdown.py reads ``bootstrap_breakdown`` independently
    # (collapsed to a single ``bootstrap`` row per session — see
    # src/analysis/timing_breakdown.py:142-156), so the merge here does NOT
    # double-count in that tool.
    if bootstrap_timing is not None:
        session_timing.merge(bootstrap_timing)

    # Build result dictionary
    timestamp = run_timestamp or format_session_id()

    # Compute bootstrap_seconds: total_time = bootstrap + tuning.
    if tuning_time_seconds is None:
        # Backwards-compat path used by older callers/tests.
        effective_tuning_time = total_time
        bootstrap_seconds = 0.0
    else:
        effective_tuning_time = tuning_time_seconds
        bootstrap_seconds = max(0.0, total_time - tuning_time_seconds)

    result = {
        "tuning_session": {
            "timing_schema_version": "1.1",
            "tuning_strategy": "bo",
            "optimizer": "bayesian_optimization",
            "bo_library": "smac3",
            "bo_surrogate": bo_surrogate,
            "bo_acquisition": "expected_improvement",
            "scoring_policy": metric_config.scoring_policy,
            "scoring_policy_version": metric_config.scoring_policy_version,
            "metric_reference_version": metric_config.metric_reference_version,
            "knob_tier": config.knob_tier,
            "num_knobs": len(knob_space.knobs),
            "workload_type": config.benchmark_config.workload_type,
            "benchmark_name": config.benchmark_config.benchmark,
            "iterations": len(iteration_log),
            "requested_iterations": (
                requested_iterations
                if requested_iterations is not None
                else config.n_iterations
            ),
            "requested_pilot_size": requested_pilot_size,
            "actual_pilot_size": actual_pilot_size,
            "seed": config.random_seed,
            "num_parallel_workers": config.max_workers,
            "total_generations": len(iteration_log),
            "total_time_seconds": total_time,
            "tuning_time_seconds": effective_tuning_time,
            "bootstrap_seconds": bootstrap_seconds,
            "bo_overhead_total_seconds": bo_overhead_total,
            "timestamp": timestamp,
            "tuning_mode": config.benchmark_config.tuning_mode.value,
            "sysbench_duration_seconds": config.benchmark_config.evaluation_duration,
            "sysbench_warmup_seconds": config.benchmark_config.warmup_duration,
            "sysbench_tables": config.benchmark_config.sysbench_tables,
            "sysbench_table_size": config.benchmark_config.sysbench_table_size,
            "sysbench_workload": config.benchmark_config.sysbench_workload,
            "tpch_scale_factor": config.benchmark_config.scale_factor,
            "tpch_warmup_passes": config.benchmark_config.warmup_passes,
            "reference_pbt_session": (
                str(config.pbt_session_path) if config.pbt_session_path else None
            ),
            "reference_pbt_knobs": list(config.pbt_knob_names or ()),
            "resource_equalization": config.pbt_worker_resources is not None,
            "early_stopped": early_stopped,
            "early_stopping_patience": config.early_stopping_patience,
            "early_stopping_enabled": config.early_stopping_enabled,
        },
        "scoring_policy": metric_config.scoring_policy,
        "scoring_policy_version": metric_config.scoring_policy_version,
        "metric_reference_version": metric_config.metric_reference_version,
        "workload_features": convert_numpy_types(metric_config.workload_features),
        "normalization_metadata": convert_numpy_types(
            metric_config.get_normalization_metadata()
        ),
        "warm_start": {"enabled": False},
        "best_configuration": {
            "score": best_score,
            "knobs": convert_numpy_types(
                knob_space.config_to_fractions(best_iteration.get("config", {}))
            ),
            "metrics": best_iteration.get("metrics", {}),
            "score_breakdown": convert_numpy_types(
                best_iteration.get("score_breakdown")
            ),
        },
        "worker_resources": {
            "ram_bytes": worker_resources.ram_bytes,
            "cpu_cores": worker_resources.cpu_cores,
            "disk_type": worker_resources.disk_type,
            "disk_read_bps": worker_resources.disk_read_bps,
            "disk_write_bps": worker_resources.disk_write_bps,
            "disk_read_iops": worker_resources.disk_read_iops,
            "disk_write_iops": worker_resources.disk_write_iops,
            "disk_class": worker_resources.disk_class,
        },
        "generation_history": generation_history,
        "bootstrap_breakdown": (
            bootstrap_timing.to_dict() if bootstrap_timing is not None else None
        ),
        "timing_summary": session_timing.aggregate(),
        "convergence": {
            "converged": early_stopped,
            "generations_without_improvement": stale_counter,
            "early_stopped": early_stopped,
            "early_stopping_patience": config.early_stopping_patience,
        },
        "system_info": system_info,
    }

    if session_environment is not None:
        result["session_environment"] = session_environment.to_dict()

    if cotenancy is not None:
        # Honest record of the co-tenant load applied during each measurement
        # window so the BO session documents the contention it ran under
        # (degree, background worker ids, load-config seed). The foreground BO
        # trial is always worker 0; ids in ``background_worker_ids`` are pure
        # load and were not measured.
        result["cotenancy"] = convert_numpy_types(cotenancy)

    # Create output directory structure
    bo_root = resolve_bo_output_root(
        output_dir=output_dir,
        benchmark_config=config.benchmark_config,
        knob_tier=config.knob_tier,
        knob_source=config.knob_source,
    )
    bo_dir = bo_root / "traces"
    bo_dir.mkdir(parents=True, exist_ok=True)

    # Write results to file (strategy-agnostic stem; matches the unified
    # tuners' traces/trace_*.json — the strategy is encoded in the path).
    output_file = bo_dir / f"trace_{timestamp}.json"
    with open(output_file, "w") as f:
        json.dump(result, f, indent=2)

    LOGGER.info(f"BO results written to {output_file}")

    return result
