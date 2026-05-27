"""Objective function wrapper for SMAC3 Bayesian Optimization."""

import time
from typing import Callable, Dict, List, Optional, Tuple
from ConfigSpace import Configuration

from src.tuner.config.knob_space import KnobSpace
from src.tuner.core.worker import Worker
from src.tuner.benchmark.orchestrator import WorkloadOrchestrator
from src.utils.metrics import MetricConfig, PerformanceMetrics
from src.scripts.bo_baseline.search_space import configspace_to_knobs
from src.utils.logger import get_logger

LOGGER = get_logger("Objective")

def evaluate_config(
    config: Configuration,
    worker: Worker,
    orchestrator: WorkloadOrchestrator,
    knob_space: KnobSpace,
    previous_config: Optional[Dict],
) -> Tuple[
    float, Dict, Optional[PerformanceMetrics], float, Optional[Dict], bool, float
]:
    """
    Evaluate a single configuration on a specific worker instance.

    After the benchmark completes successfully, this function queries
    ``pg_settings`` to read back the *actually applied* knob values so that
    PostgreSQL's internal quantization (e.g. rounding ``shared_buffers`` to
    the nearest 8 kB page) is reflected in the config dict returned to SMAC.
    Without this step, the surrogate model sees a flat landscape because many
    nearby suggested values map to the same rounded internal value.

    Parameters
    ----------
    config : Configuration
        ConfigSpace configuration to evaluate
    worker : Worker
        Worker instance bound to a specific PostgreSQL instance
    orchestrator : WorkloadOrchestrator
        Orchestrator for computing performance metrics
    knob_space : KnobSpace
        Knob space for configuration repair
    previous_config : Optional[Dict]
        Previous configuration for restart detection

    Returns
    -------
    Tuple[float, Dict, Optional[PerformanceMetrics], float, Optional[Dict], bool, float]
        (cost, knob_config, metrics, score, score_breakdown, restarted, wall_time)
        ``knob_config`` contains the *true* applied values after read-back.
    """
    t_start = time.time()

    knob_config = configspace_to_knobs(config, knob_space)
    knob_config = knob_space.repair_config_dependencies(knob_config)

    # Detect if restart is needed
    force_restart = False
    if previous_config is not None:
        for knob_def in knob_space.knobs.values():
            if knob_def.restart_required:
                prev_val = previous_config.get(knob_def.name)
                curr_val = knob_config.get(knob_def.name)
                if prev_val != curr_val:
                    force_restart = True
                    break

    worker.knob_config = knob_config
    worker.force_restart_next_eval = force_restart

    metrics, score, restarted, actual_db_config = orchestrator.evaluate_worker(
        worker, apply_config=True
    )

    # The orchestrator already verified the config and read back the true
    # DB values.  Merge them into knob_config so the surrogate model sees
    # the actual quantized values PostgreSQL is using.
    if actual_db_config:
        knob_config.update(actual_db_config)
        LOGGER.debug(
            "Merged %d actual DB values from evaluate_worker into knob_config",
            len(actual_db_config),
        )

    wall_time = time.time() - t_start

    if metrics is None or score is None:
        cost = 100.0
        score = 0.0
        score_breakdown = None
    else:
        cost = max(0.0, min(100.0, 100.0 - score))
        score_breakdown = worker.score_breakdown
        if score_breakdown is None:
            score_breakdown = orchestrator.config.metric_config.compute_score(
                metrics, worker_logger=worker.logger
            )

    return cost, knob_config, metrics, score, score_breakdown, restarted, wall_time
def create_objective(

    orchestrator: WorkloadOrchestrator,
    worker: Worker,
    knob_space: KnobSpace,
    metric_config: MetricConfig,
    iteration_log: List[Dict],
    pilot_phase_size: int = 10,
    env: Optional["DatabaseEnvironment"] = None,
    enable_snapshots: bool = False,
    snapshot_restore_interval: int = 1,
) -> Callable[[Configuration, int], float]:
    """
    Create an objective function for SMAC3 with Pilot+Freeze normalization.

    The objective uses default fallback ranges during the pilot phase (first
    `pilot_phase_size` iterations). At the end of the pilot, it calibrates
    normalization bounds from observed metrics exactly once, then freezes them
    for the remainder of the run. This keeps the surrogate model's training
    signal stable.

    Parameters
    ----------
    orchestrator : WorkloadOrchestrator
        The orchestrator for computing performance metrics
    worker : Worker
        The worker to evaluate
    knob_space : KnobSpace
        The knob space for configuration repair
    metric_config : MetricConfig
        Metric configuration for scoring
    iteration_log : List[Dict]
        Mutable list for tracking convergence
    pilot_phase_size : int
        Number of initial iterations before freezing normalization ranges
    env : Optional[DatabaseEnvironment]
        Database environment for snapshot restoration
    enable_snapshots : bool
        Whether to enable snapshot restoration
    snapshot_restore_interval : int
        Restore snapshots every N iterations

    Returns
    -------
    Callable[[Configuration, int], float]
        Objective function for SMAC3 (minimizes cost)
    """
    from typing import Dict, Any

    state: Dict[str, Any] = {
        "previous_config": None,
        "iteration_count": 0,
        "ranges_frozen": False,
    }

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
            # Handle snapshot restoration before evaluating the config
            if (
                enable_snapshots
                and env is not None
                and state["iteration_count"] > 0
                and state["iteration_count"] % snapshot_restore_interval == 0
            ):
                LOGGER.info(
                    "Restoring database snapshot for iteration %d (interval: %d)",
                    state["iteration_count"],
                    snapshot_restore_interval,
                )
                try:
                    restored = env.restore_snapshot(worker.worker_id)
                    if not restored:
                        LOGGER.error("Snapshot restore failed for worker %d", worker.worker_id)
                    else:
                        LOGGER.info("✓ Database snapshot restored successfully")
                except Exception as e:
                    LOGGER.error("Failed to restore database from snapshot: %s", e)

            cost, knob_config, metrics, score, score_breakdown, restarted, wall_time = evaluate_config(
                config, worker, orchestrator, knob_space, state["previous_config"]
            )

            iteration_entry = {
                "iteration": state["iteration_count"],
                "config": knob_config,
                "metrics": metrics.to_dict() if metrics is not None else {},
                "score": score if score is not None else 0.0,
                "score_breakdown": score_breakdown,
                "cost": cost,
                "wall_clock_seconds": wall_time,
                "restarted": restarted,
                "timestamp": time.time(),
            }
            iteration_log.append(iteration_entry)

            # Pilot+Freeze: calibrate ranges exactly once after pilot phase
            if (
                not state["ranges_frozen"]
                and state["iteration_count"] >= pilot_phase_size - 1
            ):
                all_metrics = [
                    PerformanceMetrics(**entry["metrics"])
                    for entry in iteration_log
                    if entry["metrics"]
                ]
                if all_metrics:
                    metric_config.update_ranges(all_metrics)
                    LOGGER.info(
                        "Normalization ranges frozen after %d pilot iterations",
                        state["iteration_count"] + 1,
                    )
                state["ranges_frozen"] = True

            state["previous_config"] = knob_config
            state["iteration_count"] += 1

            LOGGER.debug(
                f"Iteration {state['iteration_count']}: score={score:.2f}, cost={cost:.2f}, "
                f"wall_time={wall_time:.2f}s, frozen={state['ranges_frozen']}"
            )

            return cost

        except Exception as e:
            LOGGER.error(f"Error evaluating configuration: {e}", exc_info=True)
            return 100.0

    return objective
