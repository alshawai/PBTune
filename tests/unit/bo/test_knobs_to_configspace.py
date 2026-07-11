"""Tests for reverse mapping of knobs to ConfigSpace Configuration."""

from src.knobs import get_knob_space
from src.scripts.bo_baseline.search_space import (
    build_configspace,
    configspace_to_knobs,
    knobs_to_configspace,
)


def test_roundtrip_identity():
    """Test that config -> knobs -> config preserves values."""
    knob_space = get_knob_space("core")
    cs = build_configspace(knob_space, seed=42)

    config = cs.sample_configuration()
    knob_dict = configspace_to_knobs(config, knob_space)

    # Reverse mapping
    restored_config = knobs_to_configspace(knob_dict, knob_space, cs)

    # Values should be identical
    for hp_name in cs.keys():
        if config.get(hp_name) is not None:
            assert restored_config.get(hp_name) == config.get(hp_name)


def test_clamping_out_of_bounds():
    """Test that out-of-bounds values are clamped."""
    knob_space = get_knob_space("core")
    cs = build_configspace(knob_space, seed=42)

    config = cs.sample_configuration()
    knob_dict = configspace_to_knobs(config, knob_space)

    # Force a value out of bounds
    hp_name = "shared_buffers"
    if hp_name in knob_dict and hp_name in cs:
        hp = cs[hp_name]
        knob_dict[hp_name] = hp.upper + 1000

        restored_config = knobs_to_configspace(knob_dict, knob_space, cs)
        assert restored_config.get(hp_name) == hp.upper

        knob_dict[hp_name] = hp.lower - 1000
        restored_config = knobs_to_configspace(knob_dict, knob_space, cs)
        assert restored_config.get(hp_name) == hp.lower


def test_inactive_hp_handled():
    """Test that conditional hyperparameters are handled properly."""
    knob_space = get_knob_space("extensive")
    cs = build_configspace(knob_space, seed=42)

    # Sample until we get one where archive_mode is inactive
    config = None
    for _ in range(100):
        c = cs.sample_configuration()
        if c.get("wal_level") == "minimal":
            config = c
            break

    assert config is not None
    assert config.get("archive_mode") is None

    knob_dict = configspace_to_knobs(config, knob_space)

    # Simulate repair function setting archive_mode to off
    knob_dict["archive_mode"] = "off"

    # Reverse mapping should not crash and should produce a valid config
    restored_config = knobs_to_configspace(knob_dict, knob_space, cs)

    # Because of allow_inactive_with_values=True, we can create the config,
    # and the Configuration object will retain the value we passed in.
    assert restored_config.get("archive_mode") == "off"


def test_repaired_config_differs():
    """Test that repaired configs are successfully mapped back as different."""
    knob_space = get_knob_space("core")
    cs = build_configspace(knob_space, seed=42)

    config = cs.sample_configuration()
    original_dict = configspace_to_knobs(config, knob_space)

    # Make a copy and mutate it
    repaired_dict = dict(original_dict)

    hp_name = "shared_buffers"
    if hp_name in original_dict and hp_name in cs:
        hp = cs[hp_name]
        # Set to something else inside bounds
        new_val = hp.lower + (hp.upper - hp.lower) // 2
        # make sure it's actually different
        if new_val == original_dict[hp_name]:
            new_val = hp.lower
        repaired_dict[hp_name] = new_val

        restored_config = knobs_to_configspace(repaired_dict, knob_space, cs)
        assert restored_config != config
        assert restored_config.get(hp_name) == new_val
