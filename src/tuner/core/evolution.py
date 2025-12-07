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
from src.tuner.utils.logger_config import WorkerLoggerAdapter

logger = logging.getLogger(__name__)


def truncation_selection(
    workers: List[Worker],
    exploit_quantile: float = 0.2,
    require_ready: bool = True
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
    if require_ready:
        eligible_workers = [w for w in workers if w.is_ready()]
    else:
        eligible_workers = workers

    if len(eligible_workers) < 2:
        return []

    n_workers = len(eligible_workers)
    quantile_size = max(1, int(n_workers * exploit_quantile))

    sorted_workers = sorted(
        eligible_workers,
        key=lambda w: w.performance_score,
        reverse=True
    )

    elite_workers = sorted_workers[:quantile_size]
    poor_workers = sorted_workers[-quantile_size:]

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
    verbose: bool = True,
    exclude_knobs: Optional[List[str]] = None
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
    
    verbose : bool
        Enable verbose logging
        Default: True
    
    exclude_knobs : Optional[List[str]]
        Knobs to exclude from perturbation (keep constant)
        Used for two-stage PBT where restart knobs are frozen between restart intervals
        
    verbose : bool
        Print exploitation details
        Default: True
        
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
        require_ready=require_ready
    )

    if not pairs:
        if verbose:
            logger.info("No workers exploited (not enough ready workers)")
        return 0

    for poor_idx, elite_idx in pairs:
        poor_worker = workers[poor_idx]
        elite_worker = workers[elite_idx]

        if verbose:
            poor_logger = WorkerLoggerAdapter(logger, {'worker_id': poor_worker.worker_id})
            elite_logger = WorkerLoggerAdapter(logger, {'worker_id': elite_worker.worker_id})

            logger.info(
                "Worker-%d (score=%.4f) ← exploits Worker-%d (score=%.4f)",
                poor_worker.worker_id,
                poor_worker.performance_score,
                elite_worker.worker_id,
                elite_worker.performance_score
            )

        poor_worker.clone_from(
            elite_worker,
            current_generation,
            exclude_knobs=exclude_knobs
        )

        poor_worker.perturb(
            perturbation_factors=perturbation_factors,
            current_generation=current_generation,
            exclude_knobs=exclude_knobs
        )

        if verbose:
            logger.info("  → Copied config and applied perturbation")

    return len(pairs)


def get_elite_workers(
    workers: List[Worker],
    quantile: float = 0.2
) -> List[Worker]:
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
    sorted_workers = sorted(
        workers,
        key=lambda w: w.performance_score,
        reverse=True
    )
    return sorted_workers[:quantile_size]


def get_poor_workers(
    workers: List[Worker],
    quantile: float = 0.2
) -> List[Worker]:
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
        reverse=False  # Ascending: worst first
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
        'mean': np.mean(scores),
        'std': np.std(scores),
        'min': np.min(scores),
        'max': np.max(scores),
        'median': np.median(scores),
        'range': np.max(scores) - np.min(scores),
        'num_workers': len(workers),
        'num_ready': sum(1 for w in workers if w.is_ready()),
    }


def check_convergence(
    workers: List[Worker],
    convergence_threshold: float = 0.01
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
        
    Returns
    -------
    bool
        True if converged, False otherwise
    """
    stats = get_population_statistics(workers)
    return stats['std'] < convergence_threshold
