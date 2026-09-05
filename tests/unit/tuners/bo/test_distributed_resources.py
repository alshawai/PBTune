"""Resource-parity tests for BO after distributed PBT."""

from types import SimpleNamespace
from unittest.mock import Mock, patch

from src.tuners.base import BaseTuner
from src.tuners.bo.config import BOConfig
from src.tuners.bo.tuner import BOTuner
from src.utils.hardware_info import WorkerResources
from src.utils.types import TuningMode


def test_pbt_session_loads_nested_worker_resources(tmp_path) -> None:
    """Current unified sessions expose their worker envelope under metadata."""
    session_path = tmp_path / "trace.json"
    session_path.write_text(
        """
{
  "tuning_session": {
    "knob_tier": "minimal",
    "knob_source": "expert",
    "benchmark_name": "sysbench",
    "workload_type": "oltp",
    "tuning_mode": "offline",
    "worker_resources": {
      "ram_bytes": 34359738368,
      "cpu_cores": 8,
      "disk_type": "SSD",
      "disk_read_bps": 1000000000,
      "disk_write_bps": 900000000
    }
  }
}
""".strip(),
        encoding="utf-8",
    )
    config = BOConfig(no_cotenant=True)

    config.apply_pbt_session(session_path, set_iteration_budget=False)

    assert config.pbt_worker_resources == {
        "ram_bytes": 34359738368,
        "cpu_cores": 8,
        "disk_type": "SSD",
        "disk_read_bps": 1000000000,
        "disk_write_bps": 900000000,
    }


def test_solo_bo_uses_full_distributed_pbt_resource_envelope() -> None:
    """Solo BO must not reapply the local 80/95-percent safety threshold."""
    resources = {
        "ram_bytes": 32 * 1024**3,
        "cpu_cores": 8,
        "disk_type": "SSD",
        "disk_read_bps": 1_000_000_000,
        "disk_write_bps": 900_000_000,
        "disk_read_iops": 100_000,
        "disk_write_iops": 90_000,
        "disk_class": "local_ssd",
    }
    tuner = object.__new__(BOTuner)
    tuner.bo_config = BOConfig(
        no_cotenant=True,
        pbt_worker_resources=resources,
    )
    tuner.lifecycle = SimpleNamespace(tuning_mode=TuningMode.OFFLINE)
    tuner.full_knob_space = Mock()
    tuner.knob_space = tuner.full_knob_space

    with patch.object(BaseTuner, "_resolve_resources", autospec=True) as fallback:
        tuner._resolve_resources()

    fallback.assert_not_called()
    assert tuner.worker_resources == WorkerResources(**resources)
    tuner.full_knob_space.resolve_hardware_ranges.assert_called_once_with(
        tuner.worker_resources
    )
    assert tuner.knob_space.worker_resources == tuner.worker_resources


def test_regular_bo_keeps_standard_resource_detection() -> None:
    """The full-device override is restricted to explicit solo comparisons."""
    tuner = object.__new__(BOTuner)
    tuner.bo_config = BOConfig(
        no_cotenant=False,
        pbt_worker_resources={
            "ram_bytes": 32 * 1024**3,
            "cpu_cores": 8,
            "disk_type": "SSD",
        },
    )

    with patch.object(BaseTuner, "_resolve_resources", autospec=True) as fallback:
        tuner._resolve_resources()

    fallback.assert_called_once_with(tuner)
