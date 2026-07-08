"""Tests for :class:`SessionEnvironment` and :func:`build_session_environment`."""

from __future__ import annotations

import dataclasses
import json

import pytest

from src.utils.types import (
    WorkerResourceAllocation,
    build_session_environment,
)


def _system_info() -> dict:
    return {
        "cpu_model": "AMD EPYC 7763",
        "cpu_cores": {"physical": 8, "logical": 16},
        "ram_bytes_total": 64 * 1024**3,
        "ram": {"total_bytes": 64 * 1024**3, "total_gb": 64.0},
        "disk_type": "SSD",
        "data_disk_type": "HDD",
        "pg_version": "PostgreSQL 18.0",
        "os": {
            "system": "Linux",
            "release": "6.5.0-15-generic",
            "version": "#15-Ubuntu SMP",
            "machine": "x86_64",
        },
    }


class _FakeEnv:
    """Minimal DatabaseEnvironment stand-in for builder tests."""

    def __init__(
        self,
        *,
        pg_server_version="18.0",
        docker_version="25.0.3",
        allocations=None,
    ) -> None:
        self.pg_server_version = pg_server_version
        self.docker_version = docker_version
        self._allocations = allocations or [
            WorkerResourceAllocation(
                worker_id=0,
                cpu_cores=4,
                cpuset_cpus="0,1,2,3",
                ram_bytes=8 * 1024**3,
                docker_memory_limit_bytes=8 * 1024**3,
            ),
            WorkerResourceAllocation(
                worker_id=1,
                cpu_cores=4,
                cpuset_cpus="4,5,6,7",
                ram_bytes=8 * 1024**3,
                docker_memory_limit_bytes=8 * 1024**3,
            ),
        ]

    def get_resource_allocations(self):
        return list(self._allocations)


def test_worker_resource_allocation_is_frozen() -> None:
    alloc = WorkerResourceAllocation(
        worker_id=0,
        cpu_cores=2,
        cpuset_cpus="0,1",
        ram_bytes=1024,
        docker_memory_limit_bytes=1024,
    )
    with pytest.raises(dataclasses.FrozenInstanceError):
        alloc.cpu_cores = 4  # type: ignore[misc]


def test_session_environment_is_frozen() -> None:
    env = _FakeEnv()
    se = build_session_environment(
        env=env,
        num_parallel_workers=2,
        population_size=8,
        system_info=_system_info(),
        use_docker=True,
    )
    with pytest.raises(dataclasses.FrozenInstanceError):
        se.cpu_model = "other"  # type: ignore[misc]


def test_to_dict_round_trips_via_json() -> None:
    env = _FakeEnv()
    se = build_session_environment(
        env=env,
        num_parallel_workers=2,
        population_size=8,
        system_info=_system_info(),
        use_docker=True,
    )
    encoded = json.dumps(se.to_dict())
    decoded = json.loads(encoded)

    assert decoded["cpu_model"] == "AMD EPYC 7763"
    assert decoded["cpu_cores_physical"] == 8
    assert decoded["cpu_cores_logical"] == 16
    assert decoded["disk_type"] == "SSD"
    assert decoded["data_disk_type"] == "HDD"
    assert decoded["pg_server_version"] == "18.0"
    assert decoded["docker_version"] == "25.0.3"
    assert decoded["use_docker"] is True
    assert decoded["num_parallel_workers"] == 2
    assert decoded["population_size"] == 8
    assert decoded["cpu_pinning_scheme"] == "cpuset"
    assert len(decoded["per_worker_resources"]) == 2
    assert decoded["per_worker_resources"][0]["cpuset_cpus"] == "0,1,2,3"


def test_builder_uses_env_pg_server_version() -> None:
    env = _FakeEnv(pg_server_version="16.4")
    se = build_session_environment(
        env=env,
        num_parallel_workers=1,
        population_size=4,
        system_info=_system_info(),
        use_docker=True,
    )
    assert se.pg_server_version == "16.4"


def test_builder_handles_missing_pg_server_version_attribute() -> None:
    class _MinimalEnv:
        def get_resource_allocations(self):
            return []

    se = build_session_environment(
        env=_MinimalEnv(),  # type: ignore[arg-type]
        num_parallel_workers=1,
        population_size=1,
        system_info=_system_info(),
        use_docker=False,
    )
    assert se.pg_server_version is None
    assert se.docker_version is None
    assert se.use_docker is False
    assert se.cpu_pinning_scheme == "host"


def test_builder_handles_missing_get_resource_allocations() -> None:
    class _NoAllocEnv:
        pg_server_version = "18.0"
        docker_version = None

    se = build_session_environment(
        env=_NoAllocEnv(),  # type: ignore[arg-type]
        num_parallel_workers=1,
        population_size=1,
        system_info=_system_info(),
        use_docker=False,
    )
    assert se.per_worker_resources == []
    assert se.cpu_pinning_scheme == "none"


def test_builder_data_disk_type_optional() -> None:
    info = _system_info()
    del info["data_disk_type"]
    se = build_session_environment(
        env=_FakeEnv(),
        num_parallel_workers=2,
        population_size=8,
        system_info=info,
        use_docker=True,
    )
    assert se.data_disk_type is None


def test_builder_infers_use_docker_from_class_name() -> None:
    class DockerEnvironment(_FakeEnv):
        pass

    class BareMetalEnvironment(_FakeEnv):
        pass

    docker_env = DockerEnvironment()
    bm_env = BareMetalEnvironment(docker_version=None)

    docker_se = build_session_environment(
        env=docker_env,
        num_parallel_workers=1,
        population_size=1,
        system_info=_system_info(),
    )
    bm_se = build_session_environment(
        env=bm_env,
        num_parallel_workers=1,
        population_size=1,
        system_info=_system_info(),
    )
    assert docker_se.use_docker is True
    assert bm_se.use_docker is False
