"""
Unit tests for hardware information utilities.

These tests validate the logic for detecting containerized environments
and allocating worker resources based on system hardware. The tests use
mocking to simulate different hardware configurations and containerization
states, ensuring that the resource detection logic behaves as expected in
various scenarios.
"""

from unittest.mock import patch
from src.utils.hardware_info import (
    _is_containerized,
    detect_worker_resources,
    detect_cpu_model,
    detect_core_count,
    detect_ram_total,
    get_system_info,
)


@patch("os.path.exists")
@patch("builtins.open")
def test_is_containerized_dockerenv(_mock_open, mock_exists):
    """Test container detection via /.dockerenv file."""
    mock_exists.return_value = True
    assert _is_containerized()
    mock_exists.assert_called_with("/.dockerenv")


@patch("os.path.exists")
def test_is_containerized_false(mock_exists):
    """Test negative container detection."""
    mock_exists.return_value = False
    with patch("builtins.open", side_effect=OSError):
        assert not _is_containerized()


@patch("src.utils.hardware_info._is_containerized", return_value=False)
@patch("src.utils.hardware_info.detect_disk_type", return_value="SSD")
@patch("src.utils.hardware_info.psutil.virtual_memory")
@patch("src.utils.hardware_info.psutil.cpu_count")
@patch("src.utils.hardware_info.psutil.Process")
def test_detect_worker_resources_bare_metal(
    mock_process,
    mock_cpu_count,
    mock_virtual_memory,
    _mock_disk_type,
    _mock_is_containerized,
):
    """Test worker resource allocation on bare-metal systems."""

    class MockMem:
        """Mock psutil virtual_memory response."""

        total = 16 * 1024**3

    mock_virtual_memory.return_value = MockMem()

    class MockProcess:
        """Mock psutil process object."""

        def cpu_affinity(self):
            """Mock CPU affinity to 8 cores."""
            return list(range(8))

    mock_process.return_value = MockProcess()
    # Also mock cpu_count
    mock_cpu_count.return_value = 8

    wr = detect_worker_resources(max_parallel_workers=4)

    # RAM = (16GB * 0.8) / 4 = 12.8GB / 4 = 3.2GB
    expected_ram = int((16 * 1024**3 * 0.8) / 4)
    # CPU = floor((8 * 0.8) / 4) = floor(6.4 / 4) = floor(1.6) = 1

    assert wr.ram_bytes == expected_ram
    assert wr.cpu_cores == 1
    assert wr.disk_type == "SSD"


@patch("src.utils.hardware_info._is_containerized", return_value=True)
@patch("src.utils.hardware_info.detect_disk_type", return_value="HDD")
@patch("src.utils.hardware_info.psutil.virtual_memory")
@patch("src.utils.hardware_info.psutil.cpu_count")
@patch("src.utils.hardware_info.psutil.Process")
def test_detect_worker_resources_container(
    mock_process,
    _mock_cpu_count,
    mock_virtual_memory,
    _mock_disk_type,
    _mock_is_containerized,
):
    """Test worker resource allocation in containerized environments."""

    class MockMem:
        """Mock psutil virtual_memory response."""

        total = 4 * 1024**3

    mock_virtual_memory.return_value = MockMem()

    class MockProcess:
        """Mock psutil process object."""

        def cpu_affinity(self):
            """Mock CPU affinity to 2 cores."""
            return [0, 1]

    mock_process.return_value = MockProcess()

    # In container = 1 container per worker
    # RAM = 4GB * 0.8
    # CPU = 2 cores (from affinity) * 0.8
    wr = detect_worker_resources(max_parallel_workers=1)

    expected_ram = int(4 * 1024**3 * 0.8)
    expected_cpu = int(2 * 0.8)  # floor(1.6)

    assert wr.ram_bytes == expected_ram
    assert wr.cpu_cores == expected_cpu
    assert wr.disk_type == "HDD"


