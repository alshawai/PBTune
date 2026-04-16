"""
Unit tests for warm start functionality in the PBT tuner, focusing on
RAM relative knob specifications and the repair mechanism during worker
cloning. Tests include:
- Partial seeding of workers in population initialization with provided configs.
- Automatic repair of memory budget when cloning workers with invalid configurations.
- Building warm start configurations from a JSON file with fractional values and
  verifying the structure and provenance tracking
"""
import pytest
import json
from src.config.database import DatabaseConfig
from src.tuner.core.population import Population, PopulationConfig
from src.tuner.main import PBTTuner
from src.tuner.config import PBTConfig
from src.tuner.config.knob_space import KnobSpace, WorkerResources, KnobDefinition, KnobType


@pytest.fixture(autouse=True)
def patch_pbttuner_knob_loader(monkeypatch, request):
    """Patch PBTTuner init-time dependencies to avoid filesystem coupling in CI."""
    mock_knob_space = request.getfixturevalue("mock_knob_space")
    fake_db_config = DatabaseConfig(
        user="postgres",
        password="test-password",
        host="127.0.0.1",
        port=5432,
        dbname="test_dataset",
    )
    fixed_resources = WorkerResources(
        ram_bytes=4 * 1024 * 1024 * 1024,
        cpu_cores=4,
        disk_type='ssd',
    )

    monkeypatch.setattr(
        "src.tuner.main.get_knob_space",
        lambda _tier: mock_knob_space,
    )
    monkeypatch.setattr(
        "src.tuner.main.detect_worker_resources",
        lambda *args, **kwargs: fixed_resources,
    )
    monkeypatch.setattr(
        "src.tuner.main.get_db_config",
        lambda: fake_db_config,
    )

@pytest.fixture
def mock_knob_space():
    """Provides a mocked KnobSpace for testing warm starts with RAM relative specs."""
    defs = {
        "shared_buffers": KnobDefinition(
            name="shared_buffers",
            knob_type=KnobType.INTEGER,
            min_value=128,
            max_value=8192,
            default=1024,
            hardware_relative=True,
            resource_type="ram",
            unit="8kB"
        ),
        "work_mem": KnobDefinition(
            name="work_mem",
            knob_type=KnobType.INTEGER,
            min_value=16,
            max_value=2097152,
            default=64,
            hardware_relative=True,
            resource_type="ram",
            unit="kB"
        ),
        "maintenance_work_mem": KnobDefinition(
            name="maintenance_work_mem",
            knob_type=KnobType.INTEGER,
            min_value=64,
            max_value=2097152,
            default=128,
            hardware_relative=True,
            resource_type="ram",
            unit="kB"
        )
    }
    space = KnobSpace(list(defs.values()))

    # 4GB Worker config
    resources = WorkerResources(ram_bytes=4 * 1024 * 1024 * 1024, cpu_cores=4, disk_type='ssd')
    space.resolve_hardware_ranges(resources)
    return space

def test_warm_start_seeds_workers_partial(mock_knob_space):
    """Test partial seeding of workers in population initialize()"""
    pop_config = PopulationConfig(population_size=4)
    population = Population(knob_space=mock_knob_space, config=pop_config)

    initial_configs = [
        {"shared_buffers": 512, "work_mem": 32, "maintenance_work_mem": 128},
        {"shared_buffers": 1024, "work_mem": 64, "maintenance_work_mem": 256}
    ]

    population.initialize(initial_configs=initial_configs, random_seed=42)

    assert len(population.workers) == 4
    # First two should match provided configs
    for i in range(2):
        for k, v in initial_configs[i].items():
            assert population.workers[i].knob_config[k] == v
            
    # Rest should be LHS sampled and different
    for i in range(2, 4):
        for k in initial_configs[0].keys():
            assert k in population.workers[i].knob_config
            assert population.workers[i].knob_config[k] != initial_configs[0][k]
            assert population.workers[i].knob_config[k] != initial_configs[1][k]

def test_warm_start_seeds_workers_full(mock_knob_space):
    """Test full seeding with len(initial_configs) == population_size."""
    pop_config = PopulationConfig(population_size=2)
    population = Population(knob_space=mock_knob_space, config=pop_config)

    initial_configs = [
        {"shared_buffers": 512, "work_mem": 32, "maintenance_work_mem": 128},
        {"shared_buffers": 1024, "work_mem": 64, "maintenance_work_mem": 256}
    ]

    population.initialize(initial_configs=initial_configs, random_seed=42)
    assert len(population.workers) == 2
    for i in range(2):
        for k, v in initial_configs[i].items():
            assert population.workers[i].knob_config[k] == v

def test_warm_start_provenance(mock_knob_space, tmp_path):
    """Test PBTTuner warm start config structure from JSON and half split."""
    warm_start_path = tmp_path / "best_config.json"

    warm_start_data = {
        "shared_buffers": 0.15, # 15% (min)
        "work_mem": 0.001,      # 0.1% (min)
        "maintenance_work_mem": 0.01 # 1% (min)
    }
    with open(warm_start_path, 'w') as f:
        json.dump(warm_start_data, f)

    tuner = PBTTuner(
        knob_tier="minimal",
        pbt_config=PBTConfig(population_size=4, num_generations=1, num_parallel_workers=4),
        warm_start_path=str(warm_start_path),
        skip_schema_init=True,
    )
    tuner.knob_space = mock_knob_space

    configs = tuner._build_warm_start_configs(
        warm_start_path=warm_start_path,
        population_size=4,
        seed=42
    )

    assert len(configs) == 2
    base = configs[0]
    
    # memory budget: 4GB * 0.8 = 3.2GB.
    # Base should match fraction->absolute conversion
    assert base["shared_buffers"] == 78643  # 4GB -> sb 8kB unit: 0.15 * 4G/8192 = 78643
    assert base["work_mem"] == 4194  # wm kB unit: 0.001 * 4G/1024 = 4194
    assert base["maintenance_work_mem"] == 41943  # mwm kB unit: 0.01 * 4G/1024 = 41943

    assert tuner.warm_start_provenance["enabled"] is True
    assert tuner.warm_start_provenance["num_warm_start_workers"] == 2
    assert tuner.warm_start_provenance["num_lhs_workers"] == 2

