"""
Population Class for Population Based Training (PBT)
====================================================

The Population class manages a collection of Worker instances and orchestrates
the PBT algorithm's main loop:

1. Parallel evaluation of all workers
2. Exploit-explore step for poor performers
3. Convergence detection and early stopping

Key Responsibilities:
- Worker pool management (initialization, lifecycle)
- Orchestrating parallel evaluations
- Triggering exploit-explore at appropriate intervals
- Tracking population-level statistics and history
- Convergence detection and early stopping

Design:
- Uses composition (owns list of Workers)
- Delegates evolution logic to evolution.py functions
- Provides high-level API for PBT training loop
"""

from __future__ import annotations
from dataclasses import dataclass
from typing import List, Dict, Any, Optional, Callable
import logging
from concurrent.futures import ThreadPoolExecutor, as_completed

from src.tuner.core.worker import Worker
from src.tuner.core.evolution import (
    execute_exploit_explore,
    get_best_worker,
    get_population_statistics,
    check_convergence,
)
from src.tuner.config.knob_space import KnobSpace
from src.tuner.evaluator.metrics import PerformanceMetrics

logger = logging.getLogger(__name__)


@dataclass
class PopulationConfig:
    """
    Configuration for Population initialization and behavior.
    
    Parameters
    ----------
    population_size : int
        Number of workers in the population
    ready_interval : int
        How many steps before a worker is ready for exploit-explore
    exploit_quantile : float
        Quantile threshold for poor/elite selection (0.25 = bottom/top 25%)
    perturbation_factors : tuple[float, float]
        (lower, upper) bounds for perturbation (e.g., (0.8, 1.2) = ±20%)
    convergence_threshold : float
        Standard deviation threshold for convergence detection
    max_generations : int
        Maximum number of generations before stopping
    early_stopping_patience : int
        Generations to wait without improvement before early stopping
    """
    population_size: int = 8
    ready_interval: int = 3
    exploit_quantile: float = 0.25
    perturbation_factors: tuple[float, float] = (0.8, 1.2)
    convergence_threshold: float = 0.05
    max_generations: int = 100
    early_stopping_patience: int = 10


@dataclass
class GenerationResult:
    """
    Results from evaluating one generation.
    
    Tracks performance, exploit-explore activity, and population statistics.
    """
    generation: int
    best_score: float
    mean_score: float
    std_score: float
    num_exploited: int
    best_worker_id: int
    best_config: Dict[str, Any]
    converged: bool


