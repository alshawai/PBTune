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
        Default: 20 (suitable for quick prototyping)
        
    exploit_quantile : float
        Fraction of population to exploit/explore. Bottom exploit_quantile will
        copy from top exploit_quantile.
        Default: 0.2 (20% - from DeepMind PBT paper)
        
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


# Predefined configurations for different scenarios
# RAPID: Very quick prototyping (2-3 minutes total)
RAPID_CONFIG = PBTConfig(
    population_size=4,
    num_generations=10,
    exploit_quantile=0.25,  # More aggressive (replace 1 worker)
    ready_interval=1,
    num_parallel_workers=4,
    verbose=True
)

# STANDARD: Balanced prototyping (10-15 minutes total)
STANDARD_CONFIG = PBTConfig(
    population_size=4,
    num_generations=30,
    exploit_quantile=0.2,
    ready_interval=2,
    num_parallel_workers=4,
    verbose=True
)

# THOROUGH: Comprehensive search (30-60 minutes total)
THOROUGH_CONFIG = PBTConfig(
    population_size=8,
    num_generations=50,
    exploit_quantile=0.2,
    ready_interval=3,
    num_parallel_workers=4,  # 4 for budget laptop
    verbose=True
)

# RESEARCH: For cloud deployment with more resources
RESEARCH_CONFIG = PBTConfig(
    population_size=16,
    num_generations=100,
    exploit_quantile=0.2,
    ready_interval=5,
    num_parallel_workers=16,
    verbose=True
)
