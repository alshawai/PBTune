"""
PBT Configuration Parameters
============================

This module defines the hyperparameters for Population Based Training itself.
These control the behavior of the PBT algorithm, not the database knobs.

Key PBT Hyperparameters:
-----------------------
- population_size: Number of workers (configs being evaluated in parallel)
- num_generations: How many evolution cycles to run
- exploit_quantile: What fraction of population to replace (default: 0.2 = 20%)
- ready_interval: How many evaluations before a worker is eligible for exploit/explore
- perturbation_factors: Range for perturbing numerical knobs
"""

from dataclasses import dataclass
from typing import Tuple, Optional


@dataclass
class PBTConfig:
    """
    Configuration for Population Based Training algorithm.
    
    Attributes
    ----------
    population_size : int
        Number of workers in the population. For parallel execution, this should
        match the number of available cores/database instances.
        Default: 4 (matching a budget laptop)
        
    num_generations : int
        Number of evolution generations to run. Each generation evaluates all
        workers, then performs exploit-explore.
        Default: 20 (for quick prototyping)
        
    exploit_quantile : float
        Fraction of population to exploit/explore. Bottom exploit_quantile will
        copy from top exploit_quantile.
        Default: 0.2 (20% - from DeepMind original paper)
        
    ready_interval : int
        Number of evaluations a worker must complete before being eligible for
        exploit/explore. Prevents premature convergence.
        Default: 1 (exploit/explore every generation for quick iteration)
        
    perturbation_factors : Tuple[float, float]
        (min_factor, max_factor) for perturbing numerical knobs during exploration.
        Default: (0.8, 1.2) means ±20% perturbation
        
    resample_probability : float
        Probability of resampling a knob completely instead of perturbing.
        Default: 0.0 (pure perturbation, no resampling)
        
    num_parallel_workers : int
        Number of parallel workers for evaluation. Should be <= population_size.
        Default: 4 (use all cores)
    
    evaluation_duration : float
        Duration in seconds for each worker's workload measurement.
        Longer = more accurate but slower. Shorter = faster iterations but noisier.
        Default: 30.0 seconds
    
    warmup_queries : int
        Number of warmup queries before measurement begins.
        Ensures database caches are populated for fair comparison.
        Default: 50 queries
        
    workload_seed : Optional[int]
        Seed for workload query selection randomness. When set, all workers
        will execute the exact same sequence of queries, ensuring fair comparison
        regardless of cache state.
        Default: 42 (deterministic by default for fair comparison)
        
    enable_snapshots : bool
        Whether to enable database snapshot restoration between generations.
        When True, workers' data directories are restored to a baseline state
        before each generation to prevent data drift from write operations.
        Default: False (must be explicitly enabled)
        
    snapshot_restore_interval : int
        Restore snapshots every N generations. Only used if enable_snapshots=True.
        Default: 1
        
    random_seed : Optional[int]
        Random seed for reproducibility. If None, results will be non-deterministic.
        Default: None
        
    verbose : bool
        Whether to print detailed progress information.
        Default: True
    """

    population_size: int = 4
    num_generations: int = 20
    exploit_quantile: float = 0.2
    ready_interval: int = 1
    perturbation_factors: Tuple[float, float] = (0.8, 1.2)
    resample_probability: float = 0.0
    num_parallel_workers: int = 4
    evaluation_duration: float = 30.0
    warmup_queries: int = 50
    workload_seed: Optional[int] = 42
    enable_snapshots: bool = False
    snapshot_restore_interval: int = 1
    random_seed: Optional[int] = None
    verbose: bool = True

    def __post_init__(self):
        """Validate configuration after initialization"""
        if self.population_size < 2:
            raise ValueError("population_size must be at least 2")

        if self.num_generations < 1:
            raise ValueError("num_generations must be at least 1")

        if not 0.0 < self.exploit_quantile < 0.5:
            raise ValueError("exploit_quantile must be between 0 and 0.5")

        if self.ready_interval < 1:
            raise ValueError("ready_interval must be at least 1")

        if len(self.perturbation_factors) != 2:
            raise ValueError("perturbation_factors must be a tuple of (min, max)")
        if self.perturbation_factors[0] >= self.perturbation_factors[1]:
            raise ValueError("perturbation min must be less than max")
        if self.perturbation_factors[0] <= 0:
            raise ValueError("perturbation factors must be positive")

        if not 0.0 <= self.resample_probability <= 1.0:
            raise ValueError("resample_probability must be between 0 and 1")

        if self.num_parallel_workers < 1:
            raise ValueError("num_parallel_workers must be at least 1")
        if self.num_parallel_workers > self.population_size:
            raise ValueError("num_parallel_workers cannot exceed population_size")

        if self.evaluation_duration <= 0:
            raise ValueError("evaluation_duration must be positive")

        if self.warmup_queries < 0:
            raise ValueError("warmup_queries cannot be negative")

        if self.snapshot_restore_interval < 1:
            raise ValueError("snapshot_restore_interval must be at least 1")

    @property
    def num_workers_per_quantile(self) -> int:
        """
        Calculate number of workers in exploit/explore quantiles.
        
        In standard PBT, this is symmetric: bottom N% get replaced,
        copying from top N%. This property returns N workers for both.
        
        Returns
        -------
        int
            Number of workers in bottom/top quantile
        """
        return max(1, int(self.population_size * self.exploit_quantile))

    def to_dict(self) -> dict:
        """Convert configuration to dictionary"""
        return {
            "population_size": self.population_size,
            "num_generations": self.num_generations,
            "exploit_quantile": self.exploit_quantile,
            "ready_interval": self.ready_interval,
            "perturbation_factors": self.perturbation_factors,
            "resample_probability": self.resample_probability,
            "num_parallel_workers": self.num_parallel_workers,
            "evaluation_duration": self.evaluation_duration,
            "warmup_queries": self.warmup_queries,
            "workload_seed": self.workload_seed,
            "enable_snapshots": self.enable_snapshots,
            "snapshot_restore_interval": self.snapshot_restore_interval,
            "random_seed": self.random_seed,
            "verbose": self.verbose,
        }

    def __repr__(self) -> str:
        """String representation"""
        n = self.num_workers_per_quantile
        return (
            f"PBTConfig(\n"
            f"  population_size={self.population_size},\n"
            f"  num_generations={self.num_generations},\n"
            f"  exploit_quantile={self.exploit_quantile} "
            f"(bottom {n} copy from top {n}),\n"
            f"  ready_interval={self.ready_interval},\n"
            f"  perturbation_factors={self.perturbation_factors},\n"
            f"  num_parallel_workers={self.num_parallel_workers}\n"
            f")"
        )


