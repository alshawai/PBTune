"""Tests for fleet-resource promotion in distributed PBT runs."""

from types import SimpleNamespace
from unittest.mock import MagicMock

import pytest

from src.tuners.pbt.tuner import PBTTuner
from src.utils.hardware_info import WorkerResources


def test_device_resources_replace_coordinator_profile() -> None:
    """Agent-reported resources become authoritative before optimization."""
    tuner = PBTTuner.__new__(PBTTuner)
    tuner.env = SimpleNamespace(
        representative_resources=lambda: {
            "ram_bytes": 32 * 1024**3,
            "cpu_cores": 8,
            "disk_type": "SSD",
            "disk_read_bps": 500 * 1024**2,
            "disk_write_bps": 250 * 1024**2,
            "disk_read_iops": 80_000,
            "disk_write_iops": 60_000,
            "disk_class": "sata_ssd",
            "future_protocol_field": "ignored",
        }
    )
    tuner.worker_resources = WorkerResources(
        ram_bytes=1024**3,
        cpu_cores=1,
        disk_type="SSD",
    )
    tuner.full_knob_space = MagicMock()
    tuner.knob_space = MagicMock()
    tuner.orchestrator = SimpleNamespace(
        config=SimpleNamespace(worker_memory_budget_bytes=1024**3)
    )

    tuner._resolve_device_hardware_ranges()

    assert tuner.worker_resources.ram_bytes == 32 * 1024**3
    assert tuner.worker_resources.cpu_cores == 8
    assert tuner.worker_resources.disk_class == "sata_ssd"
    tuner.full_knob_space.resolve_hardware_ranges.assert_called_once_with(
        tuner.worker_resources
    )
    assert tuner.knob_space.worker_resources is tuner.worker_resources
    assert (
        tuner.orchestrator.config.worker_memory_budget_bytes
        == tuner.worker_resources.ram_bytes
    )


def test_missing_device_resources_never_fall_back_to_coordinator() -> None:
    """Distributed setup must fail rather than size knobs from the coordinator."""
    tuner = PBTTuner.__new__(PBTTuner)
    tuner.env = SimpleNamespace(representative_resources=lambda: None)
    tuner.worker_resources = WorkerResources(
        ram_bytes=1024**3,
        cpu_cores=1,
        disk_type="SSD",
    )

    with pytest.raises(RuntimeError, match="refusing to use coordinator hardware"):
        tuner._resolve_device_hardware_ranges()

    assert tuner.worker_resources.ram_bytes == 1024**3