"""Shared datatypes for benchmark and workload configuration."""

from __future__ import annotations

from dataclasses import dataclass, field, replace
from enum import Enum
from typing import TYPE_CHECKING, Any, Optional

from src.benchmarks.sysbench.executor import (
    DEFAULT_SYSBENCH_WORKLOAD,
    validate_sysbench_workload,
)

if TYPE_CHECKING:
    from src.utils.environments.base import DatabaseEnvironment


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
    tuning_mode: TuningMode = TuningMode.OFFLINE
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
    evaluation_duration=10.0,
    warmup_duration=5.0,
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
    evaluation_duration=120.0,
    warmup_duration=60.0,
    scale_factor=1.0,
    sysbench_tables=150,
    sysbench_table_size=100000,
    warmup_passes=1,
)

RESEARCH_BENCHMARK_CONFIG = BenchmarkConfig(
    evaluation_duration=120.0,
    warmup_duration=90.0,
    scale_factor=10.0,
    sysbench_tables=150,
    sysbench_table_size=100000,
    warmup_passes=1,
)

EXTREME_BENCHMARK_CONFIG = BenchmarkConfig(
    evaluation_duration=180.0,
    warmup_duration=120.0,
    scale_factor=10.0,
    sysbench_tables=200,
    sysbench_table_size=100000,
    warmup_passes=1,
)


@dataclass(frozen=True)
class WorkerResourceAllocation:
    """Per-worker resource allocation for SessionEnvironment provenance.

    Attributes
    ----------
    worker_id
        Zero-based worker index.
    cpu_cores
        Logical CPU cores allocated to this worker.
    cpuset_cpus
        Comma-separated cpuset list (e.g. ``"0,1"``) when enforced via
        cgroups (Docker); ``None`` for bare-metal.
    ram_bytes
        Per-worker RAM budget in bytes.
    docker_memory_limit_bytes
        ``mem_limit`` enforced via Docker, or ``None`` for bare-metal.
    disk_read_bps
        Per-worker disk read bandwidth in bytes/sec enforced via cgroup
        ``blkio``/``io.max``. ``0`` means unlimited.
    disk_write_bps
        Per-worker disk write bandwidth in bytes/sec. ``0`` means unlimited.
    disk_read_iops
        Per-worker disk read IOPS ceiling. ``0`` means unlimited.
    disk_write_iops
        Per-worker disk write IOPS ceiling. ``0`` means unlimited.
    disk_device_path
        Device node (e.g. ``/dev/sda`` or ``/dev/nvme0n1``) the blkio
        limits target. ``None`` when blkio enforcement is unavailable
        (bare-metal or device-node resolution failed).
    """

    worker_id: int
    cpu_cores: int
    cpuset_cpus: Optional[str]
    ram_bytes: int
    docker_memory_limit_bytes: Optional[int]
    disk_read_bps: int = 0
    disk_write_bps: int = 0
    disk_read_iops: int = 0
    disk_write_iops: int = 0
    disk_device_path: Optional[str] = None

    def to_dict(self) -> dict[str, Any]:
        """Serialize for JSON output."""
        return {
            "worker_id": self.worker_id,
            "cpu_cores": self.cpu_cores,
            "cpuset_cpus": self.cpuset_cpus,
            "ram_bytes": self.ram_bytes,
            "docker_memory_limit_bytes": self.docker_memory_limit_bytes,
            "disk_read_bps": self.disk_read_bps,
            "disk_write_bps": self.disk_write_bps,
            "disk_read_iops": self.disk_read_iops,
            "disk_write_iops": self.disk_write_iops,
            "disk_device_path": self.disk_device_path,
        }


