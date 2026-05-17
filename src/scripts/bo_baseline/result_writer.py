"""Result serialization for Bayesian Optimization baseline runner."""

import json
from datetime import datetime
from pathlib import Path
from typing import Any, Dict, List

from src.tuner.config.knob_space import KnobSpace
from src.utils.hardware_info import WorkerResources
from src.utils.types import BenchmarkConfig
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
    else:
        return obj


def resolve_bo_output_root(
    output_dir: Path, benchmark_config: BenchmarkConfig, knob_tier: str
) -> Path:
    """Resolve the base BO output directory under results."""
    if benchmark_config.benchmark == "sysbench":
        benchmark_key = benchmark_config.sysbench_workload
    else:
        benchmark_key = benchmark_config.benchmark

    return (
        output_dir
        / benchmark_config.workload_type
        / benchmark_key
        / "bo_runs"
        / knob_tier
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
        Total tuning time in seconds
    output_dir : Path
        Output directory for results
    metric_config : MetricConfig
        The metric configuration for scoring policy metadata
    bo_surrogate : str
        Surrogate model type (gp or rf)

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

    for i, iteration in enumerate(iteration_log):
        score = iteration.get("score", 0.0)
        if score > best_score_so_far:
            best_score_so_far = score

        # Estimate BO overhead (ask + tell time) - for now, estimate as 5% of wall time
        # In a real implementation, this would be tracked separately
        bo_overhead = iteration.get("wall_clock_seconds", 0.0) * 0.05
        bo_overhead_total += bo_overhead

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
            "generation_elapsed_seconds": bo_overhead,
            "worker_scores": [
                {
                    "worker_id": 0,
                    "score": score,
                    "metrics": iteration.get("metrics", {}),
                    "score_breakdown": convert_numpy_types(iteration.get("score_breakdown")),
                }
            ],
            "worker_configs": [
                {
                    "worker_id": 0,
                    "config": convert_numpy_types(knob_space.config_to_fractions(iteration.get("config", {}))),
                }
            ],
        }
        generation_history.append(generation_entry)

    # Build result dictionary
    timestamp = datetime.now().strftime("%Y%m%d_%H%M")

    result = {
        "tuning_session": {
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
            "seed": config.random_seed,
            "num_parallel_workers": config.max_workers,
            "total_generations": len(iteration_log),
            "total_time_seconds": total_time,
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
        },
        "scoring_policy": metric_config.scoring_policy,
        "scoring_policy_version": metric_config.scoring_policy_version,
        "metric_reference_version": metric_config.metric_reference_version,
        "workload_features": convert_numpy_types(metric_config.workload_features),
        "normalization_metadata": convert_numpy_types(metric_config.get_normalization_metadata()),
        "warm_start": {"enabled": False},
        "best_configuration": {
            "score": best_score,
            "knobs": convert_numpy_types(knob_space.config_to_fractions(best_iteration.get("config", {}))),
            "metrics": best_iteration.get("metrics", {}),
            "score_breakdown": convert_numpy_types(best_iteration.get("score_breakdown")),
        },
        "worker_resources": {
            "ram_bytes": worker_resources.ram_bytes,
            "cpu_cores": worker_resources.cpu_cores,
            "disk_type": worker_resources.disk_type,
        },
        "generation_history": generation_history,
        "convergence": {
            "converged": False,
            "generations_without_improvement": 0,
        },
        "system_info": system_info,
    }

    # Create output directory structure
    bo_root = resolve_bo_output_root(
        output_dir=output_dir,
        benchmark_config=config.benchmark_config,
        knob_tier=config.knob_tier,
    )
    bo_dir = bo_root / "baseline_sessions"
    bo_dir.mkdir(parents=True, exist_ok=True)

    # Write results to file
    output_file = bo_dir / f"bo_results_{timestamp}.json"
    with open(output_file, "w") as f:
        json.dump(result, f, indent=2)

    LOGGER.info(f"BO results written to {output_file}")

    return result
