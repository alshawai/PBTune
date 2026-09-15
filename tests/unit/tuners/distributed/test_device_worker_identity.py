# Copyright (C) 2026 Ibrahim Al-Shawa and PBTune contributors
# Licensed under the GNU General Public License v3.0
# See LICENSE file for details

"""Regression tests for device-side worker identity (issue #143).

Each device provisions exactly one PostgreSQL instance, at local index 0, but
is tagged with a fleet-global worker id. The device used to hand that *global*
id to the shared orchestrator as ``BaseWorker.worker_id``, which is the key the
environment uses to resolve a worker's container, host port and PGDATA. On any
device whose global id was not 0 that made every environment lookup miss:
memory and cache-hit collection silently returned 0.0, and the in-eval snapshot
restore provisioned a *second* instance at ``base_port + global_id`` while the
instance actually under measurement was never restored.

The fix keeps ``worker_id`` at the local instance index and carries the global
id as ``display_id``, which is logging-only.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any
from unittest.mock import MagicMock

import pytest

from src.config.database import DatabaseConfig
from src.knobs import get_knob_space
from src.tuners.distributed.agent_api import SetupRequest
from src.tuners.distributed.device_agent import LocalDeviceBackend
from src.tuners.pbt.worker import PBTWorker
from src.utils.hardware_info import WorkerResources

GLOBAL_WORKER_ID = 7


class _FakeEnv:
    """Minimal environment that only knows about local instance index 0."""

    base_dir = "/fleet/worker-7"

    def __init__(self) -> None:
        self.setup_instances_calls: list[int] = []

    def setup_instances(self, num_workers: int) -> None:
        self.setup_instances_calls.append(num_workers)

    def get_db_config(self, worker_id: int) -> DatabaseConfig:
        if worker_id != 0:
            raise KeyError(
                f"no local instance at index {worker_id}; this device only "
                "provisioned index 0"
            )
        return DatabaseConfig(
            user="postgres", password="", host="127.0.0.1", port=5440, dbname="test"
        )


@pytest.fixture
def prepared_backend(monkeypatch: pytest.MonkeyPatch) -> tuple[LocalDeviceBackend, Any]:
    """A LocalDeviceBackend whose heavy collaborators are stubbed out."""
    backend = LocalDeviceBackend(
        global_worker_id=GLOBAL_WORKER_ID,
        knob_tier="minimal",
        base_dir="/fleet/worker-7",
    )
    env = _FakeEnv()

    monkeypatch.setattr(
        "src.utils.hardware_info.detect_worker_resources",
        lambda **_: WorkerResources(
            ram_bytes=32 * 1024**3, cpu_cores=8, disk_type="SSD"
        ),
    )
    monkeypatch.setattr(
        "src.utils.environments.EnvironmentFactory.create",
        staticmethod(lambda **_: env),
    )
    monkeypatch.setattr(
        "src.benchmarks.sysbench.executor.SysbenchExecutor",
        MagicMock(return_value=MagicMock(threads=8)),
    )
    monkeypatch.setattr(
        "src.tuners.engine.orchestrator.WorkloadOrchestrator",
        MagicMock(),
    )
    return backend, env


def _request() -> SetupRequest:
    return SetupRequest(
        run_id="test",
        benchmark="sysbench",
        workload_type="oltp_read_write",
        measurement_duration=120.0,
        warmup_duration=60.0,
        tuning_mode="offline",
    )


def test_device_worker_uses_local_instance_index(prepared_backend) -> None:
    """The device's worker must address the local instance, not the global id."""
    backend, env = prepared_backend

    resp = backend.setup(_request())

    assert resp.ok
    assert env.setup_instances_calls == [1]
    # The identity the environment is keyed on must be the LOCAL index. Had this
    # stayed at the global id, every env lookup below would resolve a container
    # and port that do not exist on this device.
    assert backend._worker.worker_id == LocalDeviceBackend.LOCAL_WORKER_ID == 0
    # ...while logs still identify the worker globally.
    assert backend._worker.display_id == GLOBAL_WORKER_ID
    assert backend._worker.display_worker_id == GLOBAL_WORKER_ID
    assert str(backend._worker).startswith(f"Worker-{GLOBAL_WORKER_ID} ")


def test_device_worker_binds_to_the_local_port(prepared_backend) -> None:
    """The worker's connection must target the instance that actually exists."""
    backend, _ = prepared_backend

    backend.setup(_request())

    assert backend._worker.port == 5440
    assert backend._worker.db_config is not None
    assert backend._worker.db_config.port == 5440


def test_local_env_lookup_succeeds_for_the_device_worker(prepared_backend) -> None:
    """The worker id the orchestrator passes to the env must resolve locally.

    This is the invariant the bug violated: the shared orchestrator resolves
    metrics, snapshot restore and restart through ``env.<op>(worker.worker_id)``.
    """
    backend, env = prepared_backend

    backend.setup(_request())

    # Must not raise — the pre-fix global id (7) would have raised KeyError.
    assert env.get_db_config(backend._worker.worker_id).port == 5440


def test_display_id_is_logging_only_and_defaults_to_worker_id() -> None:
    """A worker without an explicit display id reports its own id."""
    knob_space = get_knob_space("minimal")
    worker = PBTWorker(worker_id=2, knob_space=knob_space)

    assert worker.display_worker_id == 2
    assert str(worker).startswith("Worker-2 ")


def test_display_id_does_not_shift_the_env_index() -> None:
    """Setting display_id must never change the environment-facing identity."""
    knob_space = get_knob_space("minimal")
    worker = PBTWorker(worker_id=0, display_id=7, knob_space=knob_space)

    assert worker.worker_id == 0  # what env lookups use
    assert worker.display_worker_id == 7  # what logs show
    assert "id=7" in repr(worker)


def test_base_dir_is_reported_from_the_environment(prepared_backend) -> None:
    """Sanity: setup still reports the device's own data directory."""
    backend, _ = prepared_backend

    resp = backend.setup(_request())

    assert resp.data_dir == str(Path("/fleet/worker-7"))
