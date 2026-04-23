"""Tests for JSON-backed policy loading behavior."""

import importlib.util
import json
import sys
import types
from contextlib import contextmanager
from pathlib import Path

import pytest


MODULE_PATH = Path("src/knobs/policy.py")
POLICY_JSON_PATH = Path("data/knob_policy.json")


@contextmanager
def _stub_knob_metadata_dependency():
    """Provide minimal stubs so policy.py can import cleanly in isolation."""
    target_names = ("src", "src.knobs", "src.knobs.knob_metadata")
    originals = {name: sys.modules.get(name) for name in target_names}

    src_module = types.ModuleType("src")
    knobs_module = types.ModuleType("src.knobs")
    knob_metadata_module = types.ModuleType("src.knobs.knob_metadata")
    knob_metadata_module.KNOB_TUNING_METADATA = {}

    src_module.knobs = knobs_module
    knobs_module.knob_metadata = knob_metadata_module

    sys.modules["src"] = src_module
    sys.modules["src.knobs"] = knobs_module
    sys.modules["src.knobs.knob_metadata"] = knob_metadata_module

    try:
        yield
    finally:
        for name, original in originals.items():
            if original is None:
                sys.modules.pop(name, None)
            else:
                sys.modules[name] = original


def _load_policy_module():
    """Import policy.py in isolation while preserving current process module state."""
    with _stub_knob_metadata_dependency():
        module_name = "test_policy_module"
        spec = importlib.util.spec_from_file_location(module_name, MODULE_PATH)
        module = importlib.util.module_from_spec(spec)
        sys.modules[module_name] = module
        assert spec is not None and spec.loader is not None
        spec.loader.exec_module(module)
        return module


def test_policy_loads_from_wrapped_json_with_expected_count_and_tuple_values():
    module = _load_policy_module()
    policy_json = json.loads(POLICY_JSON_PATH.read_text(encoding="utf-8"))
    expected = policy_json["AUTOTUNING_SOURCE_EXCLUSIONS"]

    assert len(module.AUTOTUNING_SOURCE_EXCLUSIONS) == len(expected)

    # Verify a known key round-trips to the tuple-based runtime contract.
    sample_key = "vacuum_cost_delay"
    assert sample_key in module.AUTOTUNING_SOURCE_EXCLUSIONS
    assert module.AUTOTUNING_SOURCE_EXCLUSIONS[sample_key] == tuple(
        expected[sample_key]
    )

    assert all(
        isinstance(value, tuple) and len(value) == 2
        for value in module.AUTOTUNING_SOURCE_EXCLUSIONS.values()
    )


def test_policy_loader_accepts_raw_dict_shape(tmp_path: Path):
    module = _load_policy_module()

    # Backward-compatible shape: plain map without wrapper key.
    raw_policy = {
        "foo_knob": ["reason_code", "reason detail"],
        "bar_knob": ["another_code", "another detail"],
    }
    raw_path = tmp_path / "raw_policy.json"
    raw_path.write_text(json.dumps(raw_policy, indent=2), encoding="utf-8")

    loaded = module._load_policy(str(raw_path))
    assert loaded == {
        "foo_knob": ("reason_code", "reason detail"),
        "bar_knob": ("another_code", "another detail"),
    }


def test_policy_loader_missing_file_raises_filenotfounderror(tmp_path: Path):
    module = _load_policy_module()
    missing_path = tmp_path / "missing_policy.json"

    with pytest.raises(FileNotFoundError):
        module._load_policy(str(missing_path))
