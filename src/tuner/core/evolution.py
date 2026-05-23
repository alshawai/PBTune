"""
PBT Evolution Strategies
=========================

This module implements the exploit and explore mechanisms that drive
Population Based Training's evolutionary optimization.

Key Concepts:
-------------
1. **Truncation Selection (Exploit)**: Poor performers copy from elite performers
2. **Perturbation (Explore)**: Copied configurations are perturbed for diversity

Mathematical Formulation (from DeepMind PBT paper):
---------------------------------------------------

Exploit Function:
    For each worker w_i:
        if performance(w_i) in bottom α quantile:
            w_j ~ Uniform(top α quantile)
            w_i ← copy(w_j)

Explore Function:
    For each exploited worker w_i:
        For each knob k:
            w_i.knobs[k] ← w_i.knobs[k] × U(0.8, 1.2)

Where:
- α = exploit_quantile (typically 0.2, meaning bottom/top 20%)
- U(a, b) = uniform random distribution between a and b

"""

from typing import List, Tuple, Optional
import logging
import numpy as np

from src.tuner.core.worker import Worker
from src.utils.logger import get_logger, get_color_context

LOGGER = get_logger("Evolution")
COLORS = get_color_context()


def truncation_selection(
    workers: List[Worker],
    exploit_quantile: float = 0.2,
    require_ready: bool = True,
    dead_config_threshold: float = 6.0,
) -> List[Tuple[int, int]]:
    """
    Identify which workers should exploit (copy from) which elite workers.

    This implements the "truncation selection" strategy from the PBT paper.
    Workers are ranked by performance, and poor performers are paired with
    elite performers for exploitation.

    Parameters
    ----------
    workers : List[Worker]
        The population of workers

    exploit_quantile : float
        Fraction of population to exploit/be exploited
        Default: 0.2 (bottom 20% copy from top 20%)

    require_ready : bool
        If True, only consider ready workers for exploitation
        Default: True (prevents exploiting workers still warming up)

    dead_config_threshold : float
        Score threshold below which a worker is treated as a dead config.
        Dead workers are always included in rescue (poor pool), bypassing
        ready gating and quantile limits.

    Returns
    -------
    List[Tuple[int, int]]
        List of (poor_worker_idx, elite_worker_idx) pairs
        Empty list if no exploitation should occur

    Notes
    -----
    **Why Random Pairing?**

    We could pair deterministically (worst with best, 2nd-worst with 2nd-best),
    but random pairing from the elite group:
    - Increases diversity (different poors copy different elites)
    - Avoids everyone converging to single best config
    - Matches original PBT paper
    """
    if len(workers) < 2:
        return []

    dead_workers = [w for w in workers if w.performance_score < dead_config_threshold]

    if require_ready:
        ready_workers = [w for w in workers if w.is_ready()]
    else:
        ready_workers = workers

    if len(ready_workers) < 2:
        return []

    quantile_size = max(1, int(len(ready_workers) * exploit_quantile))

    ready_non_dead = [
        w for w in ready_workers if w.performance_score >= dead_config_threshold
    ]
    elite_candidates = ready_non_dead.copy()

    if not elite_candidates:
        elite_candidates = [
            w for w in workers if w.performance_score >= dead_config_threshold
        ]
        if require_ready and elite_candidates:
            LOGGER.warning(
                "No ready non-dead workers available for elites; "
                "falling back to non-dead workers regardless of readiness"
            )

    if not elite_candidates:
        LOGGER.warning("No non-dead workers available; skipping exploit-explore rescue")
        return []

    sorted_elites = sorted(
        elite_candidates, key=lambda w: w.performance_score, reverse=True
    )
    elite_workers = sorted_elites[: min(quantile_size, len(sorted_elites))]

    normal_poor_workers: List[Worker] = []
    normal_quantile_size = 0
    if ready_non_dead:
        normal_quantile_size = quantile_size
        sorted_ready_non_dead = sorted(
            ready_non_dead, key=lambda w: w.performance_score, reverse=False
        )
        normal_poor_workers = sorted_ready_non_dead[:normal_quantile_size]

    elite_worker_ids = {w.worker_id for w in elite_workers}
    poor_workers: List[Worker] = []
    seen_worker_ids = set()

    target_poor_count = max(len(dead_workers), normal_quantile_size)
    candidate_workers = dead_workers.copy()

    if len(candidate_workers) < target_poor_count:
        for worker in normal_poor_workers:
            if worker.worker_id in {w.worker_id for w in candidate_workers}:
                continue
            candidate_workers.append(worker)
            if len(candidate_workers) >= target_poor_count:
                break

    for worker in candidate_workers:
        if worker.worker_id in seen_worker_ids:
            continue
        if worker.worker_id in elite_worker_ids:
            continue
        poor_workers.append(worker)
        seen_worker_ids.add(worker.worker_id)

    if not poor_workers:
        return []

    if dead_workers:
        LOGGER.info(
            "Dead-config rescue candidates this generation: %d workers (threshold=%.2f)",
            len(dead_workers),
            dead_config_threshold,
        )

    worker_to_idx = {w.worker_id: i for i, w in enumerate(workers)}
    pairs = []
    rng = np.random.default_rng()

    for poor_worker in poor_workers:
        elite_worker = rng.choice(elite_workers)  # type: ignore

        poor_idx = worker_to_idx[poor_worker.worker_id]
        elite_idx = worker_to_idx[elite_worker.worker_id]

        pairs.append((poor_idx, elite_idx))

    return pairs