def test_warm_start_cross_tier_minimal_to_core(mock_knob_space, tmp_path, caplog):
    """Minimal config loaded on core tier fills missing knobs with LHS samples and warns."""
    warm_start_path = tmp_path / "best_config.json"
    # Minimal config: missing maintenance_work_mem
    warm_start_data = {
        "shared_buffers": 0.2,
        "work_mem": 0.01
    }
    with open(warm_start_path, 'w') as f:
        json.dump(warm_start_data, f)

    tuner = PBTTuner(
        knob_tier="core",
        pbt_config=PBTConfig(population_size=2, num_generations=1, num_parallel_workers=2),
        warm_start_path=str(warm_start_path),
        skip_schema_init=True,
    )
    tuner.knob_space = mock_knob_space

    configs = tuner._build_warm_start_configs(
        warm_start_path=warm_start_path,
        population_size=2,
        seed=42
    )
    base = configs[0]
    # Maintenance work mem should be filled by LHS
    assert "maintenance_work_mem" in base
    assert base["maintenance_work_mem"] >= mock_knob_space.knobs["maintenance_work_mem"].min_value
    assert "Warm-start config missing knobs" in caplog.text

def test_warm_start_cross_tier_core_to_minimal(mock_knob_space, tmp_path, caplog):
    """Core config loaded on minimal tier drops extra knobs and warns."""
    warm_start_path = tmp_path / "best_config.json"
    warm_start_data = {
        "shared_buffers": 0.2,
        "work_mem": 0.01,
        "maintenance_work_mem": 0.03,
        "extra_knob": 0.5
    }
    with open(warm_start_path, 'w') as f:
        json.dump(warm_start_data, f)

    tuner = PBTTuner(
        knob_tier="minimal",
        pbt_config=PBTConfig(population_size=2, num_generations=1, num_parallel_workers=2),
        warm_start_path=str(warm_start_path),
        skip_schema_init=True,
    )
    # Simulate a minimal tier by removing maintenance_work_mem from the mock space
    del mock_knob_space.knobs["maintenance_work_mem"]
    tuner.knob_space = mock_knob_space

    configs = tuner._build_warm_start_configs(
        warm_start_path=warm_start_path,
        population_size=2,
        seed=42
    )
    base = configs[0]
    assert "maintenance_work_mem" not in base
    assert "extra_knob" not in base
    assert "Warm-start config dropping extra knobs" in caplog.text

def test_warm_start_invalid_absolute_values(mock_knob_space, tmp_path):
    """Reject absolute values in hardware-relative knobs."""
    warm_start_path = tmp_path / "best_config.json"
    warm_start_data = {
        "shared_buffers": 65536, # absolute
        "work_mem": 0.01
    }
    with open(warm_start_path, 'w') as f:
        json.dump(warm_start_data, f)

    tuner = PBTTuner(
        knob_tier="minimal",
        pbt_config=PBTConfig(population_size=2, num_generations=1, num_parallel_workers=2),
        warm_start_path=str(warm_start_path),
        skip_schema_init=True,
    )
    tuner.knob_space = mock_knob_space

    with pytest.raises(ValueError, match="Warm-start config contains absolute value for hardware-relative knob"):
        tuner._build_warm_start_configs(
            warm_start_path=warm_start_path,
            population_size=2,
            seed=42
        )

def test_warm_start_graduated_perturbation():
    """Graduated perturbation scale correctly across variant span."""
    tuner = PBTTuner(
        knob_tier="minimal",
        pbt_config=PBTConfig(population_size=2, num_generations=1, num_parallel_workers=2),
        skip_schema_init=True,
    )
    p0 = tuner._compute_warm_start_perturbation_factors(0)
    assert p0 == []
    
    p1 = tuner._compute_warm_start_perturbation_factors(1)
    assert p1 == [(0.65, 1.35)]
    
    p4 = tuner._compute_warm_start_perturbation_factors(4)
    assert len(p4) == 4
    assert p4[0] == (0.8, 1.2)   # spread 0.20
    assert p4[-1] == (0.5, 1.5)  # spread 0.50

def test_warm_start_deterministic_seed(mock_knob_space, tmp_path):
    """Same seed outputs identical permutation, diff seeds diverge."""
    warm_start_path = tmp_path / "best_config.json"
    warm_start_data = {
        "shared_buffers": 0.2,
        "work_mem": 0.01,
        "maintenance_work_mem": 0.03
    }
    with open(warm_start_path, 'w') as f:
        json.dump(warm_start_data, f)

    tuner = PBTTuner(
        knob_tier="minimal",
        pbt_config=PBTConfig(population_size=4, num_generations=1),
        warm_start_path=str(warm_start_path),
        skip_schema_init=True,
    )
    tuner.knob_space = mock_knob_space

    c1 = tuner._build_warm_start_configs(warm_start_path, 4, seed=42)
    c2 = tuner._build_warm_start_configs(warm_start_path, 4, seed=42)
    c3 = tuner._build_warm_start_configs(warm_start_path, 4, seed=100)

    assert c1 == c2
    assert c1 != c3
