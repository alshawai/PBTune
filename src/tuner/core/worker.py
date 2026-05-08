"""
PBT Worker - Individual Population Member
==========================================

This module defines the Worker class, which represents a single member of the
PBT population. Each worker maintains its own database configuration and
performance metrics.

Worker Lifecycle in PBT:
------------------------
1. **Initialization**: Random configuration sampled from knob space
2. **Evaluation**: Configuration applied to database, workload executed, metrics collected
3. **Ready Check**: After N evaluations, worker becomes eligible for exploit/explore
4. **Exploit**: If poor performer, copy configuration from elite worker
5. **Explore**: Perturb copied configuration to explore nearby region
6. **Repeat**: Return to evaluation with new configuration

Example:
--------
>>> from src.tuner.config import get_knob_space
>>> knob_space = get_knob_space('minimal')
>>>
>>> worker = Worker(
...     worker_id=0,
...     knob_space=knob_space,
...     ready_interval=1
... )
>>>
>>> # Check if ready for exploit/explore
>>> worker.is_ready()  # False initially
>>>
>>> # Simulate evaluation
>>> from src.tuner.benchmark import PerformanceMetrics
>>> metrics = PerformanceMetrics(latency_p95=50.0, throughput=100.0)
>>> worker.update_metrics(metrics, score=0.85)
>>>
>>> # Now ready!
>>> worker.is_ready()  # True
"""

from dataclasses import dataclass, field
from typing import Dict, Any, Optional, Tuple, List
import copy

from src.tuner.config.knob_space import KnobSpace
from src.utils.metrics import PerformanceMetrics
from src.config.database import DatabaseConfig


