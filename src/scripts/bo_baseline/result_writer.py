"""Result serialization for Bayesian Optimization baseline runner."""

import json
from datetime import datetime
from pathlib import Path
from typing import Any, Dict, List, Optional

from src.tuner.config.knob_space import KnobSpace
from src.utils.hardware_info import WorkerResources
from src.scripts.bo_baseline.config import BOConfig
from src.utils.logger import get_logger

logger = get_logger(__name__)


def write_bo_results(
    knob_space: KnobSpace,
    config: BOConfig,
    worker_resources: WorkerResources,
    system_info: Dict[str, Any],
    iteration_log: List[Dict],
    total_time: float,
    output_dir: Path,
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
        logger.warning("No valid iterations found in log")
        best_iteration = {
            "config": {},
            "metrics": {},
            "score": 0.0,
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
        bo_overhead = iteration.get("wall_time_seconds", 0.0) * 0.05
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
            "timestamp": datetime.fromtimestamp(iteration.get("timestamp", 0.0)).isoformat(),
            "iteration_wall_time_seconds": iteration.get("wall_time_seconds", 0.0),
            "bo_overhead_seconds": bo_overhead,
            "worker_scores": [
                {
                    "worker_id": 0,
                    "score": score,
                    "metrics": iteration.get("metrics", {}),
                }
            ],
            "worker_configs": [
                {
                    "worker_id": 0,
                    "config": iteration.get("config", {}),
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
            "knob_tier": config.knob_tier,
            "num_knobs": len(knob_space.knobs),
            "workload_type": config.workload_type,
            "benchmark_name": config.benchmark,
            "n_iterations": config.n_iterations,
            "seed": config.random_seed,
            "population_size": 1,
            "total_generations": len(iteration_log),
            "total_time_seconds": total_time,
            "timestamp": timestamp,
            "tuning_mode": config.tuning_mode,
            "sysbench_duration_seconds": config.evaluation_duration,
            "sysbench_warmup_seconds": config.warmup_duration,
            "sysbench_tables": config.sysbench_tables,
            "sysbench_table_size": config.sysbench_table_size,
            "sysbench_workload": config.sysbench_workload,
            "tpch_scale_factor": config.scale_factor,
            "tpch_warmup_passes": config.tpch_warmup_passes,
            "reference_pbt_session": (
                str(config.pbt_session_path) if config.pbt_session_path else None
            ),
            "reference_pbt_knobs": list(config.pbt_knob_names or ()),
        },
        "best_configuration": {
            "score": best_score,
            "knobs": best_iteration.get("config", {}),
            "metrics": best_iteration.get("metrics", {}),
        },
        "worker_resources": {
            "ram_bytes": worker_resources.ram_bytes,
            "cpu_cores": worker_resources.cpu_cores,
            "disk_type": worker_resources.disk_type,
        },
        "generation_history": generation_history,
        "convergence": {
            "converged": False,
            "iterations_without_improvement": 0,
        },
        "system_info": system_info,
    }

    # Create output directory structure
    workload_dir = output_dir / config.workload_type
    bo_dir = workload_dir / "bo_runs" / config.knob_tier / "tuning_sessions"
    bo_dir.mkdir(parents=True, exist_ok=True)

    # Write results to file
    output_file = bo_dir / f"bo_results_{timestamp}.json"
    with open(output_file, "w") as f:
        json.dump(result, f, indent=2)

    logger.info(f"BO results written to {output_file}")

    return result
