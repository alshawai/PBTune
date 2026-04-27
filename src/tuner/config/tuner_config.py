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

from src.tuner.evaluator.restart_policy import TuningMode
from src.benchmarks.sysbench.executor import (
    DEFAULT_SYSBENCH_WORKLOAD,
    validate_sysbench_workload,
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

    evaluation_duration : float
        Duration in seconds for each worker's workload measurement.
        Longer = more accurate but slower. Shorter = faster iterations but noisier.
        Default: 30.0 seconds

    warmup_duration : float
        Duration of warmup phase in seconds before measurement begins.
        Ensures database caches are populated for fair comparison.
        Default: 30.0 seconds

    random_seed : Optional[int]
        Seed for workload query selection randomness. When set, all workers
        will execute the exact same sequence of queries, ensuring fair comparison
        regardless of cache state.
        Default: 42 (deterministic by default for fair comparison)

    scale_factor : float
        Database size scale multiplier. For OLAP (TPC-H), this defines the GB generated
        by dbgen (e.g., 0.1 = ~100MB, 1.0 = ~1GB). For OLTP, this may govern table sizes.
        Default: 1.0

    warmup_passes : int
        Number of complete catalog warmup passes to execute silently before measurement.
        Highly relevant for analytical workloads rather than arbitrary duration constraints.
        Default: 0

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

    sysbench_tables : int
        Number of tables for Sysbench workload.
        Default: 10

    sysbench_table_size : int
        Number of rows per table for Sysbench workload.
        Default: 100000

    sysbench_workload : str
        Sysbench Lua script profile used for OLTP benchmarking.
        Allowed values: "oltp_read_only", "oltp_read_write", "oltp_write_only".
        Default: "oltp_read_write"

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

    tuning_mode : TuningMode
        Restart policy mode controlling how restart-required knobs are handled.
        Default: TuningMode.ONLINE

    adaptive_restart_interval : int
        Restart interval used only when tuning_mode == TuningMode.ADAPTIVE.
        Default: 10
    """

    population_size: int = 4
    num_generations: int = 20
    exploit_quantile: float = 0.2
    ready_interval: int = 1
    perturbation_factors: Tuple[float, float] = (0.8, 1.2)
    num_parallel_workers: int = 4
    evaluation_duration: float = 30.0
    warmup_duration: float = 30.0
    random_seed: Optional[int] = 42
    scale_factor: float = 1.0
    warmup_passes: int = 0
    enable_snapshots: bool = False
    snapshot_restore_interval: int = 1
    verbose: bool = True
    sysbench_tables: int = 10
    sysbench_table_size: int = 100000
    sysbench_workload: str = DEFAULT_SYSBENCH_WORKLOAD
    dead_config_threshold: float = 6.0
    dead_config_score: float = 1.0
    crash_score: float = 5.0
    tuning_mode: TuningMode = TuningMode.ONLINE
    adaptive_restart_interval: int = 10

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

        if self.evaluation_duration <= 0:
            raise ValueError("evaluation_duration must be positive")

        if self.warmup_duration < 0:
            raise ValueError("warmup_duration cannot be negative")

        if self.snapshot_restore_interval < 1:
            raise ValueError("snapshot_restore_interval must be at least 1")

        if self.scale_factor <= 0:
            raise ValueError("scale_factor must be positive")

        if self.warmup_passes < 0:
            raise ValueError("warmup_passes cannot be negative")

        self.sysbench_workload = validate_sysbench_workload(self.sysbench_workload)

        if not 0.0 < self.dead_config_score < self.crash_score:
            raise ValueError("dead_config_score must be > 0 and less than crash_score")

        if not self.crash_score < self.dead_config_threshold < 100.0:
            raise ValueError(
                "dead_config_threshold must be greater than crash_score and less than 100"
            )

        if self.adaptive_restart_interval < 1:
            raise ValueError("adaptive_restart_interval must be at least 1")

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
            "evaluation_duration": self.evaluation_duration,
            "warmup_duration": self.warmup_duration,
            "random_seed": self.random_seed,
            "scale_factor": self.scale_factor,
            "warmup_passes": self.warmup_passes,
            "enable_snapshots": self.enable_snapshots,
            "snapshot_restore_interval": self.snapshot_restore_interval,
            "verbose": self.verbose,
            "sysbench_tables": self.sysbench_tables,
            "sysbench_table_size": self.sysbench_table_size,
            "sysbench_workload": self.sysbench_workload,
            "dead_config_threshold": self.dead_config_threshold,
            "dead_config_score": self.dead_config_score,
            "crash_score": self.crash_score,
            "tuning_mode": self.tuning_mode.value,
            "adaptive_restart_interval": self.adaptive_restart_interval,
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
            f"  tuning_mode={self.tuning_mode.value},\n"
            f"  adaptive_restart_interval={self.adaptive_restart_interval},\n"
            f"  dead_config_threshold={self.dead_config_threshold},\n"
            f"  dead_config_score={self.dead_config_score},\n"
            f"  crash_score={self.crash_score}\n"
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
    warmup_duration=10.0,
    random_seed=42,
    scale_factor=0.01,
    sysbench_tables=2,
    sysbench_table_size=10000,
    warmup_passes=0,
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
    evaluation_duration=30.0,
    warmup_duration=30.0,
    random_seed=42,
    scale_factor=0.1,
    sysbench_tables=10,
    sysbench_table_size=100000,
    warmup_passes=1,
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
    evaluation_duration=45.0,
    warmup_duration=60.0,
    random_seed=42,
    scale_factor=1.0,
    sysbench_tables=20,
    sysbench_table_size=200000,
    warmup_passes=1,
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
    evaluation_duration=60.0,
    warmup_duration=60.0,
    random_seed=42,
    scale_factor=1.0,
    sysbench_tables=50,
    sysbench_table_size=500000,
    warmup_passes=2,
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
    evaluation_duration=300.0,
    warmup_duration=120.0,
    random_seed=42,
    scale_factor=10.0,
    sysbench_tables=100,
    sysbench_table_size=1000000,
    warmup_passes=2,
    enable_snapshots=True,
    snapshot_restore_interval=1,
    verbose=True,
)
