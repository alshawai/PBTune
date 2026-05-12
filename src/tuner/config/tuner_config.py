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

from dataclasses import dataclass, field
from typing import Tuple, Optional

from src.utils.types import (
    BenchmarkConfig,
    RAPID_BENCHMARK_CONFIG,
    STANDARD_BENCHMARK_CONFIG,
    THOROUGH_BENCHMARK_CONFIG,
    RESEARCH_BENCHMARK_CONFIG,
    EXTREME_BENCHMARK_CONFIG,
    clone_benchmark_config,
)


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

    num_parallel_workers : int
        Number of parallel workers for evaluation. Should be <= population_size.
        Default: 4 (use all cores)

    benchmark_config : BenchmarkConfig
        Benchmark/workload configuration shared across tuners.
        Default: STANDARD_BENCHMARK_CONFIG (via clone)

    enable_snapshots : bool
        Whether to enable database snapshot restoration between generations.
        When True, workers' data directories are restored to a baseline state
        before each generation to prevent data drift from write operations.
        Default: False (must be explicitly enabled)

    snapshot_restore_interval : int
        Restore snapshots every N generations. Only used if enable_snapshots=True.
        Default: 1

    verbose : bool
        Whether to print detailed progress information.
        Default: True

    dead_config_threshold : float
        Score threshold below which a worker is considered dead and marked
        for end-of-generation rescue logic.
        Default: 6.0

    dead_config_score : float
        Score assigned to unrecoverable dead configurations (e.g., connection failures).
        Default: 1.0

    crash_score : float
        Score assigned to crash/timeout style failures that are severe but potentially
        less catastrophic than complete connection death.
        Default: 5.0

    scoring_policy : str
        Scoring policy to use (e.g., 'fixed_v1', 'feature_driven_v2').
        Default: 'feature_driven_v2'

    scoring_policy_version : Optional[str]
        Version of the scoring policy for reproducibility.
        Default: None

    metric_reference_version : Optional[str]
        Version of the metric reference set for calibration.
        Default: None

    scoring_calibration_evals : int
        Number of initial evaluations used to calibrate the normalizer before
        switching from uniform priors to data-driven percentile anchors.
        Default: 5
    """

    population_size: int = 4
    num_generations: int = 20
    exploit_quantile: float = 0.2
    ready_interval: int = 1
    perturbation_factors: Tuple[float, float] = (0.8, 1.2)
    num_parallel_workers: int = 4
    benchmark_config: BenchmarkConfig = field(
        default_factory=lambda: clone_benchmark_config(STANDARD_BENCHMARK_CONFIG)
    )
    enable_snapshots: bool = False
    snapshot_restore_interval: int = 1
    verbose: bool = True
    dead_config_threshold: float = 6.0
    dead_config_score: float = 1.0
    crash_score: float = 5.0
    scoring_policy: str = "feature_driven_v2"
    scoring_policy_version: Optional[str] = None
    metric_reference_version: Optional[str] = None
    scoring_calibration_evals: int = 5

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

        if self.num_parallel_workers < 1:
            raise ValueError("num_parallel_workers must be at least 1")
        if self.num_parallel_workers > self.population_size:
            raise ValueError("num_parallel_workers cannot exceed population_size")

        if self.snapshot_restore_interval < 1:
            raise ValueError("snapshot_restore_interval must be at least 1")

        if not 0.0 < self.dead_config_score < self.crash_score:
            raise ValueError("dead_config_score must be > 0 and less than crash_score")

        if not self.crash_score < self.dead_config_threshold < 100.0:
            raise ValueError(
                "dead_config_threshold must be greater than crash_score and less than 100"
            )

        if isinstance(self.benchmark_config, dict):
            self.benchmark_config = BenchmarkConfig(**self.benchmark_config)
        elif not isinstance(self.benchmark_config, BenchmarkConfig):
            raise TypeError("benchmark_config must be a BenchmarkConfig instance")

        if self.scoring_calibration_evals < 1:
            raise ValueError("scoring_calibration_evals must be at least 1")

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
            "num_parallel_workers": self.num_parallel_workers,
            "benchmark_config": self.benchmark_config.to_dict(),
            "enable_snapshots": self.enable_snapshots,
            "snapshot_restore_interval": self.snapshot_restore_interval,
            "verbose": self.verbose,
            "dead_config_threshold": self.dead_config_threshold,
            "dead_config_score": self.dead_config_score,
            "crash_score": self.crash_score,
            "scoring_policy": self.scoring_policy,
            "scoring_policy_version": self.scoring_policy_version,
            "metric_reference_version": self.metric_reference_version,
            "scoring_calibration_evals": self.scoring_calibration_evals,
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
            f"  num_parallel_workers={self.num_parallel_workers},\n"
            f"  benchmark_config={self.benchmark_config},\n"
            f"  dead_config_threshold={self.dead_config_threshold},\n"
            f"  dead_config_score={self.dead_config_score},\n"
            f"  crash_score={self.crash_score},\n"
            f"  scoring_policy={self.scoring_policy},\n"
            f"  scoring_policy_version={self.scoring_policy_version},\n"
            f"  metric_reference_version={self.metric_reference_version},\n"
            f"  scoring_calibration_evals={self.scoring_calibration_evals}\n"
            f")"
        )


# Strategy: Short evaluations, few warmup, quick iterations
RAPID_CONFIG = PBTConfig(
    population_size=4,
    num_generations=10,
    exploit_quantile=0.25,
    ready_interval=1,
    num_parallel_workers=4,
    benchmark_config=clone_benchmark_config(RAPID_BENCHMARK_CONFIG),
    enable_snapshots=False,
    verbose=True,
)

# Strategy: Moderate evaluations, balanced accuracy vs speed
STANDARD_CONFIG = PBTConfig(
    population_size=4,
    num_generations=30,
    exploit_quantile=0.2,
    ready_interval=2,
    num_parallel_workers=4,
    benchmark_config=clone_benchmark_config(STANDARD_BENCHMARK_CONFIG),
    enable_snapshots=True,
    snapshot_restore_interval=5,
    verbose=True,
)

# Strategy: Longer evaluations for accuracy, more exploration
THOROUGH_CONFIG = PBTConfig(
    population_size=8,
    num_generations=50,
    exploit_quantile=0.2,
    ready_interval=3,
    num_parallel_workers=4,
    benchmark_config=clone_benchmark_config(THOROUGH_BENCHMARK_CONFIG),
    enable_snapshots=True,
    snapshot_restore_interval=1,
    verbose=True,
)

# Strategy: Production-grade measurements, extensive exploration
RESEARCH_CONFIG = PBTConfig(
    population_size=16,
    num_generations=100,
    exploit_quantile=0.2,
    ready_interval=5,
    num_parallel_workers=16,
    benchmark_config=clone_benchmark_config(RESEARCH_BENCHMARK_CONFIG),
    enable_snapshots=True,
    snapshot_restore_interval=1,
    verbose=True,
)

# Strategy: Heavy-duty benchmark requirements (>10GB analytical scaling)
EXTREME_CONFIG = PBTConfig(
    population_size=16,
    num_generations=200,
    exploit_quantile=0.2,
    ready_interval=10,
    num_parallel_workers=16,
    benchmark_config=clone_benchmark_config(EXTREME_BENCHMARK_CONFIG),
    enable_snapshots=True,
    snapshot_restore_interval=1,
    verbose=True,
)
