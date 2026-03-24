"""
Unit tests for hardware normalization logic in the KnobSpace class.

These tests verify the correct resolution of hardware-relative knob ranges
based on system resources, the conversion between absolute and fractional
configuration representations, and the enforcement of memory budget constraints
in database parameter tuning. The tests cover:
- Initialization and correctness of WorkerResources.
- Dynamic range calculation for RAM-, CPU-, and disk-relative knobs.
- Conversion between configuration values and hardware-relative
  fractions, including round-trip accuracy.
- Enforcement and repair of memory budgets, ensuring that configuration
  values do not exceed specified limits.
- Dependency repair logic that adjusts related configuration parameters
  to maintain consistency and respect resource constraints.

Fixtures and mock objects are used to simulate various hardware environments and knob definitions.
Unit tests for hardware normalization logic in KnobSpace. 
"""
import pytest

from src.tuner.config.knob_space import (
    KnobSpace,
    KnobDefinition,
    KnobType,
    KnobScale,
)
from src.tuner.utils.hardware_info import WorkerResources


@pytest.fixture
def mock_knob_space():
    """Create a mock KnobSpace with hardware-relative knobs for testing."""
    defs = [
        # RAM-relative: shared_buffers (Fraction Min 0.15, Max 0.40, unit 8192)
        KnobDefinition(
            name="shared_buffers",
            knob_type=KnobType.INTEGER,
            scale=KnobScale.LOG,
            hardware_relative=True,
            resource_type="ram",
            step=1,
            unit="8kB"
        ),
        # RAM-relative: work_mem (Fraction Min 0.001, Max 0.02, unit 1024)
        KnobDefinition(
            name="work_mem",
            knob_type=KnobType.INTEGER,
            scale=KnobScale.LOG,
            hardware_relative=True,
            resource_type="ram",
            step=1,
            unit="kB"
        ),
        KnobDefinition(
            name="maintenance_work_mem",
            knob_type=KnobType.INTEGER,
            scale=KnobScale.LOG,
            hardware_relative=True,
            resource_type="ram",
            step=1,
            unit="kB"
        ),
        # CPU-relative: max_worker_processes (Fraction Min 0.50, Max 2.0, floor 4)
        KnobDefinition(
            name="max_worker_processes",
            knob_type=KnobType.INTEGER,
            scale=KnobScale.LINEAR,
            hardware_relative=True,
            resource_type="cpu",
            step=1
        ),
        # Disk-relative: random_page_cost (SSD: 1.0-1.5, HDD: 3.0-4.0, unknown: 0.1-4.0)
        KnobDefinition(
            name="random_page_cost",
            knob_type=KnobType.REAL,
            scale=KnobScale.LINEAR,
            hardware_relative=True,
            resource_type="disk_type"
        ),
        # Absolute knob
        KnobDefinition(
            name="max_connections",
            knob_type=KnobType.INTEGER,
            scale=KnobScale.LINEAR,
            hardware_relative=False,
            min_value=50,
            max_value=200,
            step=1
        )
    ]
    return KnobSpace(defs)


def test_worker_resources_creation():
    """Test WorkerResources initialization bounds and constraints."""
    wr = WorkerResources(ram_bytes=1024*1024*1024, cpu_cores=4, disk_type="SSD")
    assert wr.ram_bytes == 1024**3
    assert wr.cpu_cores == 4
    assert wr.disk_type == "SSD"


def test_resolve_hardware_ranges_ram(mock_knob_space):
    # 1 GB RAM
    gb = 1024 * 1024 * 1024
    wr = WorkerResources(ram_bytes=gb, cpu_cores=8, disk_type="SSD")

    mock_knob_space.resolve_hardware_ranges(wr)

    sb = mock_knob_space.knobs["shared_buffers"]
    # 0.15 of 1GB / 8192
    expected_sb_min = int(gb * 0.15 / 8192)
    expected_sb_max = int(gb * 0.40 / 8192)

    assert sb.min_value == expected_sb_min
    assert sb.max_value == expected_sb_max


