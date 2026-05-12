"""Shared datatypes for benchmark and workload configuration."""

from __future__ import annotations

from dataclasses import dataclass, replace
from enum import Enum
from typing import Optional

from src.benchmarks.sysbench.executor import (
    DEFAULT_SYSBENCH_WORKLOAD,
    validate_sysbench_workload,
)


class TuningMode(str, Enum):
    """Tuning mode controlling restart behavior and knob scope.

    ONLINE
        Runtime knobs only. No restarts during normal flow.
        Equivalent to OtterTune's "dynamic-only" mode.

    OFFLINE
        All knobs including postmaster. Restart every generation when
        restart-required knobs are present. Slower but maximally optimized.

    ADAPTIVE
        All knobs with batched restarts every N generations.
        WARNING: May produce phantom configs where restart-required knob
        values don't reflect what was actually running during measurement.
        Preserved for backward compatibility and research comparison.
    """

    ONLINE = "online"
    OFFLINE = "offline"
    ADAPTIVE = "adaptive"


@dataclass
class BenchmarkConfig:
    """Benchmark and workload configuration settings.

    Args:
        benchmark: Benchmark driver name (e.g., "sysbench", "tpch").
        workload_type: Workload flavor (e.g., "oltp", "olap", "mixed").
        workload_file: Optional custom workload file path for template workloads.
        evaluation_duration: Measurement duration in seconds.
        warmup_duration: Warmup duration in seconds.
        warmup_passes: Warmup passes for benchmarks that support it.
        sysbench_tables: Number of sysbench tables.
        sysbench_table_size: Rows per sysbench table.
        sysbench_workload: Sysbench workload script name.
        scale_factor: Benchmark scale factor (TPC-H or template workloads).
        tuning_mode: Restart policy mode (offline, online, adaptive).
        adaptive_restart_interval: Restart interval for adaptive mode.
    """

    benchmark: str = "sysbench"
    workload_type: str = "oltp"
    workload_file: Optional[str] = None
    evaluation_duration: float = 30.0
    warmup_duration: float = 30.0
    warmup_passes: int = 1
    sysbench_tables: int = 10
    sysbench_table_size: int = 100000
    sysbench_workload: str = DEFAULT_SYSBENCH_WORKLOAD
    scale_factor: float = 0.1
    tuning_mode: TuningMode = TuningMode.ONLINE
    adaptive_restart_interval: int = 10

    def __post_init__(self) -> None:
        if isinstance(self.tuning_mode, str):
            self.tuning_mode = TuningMode(self.tuning_mode)

        if self.evaluation_duration <= 0:
            raise ValueError("evaluation_duration must be positive")

        if self.warmup_duration < 0:
            raise ValueError("warmup_duration cannot be negative")

        if self.warmup_passes < 0:
            raise ValueError("warmup_passes cannot be negative")

        if self.scale_factor <= 0:
            raise ValueError("scale_factor must be positive")

        if self.sysbench_tables < 1:
            raise ValueError("sysbench_tables must be at least 1")

        if self.sysbench_table_size < 1:
            raise ValueError("sysbench_table_size must be at least 1")

        self.sysbench_workload = validate_sysbench_workload(self.sysbench_workload)

        if self.adaptive_restart_interval < 1:
            raise ValueError("adaptive_restart_interval must be at least 1")

    def to_dict(self) -> dict[str, object]:
        """Serialize benchmark configuration for JSON output."""
        return {
            "benchmark": self.benchmark,
            "workload_type": self.workload_type,
            "workload_file": self.workload_file,
            "evaluation_duration": self.evaluation_duration,
            "warmup_duration": self.warmup_duration,
            "warmup_passes": self.warmup_passes,
            "sysbench_tables": self.sysbench_tables,
            "sysbench_table_size": self.sysbench_table_size,
            "sysbench_workload": self.sysbench_workload,
            "scale_factor": self.scale_factor,
            "tuning_mode": self.tuning_mode.value,
            "adaptive_restart_interval": self.adaptive_restart_interval,
        }


def clone_benchmark_config(config: BenchmarkConfig) -> BenchmarkConfig:
    """Create a shallow copy of a benchmark config instance."""
    return replace(config)


RAPID_BENCHMARK_CONFIG = BenchmarkConfig(
    evaluation_duration=15.0,
    warmup_duration=10.0,
    scale_factor=0.01,
    sysbench_tables=2,
    sysbench_table_size=10000,
    warmup_passes=0,
)

STANDARD_BENCHMARK_CONFIG = BenchmarkConfig(
    evaluation_duration=30.0,
    warmup_duration=30.0,
    scale_factor=0.1,
    sysbench_tables=10,
    sysbench_table_size=100000,
    warmup_passes=1,
)

THOROUGH_BENCHMARK_CONFIG = BenchmarkConfig(
    evaluation_duration=45.0,
    warmup_duration=60.0,
    scale_factor=1.0,
    sysbench_tables=20,
    sysbench_table_size=200000,
    warmup_passes=1,
)

RESEARCH_BENCHMARK_CONFIG = BenchmarkConfig(
    evaluation_duration=60.0,
    warmup_duration=60.0,
    scale_factor=1.0,
    sysbench_tables=50,
    sysbench_table_size=500000,
    warmup_passes=2,
)

EXTREME_BENCHMARK_CONFIG = BenchmarkConfig(
    evaluation_duration=300.0,
    warmup_duration=120.0,
    scale_factor=10.0,
    sysbench_tables=100,
    sysbench_table_size=1000000,
    warmup_passes=2,
)