@patch("src.utils.hardware_info.platform.system", return_value="Windows")
@patch(
    "src.utils.hardware_info.platform.processor",
    return_value="Mocked Intel(R) Core(TM) i9",
)
def test_detect_cpu_model(_mock_processor, _mock_system):
    """Test CPU model detection returns a valid non-empty string."""
    cpu = detect_cpu_model()
    assert isinstance(cpu, str)
    assert len(cpu) > 0
    assert cpu == "Mocked Intel(R) Core(TM) i9"


def test_detect_core_count():
    """Test core count detection returns positive integers."""
    cores = detect_core_count()
    assert isinstance(cores, dict)

    assert "physical" in cores
    assert isinstance(cores["logical"], int)

    assert cores["physical"] > 0
    assert cores["logical"] > 0


def test_detect_ram_total():
    """Test RAM detection returns dict with correct keys and positive values."""
    ram = detect_ram_total()
    assert isinstance(ram, dict)

    assert "total_bytes" in ram
    assert isinstance(ram["total_gb"], float)

    assert ram["total_bytes"] > 0
    assert ram["total_gb"] > 0.0


@patch("src.utils.hardware_info.detect_pg_version", return_value="PostgreSQL 14.2")
def test_system_info_dict_keys(_mock_pg_version):
    """Test get_system_info returns dict with all expected keys."""
    sys_info = get_system_info()
    expected_keys = {"cpu_model", "cpu_cores", "ram", "disk_type", "os", "pg_version"}
    assert isinstance(sys_info, dict)
    for key in expected_keys:
        assert key in sys_info


import pytest
from src.utils.hardware_info import parse_ram_value, resolve_manual_worker_resources


def test_parse_ram_value():
    assert parse_ram_value("3G") == 3221225472
    assert parse_ram_value("512M") == 536870912
    assert parse_ram_value("1024K") == 1048576
    assert parse_ram_value("3221225472") == 3221225472
    assert parse_ram_value("5GB") == 5368709120
    assert parse_ram_value("10 MB") == 10485760
    assert parse_ram_value("5") == 5
    with pytest.raises(ValueError):
        parse_ram_value("invalid")


@patch("src.utils.hardware_info.detect_disk_type", return_value="SSD")
@patch("src.utils.hardware_info.psutil.virtual_memory")
@patch("src.utils.hardware_info.psutil.cpu_count")
@patch("src.utils.hardware_info.psutil.Process")
def test_resolve_manual_worker_resources_valid(
    mock_process,
    mock_cpu_count,
    mock_virtual_memory,
    _mock_disk_type,
):
    class MockMem:
        total = 16 * 1024**3

    mock_virtual_memory.return_value = MockMem()

    class MockProcess:
        def cpu_affinity(self):
            return list(range(8))

    mock_process.return_value = MockProcess()
    mock_cpu_count.return_value = 8

    wr = resolve_manual_worker_resources(worker_ram="2G", worker_cpus=1, num_workers=4)
    assert wr.ram_bytes == 2 * 1024**3
    assert wr.cpu_cores == 1
    assert wr.disk_type == "SSD"


@patch("src.utils.hardware_info.detect_disk_type", return_value="SSD")
@patch("src.utils.hardware_info.psutil.virtual_memory")
@patch("src.utils.hardware_info.psutil.cpu_count")
@patch("src.utils.hardware_info.psutil.Process")
def test_resolve_manual_worker_resources_exceeds_ram(
    mock_process,
    mock_cpu_count,
    mock_virtual_memory,
    _mock_disk_type,
):
    class MockMem:
        total = 16 * 1024**3

    mock_virtual_memory.return_value = MockMem()

    class MockProcess:
        def cpu_affinity(self):
            return list(range(8))

    mock_process.return_value = MockProcess()
    mock_cpu_count.return_value = 8

    # 5G * 4 = 20G > 16G -> Fallback
    wr = resolve_manual_worker_resources(worker_ram="5G", worker_cpus=2, num_workers=4)
    expected_auto_ram = int((16 * 1024**3 * 0.8) / 4)
    assert wr.ram_bytes == expected_auto_ram