def execute_exploit_explore(
    workers: List[Worker],
    exploit_quantile: float = 0.2,
    perturbation_factors: Tuple[float, float] = (0.8, 1.2),
    current_generation: int = 0,
    require_ready: bool = True,
    dead_config_threshold: float = 6.0,
    exclude_knobs: Optional[List[str]] = None,
) -> int:
    """
    Execute complete exploit-explore cycle for the population.

    Parameters
    ----------
    workers : List[Worker]
        The population of workers (modified in-place)

    exploit_quantile : float
        Fraction of population to exploit/be exploited
        Default: 0.2 (20%)

    perturbation_factors : Tuple[float, float]
        (min_factor, max_factor) for perturbation
        Default: (0.8, 1.2) means ±20%

    current_generation : int
        Current generation number (for tracking)
        Default: 0

    require_ready : bool
        Only exploit ready workers
        Default: True

    dead_config_threshold : float
        Score threshold used to force dead workers into exploit rescue pool.

    exclude_knobs : Optional[List[str]]
        Knobs to exclude from perturbation (keep constant)
        Used for two-stage PBT where restart knobs are frozen between restart intervals

    Returns
    -------
    int
        Number of workers that were exploited

    Notes
    -----
    **In-Place Modification:**

    This function modifies the workers list in-place. After calling:
    - Some workers will have new configurations
    - parent_id and generation_created will be updated
    - performance_score will NOT be updated (requires re-evaluation)

    **Important:** After exploit-explore, workers MUST be re-evaluated
    to measure performance of their new configurations!

    Examples
    --------
    >>> # Typical usage in Population.exploit_and_explore()
    >>> num_exploited = execute_exploit_explore(
    ...     workers=self.workers,
    ...     exploit_quantile=self.config.exploit_quantile,
    ...     perturbation_factors=self.config.perturbation_factors,
    ...     current_generation=self.current_generation,
    ...     verbose=self.config.verbose
    ... )
    >>> print(f"Exploited {num_exploited} workers")
    """
    pairs = truncation_selection(
        workers=workers,
        exploit_quantile=exploit_quantile,
        require_ready=require_ready,
        dead_config_threshold=dead_config_threshold,
    )

    if not pairs:
        LOGGER.debug(" ➤ No workers exploited (not enough ready workers)")
        return 0

    for poor_idx, elite_idx in pairs:
        poor_worker = workers[poor_idx]
        elite_worker = workers[elite_idx]

        poor_worker.logger.info(
            "(score=%.3f%%) ← exploits [Worker-%d] (score=%.3f%%)",
            poor_worker.performance_score,
            elite_worker.worker_id,
            elite_worker.performance_score,
        )

        poor_worker.clone_from(
            elite_worker, current_generation, exclude_knobs=exclude_knobs
        )

        poor_worker.perturb(
            perturbation_factors=perturbation_factors,
            current_generation=current_generation,
            exclude_knobs=exclude_knobs,
        )

        LOGGER.debug(" ➤ Copied config and applied perturbation")

    LOGGER.info(
        "%s➤ Exploit-explore complete: %d workers modified.%s",
        COLORS.bold, len(pairs), COLORS.reset
    )

    return len(pairs)