@dataclass(frozen=True)
class SessionEnvironment:
    """Complete hardware/software/topology snapshot for a tuning session.

    The block is meant for verbatim inclusion in session JSON output so
    downstream reproducibility tooling can recover the full runtime
    context without inspecting host state.
    """

    # CPU
    cpu_model: str
    cpu_cores_physical: int
    cpu_cores_logical: int
    # RAM
    ram_bytes_total: int
    # Storage
    disk_type: str
    data_disk_type: Optional[str]
    # OS
    kernel_version: str
    os_system: str
    os_release: str
    os_version: str
    os_machine: str
    # Software
    pg_client_version: str
    pg_server_version: Optional[str]
    docker_version: Optional[str]
    # Tuning topology
    use_docker: bool
    num_parallel_workers: int
    population_size: int
    cpu_pinning_scheme: str  # "cpuset" | "host" | "none"
    per_worker_resources: list[WorkerResourceAllocation] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        """Serialize for JSON output."""
        return {
            "cpu_model": self.cpu_model,
            "cpu_cores_physical": self.cpu_cores_physical,
            "cpu_cores_logical": self.cpu_cores_logical,
            "ram_bytes_total": self.ram_bytes_total,
            "disk_type": self.disk_type,
            "data_disk_type": self.data_disk_type,
            "kernel_version": self.kernel_version,
            "os_system": self.os_system,
            "os_release": self.os_release,
            "os_version": self.os_version,
            "os_machine": self.os_machine,
            "pg_client_version": self.pg_client_version,
            "pg_server_version": self.pg_server_version,
            "docker_version": self.docker_version,
            "use_docker": self.use_docker,
            "num_parallel_workers": self.num_parallel_workers,
            "population_size": self.population_size,
            "cpu_pinning_scheme": self.cpu_pinning_scheme,
            "per_worker_resources": [
                alloc.to_dict() for alloc in self.per_worker_resources
            ],
        }


def build_session_environment(
    *,
    env: "DatabaseEnvironment",
    num_parallel_workers: int,
    population_size: int,
    system_info: dict[str, Any],
    use_docker: Optional[bool] = None,
) -> SessionEnvironment:
    """Compose a :class:`SessionEnvironment` from ``system_info`` + env metadata.

    Parameters
    ----------
    env
        The active :class:`DatabaseEnvironment`. Used to read
        ``pg_server_version``, ``docker_version``, and per-worker
        resource allocations.
    num_parallel_workers
        Number of workers running concurrently within a generation.
    population_size
        Total PBT population size (or BO trial count for BO sessions).
    system_info
        Output of :func:`src.utils.hardware_info.get_system_info`.
    use_docker
        Whether the backend is the Docker backend. When ``None``, this is
        inferred from the env's class name.
    """
    import platform

    if use_docker is None:
        use_docker = type(env).__name__ == "DockerEnvironment"

    cpu_cores = system_info.get("cpu_cores", {}) or {}
    ram = system_info.get("ram", {}) or {}
    os_info = system_info.get("os", {}) or {}

    per_worker_resources = []
    cpu_pinning_scheme = "cpuset" if use_docker else "host"
    try:
        per_worker_resources = list(env.get_resource_allocations())
    except (AttributeError, NotImplementedError):
        cpu_pinning_scheme = "none"

    return SessionEnvironment(
        cpu_model=str(system_info.get("cpu_model", "unknown")),
        cpu_cores_physical=int(cpu_cores.get("physical", 0) or 0),
        cpu_cores_logical=int(cpu_cores.get("logical", 0) or 0),
        ram_bytes_total=int(ram.get("total_bytes", 0) or 0),
        disk_type=str(system_info.get("disk_type", "unknown")),
        data_disk_type=(
            str(system_info["data_disk_type"])
            if system_info.get("data_disk_type") is not None
            else None
        ),
        kernel_version=platform.release(),
        os_system=str(os_info.get("system", "unknown")),
        os_release=str(os_info.get("release", "")),
        os_version=str(os_info.get("version", "")),
        os_machine=str(os_info.get("machine", "")),
        pg_client_version=str(system_info.get("pg_version", "unknown")),
        pg_server_version=getattr(env, "pg_server_version", None),
        docker_version=getattr(env, "docker_version", None),
        use_docker=bool(use_docker),
        num_parallel_workers=int(num_parallel_workers),
        population_size=int(population_size),
        cpu_pinning_scheme=cpu_pinning_scheme,
        per_worker_resources=per_worker_resources,
    )
