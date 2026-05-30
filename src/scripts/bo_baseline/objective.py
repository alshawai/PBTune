"""Objective function wrapper for SMAC3 Bayesian Optimization."""

import time
from typing import Callable, Dict, List, Optional, Tuple, TYPE_CHECKING
from ConfigSpace import Configuration

from src.tuner.config.knob_space import KnobSpace
from src.tuner.core.worker import Worker
from src.tuner.benchmark.orchestrator import WorkloadOrchestrator
from src.utils.metrics import MetricConfig, PerformanceMetrics
from src.scripts.bo_baseline.search_space import configspace_to_knobs
from src.utils.logger import get_logger

LOGGER = get_logger("Objective")

if TYPE_CHECKING:
    # Import only for type-checkers to avoid runtime import cycles
    from src.utils.environments import DatabaseEnvironment


def evaluate_config(
    config: Configuration,
    worker: Worker,
    orchestrator: WorkloadOrchestrator,
    knob_space: KnobSpace,
    previous_config: Optional[Dict],
    skip_scoring: bool = False,
) -> Tuple[
    Optional[float], Dict, Optional[PerformanceMetrics], Optional[float], Optional[Dict], bool, float
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
    skip_scoring : bool, default=False
        When True (Phase 1 pilot collection), knob application and metric
        collection proceed normally but cost/score/breakdown are returned as
        None. The caller is responsible for scoring later with calibrated
        normalization ranges.

    Returns
    -------
    Tuple[Optional[float], Dict, Optional[PerformanceMetrics], Optional[float], Optional[Dict], bool, float]
        (cost, knob_config, metrics, score, score_breakdown, restarted, wall_time)
        ``cost``, ``score``, and ``score_breakdown`` are None when skip_scoring=True.
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
        cost = 100.0 if not skip_scoring else None
        score = 0.0 if not skip_scoring else None
        score_breakdown = None
    elif skip_scoring:
        # Phase 1 pilot: metrics collected, scoring deferred until calibration
        cost = None
        score = None
        score_breakdown = None
    else:
        cost = max(0.0, min(100.0, 100.0 - score))
        score_breakdown = worker.score_breakdown
        if score_breakdown is None:
            engine = orchestrator._get_scoring_engine()
            score_breakdown = engine.compute_breakdown(
                metrics, worker_logger=worker.logger
            )

    return cost, knob_config, metrics, score, score_breakdown, restarted, wall_time