def test_resolve_hardware_ranges_cpu(mock_knob_space):
    """Test dynamic range resolution for CPU bounds."""
    wr = WorkerResources(ram_bytes=1024**3, cpu_cores=8, disk_type="SSD")

    mock_knob_space.resolve_hardware_ranges(wr)

    mwp = mock_knob_space.knobs["max_worker_processes"]
    # min: max(4, round(8 * 0.5)) -> max(4, 4) = 4
    # max: max(4, round(8 * 2.0)) -> 16
    assert mwp.min_value == 4
    assert mwp.max_value == 16


def test_resolve_hardware_ranges_disk_ssd(mock_knob_space):
    wr = WorkerResources(ram_bytes=1024**3, cpu_cores=8, disk_type="SSD")
    mock_knob_space.resolve_hardware_ranges(wr)
    rpc = mock_knob_space.knobs["random_page_cost"]
    assert rpc.min_value == 1.0
    assert rpc.max_value == 1.5


def test_resolve_hardware_ranges_disk_hdd(mock_knob_space):
    wr = WorkerResources(ram_bytes=1024**3, cpu_cores=8, disk_type="HDD")
    mock_knob_space.resolve_hardware_ranges(wr)
    rpc = mock_knob_space.knobs["random_page_cost"]
    assert rpc.min_value == 3.0
    assert rpc.max_value == 4.0


def test_config_fractions_conversion_roundtrip(mock_knob_space):
    gb = 1024 * 1024 * 1024
    wr = WorkerResources(ram_bytes=gb, cpu_cores=8, disk_type="SSD")
    mock_knob_space.resolve_hardware_ranges(wr)

    original_config = {
        "shared_buffers": 32768,       # 256MB
        "work_mem": 4096,              # 4MB
        "maintenance_work_mem": 32768, # 32MB => 0.03125 fraction (in bounds 0.01-0.05)
        "max_worker_processes": 8,
        "random_page_cost": 1.2,       # disk type, remains absolute
        "max_connections": 100         # absolute knob, remains absolute
    }

    fractions = mock_knob_space.config_to_fractions(original_config)

    # Ram checking
    expected_sb_frac = (32768 * 8192) / gb
    expected_mwm_frac = (32768 * 1024) / gb
    assert fractions["shared_buffers"] == expected_sb_frac
    assert fractions["maintenance_work_mem"] == expected_mwm_frac

    # CPU checking
    assert fractions["max_worker_processes"] == 8 / 8.0

    # Absolute checking
    assert fractions["random_page_cost"] == 1.2
    assert fractions["max_connections"] == 100

    # Round back
    recovered = mock_knob_space.fractions_to_config(fractions)

    for k, v in original_config.items():
        assert recovered[k] == v


def test_memory_budget_repair_within_budget(mock_knob_space):
    budget = 800 * 1024 * 1024  # 800MB
    config = {
        "shared_buffers": 16384,  # 128MB
        "work_mem": 1024,         # 1MB
        "maintenance_work_mem": 65536, # 64MB
        "max_connections": 100
    }
    # Total = 128 + (100 * 1) + 64 = 292MB < 800MB
    repaired = mock_knob_space._repair_memory_budget(dict(config), budget)

    assert repaired == config


def test_memory_budget_repair_exceeds_budget(mock_knob_space):
    budget = 500 * 1024 * 1024  # 500MB

    # 256MB sb + (200 * 4MB) + 128MB mwm = 256 + 800 + 128 = 1184MB
    config = {
        "shared_buffers": 32768,       # 256MB
        "work_mem": 4096,              # 4MB
        "maintenance_work_mem": 131072, # 128MB
        "max_connections": 200
    }

    mock_knob_space.knobs["shared_buffers"].min_value = 0
    mock_knob_space.knobs["shared_buffers"].max_value = 1000000
    mock_knob_space.knobs["work_mem"].min_value = 0
    mock_knob_space.knobs["work_mem"].max_value = 1000000
    mock_knob_space.knobs["maintenance_work_mem"].min_value = 0
    mock_knob_space.knobs["maintenance_work_mem"].max_value = 1000000

    repaired = mock_knob_space._repair_memory_budget(dict(config), budget)

    scale = budget / (1184 * 1024 * 1024)

    # They should be scaled down proportionally
    assert repaired["shared_buffers"] == int(32768 * scale)
    assert repaired["work_mem"] == int(4096 * scale)
    assert repaired["maintenance_work_mem"] == int(131072 * scale)
    assert repaired["max_connections"] == int(200 * scale)

    # Check total memory is now <= budget
    total_repaired = (
        repaired["shared_buffers"] * 8192 +
        (repaired["max_connections"] * repaired["work_mem"] * 1024) +
        repaired["maintenance_work_mem"] * 1024
    )

    assert total_repaired <= budget
    # The ratio of shared_buffers to work_mem to maintenance_work_mem should 
    # be somewhat intact, though max_connections also scaled down, meaning
    # connection_memory = (scale^2 * original_connection_memory).
    # This is fine, as per the design.