class Population:
    """
    Manages a population of Workers for Population Based Training.
    
    The Population class is the orchestrator of the PBT algorithm. It manages
    a pool of Worker instances, coordinates parallel evaluations, and triggers
    exploit-explore steps according to the PBT paper's algorithm.
    
    Attributes
    ----------
    workers : List[Worker]
        The population of workers being trained
    knob_space : KnobSpace
        The search space for knob configurations
    config : PopulationConfig
        Configuration parameters for the population
    current_generation : int
        Current generation number (starts at 0)
    history : List[GenerationResult]
        Historical record of each generation's results
    best_overall_score : float
        Best score achieved across all generations
    generations_without_improvement : int
        Counter for early stopping
    
    Example
    -------
    >>> from src.tuner.config import get_knob_space
    >>> knob_space = get_knob_space('minimal')
    >>> config = PopulationConfig(population_size=4, max_generations=50)
    >>> 
    >>> # Define your evaluation function
    >>> def evaluate_fn(worker):
    ...     # Run workload, measure performance
    ...     metrics = PerformanceMetrics(latency_p95=100.0, throughput=500.0)
    ...     score = compute_score(metrics)
    ...     return metrics, score
    >>> 
    >>> population = Population(knob_space, config)
    >>> population.initialize()
    >>> 
    >>> # Run PBT training loop
    >>> for generation in range(config.max_generations):
    ...     result = population.train_generation(evaluate_fn)
    ...     print(f"Gen {generation}: best={result.best_score:.4f}")
    ...     
    ...     if result.converged or population.should_stop():
    ...         break
    """

    def __init__(
        self,
        knob_space: KnobSpace,
        config: Optional[PopulationConfig] = None,
    ):
        """
        Initialize a Population instance.
        
        Parameters
        ----------
        knob_space : KnobSpace
            The search space for knob configurations
        config : Optional[PopulationConfig]
            Configuration parameters. Uses defaults if None.
        """
        self.knob_space = knob_space
        self.config = config or PopulationConfig()

        self.workers: List[Worker] = []
        self.current_generation: int = 0
        self.history: List[GenerationResult] = []

        self.best_overall_score: float = 0.0
        self.generations_without_improvement: int = 0

        logger.info(
            "Created Population: size=%s, ready_interval=%s, exploit_quantile=%s",
            self.config.population_size,
            self.config.ready_interval,
            self.config.exploit_quantile
        )

    def initialize(self, initial_configs: Optional[List[Dict[str, Any]]] = None) -> None:
        """
        Initialize the worker population.
        
        Creates Worker instances with either provided configurations or
        random sampling from the knob space.
        
        Parameters
        ----------
        initial_configs : Optional[List[Dict[str, Any]]]
            Optional list of initial configurations. If provided, must match
            population_size. If None, workers are initialized with random configs.
        
        Raises
        ------
        ValueError
            If initial_configs is provided but length doesn't match population_size
        """
        if initial_configs is not None:
            if len(initial_configs) != self.config.population_size:
                raise ValueError(
                    f"initial_configs length ({len(initial_configs)}) must match "
                    f"population_size ({self.config.population_size})"
                )

        self.workers = []
        for worker_id in range(self.config.population_size):
            config = initial_configs[worker_id] if initial_configs else None
            worker = Worker(
                worker_id=worker_id,
                knob_space=self.knob_space,
                knob_config=config,
                ready_interval=self.config.ready_interval,
            )
            self.workers.append(worker)

        logger.info("Initialized %s workers", len(self.workers))

    def evaluate_generation(
        self,
        evaluate_fn: Callable[[Worker], tuple[PerformanceMetrics, float]],
        parallel: bool = True,
        max_workers: Optional[int] = None,
    ) -> None:
        """
        Evaluate all workers in the current generation.
        
        Executes the evaluation function for each worker, either in parallel
        (using ThreadPoolExecutor) or sequentially. Updates each worker's
        metrics and performance score.
        
        Parameters
        ----------
        evaluate_fn : Callable[[Worker], tuple[PerformanceMetrics, float]]
            Function that takes a Worker and returns (metrics, score).
            This function should:
            1. Apply the worker's knob configuration to PostgreSQL
            2. Run the workload
            3. Measure performance
            4. Compute a score
            5. Return (PerformanceMetrics, score)
        parallel : bool, default=True
            Whether to evaluate workers in parallel
        max_workers : Optional[int]
            Maximum number of parallel threads. Defaults to population_size.
        
        Example
        -------
        >>> def my_evaluate(worker):
        ...     apply_config(worker.knob_config)
        ...     metrics = run_workload()
        ...     score = compute_score(metrics)
        ...     return metrics, score
        >>> 
        >>> population.evaluate_generation(my_evaluate, parallel=True)
        """
        logger.info(
            "Evaluating generation %s (%s)",
            self.current_generation,
            'parallel' if parallel else 'sequential'
        )

        if not parallel:
            for worker in self.workers:
                try:
                    metrics, score = evaluate_fn(worker)
                    worker.update_metrics(metrics, score)
                    logger.debug(
                        "Worker-%s: score=%.4f, step_count=%s",
                        worker.worker_id, score, worker.step_count
                    )
                except Exception as e:
                    logger.error("Error evaluating Worker-%s: %s", worker.worker_id, e)
                    raise
        else:
            max_workers = max_workers or self.config.population_size
            with ThreadPoolExecutor(max_workers=max_workers) as executor:
                # Submit all evaluation tasks
                future_to_worker = {
                    executor.submit(evaluate_fn, worker): worker
                    for worker in self.workers
                }

                # Collect results as they complete
                for future in as_completed(future_to_worker):
                    worker = future_to_worker[future]
                    try:
                        metrics, score = future.result()
                        worker.update_metrics(metrics, score)
                        logger.debug(
                            "Worker-%s: score=%.4f, step_count=%s",
                            worker.worker_id, score, worker.step_count
                        )
                    except Exception as e:
                        logger.error("Error evaluating Worker-%s: %s", worker.worker_id, e)
                        raise

        logger.info("Generation %s evaluation complete", self.current_generation)

    def exploit_and_explore(self, require_ready: bool = True, verbose: bool = False) -> int:
        """
        Perform exploit-explore step on poor-performing workers.
        
        Delegates to evolution.execute_exploit_explore() to perform:
        1. Identify poor and elite workers (truncation selection)
        2. Clone elite configs to poor workers (exploit)
        3. Perturb poor workers' configs (explore)
        
        Parameters
        ----------
        require_ready : bool, default=True
            Only consider workers that have completed ready_interval steps
        verbose : bool, default=False
            Enable verbose logging of exploit-explore details
        
        Returns
        -------
        int
            Number of workers that were exploited and explored
        """
        num_exploited = execute_exploit_explore(
            workers=self.workers,
            exploit_quantile=self.config.exploit_quantile,
            perturbation_factors=self.config.perturbation_factors,
            current_generation=self.current_generation,
            require_ready=require_ready,
            verbose=verbose,
        )

        logger.info(
            "Exploit-explore complete: %s workers modified (generation %s)",
            num_exploited, self.current_generation
        )

        return num_exploited

    def record_generation(self) -> GenerationResult:
        """
        Record statistics and results for the current generation.
        
        Computes population statistics, identifies best worker, checks
        convergence, and updates history.
        
        Returns
        -------
        GenerationResult
            Summary of this generation's performance
        """
        stats = get_population_statistics(self.workers)
        best_worker = get_best_worker(self.workers)
        converged = check_convergence(
            self.workers,
            self.config.convergence_threshold
        )

        result = GenerationResult(
            generation=self.current_generation,
            best_score=stats['max'],
            mean_score=stats['mean'],
            std_score=stats['std'],
            num_exploited=0,  # Will be updated by train_generation
            best_worker_id=best_worker.worker_id,
            best_config=best_worker.knob_config.copy(),  # type: ignore
            converged=converged,
        )
        self.history.append(result)

        if result.best_score > self.best_overall_score:
            self.best_overall_score = result.best_score
            self.generations_without_improvement = 0
            logger.info("New best score: %.4f", self.best_overall_score)
        else:
            self.generations_without_improvement += 1

        return result

    def train_generation(
        self,
        evaluate_fn: Callable[[Worker], tuple[PerformanceMetrics, float]],
        parallel: bool = True,
        require_ready: bool = True,
        verbose: bool = False,
    ) -> GenerationResult:
        """
        Execute one complete PBT generation.
        
        This is the main training loop method. It:
        1. Evaluates all workers
        2. Records generation statistics
        3. Performs exploit-explore if appropriate
        4. Increments generation counter
        
        Parameters
        ----------
        evaluate_fn : Callable[[Worker], tuple[PerformanceMetrics, float]]
            Function to evaluate each worker
        parallel : bool, default=True
            Whether to evaluate workers in parallel
        require_ready : bool, default=True
            Only exploit-explore ready workers
        verbose : bool, default=False
            Enable verbose logging
        
        Returns
        -------
        GenerationResult
            Summary of this generation's results
        
        Example
        -------
        >>> def evaluate(worker):
        ...     # Your evaluation logic
        ...     return metrics, score
        >>> 
        >>> for gen in range(max_generations):
        ...     result = population.train_generation(evaluate)
        ...     print(f"Best: {result.best_score:.4f}")
        ...     if population.should_stop():
        ...         break
        """
        logger.info("=" * 30)
        logger.info("Starting generation %s", self.current_generation)

        self.evaluate_generation(evaluate_fn, parallel=parallel)

        result = self.record_generation()

        num_exploited = self.exploit_and_explore(
            require_ready=require_ready,
            verbose=verbose
        )
        result.num_exploited = num_exploited
        self.current_generation += 1

        logger.info(
            "Generation %s complete: best=%.4f, mean=%.4f, std=%.4f, exploited=%s, converged=%s",
            result.generation, result.best_score, result.mean_score,
            result.std_score, num_exploited, result.converged
        )

        return result

    def should_stop(self) -> bool:
        """
        Check if training should stop early.
        
        Stops if:
        1. Maximum generations reached
        2. Early stopping patience exceeded (no improvement)
        3. Population has converged
        
        Returns
        -------
        bool
            True if training should stop
        """
        if self.current_generation >= self.config.max_generations:
            logger.info("Stopping: max_generations reached")
            return True

        if self.generations_without_improvement >= self.config.early_stopping_patience:
            logger.info(
                "Stopping: no improvement for %s generations",
                self.config.early_stopping_patience
            )
            return True

        if self.history and self.history[-1].converged:
            logger.info("Stopping: population converged")
            return True

        return False

    def get_best_configuration(self) -> tuple[Dict[str, Any], float]:
        """
        Get the best configuration found so far.
        
        Returns
        -------
        tuple[Dict[str, Any], float]
            (best_config, best_score) tuple
        """
        best_worker = get_best_worker(self.workers)
        return best_worker.knob_config.copy(), best_worker.performance_score  # type: ignore

    def get_population_summary(self) -> Dict[str, Any]:
        """
        Get summary statistics for the current population.
        
        Returns
        -------
        Dict[str, Any]
            Dictionary with population statistics, generation info, and best config
        """
        stats = get_population_statistics(self.workers)
        best_worker = get_best_worker(self.workers)

        return {
            'current_generation': self.current_generation,
            'population_size': len(self.workers),
            'best_score': stats['max'],
            'mean_score': stats['mean'],
            'std_score': stats['std'],
            'min_score': stats['min'],
            'best_worker_id': best_worker.worker_id,
            'best_config': best_worker.knob_config.copy(),  # type: ignore
            'converged': check_convergence(self.workers, self.config.convergence_threshold),
            'best_overall_score': self.best_overall_score,
            'generations_without_improvement': self.generations_without_improvement,
        }

    def __repr__(self) -> str:
        """String representation of the Population."""
        return (
            f"Population(size={len(self.workers)}, "
            f"generation={self.current_generation}, "
            f"best_score={self.best_overall_score:.4f})"
        )
