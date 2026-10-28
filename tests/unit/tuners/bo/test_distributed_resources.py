"""Resource-parity tests for BO after distributed PBT."""

from pathlib import Path
from typing import Any, Dict, Optional
from unittest.mock import patch

import pytest

from src.knobs import get_knob_space
from src.tuners.bo.config import BOConfig
from src.tuners.bo.tuner import BOTuner
from src.tuners.utils.types import TunerLifecycleConfig, TuningStrategy
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


_SOLO_RESOURCES = {
    "ram_bytes": 32 * 1024**3,
    "cpu_cores": 8,
    "disk_type": "SSD",
    "disk_read_bps": 1_000_000_000,
    "disk_write_bps": 900_000_000,
    "disk_read_iops": 100_000,
    "disk_write_iops": 90_000,
    "disk_class": "local_ssd",
}

# What local detection reports; deliberately unlike the session envelope.
_LOCAL_RESOURCES = WorkerResources(ram_bytes=8 * 1024**3, cpu_cores=2, disk_type="SSD")


def _bo_tuner(
    *,
    no_cotenant: bool,
    pbt_worker_resources: Optional[Dict[str, Any]],
    tuning_mode: TuningMode = TuningMode.OFFLINE,
    data_root: Optional[Path] = None,
) -> BOTuner:
    """BOTuner past ``_load_knob_space``, ready for ``_resolve_resources``."""
    tuner = object.__new__(BOTuner)
    tuner.bo_config = BOConfig(
        no_cotenant=no_cotenant,
        pbt_worker_resources=pbt_worker_resources,
    )
    tuner.lifecycle = TunerLifecycleConfig(
        strategy=TuningStrategy.BO,
        knob_tier="minimal",
        knob_source="expert",
        num_parallel_workers=1,
        tuning_mode=tuning_mode,
    )
    tuner.data_root = data_root
    tuner.orchestrator = None
    tuner.worker_resources = None
    tuner.full_knob_space = get_knob_space("minimal")
    tuner.knob_space = tuner.full_knob_space
    return tuner


def _resolve_with_local_detection(tuner: BOTuner) -> None:
    with patch(
        "src.tuners.base.resolve_worker_resources", return_value=_LOCAL_RESOURCES
    ):
        tuner._resolve_resources()


@pytest.mark.parametrize(
    "tuning_mode", [TuningMode.OFFLINE, TuningMode.ONLINE], ids=lambda m: m.value
)
def test_solo_bo_uses_full_distributed_pbt_resource_envelope(tuning_mode) -> None:
    """Solo BO must not reapply the local 80/95-percent safety threshold."""
    tuner = _bo_tuner(
        no_cotenant=True,
        pbt_worker_resources=_SOLO_RESOURCES,
        tuning_mode=tuning_mode,
    )

    _resolve_with_local_detection(tuner)

    expected = WorkerResources(**_SOLO_RESOURCES)
    assert tuner.worker_resources == expected
    assert tuner.full_knob_space.worker_resources == expected
    assert tuner.knob_space.worker_resources == expected


def test_solo_bo_online_mode_tunes_restart_free_view() -> None:
    """The tuning-mode filter still applies on the session-envelope path."""
    tuner = _bo_tuner(
        no_cotenant=True,
        pbt_worker_resources=_SOLO_RESOURCES,
        tuning_mode=TuningMode.ONLINE,
    )

    _resolve_with_local_detection(tuner)

    assert "shared_buffers" in tuner.full_knob_space.knobs
    assert "shared_buffers" not in tuner.knob_space.knobs


def test_solo_bo_rejects_non_positive_resources() -> None:
    """Invalid session resources fail before anything is adopted."""
    tuner = _bo_tuner(
        no_cotenant=True,
        pbt_worker_resources={**_SOLO_RESOURCES, "ram_bytes": 0},
    )

    with pytest.raises(ValueError, match="positive RAM and CPU"):
        _resolve_with_local_detection(tuner)

    assert tuner.worker_resources is None


@pytest.mark.parametrize(
    "no_cotenant, pbt_worker_resources",
    [
        pytest.param(False, _SOLO_RESOURCES, id="cotenant"),
        pytest.param(True, None, id="solo-without-session-resources"),
    ],
)
def test_bo_falls_back_to_local_detection(
    no_cotenant, pbt_worker_resources, tmp_path
) -> None:
    """The full-device override is restricted to explicit solo comparisons."""
    tuner = _bo_tuner(
        no_cotenant=no_cotenant,
        pbt_worker_resources=pbt_worker_resources,
        tuning_mode=TuningMode.ONLINE,
        data_root=tmp_path,
    )

    _resolve_with_local_detection(tuner)

    assert tuner.worker_resources == _LOCAL_RESOURCES
    assert tuner.knob_space.worker_resources == _LOCAL_RESOURCES
    assert "shared_buffers" not in tuner.knob_space.knobs
