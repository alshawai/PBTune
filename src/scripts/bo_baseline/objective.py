"""Objective function wrapper for SMAC3 Bayesian Optimization."""

import time
from typing import Callable, Dict, Any, List, Optional
from ConfigSpace import Configuration

from src.tuner.config.knob_space import KnobSpace
from src.tuner.core.worker import Worker
from src.tuner.evaluator.evaluator import Evaluator
from src.utils.environments.base import DatabaseEnvironment
from src.utils.metrics import MetricConfig, PerformanceMetrics
from src.scripts.bo_baseline.search_space import configspace_to_knobs
from src.utils.logger import get_logger

logger = get_logger(__name__)


def create_objective(
    evaluator: Evaluator,
    worker: Worker,
    knob_space: KnobSpace,
    env: DatabaseEnvironment,
    metric_config: MetricConfig,
    iteration_log: List[Dict],
    range_update_interval: int = 5,
) -> Callable[[Configuration, int], float]:
    """
    Create an objective function for SMAC3.

    Parameters
    ----------
    evaluator : Evaluator
        The evaluator for computing performance metrics
    worker : Worker
        The worker to evaluate
    knob_space : KnobSpace
        The knob space for configuration repair
    env : DatabaseEnvironment
        The database environment
    metric_config : MetricConfig
        Metric configuration for scoring
    iteration_log : List[Dict]
        Mutable list for tracking convergence
    range_update_interval : int
        How often to update metric ranges

    Returns
    -------
    Callable[[Configuration, int], float]
        Objective function for SMAC3 (minimizes cost)
    """
    # Closure state for tracking previous config
    state = {"previous_config": None, "iteration_count": 0}

    def objective(config: Configuration, seed: int = 0) -> float:
        """
        Evaluate a configuration and return cost (lower is better).

        Parameters
        ----------
        config : Configuration
            ConfigSpace configuration to evaluate
        seed : int
            Random seed (unused, for SMAC compatibility)

        Returns
        -------
        float
            Cost value (100 - score), with penalties for failures
        """
        try:
            t_start = time.time()

            # Convert ConfigSpace config to knob dict
            knob_config = configspace_to_knobs(config, knob_space)

            # Repair dependencies
            knob_config = knob_space.repair_config_dependencies(knob_config)

            # Detect if restart is needed
            force_restart = False
            if state["previous_config"] is not None:
                # Check if any restart-required knobs changed
                for knob_def in knob_space.knobs.values():
                    if knob_def.restart_required:
                        prev_val = state["previous_config"].get(knob_def.name)
                        curr_val = knob_config.get(knob_def.name)
                        if prev_val != curr_val:
                            force_restart = True
                            break

            # Update worker configuration
            worker.knob_config = knob_config
            worker.force_restart_next_eval = force_restart

            # Evaluate worker
            metrics, score, restarted = evaluator.evaluate_worker(
                worker, apply_config=True
            )

            wall_time = time.time() - t_start

            # Compute cost (SMAC minimizes)
            if metrics is None or score is None:
                # Evaluation failed
                cost = 100.0
            else:
                # Normal case: cost = 100 - score
                cost = max(0.0, min(100.0, 100.0 - score))

            # Log iteration
            iteration_entry = {
                "iteration": state["iteration_count"],
                "config": knob_config,
                "metrics": metrics.to_dict() if metrics is not None else {},
                "score": score if score is not None else 0.0,
                "cost": cost,
                "wall_time_seconds": wall_time,
                "restarted": restarted,
                "timestamp": time.time(),
            }
            iteration_log.append(iteration_entry)

            # Adaptive range update every N iterations
            if (
                state["iteration_count"] % range_update_interval == 0
                and state["iteration_count"] > 0
            ):
                # Collect all metrics from iteration log
                all_metrics = [
                    PerformanceMetrics(**entry["metrics"])
                    for entry in iteration_log
                    if entry["metrics"]
                ]
                if all_metrics:
                    metric_config.expand_ranges_for_metrics(all_metrics)

            state["previous_config"] = knob_config
            state["iteration_count"] += 1

            logger.debug(
                f"Iteration {state['iteration_count']}: score={score:.2f}, cost={cost:.2f}, "
                f"wall_time={wall_time:.2f}s"
            )

            return cost

        except Exception as e:
            logger.error(f"Error evaluating configuration: {e}", exc_info=True)
            return 100.0

    return objective