# Strategy: Short evaluations, few warmup, quick iterations
RAPID_CONFIG = PBTConfig(
    population_size=4,
    num_generations=10,
    exploit_quantile=0.25,
    ready_interval=1,
    num_parallel_workers=4,
    evaluation_duration=15.0,
    warmup_queries=20,
    workload_seed=42,
    enable_snapshots=False,
    verbose=True
)

# Strategy: Moderate evaluations, balanced accuracy vs speed
STANDARD_CONFIG = PBTConfig(
    population_size=4,
    num_generations=30,
    exploit_quantile=0.2,
    ready_interval=2,
    num_parallel_workers=4,
    evaluation_duration=30.0,
    warmup_queries=50,
    workload_seed=42,
    enable_snapshots=True,
    snapshot_restore_interval=5,
    verbose=True
)

# Strategy: Longer evaluations for accuracy, more exploration
THOROUGH_CONFIG = PBTConfig(
    population_size=8,
    num_generations=50,
    exploit_quantile=0.2,
    ready_interval=3,
    num_parallel_workers=4,
    evaluation_duration=45.0,
    warmup_queries=100,
    workload_seed=42,
    enable_snapshots=True,
    snapshot_restore_interval=1,
    verbose=True
)

# Strategy: Production-grade measurements, extensive exploration
RESEARCH_CONFIG = PBTConfig(
    population_size=16,
    num_generations=100,
    exploit_quantile=0.2,
    ready_interval=5,
    num_parallel_workers=16,
    evaluation_duration=60.0,
    warmup_queries=200,
    workload_seed=42,
    enable_snapshots=True,
    snapshot_restore_interval=1,
    verbose=True
)
