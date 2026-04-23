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
