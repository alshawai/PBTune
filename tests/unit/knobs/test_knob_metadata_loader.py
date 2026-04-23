"""Tests for JSON-backed knob metadata loading and tier derivation."""

import dataclasses
import importlib.util
import json
import sys
from pathlib import Path

import pytest


MODULE_PATH = Path("src/knobs/knob_metadata.py")
DATA_PATH = Path("data/knob_metadata.json")
EXPECTED_METADATA_COUNT = 80


def _load_knob_metadata_module():
    """Load knob_metadata.py directly to avoid package import side effects."""
    module_name = "test_knob_metadata_module"
    spec = importlib.util.spec_from_file_location(module_name, MODULE_PATH)
    module = importlib.util.module_from_spec(spec)
    sys.modules[module_name] = module
    assert spec is not None and spec.loader is not None
    spec.loader.exec_module(module)
    return module


def _load_metadata_json() -> dict:
    """Load canonical knob metadata JSON used by runtime loader tests."""
    return json.loads(DATA_PATH.read_text(encoding="utf-8"))


def test_knob_tuning_metadata_loads_from_json_with_expected_count():
    module = _load_knob_metadata_module()
    metadata_json = _load_metadata_json()

    assert len(module.KNOB_TUNING_METADATA) == EXPECTED_METADATA_COUNT
    assert len(module.KNOB_TUNING_METADATA) == len(metadata_json)
    assert all(
        isinstance(value, module.TuningMetadata)
        for value in module.KNOB_TUNING_METADATA.values()
    )


def test_tuning_metadata_fields_match_json_keys():
    module = _load_knob_metadata_module()
    metadata_json = _load_metadata_json()
    dataclass_fields = set(module.TuningMetadata.__dataclass_fields__.keys())

    # Enforce schema parity so dataclass construction stays forward-compatible.
    for knob_name, knob_payload in metadata_json.items():
        assert set(knob_payload.keys()) == dataclass_fields, knob_name


def test_impact_tiers_derive_correctly_from_loaded_metadata():
    module = _load_knob_metadata_module()
    metadata_json = _load_metadata_json()

    expected_minimal = [
        k for k, v in metadata_json.items() if v["impact_tier"] == "minimal"
    ]
    expected_core = [
        k for k, v in metadata_json.items() if v["impact_tier"] in ("minimal", "core")
    ]
    expected_standard = [
        k
        for k, v in metadata_json.items()
        if v["impact_tier"] in ("minimal", "core", "standard")
    ]

    assert module.IMPACT_TIERS["minimal"] == expected_minimal
    assert module.IMPACT_TIERS["core"] == expected_core
    assert module.IMPACT_TIERS["standard"] == expected_standard
    assert module.IMPACT_TIERS["extensive"] is None


def test_get_knobs_by_tier_returns_same_derived_results():
    module = _load_knob_metadata_module()

    assert module.get_knobs_by_tier("minimal") == module.IMPACT_TIERS["minimal"]
    assert module.get_knobs_by_tier("core") == module.IMPACT_TIERS["core"]
    assert module.get_knobs_by_tier("standard") == module.IMPACT_TIERS["standard"]
    assert module.get_knobs_by_tier("MiNiMaL") == module.IMPACT_TIERS["minimal"]


def test_missing_json_file_raises_actionable_filenotfounderror(tmp_path: Path):
    module = _load_knob_metadata_module()
    missing_path = tmp_path / "missing_knob_metadata.json"

    with pytest.raises(
        FileNotFoundError, match="Knob metadata file not found"
    ) as exc_info:
        module._load_metadata(str(missing_path))

    message = str(exc_info.value)
    assert str(missing_path) in message
    assert "Generate it with the metadata export step" in message


def test_round_trip_dict_json_loaded_dict_values_identical(tmp_path: Path):
    module = _load_knob_metadata_module()

    # Convert dataclass instances to pure JSON-serializable dictionaries.
    original_dict = {
        knob: dataclasses.asdict(metadata)
        for knob, metadata in module.KNOB_TUNING_METADATA.items()
    }

    json_path = tmp_path / "round_trip_knob_metadata.json"
    json_path.write_text(json.dumps(original_dict, indent=2), encoding="utf-8")

    loaded_metadata = module._load_metadata(str(json_path))
    loaded_dict = {
        knob: dataclasses.asdict(metadata) for knob, metadata in loaded_metadata.items()
    }

    assert loaded_dict == original_dict