@patch("src.utils.hardware_info.detect_disk_type", return_value="SSD")
@patch("src.utils.hardware_info.psutil.virtual_memory")
@patch("src.utils.hardware_info.psutil.cpu_count")
@patch("src.utils.hardware_info.psutil.Process")
def test_resolve_manual_worker_resources_exceeds_cpu(
    mock_process,
    mock_cpu_count,
    mock_virtual_memory,
    _mock_disk_type,
):
    class MockMem:
        total = 16 * 1024**3

    mock_virtual_memory.return_value = MockMem()

    class MockProcess:
        def cpu_affinity(self):
            return list(range(8))

    mock_process.return_value = MockProcess()
    mock_cpu_count.return_value = 8

    # 3 CPUs * 4 = 12 CPUs > 8 CPUs -> Fallback
    wr = resolve_manual_worker_resources(worker_ram="2G", worker_cpus=3, num_workers=4)
    expected_auto_cpu = max(1, int((8 * 0.8) / 4))
    assert wr.cpu_cores == expected_auto_cpu


@patch("src.utils.hardware_info.detect_disk_type", return_value="SSD")
@patch("src.utils.hardware_info.psutil.virtual_memory")
@patch("src.utils.hardware_info.psutil.cpu_count")
@patch("src.utils.hardware_info.psutil.Process")
def test_resolve_manual_worker_resources_partial_override(
    mock_process,
    mock_cpu_count,
    mock_virtual_memory,
    _mock_disk_type,
):
    class MockMem:
        total = 16 * 1024**3

    mock_virtual_memory.return_value = MockMem()

    class MockProcess:
        def cpu_affinity(self):
            return list(range(8))

    mock_process.return_value = MockProcess()
    mock_cpu_count.return_value = 8

    wr = resolve_manual_worker_resources(
        worker_ram="2G", worker_cpus=None, num_workers=4
    )
    expected_auto_cpu = max(1, int((8 * 0.8) / 4))
    assert wr.ram_bytes == 2 * 1024**3
    assert wr.cpu_cores == expected_auto_cpu


@patch("src.utils.hardware_info.detect_disk_type_for_path", return_value="HDD")
@patch("src.utils.hardware_info.psutil.virtual_memory")
@patch("src.utils.hardware_info.psutil.cpu_count")
@patch("src.utils.hardware_info.psutil.Process")
def test_resolve_manual_worker_resources_disk_type_always_inferred(
    mock_process,
    mock_cpu_count,
    mock_virtual_memory,
    _mock_disk_type,
):
    class MockMem:
        total = 16 * 1024**3

    mock_virtual_memory.return_value = MockMem()

    class MockProcess:
        def cpu_affinity(self):
            return list(range(8))

    mock_process.return_value = MockProcess()
    mock_cpu_count.return_value = 8

    from pathlib import Path

    wr = resolve_manual_worker_resources(
        worker_ram="2G", worker_cpus=1, num_workers=4, data_path=Path("/tmp/data")
    )
    assert wr.disk_type == "HDD"


# ---------------------------------------------------------------------------
# Disk bandwidth detection + partitioning
# ---------------------------------------------------------------------------


@patch("src.utils.hardware_info._resolve_host_disk_budget")
@patch("src.utils.hardware_info.detect_disk_type", return_value="SSD")
@patch("src.utils.hardware_info.psutil.virtual_memory")
@patch("src.utils.hardware_info.psutil.cpu_count")
@patch("src.utils.hardware_info.psutil.Process")
def test_disk_budget_partitioning_divides_by_workers(
    mock_process,
    mock_cpu_count,
    mock_virtual_memory,
    _mock_disk_type,
    mock_resolve_host_budget,
):
    """Per-worker disk budget should be (host * 0.8) / num_workers."""

    class MockMem:
        total = 16 * 1024**3

    mock_virtual_memory.return_value = MockMem()

    class MockProcess:
        def cpu_affinity(self):
            return list(range(8))

    mock_process.return_value = MockProcess()
    mock_cpu_count.return_value = 8

    # NVMe PCIe 4.0 ceilings
    mock_resolve_host_budget.return_value = (
        {
            "read_bps": 6 * 1024 * 1024 * 1024,
            "write_bps": 4 * 1024 * 1024 * 1024,
            "read_iops": 700_000,
            "write_iops": 600_000,
        },
        "nvme_pcie4",
    )

    wr = detect_worker_resources(max_parallel_workers=4)

    expected_read = int(6 * 1024 * 1024 * 1024 * 0.8 / 4)
    expected_write = int(4 * 1024 * 1024 * 1024 * 0.8 / 4)
    assert wr.disk_read_bps == expected_read
    assert wr.disk_write_bps == expected_write
    assert wr.disk_read_iops == int(700_000 * 0.8 / 4)
    assert wr.disk_write_iops == int(600_000 * 0.8 / 4)
    assert wr.disk_class == "nvme_pcie4"