@dataclass
class Worker:
    """
    Single member of the PBT population.

    Each worker represents one database configuration being evaluated.
    Workers maintain their own configuration, performance metrics, and
    evolutionary history.

    Attributes
    ----------
    worker_id : int
        Unique identifier for this worker (0 to population_size-1)

    knob_space : KnobSpace
        The search space defining valid configurations

    knob_config : Dict[str, Any]
        Current knob configuration (PostgreSQL parameters)
        If None at initialization, a random config will be sampled

    performance_score : float
        Composite performance score (higher = better)
        This is what PBT optimizes (maximizes)
        Default: 0.0 (not yet evaluated)

    metrics : Optional[PerformanceMetrics]
        Detailed performance measurements from last evaluation
        None until first evaluation completes

    step_count : int
        Number of times this worker has been evaluated
        Used for the "ready mechanism" - workers must complete
        ready_interval evaluations before being eligible for exploit/explore

    ready_interval : int
        How many evaluations before worker can be exploited/explored
        From PBT paper: prevents premature convergence
        Typical values: 1 (aggressive), 3-5 (conservative)

    parent_id : Optional[int]
        Worker ID that this configuration was copied from
        None for initial random configs, set during exploit phase
        Used for lineage tracking and analysis

    generation_created : int
        Which generation this worker was created/last exploited
        Used for tracking evolutionary history

    config_history : list
        Optional: Track configuration changes over time
        Useful for analysis and debugging

    port : Optional[int]
        PostgreSQL instance port for this worker
        Set by instance manager during initialization
        Each worker gets its own port (base_port + worker_id)

    db_config : Optional[DatabaseConfig]
        Instance-specific database configuration
        Set by instance manager during initialization
        Contains host, port, dbname, user, password for worker's instance

    force_restart_next_eval : bool
        Whether evaluator should force a PostgreSQL restart on the next
        configuration application. Used after dead-worker rescue to ensure
        restart-required knobs are actually activated before benchmarking.

    Notes
    -----
    **Performance Score:**
    The score is computed from PerformanceMetrics using MetricConfig.
    It's a single number that PBT tries to maximize:
    - OLTP: Emphasizes low latency + high throughput
    - OLAP: Emphasizes query time + resource efficiency
    """

    worker_id: int
    knob_space: KnobSpace
    ready_interval: int = 1

    knob_config: Optional[Dict[str, Any]] = None

    performance_score: float = 0.0
    metrics: Optional[PerformanceMetrics] = None

    performance_history: List[PerformanceMetrics] = field(default_factory=list)

    step_count: int = 0
    parent_id: Optional[int] = None
    generation_created: int = 0

    config_history: list = field(default_factory=list)

    port: Optional[int] = None
    db_config: Optional[DatabaseConfig] = None
    force_restart_next_eval: bool = False

    def __post_init__(self):
        """Initialize worker with random configuration if none provided."""
        if self.knob_config is None:
            self.knob_config = self.knob_space.sample_random_config(
                seed=None  # Different seed for each worker ensures diversity
            )

    def is_ready(self) -> bool:
        """
        Check if worker is ready for exploit/explore operations.

        Workers must complete at least ready_interval evaluations before
        they can participate in exploit/explore. This is the "ready mechanism"
        from the PBT paper.

        Returns
        -------
        bool
            True if step_count >= ready_interval, False otherwise

        Notes
        -----
        From DeepMind PBT paper:
        "Workers are only eligible for exploitation/exploration after they
        have trained for a minimum number of steps. This prevents poor
        performing workers from being immediately replaced."
        """
        return self.step_count >= self.ready_interval

    def clone_from(
        self,
        other: "Worker",
        current_generation: int,
        exclude_knobs: Optional[List[str]] = None,
    ) -> None:
        """
        Copy configuration from another worker (EXPLOIT phase).

        Parameters
        ----------
        other : Worker
            The elite worker to copy from (must be in top quantile)

        current_generation : int
            The current generation number (for tracking)

        exclude_knobs : Optional[List[str]]
            Knobs to exclude from copying (e.g., restart-required knobs between restart intervals)

        Notes
        -----
        What gets copied:
        - knob_config: The actual PostgreSQL parameters (excluding those in exclude_knobs)

        What does NOT get copied:
        - performance_score: Will be recalculated after explore/evaluate
        - metrics: Will be measured fresh
        - step_count: Maintained (worker keeps its evaluation count)
        - worker_id: Never changes (identity preserved)
        - Knobs in exclude_knobs list (e.g., restart-required knobs remain unchanged)
        """
        if exclude_knobs:
            if other.knob_config:
                for knob_name, value in other.knob_config.items():
                    if knob_name not in exclude_knobs:
                        if self.knob_config:
                            self.knob_config[knob_name] = copy.deepcopy(value)
        else:
            self.knob_config = copy.deepcopy(other.knob_config)

        # Enforce memory budget validation after mixing configurations
        if self.knob_config:
            self.knob_config = self.knob_space.repair_config_dependencies(
                self.knob_config
            )

        self.parent_id = other.worker_id
        self.generation_created = current_generation

        if self.config_history is not None:
            self.config_history.append(
                {
                    "generation": current_generation,
                    "action": "exploit",
                    "parent_id": other.worker_id,
                    "config": copy.deepcopy(self.knob_config),
                }
            )

    def perturb(
        self,
        perturbation_factors: Tuple[float, float] = (0.8, 1.2),
        current_generation: Optional[int] = None,
        seed: Optional[int] = None,
        exclude_knobs: Optional[List[str]] = None,
    ) -> None:
        """
        Perturb configuration (EXPLORE phase).

        Parameters
        ----------
        perturbation_factors : Tuple[float, float]
            (min_factor, max_factor) for perturbation
            Default: (0.8, 1.2) means multiply by random value in [0.8, 1.2]

        current_generation : Optional[int]
            Current generation number for history tracking

        seed : Optional[int]
            Random seed for reproducibility

        exclude_knobs : Optional[List[str]]
            Knobs to exclude from perturbation (keep constant)

        Notes
        -----
        Without perturbation, all workers would eventually converge to the
        same configuration (the current best). Perturbation maintains
        diversity and allows exploration of nearby configurations.
        """
        self.knob_config = self.knob_space.perturb_config(
            config=self.knob_config,  # type: ignore
            perturbation_factor=perturbation_factors,
            seed=seed,
            exclude_knobs=exclude_knobs,
        )

        if self.config_history is not None and current_generation is not None:
            self.config_history.append(
                {
                    "generation": current_generation,
                    "action": "explore",
                    "perturbation_factors": perturbation_factors,
                    "config": copy.deepcopy(self.knob_config),
                }
            )

    def update_metrics(self, metrics: PerformanceMetrics, score: float) -> None:
        """
        Update worker's performance after evaluation.

        This is called after a workload evaluation completes. It updates
        the worker's performance metrics and increments the step counter.

        Parameters
        ----------
        metrics : PerformanceMetrics
            Detailed performance measurements (latency, throughput, CPU, etc.)

        score : float
            Composite performance score computed from metrics
            Higher = better performance
        """
        self.metrics = metrics
        self.performance_score = score
        self.step_count += 1
        self.performance_history.append(metrics)

    def get_config_copy(self) -> Dict[str, Any]:
        """
        Get a deep copy of the current configuration.

        Returns a copy to prevent accidental modification of the worker's
        internal state.

        Returns
        -------
        Dict[str, Any]
            Deep copy of knob_config
        """
        return copy.deepcopy(self.knob_config)  # type: ignore

    def reset_to_random(self, seed: Optional[int] = None) -> None:
        """
        Reset worker to a new random configuration.

        Useful for restarting a worker or implementing advanced evolution
        strategies (e.g., periodic random restarts to avoid local optima).

        Parameters
        ----------
        seed : Optional[int]
            Random seed for reproducibility
        """
        self.knob_config = self.knob_space.sample_random_config(seed=seed)
        self.performance_score = 0.0
        self.metrics = None
        self.parent_id = None
        self.force_restart_next_eval = False

    def __repr__(self) -> str:
        """Human-readable representation."""
        status = "ready" if self.is_ready() else "not ready"
        parent_str = f", parent={self.parent_id}" if self.parent_id is not None else ""
        port_str = f", port={self.port}" if self.port is not None else ""

        return (
            f"Worker(id={self.worker_id}, "
            f"score={self.performance_score:.4f}, "
            f"steps={self.step_count}, "
            f"status={status}"
            f"{parent_str}"
            f"{port_str})"
        )

    def __str__(self) -> str:
        """Simple string representation."""
        return f"Worker-{self.worker_id} (score={self.performance_score:.4f})"

    def to_dict(self) -> Dict[str, Any]:
        """
        Convert worker to dictionary for serialization.

        Useful for logging, checkpointing, and analysis.

        Returns
        -------
        Dict[str, Any]
            Dictionary containing worker state
        """
        return {
            "worker_id": self.worker_id,
            "knob_config": self.knob_config,
            "performance_score": self.performance_score,
            "metrics": self.metrics.to_dict() if self.metrics else None,
            "step_count": self.step_count,
            "ready_interval": self.ready_interval,
            "is_ready": self.is_ready(),
            "parent_id": self.parent_id,
            "generation_created": self.generation_created,
            "port": self.port,
            "force_restart_next_eval": self.force_restart_next_eval,
        }