def test_repair_config_dependencies_triggers_budget(mock_knob_space):
    """Test that config dependency repair respects total memory budget overrides."""
    budget = 500 * 1024 * 1024

    config = {
        "shared_buffers": 32768,       # 256MB
        "work_mem": 4096,              # 4MB
        "maintenance_work_mem": 131072, # 128MB
        "max_connections": 200
    }

    # Override bounds limits to avoid clamp
    for k in config:
        if k in mock_knob_space.knobs:
            mock_knob_space.knobs[k].min_value = 0
            mock_knob_space.knobs[k].max_value = 1000000

    repaired = mock_knob_space.repair_config_dependencies(config, budget_ram_bytes=budget)

    # Should scale down. E.g max connections should be less than 200
    assert repaired["max_connections"] < 200
    assert repaired["shared_buffers"] < 32768

def test_resolve_hardware_ranges_disk_unknown(mock_knob_space):
    """Test disk unknown resolution."""
    wr = WorkerResources(ram_bytes=1024**3, cpu_cores=8, disk_type="unknown")
    mock_knob_space.resolve_hardware_ranges(wr)
    rpc = mock_knob_space.knobs["random_page_cost"]
    assert rpc.min_value == 0.1
    assert rpc.max_value == 4.0

def test_fractions_to_config_units(mock_knob_space):
    """Test unit conversions from fractions to absolute."""
    gb = 4 * 1024**3
    wr = WorkerResources(ram_bytes=gb, cpu_cores=4, disk_type="SSD")
    mock_knob_space.resolve_hardware_ranges(wr)

    fractions = {
        "shared_buffers": 0.25, # 1GB
        "work_mem": 0.02,       # 81.9MB (max allowed fraction)
    }
    config = mock_knob_space.fractions_to_config(fractions)

    # shared_buffers is in 8kB = 8192 bytes. 1GB / 8192 = 131072
    assert config["shared_buffers"] == 131072
    # work_mem is in kB = 1024 bytes. 0.02 * 4GB / 1024 = 83886.08 -> 83886
    assert config["work_mem"] == 83886

def test_detect_worker_resources_bare_metal(mock_knob_space):
    """Integration test: resolve ranges with realistically detected bare-metal resources."""
    from src.tuner.utils.hardware_info import detect_worker_resources
    from unittest.mock import patch
    with patch("src.tuner.utils.hardware_info._is_containerized", return_value=False), \
         patch("src.tuner.utils.hardware_info.detect_disk_type", return_value="SSD"), \
         patch("src.tuner.utils.hardware_info.psutil.virtual_memory") as mock_vm, \
         patch("src.tuner.utils.hardware_info.psutil.Process") as mock_process, \
         patch("src.tuner.utils.hardware_info.psutil.cpu_count") as mock_cpu_count:

        class MockMem:
            total = 32 * 1024**3
        mock_vm.return_value = MockMem()

        class MockProcess:
            def cpu_affinity(self):
                return list(range(16))
        mock_process.return_value = MockProcess()
        mock_cpu_count.return_value = 16

        wr = detect_worker_resources(max_parallel_workers=4)
        mock_knob_space.resolve_hardware_ranges(wr)

        # 32GB * 0.8 / 4 = 6.4GB
        assert mock_knob_space.worker_resources.ram_bytes == int(32 * 1024**3 * 0.8 / 4)

        sb = mock_knob_space.knobs["shared_buffers"]
        expected_sb_min = int(6.4 * 1024**3 * 0.15 / 8192)
        assert sb.min_value == expected_sb_min

