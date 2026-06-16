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
from src.tuner.core.barriers import GenerationBarrier

from src.tuner.benchmark.orchestrator import WorkloadOrchestrator
from src.tuner.config.knob_space import KnobSpace
from src.utils.environments import DatabaseEnvironment
from src.utils.logger.helpers import log_section_header, log_worker_metrics_table
from src.utils.metrics import PerformanceMetrics
from src.utils.scoring.contracts import ScoreBreakdown
from src.utils.logger import get_logger, get_color_context
from src.utils.timing import TimingRecorder

LOGGER = get_logger("Population")
COLORS = get_color_context()


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
    disable_early_stopping : bool
        Disable the no-improvement early stop gate when True
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
    disable_early_stopping: bool = False
    dead_config_threshold: float = 6.0
    resample_min_change_ratio: float = 0.6
    resample_probability: float = 0.0


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

    def to_dict(self) -> Dict[str, Any]:
        """Convert GenerationResult to a dictionary for logging or serialization."""
        return {
            "generation": self.generation,
            "best_score": self.best_score,
            "mean_score": self.mean_score,
            "std_score": self.std_score,
            "num_exploited": self.num_exploited,
            "best_worker_id": self.best_worker_id,
            "best_config": self.best_config,
            "converged": self.converged,
        }


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
        orchestrator: Optional[WorkloadOrchestrator] = None,
    ):
        """
        Initialize a Population instance.

        Parameters
        ----------
        knob_space : KnobSpace
            The search space for knob configurations
        config : Optional[PopulationConfig]
            Configuration parameters. Uses defaults if None.
        orchestrator : Optional[WorkloadOrchestrator]
            WorkloadOrchestrator instance (for accessing metric config). If None, adaptive
            normalization will use global config objects (less clean but works).
        """
        self.knob_space = knob_space
        self.config = config or PopulationConfig()
        self.orchestrator = orchestrator

        self.workers: List[Worker] = []
        self.current_generation: int = 0
        self.history: List[GenerationResult] = []

        self.best_overall_score: float = 0.0
        self.best_overall_metrics: Optional[PerformanceMetrics] = None
        self.best_overall_config: Dict[str, Any] = {}
        self.best_overall_score_breakdown: Optional[ScoreBreakdown] = None
        self.generations_without_improvement: int = 0

        # Snapshot support (configured via setup_snapshots() method)
        self.enable_snapshots: bool = False
        self.restore_interval: int = 5
        self.env: Optional[DatabaseEnvironment] = None

        # Per-generation flag: True when snapshot restore is due this gen.
        # Set by train_generation(), consumed by evaluate_fn via the orchestrator.
        self._restore_due_this_gen: bool = False

        self._ranges_calibrated: bool = False
        # One-shot flag: True for the single train_generation cycle in which
        # the normalizer transitions from uncalibrated to calibrated. Set
        # inside update_metric_ranges_if_needed(); consumed (and reset) by
        # _finalize_scores() to force a rescore so historical worker scores
        # — computed against the pre-calibration normalizer — are realigned
        # to the freshly-fit anchors.
        self._just_calibrated: bool = False
        self._features_refined: bool = False

        LOGGER.info(
            "➤ Created Population: size=%s, ready_interval=%s, exploit_quantile=%s",
            self.config.population_size,
            self.config.ready_interval,
            self.config.exploit_quantile,
        )

    def initialize(
        self,
        initial_configs: Optional[List[Dict[str, Any]]] = None,
        random_seed: Optional[int] = None,
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
                LOGGER.debug(
                    " Partial seeding: %d configs provided, filling %d configs with LHS",
                    len(initial_configs),
                    num_lhs_needed,
                )
                lhs_configs = self.knob_space.sample_diverse_configs(
                    num_samples=num_lhs_needed, seed=random_seed
                )
                initial_configs = initial_configs + lhs_configs
        else:
            LOGGER.debug(" Sampling %d configs with LHS", self.config.population_size)
            initial_configs = self.knob_space.sample_diverse_configs(
                num_samples=self.config.population_size, seed=random_seed
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
        dbname: str = "postgres",
        user: str = "postgres",
        password: str = "",
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
                host="127.0.0.1",
                port=instance.port,
                dbname=dbname,
                user=user,
                password=password,
            )
            worker_logger = get_logger("Population", worker_id=worker.worker_id)
            worker_logger.debug("➤ Assigned to instance port %d", worker.port)

        LOGGER.debug("➤ Instance configurations assigned to all workers")

    def setup_snapshots(
        self,
        env: DatabaseEnvironment,
        pbt_config: Any,
    ) -> None:
        """
        Register snapshot configuration for database restoration during training.
        """
        self.env = env
        self.enable_snapshots = getattr(pbt_config, "enable_snapshots", False)
        self.restore_interval = getattr(pbt_config, "snapshot_restore_interval", 5)

        if not self.enable_snapshots:
            LOGGER.debug(
                "%sSnapshots are disabled in config%s", COLORS.italic, COLORS.reset
            )
            return

        LOGGER.info(
            "Snapshot restoration enabled: interval=%s%d%s",
            COLORS.bold,
            self.restore_interval,
            COLORS.reset,
        )

    def evaluate_generation(
        self,
        evaluate_fn: Callable[..., tuple[PerformanceMetrics, float]],
        parallel: bool = True,
        max_workers: Optional[int] = None,
        synchronize_workers: bool = False,
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
        synchronize_workers : bool, default=False
            When True and parallel, insert threading.Barrier sync points
            between every sub-step so workers advance in lockstep.

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
        self._features_refined = False
        max_workers = max_workers or self.config.population_size
        # Barrier usage flag — actual barrier objects are created per-batch
        # in parallel mode to match the number of concurrent threads.
        use_barriers = parallel and synchronize_workers

        if not parallel or max_workers == 1:
            LOGGER.info(
                "%sStarting sequential evaluation of %d workers...%s",
                COLORS.bold,
                len(self.workers),
                COLORS.reset,
            )
            disabled_barriers = GenerationBarrier(num_workers=1, enabled=False)

            for worker in self.workers:
                try:
                    metrics, score = evaluate_fn(worker, barriers=disabled_barriers)
                    worker.update_metrics(metrics, score)

                except Exception as e:
                    worker.logger.error("Error evaluating: %s", e)
                    raise
        else:
            LOGGER.info(
                "%sStarting parallel evaluation of %d workers with max_workers=%d...%s",
                COLORS.bold,
                len(self.workers),
                max_workers,
                COLORS.reset,
            )
            num_workers = len(self.workers)

            # Hybrid mode: when max_workers < population, we must evaluate
            # in batches.  Barriers synchronize threads *within* a batch
            # (since only batch-mates run concurrently and contend for
            # shared hardware resources).
            if max_workers >= num_workers:
                batches = [self.workers]  # All workers fit in a single batch
            else:
                batches = [  # Chunk workers into batches of max_workers.
                    self.workers[i : i + max_workers]
                    for i in range(0, num_workers, max_workers)
                ]
                LOGGER.info(
                    " %sHybrid mode: %d workers evaluated in %d batches of up to %d workers%s",
                    COLORS.italic,
                    num_workers,
                    len(batches),
                    max_workers,
                    COLORS.reset,
                )

            for batch_idx, batch in enumerate(batches):
                # Create batch-local barriers sized to this batch.
                batch_barriers = GenerationBarrier(
                    num_workers=len(batch),
                    enabled=use_barriers,
                )
                if use_barriers and len(batches) > 1:
                    LOGGER.debug(
                        " %sBatch %d/%d: %d workers, barriers enabled%s",
                        COLORS.italic,
                        batch_idx + 1,
                        len(batches),
                        len(batch),
                        COLORS.reset,
                    )

                with ThreadPoolExecutor(max_workers=len(batch)) as executor:
                    # Submit all evaluation tasks in this batch
                    future_to_worker = {
                        executor.submit(
                            evaluate_fn, worker, barriers=batch_barriers
                        ): worker
                        for worker in batch
                    }

                    # Collect results as they complete.
                    # No timeout — barriers wait indefinitely for peers.
                    # Dead workers call drain_remaining() from their
                    # exception handlers; truly stuck workers are handled
                    # by abort() if needed.
                    for future in as_completed(future_to_worker):
                        worker = future_to_worker[future]
                        try:
                            metrics, score = future.result()
                            worker.update_metrics(metrics, score)
                            worker_logger = get_logger(
                                "PopulationWorker",
                                worker_id=worker.worker_id,
                            )
                            worker_logger.debug(
                                "score=%.4f, step_count=%s",
                                score,
                                worker.step_count,
                            )
                        except Exception as e:
                            worker_logger = get_logger(
                                "PopulationWorker",
                                worker_id=worker.worker_id,
                            )
                            worker_logger.error("Error evaluating: %s", e)
                            # Abort barriers so remaining threads unblock
                            batch_barriers.abort()
                            raise

        LOGGER.info(
            "%s➤ Evaluation of %d workers is completed.%s",
            COLORS.bold,
            self.config.population_size,
            COLORS.reset,
        )

        healthy_workers = [
            worker
            for worker in self.workers
            if worker.metrics is not None and worker.metrics.failure_type is None
        ]
        if not healthy_workers:
            LOGGER.warning(
                "Skipping workload feature refinement: %sno healthy workers%s",
                COLORS.italic,
                COLORS.reset,
            )
            self._features_refined = False
        else:
            LOGGER.info("Refining workload features at generation level...")
            self._features_refined = bool(
                self.orchestrator.refine_workload_features_from_generation(  # type: ignore
                    self.workers
                )
            )

    @staticmethod
    def _config_change_ratio(
        old_config: Dict[str, Any], new_config: Dict[str, Any]
    ) -> float:
        """Return fraction of knob entries whose values changed."""
        all_keys = set(old_config.keys()) | set(new_config.keys())
        if not all_keys:
            return 0.0

        changed = sum(
            1 for key in all_keys if old_config.get(key) != new_config.get(key)
        )
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
            LOGGER.warning(
                "%sDead-config rescue fallback: no alive workers available; "
                "resampling %d dead workers for next generation%s",
                COLORS.warning,
                len(dead_workers),
                COLORS.reset,
            )

            seed_base = (self.current_generation + 1) * 1000
            candidate_pool_size = max(len(dead_workers) * 4, len(dead_workers))
            lhs_candidates = self.knob_space.sample_diverse_configs(
                num_samples=candidate_pool_size,
                seed=seed_base,
                quiet=True,
            )
            min_change_ratio = max(0.0, min(1.0, self.config.resample_min_change_ratio))

            resampled = 0
            for dead_worker in dead_workers:
                dead_logger = get_logger(
                    "WorkerRescuer", worker_id=dead_worker.worker_id
                )
                previous_config = dead_worker.get_config_copy()
                selected_config, change_ratio = self._choose_diverse_resample_config(
                    previous_config,
                    lhs_candidates,
                    min_change_ratio,
                )
                dead_worker.knob_config = selected_config
                dead_worker.performance_score = 0.0
                dead_worker.metrics = None
                dead_worker.score_breakdown = None
                dead_worker.force_restart_next_eval = True

                dead_worker.parent_id = None
                dead_worker.generation_created = self.current_generation + 1
                config_changed = dead_worker.knob_config != previous_config

                if self.env is not None:
                    recovered = False
                    try:
                        recovered = self.env.recover_instance(dead_worker.worker_id)
                    except (ConnectionError, RuntimeError, OSError) as exc:
                        dead_logger.error(
                            " ➤ Fallback resample recovery raised an unexpected error: %s",
                            exc,
                            exc_info=True,
                        )

                    if not recovered:
                        dead_logger.error(
                            " ➤ Fallback resample could not recover worker instance"
                        )
                    else:
                        dead_logger.debug(
                            " ➤ Recovered instance after all-dead fallback resample"
                        )

                dead_logger.debug(
                    " ➤ Resample outcome: changed_config=%s changed_ratio=%.3f",
                    config_changed,
                    change_ratio,
                )

                dead_logger.warning(
                    " ➤ No alive donor available; resampled a fresh configuration for next generation"
                )
                resampled += 1

            return resampled

        # If alive workers exist, we defer their rescue to execute_exploit_explore.
        # execute_exploit_explore will correctly pair them with an *elite* worker,
        # perturb the configuration, and physically clone the database.
        LOGGER.debug(
            " ➤ Deferring dead worker rescue to execute_exploit_explore for elite config and perturbation."
        )
        return 0

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
        if self._ranges_calibrated:
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
        # Minimum: 5 generations worth of data (5 * population_size), or 20, whichever is larger
        min_samples_needed = max(20, 5 * len(self.workers))

        if len(all_metrics) < min_samples_needed:
            LOGGER.debug(
                "%s Waiting for sufficient samples for adaptive normalization: "
                "%d/%d (generation %d)%s",
                COLORS.italic,
                len(all_metrics),
                min_samples_needed,
                self.current_generation,
                COLORS.reset,
            )
            return

        if excluded_failure_metrics > 0:
            LOGGER.debug(
                " %sExcluded %d failure-tagged metrics from adaptive normalization updates%s",
                COLORS.italic,
                excluded_failure_metrics,
                COLORS.reset,
            )

        LOGGER.info(
            " %sUpdating normalization ranges from %d observations across %d workers%s",
            COLORS.bold,
            len(all_metrics),
            len(self.workers),
            COLORS.reset,
        )

        if self.orchestrator is not None and hasattr(self.orchestrator, "config"):
            metric_config = self.orchestrator.config.metric_config

            try:
                already_initialized = getattr(
                    metric_config, "_ranges_initialized", False
                )
                if already_initialized:
                    LOGGER.debug("➤ Metric ranges already initialized, skipping update")
                metric_config.update_ranges(all_metrics)
                LOGGER.info(
                    "➤ Adaptive normalization activated for %s "
                    "workload (based on %d observations)",
                    metric_config.workload_type.value,
                    len(all_metrics),
                )

            except AttributeError as e:
                LOGGER.warning("➤ Failed to update metric ranges: %s", e)

        self._ranges_calibrated = True
        self._just_calibrated = True
        LOGGER.info("➤ Metric normalization ranges updated successfully")

    def _build_worker_metric_payload_from_metrics(
        self,
        metrics: PerformanceMetrics,
        score: float | None,
    ) -> dict[str, Any]:
        """Build a reusable metric payload for the worker metrics table."""
        return {
            "score": score,
            "latency_p95": f"{metrics.latency_p95:.2f}{metrics.latency_unit}",
            "latency_p99": f"{metrics.latency_p99:.2f}{metrics.latency_unit}",
            "latency_variance": (
                f"{metrics.latency_variance:.2f}{metrics.latency_unit}"
            ),
            "tail_amplification": f"{metrics.tail_amplification:.2f}",
            "throughput": f"{metrics.throughput:.1f} {metrics.throughput_unit}",
            "throughput_variance": (
                f"{metrics.throughput_variance:.2f} {metrics.throughput_unit}"
            ),
            "error_rate": f"{metrics.error_rate * 100.0:.2f}%",
            "memory_pressure": f"{metrics.memory_pressure * 100.0:.2f}%",
            "buffer_miss_rate": f"{metrics.buffer_miss_rate * 100.0:.2f}%",
            "scan_efficiency": f"{metrics.scan_efficiency * 100.0:.1f}%",
            "total_queries": metrics.total_queries,
            "total_time": f"{metrics.total_time:.2f}s",
            "io_read_mb": f"{metrics.io_read_mb:.2f} MB",
            "io_write_mb": f"{metrics.io_write_mb:.2f} MB",
            "rows_examined": metrics.rows_examined,
            "rows_returned": metrics.rows_returned,
            "cache_hit_ratio": f"{metrics.cache_hit_ratio * 100.0:.1f}%",
            "memory_utilization": f"{metrics.memory_utilization * 100.0:.2f}%",
        }

    def _build_worker_metric_payload(self, worker: Worker) -> dict[str, Any] | None:
        """Build a reusable metric payload for the worker metrics table."""
        metrics = worker.metrics
        if metrics is None:
            return None

        return self._build_worker_metric_payload_from_metrics(
            metrics,
            worker.performance_score,
        )

    def _prepare_generation_worker_metrics_table_payloads(
        self,
    ) -> tuple[list[dict[str, Any]], list[str], dict[str, Any] | None]:
        """Prepare current-generation rows and the finalized historical best row."""
        worker_metric_payloads: list[dict[str, Any]] = []
        worker_labels: list[str] = []

        for worker in self.workers:
            payload = self._build_worker_metric_payload(worker)
            if payload is None:
                continue

            worker_metric_payloads.append(payload)
            worker_labels.append(f"Worker-{worker.worker_id}")

        best_worker_payload: dict[str, Any] | None = None
        if self.best_overall_metrics is not None:
            best_worker_payload = self._build_worker_metric_payload_from_metrics(
                self.best_overall_metrics,
                self.best_overall_score,
            )

        return worker_metric_payloads, worker_labels, best_worker_payload

    def _log_generation_worker_metrics_table(self) -> None:
        """Render the finalized generation worker table for both sequential and parallel runs."""
        worker_metric_payloads, worker_labels, best_worker_payload = (
            self._prepare_generation_worker_metrics_table_payloads()
        )

        if not worker_metric_payloads:
            return

        log_worker_metrics_table(
            LOGGER,
            worker_metric_payloads,
            worker_labels=worker_labels,
            best_worker_metric=best_worker_payload,
            best_worker_label="Best Worker",
            title=(
                f"\n{COLORS.bold}🔷 Generation {self.current_generation} Worker Metrics 🔷"
                f"{COLORS.reset}"
            ),
        )

    def record_generation(self) -> GenerationResult:
        """
        Record statistics and results for the current generation.

        Computes population statistics, identifies best worker, and checks
        convergence.

        Returns
        -------
        GenerationResult
            Summary of this generation's performance
        """
        stats = get_population_statistics(self.workers)
        best_worker = get_best_worker(self.workers)
        converged = False

        if self._ranges_calibrated:
            converged = check_convergence(
                self.workers,
                self.config.convergence_threshold,
                dead_config_threshold=self.config.dead_config_threshold,
                min_valid_workers=2,
            )
        else:
            LOGGER.debug(
                "%s Convergence check deferred: adaptive normalization not yet active%s",
                COLORS.italic,
                COLORS.reset,
            )

        result = GenerationResult(
            generation=self.current_generation,
            best_score=stats["max"],
            mean_score=stats["mean"],
            std_score=stats["std"],
            num_exploited=0,  # Will be updated by train_generation
            best_worker_id=best_worker.worker_id,
            best_config=best_worker.knob_config.copy(),  # type: ignore
            converged=converged,
        )
        self.history.append(result)

        return result

    @staticmethod
    def _invoke_optional_worker_callback(callback: Any, worker_id: int) -> bool:
        """Invoke a worker callback only when present and callable."""
        if not callable(callback):
            return False
        return bool(callback(worker_id))

    def train_generation(
        self,
        evaluate_fn: Callable[..., Tuple[PerformanceMetrics, float]],
        parallel: bool = True,
        require_ready: bool = True,
        max_workers: Optional[int] = None,
        synchronize_workers: bool = False,
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
        synchronize_workers : bool, default=False
            When True and parallel, insert lockstep barriers between every
            sub-step of worker evaluation for experimental fairness.

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
        # Per-generation timing recorder (fresh each generation).
        self.generation_timing = TimingRecorder()

        # Determine if snapshot restore is due this generation.
        # The actual restore happens inside evaluate_worker (after apply_only
        # writes the new knobs to auto.conf), so the restore preserves the
        # freshly-written configuration and serves as the restart.
        self._restore_due_this_gen = bool(
            self.enable_snapshots
            and self.current_generation > 0
            and self.current_generation % self.restore_interval == 0
        )

        if self._restore_due_this_gen:
            log_section_header(
                LOGGER,
                "%sSnapshot restore due for generation %d (will restore per-worker during eval)%s",
                COLORS.bold,
                self.current_generation,
                COLORS.reset,
                top_separator=False,
            )

        self.evaluate_generation(
            evaluate_fn,
            parallel=parallel,
            max_workers=max_workers,
            synchronize_workers=synchronize_workers,
        )

        rescued = self.rescue_dead_workers()
        if rescued > 0:
            LOGGER.info("➤ Immediate dead-config rescue recovered %d workers", rescued)

        LOGGER.info("Checking for adaptive normalization updates...")
        self.update_metric_ranges_if_needed()

        LOGGER.info("Determining final scores...")
        self._finalize_scores()

        self._log_generation_worker_metrics_table()

        LOGGER.info("Recording generation...")
        result = self.record_generation()

        LOGGER.info("Performing evolution step...")
        with self.generation_timing.span("evolve"):
            pairs_exploited = execute_exploit_explore(
                workers=self.workers,
                exploit_quantile=self.config.exploit_quantile,
                perturbation_factors=self.config.perturbation_factors,
                current_generation=self.current_generation,
                require_ready=require_ready,
                dead_config_threshold=self.config.dead_config_threshold,
                exclude_knobs=None,
                resample_probability=self.config.resample_probability,
            )

            if self.env is not None and pairs_exploited:
                clones_by_source: dict[int, list[int]] = {}
                for poor_idx, elite_idx in pairs_exploited:
                    source_id = self.workers[elite_idx].worker_id
                    target_id = self.workers[poor_idx].worker_id
                    if source_id not in clones_by_source:
                        clones_by_source[source_id] = []
                    clones_by_source[source_id].append(target_id)

                for source_id, target_ids in clones_by_source.items():
                    LOGGER.info(
                        " ➤ Physically cloning database from elite worker %d to %d poor workers...",
                        source_id,
                        len(target_ids),
                    )
                    self.env.clone_instances(source_id, target_ids)

        result.num_exploited = len(pairs_exploited)
        self.current_generation += 1

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
            LOGGER.info(
                "%s➤ Stopping: %smax_generations reached.%s",
                COLORS.bold,
                COLORS.violet,
                COLORS.reset,
            )
            return True

        if (
            not self.config.disable_early_stopping
            and self.generations_without_improvement
            >= self.config.early_stopping_patience
        ):
            LOGGER.info(
                "%s➤ Stopping: %sno improvement for %s generations.%s",
                COLORS.bold,
                COLORS.violet,
                self.config.early_stopping_patience,
                COLORS.reset,
            )
            return True

        if self.history and self.history[-1].converged:
            LOGGER.info(
                "%s➤ Stopping: %spopulation converged.%s",
                COLORS.bold,
                COLORS.violet,
                COLORS.reset,
            )
            return True

        LOGGER.info(
            "%sStopping criteria not met, continuing...%s", COLORS.italic, COLORS.reset
        )
        return False

    def _determine_overall_best(self, best_current: Worker) -> None:
        if best_current.performance_score >= self.best_overall_score:
            self.best_overall_score = best_current.performance_score
            self.best_overall_metrics = best_current.metrics
            self.best_overall_score_breakdown = best_current.score_breakdown
            self.best_overall_config = best_current.get_config_copy()
            self.generations_without_improvement = 0
        else:
            self.generations_without_improvement += 1

    def _finalize_scores(self) -> None:
        """
        Finalize scoring for the current generation.

        1. Expand ranges if saturation or drift is detected
        2. Rescore all workers with final weights and ranges
        3. Rescore historical best
        4. Update historical best config if a current worker is genuinely better
        """
        scored_workers = [w for w in self.workers if w.metrics is not None]
        if not scored_workers:
            LOGGER.warning(
                " %sAll workers have malformed metrics.%s", COLORS.warning, COLORS.reset
            )
            return

        best_current = max(scored_workers, key=lambda w: w.performance_score)

        LOGGER.debug(
            " Checking for saturation/drift to determine if range expansion is needed..."
        )
        if not self._ranges_calibrated:
            self._determine_overall_best(best_current)

            LOGGER.debug(
                "➤ Skipping range expansion check: %sNormalizer is not yet calibrated.%s",
                COLORS.italic,
                COLORS.reset,
            )
            return

        LOGGER.debug(" Detecting metric ranges saturation/drift...")
        metric_config = self.orchestrator.config.metric_config  # type: ignore
        dead_threshold = self.config.dead_config_threshold if self.config else 6.0
        current_metrics = [
            w.metrics
            for w in self.workers
            if w.metrics is not None and w.performance_score > dead_threshold
        ]

        if not current_metrics:
            LOGGER.warning(
                " ➤ No viable metrics for saturation check: %sAll workers are dead%s",
                COLORS.italic,
                COLORS.reset,
            )

        ranges_expanded = metric_config.expand_ranges_for_metrics(
            current_metrics,
            expansion_factor=0.25,  # 25% headroom for continued improvement
        )
        if ranges_expanded:
            LOGGER.debug(" ➤ Saturation/drift detected: expanded normalizer ranges")

        # First-time calibration is a stronger event than incremental range
        # expansion: every worker's recorded score was computed against the
        # pre-calibration normalizer, so we must force a rescore to realign
        # them with the freshly-fit anchors. Treat it as a ranges-changed
        # signal for the weights-update path as well.
        just_calibrated = self._just_calibrated
        self._just_calibrated = False  # consume the one-shot flag

        weights_updated = self.orchestrator.maybe_update_feature_weights(  # type: ignore
            self.current_generation,
            force=ranges_expanded or just_calibrated,
            log_every=5,
        )

        if not (ranges_expanded or weights_updated or just_calibrated):
            self._determine_overall_best(best_current)

            LOGGER.debug(
                " ➤ No rescore needed: ranges_expanded=%s, features_refined=%s",
                ranges_expanded,
                self._features_refined,
            )
            return

        LOGGER.debug(
            " %sRescoring required%s - ranges_expanded=%s, features_refined=%s, "
            "just_calibrated=%s",
            COLORS.bold,
            COLORS.reset,
            ranges_expanded,
            weights_updated,
            just_calibrated,
        )
        significant_changes = 0
        engine = self.orchestrator.scorer  # type: ignore
        for worker in self.workers:
            if worker.metrics is not None:
                old_score = worker.performance_score
                breakdown = engine.compute_breakdown(
                    worker.metrics, worker_logger=worker.logger
                )
                worker.score_breakdown = breakdown
                new_score = breakdown.final_score
                worker.performance_score = new_score

                if abs(new_score - old_score) > 0.5:  # Log significant changes
                    significant_changes += 1

        if self.best_overall_metrics is not None:
            LOGGER.info(" Rescoring historical best configuration...")
            self.best_overall_score_breakdown = engine.compute_breakdown(
                self.best_overall_metrics
            )
            self.best_overall_score = self.best_overall_score_breakdown.final_score
        LOGGER.debug(
            " ➤ Rescoring complete: %s%d%s workers with significant score changes",
            COLORS.bold,
            significant_changes,
            COLORS.reset,
        )

        best_current = max(scored_workers, key=lambda w: w.performance_score)
        self._determine_overall_best(best_current)

        LOGGER.info(
            "➤ Finalized scores after %s",
            "feature-weight refresh" if weights_updated else "expanding metric ranges",
        )
        return

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
            "current_generation": self.current_generation,
            "population_size": len(self.workers),
            "best_score": stats["max"],
            "mean_score": stats["mean"],
            "std_score": stats["std"],
            "min_score": stats["min"],
            "best_worker_id": best_worker.worker_id,
            "best_config": best_worker.knob_config.copy(),  # type: ignore
            "converged": check_convergence(
                self.workers,
                self.config.convergence_threshold,
                dead_config_threshold=self.config.dead_config_threshold,
                min_valid_workers=2,
            ),
            "best_overall_score": self.best_overall_score,
            "generations_without_improvement": self.generations_without_improvement,
        }

    def __repr__(self) -> str:
        """String representation of the Population."""
        return (
            f"Population(size={len(self.workers)}, "
            f"generation={self.current_generation}, "
            f"best_score={self.best_overall_score:.4f})"
        )
