"""Tests for ConfigSpace constraints and logic."""

from src.tuner.config import get_knob_space
from src.scripts.bo_baseline.search_space import build_configspace


def test_wal_minimal_deactivates_archive_mode():
    """Test that wal_level=minimal deactivates archive_mode and related features."""
    knob_space = get_knob_space("extensive")
    cs = build_configspace(knob_space, seed=42)

    # Make sure both knobs are in the space
    assert "wal_level" in cs
    assert "archive_mode" in cs
    assert "max_wal_senders" in cs
    assert "summarize_wal" in cs

    # Sample 100 configurations and check the constraint
    for _ in range(100):
        config = cs.sample_configuration()
        if config.get("wal_level") == "minimal":
            # ConfigSpace returns None for inactive hyperparameters
            assert config.get("archive_mode") is None
            assert config.get("max_wal_senders") is None
            assert config.get("summarize_wal") is None


def test_huge_pages_sysv_forbidden():
    """Test that huge_pages=on|try is never sampled with shared_memory_type=sysv."""
    knob_space = get_knob_space("extensive")
    cs = build_configspace(knob_space, seed=42)

    assert "huge_pages" in cs
    assert "shared_memory_type" in cs

    # Sample 200 configurations and check the constraint
    for _ in range(200):
        config = cs.sample_configuration()
        hp = config.get("huge_pages")
        smt = config.get("shared_memory_type")
        assert not (hp in ("on", "try") and smt == "sysv")


def test_max_worker_processes_relation():
    """Test that max_worker_processes >= max_parallel_workers."""
    knob_space = get_knob_space("extensive")
    cs = build_configspace(knob_space, seed=42)

    assert "max_worker_processes" in cs
    assert "max_parallel_workers" in cs

    for _ in range(100):
        config = cs.sample_configuration()
        wp = config.get("max_worker_processes")
        pw = config.get("max_parallel_workers")
        assert wp >= pw


def test_wal_size_relation():
    """Test that min_wal_size <= max_wal_size."""
    knob_space = get_knob_space("extensive")
    cs = build_configspace(knob_space, seed=42)

    assert "min_wal_size" in cs
    assert "max_wal_size" in cs

    for _ in range(100):
        config = cs.sample_configuration()
        min_wal = config.get("min_wal_size")
        max_wal = config.get("max_wal_size")
        assert min_wal <= max_wal


def test_conditions_skip_when_knobs_absent():
    """Test that conditions are safely skipped when knobs are not in the tier."""
    # minimal tier does not contain wal_level or huge_pages
    knob_space = get_knob_space("minimal")

    # This should not raise any KeyError or ValueError
    cs = build_configspace(knob_space, seed=42)
    assert cs is not None