def test_detect_worker_resources_container(mock_knob_space):
    """Integration test: resolve ranges with container limits."""
    from src.tuner.utils.hardware_info import detect_worker_resources
    from unittest.mock import patch
    with patch("src.tuner.utils.hardware_info._is_containerized", return_value=True), \
         patch("src.tuner.utils.hardware_info.detect_disk_type", return_value="SSD"), \
         patch("src.tuner.utils.hardware_info.psutil.virtual_memory") as mock_vm, \
         patch("src.tuner.utils.hardware_info.psutil.Process") as mock_process:

        class MockMem:
            total = 8 * 1024**3
        mock_vm.return_value = MockMem()

        class MockProcess:
            def cpu_affinity(self):
                return [0, 1]
        mock_process.return_value = MockProcess()

        wr = detect_worker_resources(max_parallel_workers=2)
        mock_knob_space.resolve_hardware_ranges(wr)

        # 8GB * 0.8 / 2 = 3.2GB
        assert mock_knob_space.worker_resources.ram_bytes == int(8 * 1024**3 * 0.8 / 2)
        # 2 cores * 0.8 / 2 = 0.8 => floor is 0 => max(1, 0) = 1
        assert mock_knob_space.worker_resources.cpu_cores == 1

def test_memory_budget_repair_ratios(mock_knob_space):
    """Test that extreme memory budgets preserve relative ratios between knobs."""
    budget = 100 * 1024 * 1024 # 100MB
    config = {
        "shared_buffers": 32768,       # 256MB
        "work_mem": 4096,              # 4MB
        "maintenance_work_mem": 131072, # 128MB
        "max_connections": 100
    }

    for k in config:
        if k in mock_knob_space.knobs:
            mock_knob_space.knobs[k].min_value = 0
            mock_knob_space.knobs[k].max_value = 10000000

    repaired = mock_knob_space._repair_memory_budget(dict(config), budget)

    sb_bytes_orig = 32768 * 8192
    mwm_bytes_orig = 131072 * 1024
    assert sb_bytes_orig == mwm_bytes_orig * 2

    sb_bytes_new = repaired["shared_buffers"] * 8192
    mwm_bytes_new = repaired["maintenance_work_mem"] * 1024

    assert abs((sb_bytes_new / mwm_bytes_new) - 2.0) < 0.05

def test_worker_clone_memory_budget_repair(mock_knob_space):
    """Test Worker clone_from enforces bounds AND budget repair."""
    from src.tuner.core.worker import Worker

    wr = WorkerResources(ram_bytes=4 * 1024**3, cpu_cores=4, disk_type='ssd')
    mock_knob_space.resolve_hardware_ranges(wr)

    worker1 = Worker(worker_id=0, knob_space=mock_knob_space)
    worker1.knob_config = {
        "shared_buffers": 400000,
        "work_mem": 20480,
        "maintenance_work_mem": 2000000
    }

    worker2 = Worker(worker_id=1, knob_space=mock_knob_space)
    worker2.clone_from(worker1, current_generation=1)

    assert worker2.knob_config is not None
    assert worker2.knob_config["shared_buffers"] < 400000

def test_perturbation_bound_exceed_repairs(mock_knob_space):
    """Test that perturbations that exceed bounds are clamped properly."""
    wr = WorkerResources(ram_bytes=4 * 1024**3, cpu_cores=4, disk_type='ssd')
    mock_knob_space.resolve_hardware_ranges(wr)

    config = {
        "shared_buffers": mock_knob_space.knobs["shared_buffers"].max_value,
        "max_connections": mock_knob_space.knobs["max_connections"].max_value
    }

    perturbed = mock_knob_space.perturb_config(config, (2.0, 2.0))

    assert perturbed["shared_buffers"] <= mock_knob_space.knobs["shared_buffers"].max_value
    assert perturbed["max_connections"] <= mock_knob_space.knobs["max_connections"].max_value

def test_config_to_fractions_cpu_knob(mock_knob_space):
    """Test fractions extraction for a CPU relative knob."""
    wr = WorkerResources(ram_bytes=1024**3, cpu_cores=8, disk_type='ssd')
    mock_knob_space.resolve_hardware_ranges(wr)

    frac = mock_knob_space.config_to_fractions({"max_worker_processes": 8})
    assert frac["max_worker_processes"] == 1.0
