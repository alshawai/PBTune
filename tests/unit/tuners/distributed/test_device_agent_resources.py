"""Resource-allocation tests for dedicated distributed device agents."""

from pathlib import Path
from unittest.mock import patch

from src.tuners.distributed.device_agent import LocalDeviceBackend
from src.utils.hardware_info import WorkerResources


def test_device_agent_allocates_full_dedicated_device() -> None:
    """One worker per device receives 100% of detected device capacity."""
    backend = LocalDeviceBackend(
        global_worker_id=0,
        knob_tier="minimal",
        base_dir="/fleet/worker-0",
    )
    detected = WorkerResources(
        ram_bytes=32 * 1024**3,
        cpu_cores=8,
        disk_type="SSD",
    )

    with patch(
        "src.utils.hardware_info.detect_worker_resources",
        return_value=detected,
    ) as detect:
        resources = backend._detect_resources(Path(backend.base_dir))

    assert resources is detected
    detect.assert_called_once_with(
        max_parallel_workers=1,
        data_path=Path("/fleet/worker-0"),
        threshold=1.0,
    )