@patch("shutil.which", return_value=None)
def test_probe_disk_returns_none_without_fio(_mock_which, tmp_path):
    """When fio is not on PATH, the probe falls back gracefully."""
    from src.utils.hardware_info import _probe_disk_with_fio

    assert _probe_disk_with_fio(tmp_path) is None


def test_heuristic_disk_budget_usb_caps_media():
    """USB attachment caps the underlying SATA-SSD media throughput."""
    from src.utils.hardware_info import _heuristic_disk_budget, _DISK_CLASS_BUDGETS

    usb_budget = _heuristic_disk_budget("usb_external")
    sata_budget = _DISK_CLASS_BUDGETS["sata_ssd"]
    usb_caps = _DISK_CLASS_BUDGETS["usb_external"]
    for key in ("read_bps", "write_bps", "read_iops", "write_iops"):
        assert usb_budget[key] == min(usb_caps[key], sata_budget[key])


def test_heuristic_disk_budget_falls_back_to_unknown():
    """Unknown classes still return a non-empty conservative budget."""
    from src.utils.hardware_info import _heuristic_disk_budget

    budget = _heuristic_disk_budget("definitely-not-a-class")
    for key in ("read_bps", "write_bps", "read_iops", "write_iops"):
        assert budget[key] > 0


def test_resolve_parent_block_device_walks_partition_to_disk(tmp_path):
    """Partition device nodes must resolve to the parent disk node.

    Regression: cgroup v2 io.max is enforced on the parent disk, not on
    individual partitions. Writing rbps to /dev/sda3 raises ENODEV
    ("no such device") because the kernel I/O scheduler lives on /dev/sda.
    """
    from src.utils.hardware_info import _resolve_parent_block_device

    # Build a fake sysfs layout that mirrors the partition->disk topology.
    sys_class_block = tmp_path / "sys" / "class" / "block"
    sys_devices = tmp_path / "sys" / "devices" / "pci0000:00" / "sda"
    sys_devices_part = sys_devices / "sda3"
    sys_devices_part.mkdir(parents=True)
    (sys_devices_part / "partition").write_text("3\n")
    sys_class_block.mkdir(parents=True)
    # /sys/class/block/sda3 -> ../../devices/.../sda/sda3
    (sys_class_block / "sda3").symlink_to(sys_devices_part)
    (sys_class_block / "sda").symlink_to(sys_devices)

    # Mock the sysfs path used by _resolve_parent_block_device.
    fake_dev_node = tmp_path / "dev"
    fake_dev_node.mkdir()
    (fake_dev_node / "sda").touch()
    (fake_dev_node / "sda3").touch()

    with patch("src.utils.hardware_info.Path") as mock_path:

        def _path_factory(arg):
            arg_str = str(arg)
            if arg_str.startswith("/sys/class/block/"):
                return sys_class_block / arg_str.split("/")[-1]
            if arg_str.startswith("/dev/"):
                return fake_dev_node / arg_str.split("/")[-1]
            from pathlib import Path as RealPath

            return RealPath(arg)

        mock_path.side_effect = _path_factory

        resolved = _resolve_parent_block_device("/dev/sda3")

    assert resolved == "/dev/sda"


