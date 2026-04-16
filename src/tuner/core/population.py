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
from typing import List, Dict, Any, Optional, Callable, Tuple
from concurrent.futures import as_completed
from concurrent.futures.thread import ThreadPoolExecutor

from src.config.database import DatabaseConfig
from src.tuner.core.worker import Worker
from src.tuner.core.evolution import (
    execute_exploit_explore,
    get_best_worker,
    get_population_statistics,
    check_convergence,
)
from src.tuner.config.knob_space import KnobSpace
from src.utils.environments import DatabaseEnvironment
from src.utils.metrics import PerformanceMetrics
from src.utils.logger import get_logger

logger = get_logger(__name__)


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
    dead_config_threshold : float
        Score threshold below which workers are classified as dead configs
        for end-of-generation rescue handling.
    resample_min_change_ratio : float
        Minimum fraction of knobs that should differ between old and fallback
        resampled configs in all-dead rescue.
    """
    population_size: int = 8
    ready_interval: int = 3
    exploit_quantile: float = 0.25
    perturbation_factors: tuple[float, float] = (0.8, 1.2)
    convergence_threshold: float = 0.5
    max_generations: int = 100
    early_stopping_patience: int = 10
    dead_config_threshold: float = 6.0
    resample_min_change_ratio: float = 0.6


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
        evaluator: Optional[Any] = None,
    ):
        """
        Initialize a Population instance.
        
        Parameters
        ----------
        knob_space : KnobSpace
            The search space for knob configurations
        config : Optional[PopulationConfig]
            Configuration parameters. Uses defaults if None.
        evaluator : Optional[Evaluator]
            Evaluator instance (for accessing metric config). If None, adaptive
            normalization will use global config objects (less clean but works).
        """
        self.knob_space = knob_space
        self.config = config or PopulationConfig()
        self.evaluator = evaluator

        self.workers: List[Worker] = []
        self.current_generation: int = 0
        self.history: List[GenerationResult] = []

        self.best_overall_score: float = 0.0
        self.best_overall_metrics: Optional[PerformanceMetrics] = None
        self.best_overall_config: Dict[str, Any] = {}
        self.generations_without_improvement: int = 0

        # Snapshot support (configured via setup_snapshots() method)
        self.enable_snapshots: bool = False
        self.restore_interval: int = 5
        self.env: Optional[DatabaseEnvironment] = None

        self._ranges_updated: bool = False

        logger.debug(
            "-> Created Population: size=%s, ready_interval=%s, exploit_quantile=%s",
            self.config.population_size,
            self.config.ready_interval,
            self.config.exploit_quantile
        )

    def initialize(
        self,
        initial_configs: Optional[List[Dict[str, Any]]] = None,
        random_seed: Optional[int] = None
    ) -> None:
        """
        Initialize the worker population.
        
        Uses Latin Hypercube Sampling (LHS) for diverse initial configurations.
        This ensures better coverage of the search space and reduces early convergence.
        
        Parameters
        ----------
        initial_configs : Optional[List[Dict[str, Any]]]
            Optional list of initial configurations. Can be shorter than population_size
            (for partial seeding), in which case the rest are filled via LHS.
        seed : Optional[int], default=None
            Random seed for sampling.
        
        Raises
        ------
        ValueError
            If initial_configs is provided but length is greater than population_size
        
        Note
        ----
        After calling this, call setup_worker_instances() to assign instance configs.
        """
        if initial_configs is not None:
            if len(initial_configs) > self.config.population_size:
                raise ValueError(
                    f"initial_configs length ({len(initial_configs)}) cannot exceed "
                    f"population_size ({self.config.population_size})"
                )
            num_lhs_needed = self.config.population_size - len(initial_configs)
            if num_lhs_needed > 0:
                logger.debug(
                    "Partial seeding: %d configs provided, filling %d configs with LHS",
                    len(initial_configs), num_lhs_needed
                )
                lhs_configs = self.knob_space.sample_diverse_configs(
                    num_samples=num_lhs_needed,
                    seed=random_seed
                )
                initial_configs = initial_configs + lhs_configs
        else:
            initial_configs = self.knob_space.sample_diverse_configs(
                num_samples=self.config.population_size,
                seed=random_seed
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

    def setup_worker_instances(
        self,
        instances: List[Any],
        dbname: str = 'postgres',
        user: str = 'postgres',
        password: str = ''
    ) -> None:
        """
        Assign PostgreSQL instance configurations to workers.
        
        Parameters
        ----------
        instances : List[InstanceConfig]
            List of InstanceConfig objects from PostgresInstanceManager
        dbname : str
            Database name to connect to
        user : str
            PostgreSQL username
        password : str
            PostgreSQL password# Snapshot manager deleted

        
        Example
        -------
        >>> population.initialize()
        >>> instance_manager = PostgresInstanceManager(...)
        >>> instances = instance_manager.setup_instances(num_workers=8)
        >>> population.setup_worker_instances(instances, dbname='mydb', user='myuser')
        """

        if len(instances) != len(self.workers):
            raise ValueError(
                f"Number of instances ({len(instances)}) must match "
                f"number of workers ({len(self.workers)})"
            )

        for worker in self.workers:
            instance = instances[worker.worker_id]
            worker.port = instance.port

            worker.db_config = DatabaseConfig(
                host='127.0.0.1',
                port=instance.port,
                dbname=dbname,
                user=user,
                password=password
            )

            worker_logger = get_logger(__name__, worker_id=worker.worker_id)
            worker_logger.info(
                "Assigned to instance port %d",
                worker.port
            )

    def setup_snapshots(
        self,
        env: DatabaseEnvironment,
        pbt_config: Any,
    ) -> None:
        """
        Register snapshot configuration for database restoration during training.
        """
        self.env = env
        self.enable_snapshots = getattr(pbt_config, 'enable_snapshots', False)
        self.restore_interval = getattr(pbt_config, 'snapshot_restore_interval', 5)

        if not self.enable_snapshots:
            logger.debug("Snapshots disabled in config")
            return

        logger.info(
            "Snapshot restoration enabled: interval=%d",
            self.restore_interval
        )

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
            "Evaluating generation %s - %s",
            self.current_generation,
            'parallel' if parallel else 'sequential'
        )

        if not parallel:
            for worker in self.workers:
                try:
                    metrics, score = evaluate_fn(worker)
                    worker.update_metrics(metrics, score)
                    worker_logger = get_logger(__name__, worker_id=worker.worker_id)
                    worker_logger.debug(
                        "score=%.4f, step_count=%s",
                        score, worker.step_count
                    )
                except Exception as e:
                    worker_logger = get_logger(__name__, worker_id=worker.worker_id)
                    worker_logger.error("Error evaluating: %s", e)
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
                        worker_logger = get_logger(__name__, worker_id=worker.worker_id)
                        worker_logger.debug(
                            "score=%.4f, step_count=%s",
                            score, worker.step_count
                        )
                    except Exception as e:
                        worker_logger = get_logger(__name__, worker_id=worker.worker_id)
                        worker_logger.error("Error evaluating: %s", e)
                        raise

        logger.info("Generation %s evaluation complete", self.current_generation)

    @staticmethod
    def _config_change_ratio(old_config: Dict[str, Any], new_config: Dict[str, Any]) -> float:
        """Return fraction of knob entries whose values changed."""
        all_keys = set(old_config.keys()) | set(new_config.keys())
        if not all_keys:
            return 0.0

        changed = sum(1 for key in all_keys if old_config.get(key) != new_config.get(key))
        return changed / len(all_keys)

    def _choose_diverse_resample_config(
        self,
        previous_config: Dict[str, Any],
        candidates: List[Dict[str, Any]],
        min_change_ratio: float,
    ) -> tuple[Dict[str, Any], float]:
        """Pick and remove the most-diverse candidate from a shared candidate pool."""
        if not candidates:
            fallback = self.knob_space.sample_random_config()
            return fallback, self._config_change_ratio(previous_config, fallback)

        best_index = 0
        best_ratio = -1.0
        for index, candidate in enumerate(candidates):
            ratio = self._config_change_ratio(previous_config, candidate)
            if ratio > best_ratio:
                best_ratio = ratio
                best_index = index

        selected = candidates.pop(best_index)
        if best_ratio >= min_change_ratio:
            return selected, best_ratio

        # Fallback one-shot perturbation if all pool candidates are too similar.
        perturbed = self.knob_space.perturb_config(
            previous_config,
            perturbation_factor=(0.5, 1.5),
            seed=((self.current_generation + 1) * 1000) + len(previous_config),
        )
        perturbed_ratio = self._config_change_ratio(previous_config, perturbed)
        if perturbed_ratio > best_ratio:
            return perturbed, perturbed_ratio

        return selected, best_ratio

    def rescue_dead_workers(
        self,
        evaluate_fn: Callable[[Worker], tuple[PerformanceMetrics, float]],
    ) -> int:
        """Immediately rescue dead workers by exploiting alive configs.

        Rescue flow:
        1. Detect dead workers from failure-tagged metrics and low score.
        2. If alive donors exist, clone an alive worker's config.
        3. Otherwise, resample a fresh random config for next-generation escape.
        4. Recover the worker's PostgreSQL instance.
        """
        dead_workers = [
            worker
            for worker in self.workers
            if worker.metrics is not None
            and worker.metrics.failure_type is not None
            and worker.performance_score < self.config.dead_config_threshold
        ]
        if not dead_workers:
            return 0

        alive_workers = [
            worker
            for worker in self.workers
            if worker.metrics is not None
            and worker.metrics.failure_type is None
            and worker.performance_score >= self.config.dead_config_threshold
        ]
        if not alive_workers:
            logger.warning(
                "Dead-config rescue fallback: no alive workers available; resampling %d dead workers for next generation",
                len(dead_workers),
            )

            seed_base = (self.current_generation + 1) * 1000
            candidate_pool_size = max(len(dead_workers) * 4, len(dead_workers))
            lhs_candidates = self.knob_space.sample_diverse_configs(
                num_samples=candidate_pool_size,
                seed=seed_base,
            )
            min_change_ratio = max(0.0, min(1.0, self.config.resample_min_change_ratio))

            resampled = 0
            for dead_worker in dead_workers:
                dead_logger = get_logger(__name__, worker_id=dead_worker.worker_id)
                previous_config = dead_worker.get_config_copy()
                selected_config, change_ratio = self._choose_diverse_resample_config(
                    previous_config,
                    lhs_candidates,
                    min_change_ratio,
                )
                dead_worker.knob_config = selected_config
                dead_worker.performance_score = 0.0
                dead_worker.metrics = None

                dead_worker.parent_id = None
                dead_worker.generation_created = self.current_generation + 1
                config_changed = dead_worker.knob_config != previous_config

                if self.env is not None:
                    if not self.env.recover_instance(dead_worker.worker_id):
                        dead_logger.error(
                            "[DEAD_CONFIG] Fallback resample could not recover worker instance"
                        )
                    else:
                        dead_logger.info(
                            "[DEAD_CONFIG] Recovered instance after all-dead fallback resample"
                        )

                dead_logger.info(
                    "[DEAD_CONFIG] Resample outcome: changed_config=%s changed_ratio=%.3f",
                    config_changed,
                    change_ratio,
                )

                dead_logger.warning(
                    "[DEAD_CONFIG] No alive donor available; resampled a fresh configuration for next generation"
                )
                resampled += 1

            return resampled

        alive_workers = sorted(alive_workers, key=lambda worker: worker.performance_score, reverse=True)

        rescued = 0
        for index, dead_worker in enumerate(dead_workers):
            donor = alive_workers[index % len(alive_workers)]
            dead_logger = get_logger(__name__, worker_id=dead_worker.worker_id)

            dead_logger.warning(
                "[DEAD_CONFIG] Triggering immediate rescue: exploit Worker-%d (score=%.4f)",
                donor.worker_id,
                donor.performance_score,
            )

            dead_worker.clone_from(donor, self.current_generation)

            if self.env is not None:
                if not self.env.recover_instance(dead_worker.worker_id):
                    dead_logger.error(
                        "[DEAD_CONFIG] Instance recovery failed during immediate rescue"
                    )
                    continue

                rescued += 1
                dead_logger.info(
                    "[DEAD_CONFIG] Instance recovered. Will be re-evaluated in next generation."
                )
            else:
                rescued += 1  # If no env, just count as rescued

        return rescued

    def update_metric_ranges_if_needed(self) -> None:
        """
        Update metric normalization ranges after initial exploration phase.
        
        This implements OtterTune's adaptive approach: after collecting enough
        data from initial evaluations, compute realistic min/max ranges based
        on used hardware's actual performance.
        
        Strategy:
        - Collect metrics from multiple generations to capture performance variability
        - Wait for at least 2 full generations (2 * population_size samples)
        - Use percentile-based ranges to be robust to outliers
        
        Called automatically during train_generation() after evaluations.
        """
        if self._ranges_updated:
            return

        all_metrics: List[PerformanceMetrics] = []
        excluded_failure_metrics = 0
        for worker in self.workers:
            for metric in worker.performance_history:
                if metric.failure_type is None:
                    all_metrics.append(metric)
                else:
                    excluded_failure_metrics += 1

        # Need samples from multiple generations to capture variability
        # Minimum: 2 generations worth of data (2 * population_size)
        min_samples_needed = max(8, 2 * len(self.workers))

        if len(all_metrics) < min_samples_needed:
            logger.debug(
                "Waiting for sufficient samples for adaptive normalization: "
                "%d/%d (generation %d)",
                len(all_metrics), min_samples_needed, self.current_generation
            )
            return

        if excluded_failure_metrics > 0:
            logger.debug(
                "Excluded %d failure-tagged metrics from adaptive normalization updates",
                excluded_failure_metrics
            )

        logger.info(
            "Updating normalization ranges from %d observations across %d workers",
            len(all_metrics), len(self.workers)
        )

        if self.evaluator is not None and hasattr(self.evaluator, 'config'):
            metric_config = self.evaluator.config.metric_config

            try:
                already_initialized = getattr(metric_config, '_ranges_initialized', False)
                if not already_initialized:
                    metric_config.update_ranges(all_metrics)
                    logger.info(
                        "✓ Adaptive normalization activated for %s "
                        "workload (based on %d observations)",
                        metric_config.workload_type.value,
                        len(all_metrics)
                    )

                    # Rescore current generation and best overall score with new adaptive ranges
                    logger.info("♻️  Rescoring current generation with adaptive ranges...")
                    for worker in self.workers:
                        if worker.metrics is not None:
                            worker.performance_score = metric_config.compute_score(worker.metrics)

                    logger.info(
                        "♻️  Resetting historical best score to align with new adaptive bounds"
                    )
                    self.best_overall_score = 0.0
                    self.best_overall_metrics = None
                    self.best_overall_config = {}
                    self.generations_without_improvement = 0
                else:
                    logger.debug("Metric ranges already initialized, skipping update")
            except AttributeError as e:
                logger.warning("Failed to update metric ranges: %s", e)

        self._ranges_updated = True
        logger.info("Metric normalization ranges updated successfully")

    def exploit_and_explore(
        self,
        require_ready: bool = True,
        exclude_knobs: Optional[List[str]] = None
    ) -> int:
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
        exclude_knobs : Optional[List[str]]
            Knobs to exclude from perturbation (keep constant)
        
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
            dead_config_threshold=self.config.dead_config_threshold,
            exclude_knobs=exclude_knobs
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
        converged = False
        if self._ranges_updated:
            converged = check_convergence(
                self.workers,
                self.config.convergence_threshold
            )
        else:
            logger.debug(
                "Convergence check deferred: adaptive normalization not yet active"
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
            self.best_overall_metrics = best_worker.metrics
            self.best_overall_config = best_worker.knob_config.copy()  # type: ignore
            self.generations_without_improvement = 0
            logger.info("New best score: %.4f", self.best_overall_score)
        else:
            self.generations_without_improvement += 1

        return result

    def train_generation(
        self,
        evaluate_fn: Callable[[Worker], Tuple[PerformanceMetrics, float]],
        parallel: bool = True,
        require_ready: bool = True,
        max_workers: Optional[int] = None,
    ) -> GenerationResult:
        """
        Execute one complete PBT generation.
        
        This is the main training loop method. It:
        1. Evaluates all workers with their current configurations
        2. Performs exploit-explore to evolve poor performers
        3. Records generation statistics
        4. Increments generation counter
        
        Parameters
        ----------
        evaluate_fn : Callable[[Worker], tuple[PerformanceMetrics, float]]
            Function to evaluate each worker
        parallel : bool, default=True
            Whether to evaluate workers in parallel
        require_ready : bool, default=True
            Only exploit-explore ready workers
        max_workers : Optional[int], default=None
            Maximum parallel workers for evaluation. When less than
            population_size, workers evaluate in batches. If None,
            defaults to population_size.
        
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
        # Restore database snapshots if enabled and it's time to restore
        if (
            self.enable_snapshots and
            self.current_generation > 0 and
            self.current_generation % self.restore_interval == 0
        ):
            logger.info(
                "Restoring database snapshots for generation %d (interval: %d)",
                self.current_generation,
                self.restore_interval
            )

            try:
                if self.env:
                    for worker in self.workers:
                        self.env.restore_snapshot(worker.worker_id)
                    logger.info("✓ Database snapshots restored successfully")
                else:
                    logger.warning("Snapshot restoration requested but env not available")
            except Exception as e:
                logger.error("Failed to restore databases from snapshots: %s", e)
                logger.debug("Exception details:", exc_info=True)
                raise

        self.evaluate_generation(evaluate_fn, parallel=parallel, max_workers=max_workers)

        rescued = self.rescue_dead_workers(evaluate_fn)
        if rescued > 0:
            logger.info("Immediate dead-config rescue recovered %d workers", rescued)

        self.update_metric_ranges_if_needed()

        # Check for score saturation and expand ranges if needed
        self._check_and_handle_saturation(evaluate_fn)

        num_exploited = self.exploit_and_explore(
            require_ready=require_ready,
            exclude_knobs=None  # No restrictions in multi-instance mode
        )

        result = self.record_generation()
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

    def _check_and_handle_saturation(
        self,
        evaluate_fn: Callable[[Worker], Tuple[PerformanceMetrics, float]]
    ) -> None:
        """
        Check if any workers' raw metrics exceed normalization bounds and expand if needed.

        Clamping (np.clip) in compute_score() destroys rank ordering between workers
        whose raw metrics fall outside [min, max]. This method detects when ANY worker's
        raw latency or throughput exceeds the current bounds, then expands the ranges
        and rescores all workers to restore correct relative ordering.

        When bounds exceedance is detected:
        1. Identify which worker(s) have raw metrics outside current bounds
        2. Record their PRE-expansion scores
        3. Expand ranges to accommodate the exceeded values (with headroom)
        4. Rescore all workers with new ranges
        5. Update historical best score on new ranges

        Parameters
        ----------
        evaluate_fn : Callable[[Worker], tuple[PerformanceMetrics, float]]
            Evaluation function (only used to get metric config for rescoring)
        """
        # Only check after ranges are initialized
        if not self._ranges_updated or self.evaluator is None:
            return

        metric_config = self.evaluator.config.metric_config

        # Check each worker for out-of-bounds metrics (would be clamped by np.clip)
        # Trigger on raw metric bound exceedance, not normalized score threshold,
        # because clamping destroys rank ordering between workers long before
        # the normalized component hits the saturation ceiling.
        clamped_workers = []
        pre_clamp_scores = {}

        for worker in self.workers:
            if worker.metrics is not None:
                pre_clamp_scores[worker.worker_id] = worker.performance_score
                latency = getattr(
                    worker.metrics,
                    f"latency_{metric_config.latency_metric}"
                )
                throughput = worker.metrics.throughput

                exceeds_bounds = (
                    (latency > 0 and latency < metric_config.latency_min)
                    or (throughput > 0 and throughput > metric_config.throughput_max)
                )
                if exceeds_bounds:
                    clamped_workers.append(worker)

        # If no workers exceed bounds, nothing to do
        if not clamped_workers:
            return

        # Find the best clamped worker (highest PRE-expansion score)
        best_clamped_worker = max(clamped_workers, key=lambda w: w.performance_score)
        best_clamped_pre_score = best_clamped_worker.performance_score

        logger.info(
            "⚠️  Metric bounds exceeded in %d/%d workers (generation %d)",
            len(clamped_workers), len(self.workers), self.current_generation
        )
        logger.info(
            "    Best clamped: Worker-%d with PRE-expansion score %.4f",
            best_clamped_worker.worker_id, best_clamped_pre_score
        )

        # Expand ranges based on current generation's metrics
        dead_threshold = (
            self.config.dead_config_threshold
            if self.config else 6.0
        )
        current_metrics = [
            w.metrics for w in self.workers
            if w.metrics is not None and w.performance_score > dead_threshold
        ]
        if not current_metrics:
            return

        ranges_expanded = metric_config.expand_ranges_for_metrics(
            current_metrics,
            expansion_factor=0.25  # 25% headroom for continued improvement
        )

        if not ranges_expanded:
            logger.debug("Ranges not expanded, no rescoring needed")
            return

        # Rescore all workers in current generation with new ranges
        logger.info("♻️  Rescoring current generation with expanded ranges...")
        for worker in self.workers:
            if worker.metrics is not None:
                old_score = worker.performance_score
                new_score = metric_config.compute_score(worker.metrics)
                worker.performance_score = new_score

                if abs(new_score - old_score) > 0.5:  # Log significant changes
                    logger.debug(
                        "  Worker-%d: %.4f → %.4f (Δ%.4f)",
                        worker.worker_id, old_score, new_score, new_score - old_score
                    )

        old_unscaled_best = self.best_overall_score
        if self.best_overall_metrics is not None:
            self.best_overall_score = metric_config.compute_score(
                self.best_overall_metrics
            )
            logger.info(
                "♻️  Rescored historical best score on expanded bounds: %.4f → %.4f",
                old_unscaled_best, self.best_overall_score
            )

        # record_generation() will natively handle comparing the current workers 
        # (now rescored) against the historical best (also rescored).

    def get_best_configuration(self) -> tuple[Dict[str, Any], float]:
        """
        Get the best configuration found so far.
        
        Returns
        -------
        tuple[Dict[str, Any], float]
            (best_config, best_score) tuple
        """
        if not self.best_overall_config:
            best_worker = get_best_worker(self.workers)
            return best_worker.knob_config.copy(), best_worker.performance_score  # type: ignore
        return self.best_overall_config.copy(), self.best_overall_score

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
