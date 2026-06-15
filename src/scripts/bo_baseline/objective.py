"""Objective function wrapper for SMAC3 Bayesian Optimization."""

import time
from typing import Dict, Optional, Tuple, TYPE_CHECKING
from ConfigSpace import Configuration

from src.tuner.config.knob_space import KnobSpace
from src.tuner.core.worker import Worker
from src.tuner.benchmark.orchestrator import WorkloadOrchestrator
from src.utils.metrics import PerformanceMetrics
from src.scripts.bo_baseline.search_space import configspace_to_knobs, get_config_drift
from src.utils.logger import get_logger

LOGGER = get_logger("Objective")

if TYPE_CHECKING:
    # Import only for type-checkers to avoid runtime import cycles
    pass


def evaluate_config(
    config: Configuration,
    worker: Worker,
    orchestrator: WorkloadOrchestrator,
    knob_space: KnobSpace,
    previous_config: Optional[Dict],
    seed: Optional[int] = None,
) -> Tuple[
    Optional[float],
    Dict,
    Optional[PerformanceMetrics],
    Optional[float],
    Optional[Dict],
    bool,
    float,
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
    seed : Optional[int]
        Optional random seed for reproducibility.

    Returns
    -------
    Tuple[Optional[float], Dict, Optional[PerformanceMetrics], Optional[float], Optional[Dict], bool, float]
        (cost, knob_config, metrics, score, score_breakdown, restarted, wall_time).
        ``knob_config`` contains the *true* applied values after read-back.
        ``cost`` and ``score`` are 100.0 / 0.0 on failure.
    """
    t_start = time.time()

    bo_suggested_config = configspace_to_knobs(config, knob_space)
    knob_config = knob_space.repair_config_dependencies(dict(bo_suggested_config))

    repair_drift = get_config_drift(bo_suggested_config, knob_config)

    if repair_drift:
        drift_str = ", ".join(
            f"{k}: {v1} -> {v2}" for k, (v1, v2) in repair_drift.items()
        )
        LOGGER.info(f"➤ BO Suggestion Repaired: {drift_str}")

    # Detect if restart is needed
    force_restart = False
    trigger_knob: str | None = None
    if previous_config is not None:
        for knob_def in knob_space.knobs.values():
            if knob_def.restart_required:
                prev_val = previous_config.get(knob_def.name)
                curr_val = knob_config.get(knob_def.name)
                if prev_val != curr_val:
                    force_restart = True
                    trigger_knob = knob_def.name
                    break
    else:
        # Very first evaluation: must restart to guarantee DB state matches config
        force_restart = True

    if force_restart:
        if trigger_knob is not None:
            LOGGER.debug(
                "Restart required — knob '%s' changed: %r → %r",
                trigger_knob,
                previous_config.get(trigger_knob) if previous_config else None,
                knob_config.get(trigger_knob),
            )
        else:
            LOGGER.debug("Restart required — first evaluation, forcing clean DB state")

    worker.knob_config = knob_config.copy()
    worker.force_restart_next_eval = force_restart

    metrics, score, restarted, actual_db_config = orchestrator.evaluate_worker(
        worker, apply_config=True, random_seed=seed
    )

    # The orchestrator already verified the config and read back the true
    # DB values.  Merge them into knob_config so the surrogate model sees
    # the actual quantized values PostgreSQL is using.
    if actual_db_config:
        db_drift = get_config_drift(knob_config, actual_db_config)
        if db_drift:
            drift_str = ", ".join(
                f"{k}: {v1} -> {v2}" for k, (v1, v2) in db_drift.items()
            )
            LOGGER.info(f"➤ DB Internal Quantization: {drift_str}")

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
        LOGGER.warning(
            "evaluate_config returning failure result (cost=100, score=0): "
            "metrics=%s, score=%s, wall_time=%.2fs",
            "None" if metrics is None else "present",
            score,
            wall_time,
        )
    else:
        cost = max(0.0, min(100.0, 100.0 - score))
        score_breakdown = worker.score_breakdown
        if score_breakdown is None:
            engine = orchestrator._get_scoring_engine()
            score_breakdown = engine.compute_breakdown(
                metrics, worker_logger=worker.logger
            )

    return cost, knob_config, metrics, score, score_breakdown, restarted, wall_time