def get_elite_workers(workers: List[Worker], quantile: float = 0.2) -> List[Worker]:
    """
    Get the elite (top-performing) workers from the population.

    Useful for analysis, checkpointing, or extracting best configurations.

    Parameters
    ----------
    workers : List[Worker]
        The population

    quantile : float
        Fraction of top performers to return
        Default: 0.2 (top 20%)

    Returns
    -------
    List[Worker]
        Elite workers sorted by performance (best first)
    """
    quantile_size = max(1, int(len(workers) * quantile))
    sorted_workers = sorted(workers, key=lambda w: w.performance_score, reverse=True)
    return sorted_workers[:quantile_size]


def get_poor_workers(workers: List[Worker], quantile: float = 0.2) -> List[Worker]:
    """
    Get the poor (bottom-performing) workers from the population.

    Useful for analysis or debugging convergence issues.

    Parameters
    ----------
    workers : List[Worker]
        The population

    quantile : float
        Fraction of bottom performers to return
        Default: 0.2 (bottom 20%)

    Returns
    -------
    List[Worker]
        Poor workers sorted by performance (worst first)
    """
    quantile_size = max(1, int(len(workers) * quantile))
    sorted_workers = sorted(
        workers,
        key=lambda w: w.performance_score,
        reverse=False,  # Ascending: worst first
    )
    return sorted_workers[:quantile_size]


def get_best_worker(workers: List[Worker]) -> Worker:
    """
    Get the single best worker from the population.

    Parameters
    ----------
    workers : List[Worker]
        The population

    Returns
    -------
    Worker
        Worker with highest performance_score
    """
    return max(workers, key=lambda w: w.performance_score)


def get_population_statistics(workers: List[Worker]) -> dict:
    """
    Compute statistical summary of population performance.

    Useful for monitoring PBT progress and detecting convergence.

    Parameters
    ----------
    workers : List[Worker]
        The population

    Returns
    -------
    dict
        Statistics including mean, std, min, max, median scores
    """
    scores = [w.performance_score for w in workers]

    return {
        "mean": np.mean(scores),
        "std": np.std(scores),
        "min": np.min(scores),
        "max": np.max(scores),
        "median": np.median(scores),
        "range": np.max(scores) - np.min(scores),
        "num_workers": len(workers),
        "num_ready": sum(1 for w in workers if w.is_ready()),
    }


def check_convergence(
    workers: List[Worker],
    convergence_threshold: float = 0.01,
    dead_config_threshold: float = 0.0,
    min_valid_workers: int = 2,
) -> bool:
    """
    Check if population has converged (all workers similar performance).

    Convergence indicates that:
    - Population has stabilized
    - Further exploration unlikely to improve
    - May want to restart or stop optimization

    Parameters
    ----------
    workers : List[Worker]
        The population

    convergence_threshold : float
        Maximum allowed standard deviation for convergence
        Default: 0.01 (very tight convergence)

    dead_config_threshold : float
        Minimum score considered a healthy, non-dead worker.
        Workers below this threshold are excluded from convergence checks.

    min_valid_workers : int
        Minimum number of healthy workers required to evaluate convergence.
        Convergence is not meaningful with fewer than this count.

    Returns
    -------
    bool
        True if converged, False otherwise
    """
    valid_workers = [
        worker
        for worker in workers
        if worker.metrics is not None
        and worker.metrics.failure_type is None
        and worker.performance_score >= dead_config_threshold
    ]

    if len(valid_workers) < min_valid_workers:
        LOGGER.debug(
            "Skipping convergence check: %d valid workers (minimum=%d)",
            len(valid_workers),
            min_valid_workers,
        )
        return False

    stats = get_population_statistics(valid_workers)
    return stats["std"] < convergence_threshold