def test_resolve_parent_block_device_passes_through_whole_disk(tmp_path):
    """Whole-disk device nodes (no 'partition' marker) pass through unchanged."""
    from src.utils.hardware_info import _resolve_parent_block_device

    # No /sys/class/block/<name>/partition file => not a partition.
    sys_class_block = tmp_path / "sys" / "class" / "block"
    sys_devices = tmp_path / "sys" / "devices" / "pci0000:00" / "nvme0n1"
    sys_devices.mkdir(parents=True)
    sys_class_block.mkdir(parents=True)
    (sys_class_block / "nvme0n1").symlink_to(sys_devices)

    with patch("src.utils.hardware_info.Path") as mock_path:

        def _path_factory(arg):
            arg_str = str(arg)
            if arg_str.startswith("/sys/class/block/"):
                return sys_class_block / arg_str.split("/")[-1]
            from pathlib import Path as RealPath

            return RealPath(arg)

        mock_path.side_effect = _path_factory

        resolved = _resolve_parent_block_device("/dev/nvme0n1")

    assert resolved == "/dev/nvme0n1"


@patch("src.utils.hardware_info._resolve_host_disk_budget")
@patch("src.utils.hardware_info.detect_disk_type", return_value="SSD")
@patch("src.utils.hardware_info.psutil.virtual_memory")
@patch("src.utils.hardware_info.psutil.cpu_count")
@patch("src.utils.hardware_info.psutil.Process")
def test_resolve_manual_disk_overrides_take_precedence(
    mock_process,
    mock_cpu_count,
    mock_virtual_memory,
    _mock_disk_type,
    mock_resolve_host_budget,
):
    """Per-field manual disk overrides win over auto-detected values."""
    from src.utils.hardware_info import resolve_manual_worker_resources

    class MockMem:
        total = 16 * 1024**3

    mock_virtual_memory.return_value = MockMem()

    class MockProcess:
        def cpu_affinity(self):
            return list(range(8))

    mock_process.return_value = MockProcess()
    mock_cpu_count.return_value = 8

    mock_resolve_host_budget.return_value = (
        {
            "read_bps": 6 * 1024 * 1024 * 1024,
            "write_bps": 4 * 1024 * 1024 * 1024,
            "read_iops": 700_000,
            "write_iops": 600_000,
        },
        "nvme_pcie4",
    )

    wr = resolve_manual_worker_resources(
        num_workers=2,
        worker_disk_write_bps=50_000_000,
        worker_disk_read_iops=10_000,
    )

    # Overrides preserved verbatim
    assert wr.disk_write_bps == 50_000_000
    assert wr.disk_read_iops == 10_000
    # Non-overridden fields auto-detected
    assert wr.disk_read_bps == int(6 * 1024 * 1024 * 1024 * 0.8 / 2)
    assert wr.disk_write_iops == int(600_000 * 0.8 / 2)


@patch("src.utils.hardware_info._resolve_host_disk_budget")
@patch("src.utils.hardware_info.detect_disk_type", return_value="SSD")
@patch("src.utils.hardware_info.psutil.virtual_memory")
@patch("src.utils.hardware_info.psutil.cpu_count")
@patch("src.utils.hardware_info.psutil.Process")
def test_resolve_manual_disk_overflow_falls_back(
    mock_process,
    mock_cpu_count,
    mock_virtual_memory,
    _mock_disk_type,
    mock_resolve_host_budget,
):
    """Overrides exceeding 95% of host capacity fall back to auto-detected."""
    from src.utils.hardware_info import resolve_manual_worker_resources

    class MockMem:
        total = 16 * 1024**3

    mock_virtual_memory.return_value = MockMem()

    class MockProcess:
        def cpu_affinity(self):
            return list(range(8))

    mock_process.return_value = MockProcess()
    mock_cpu_count.return_value = 8

    host_budget = {
        "read_bps": 1_000_000_000,
        "write_bps": 1_000_000_000,
        "read_iops": 100_000,
        "write_iops": 100_000,
    }
    mock_resolve_host_budget.return_value = (host_budget, "sata_ssd")

    # 600M/worker × 2 workers = 1200M > 95% × 1G
    wr = resolve_manual_worker_resources(
        num_workers=2,
        worker_disk_write_bps=600_000_000,
    )

    # Falls back to auto-detected
    assert wr.disk_write_bps == int(1_000_000_000 * 0.8 / 2)
