# Import Analysis

> 9 nodes · cohesion 0.33

## Key Concepts

- **_load_policy_module()** (6 connections) — `tests/unit/knobs/test_policy_loader.py`
- **test_policy_loader.py** (6 connections) — `tests/unit/knobs/test_policy_loader.py`
- **_stub_knob_metadata_dependency()** (3 connections) — `tests/unit/knobs/test_policy_loader.py`
- **test_policy_loader_accepts_raw_dict_shape()** (3 connections) — `tests/unit/knobs/test_policy_loader.py`
- **test_policy_loader_missing_file_raises_filenotfounderror()** (3 connections) — `tests/unit/knobs/test_policy_loader.py`
- **test_policy_loads_from_wrapped_json_with_expected_count_and_tuple_values()** (2 connections) — `tests/unit/knobs/test_policy_loader.py`
- **Tests for JSON-backed policy loading behavior.** (1 connections) — `tests/unit/knobs/test_policy_loader.py`
- **Provide minimal stubs so policy.py can import cleanly in isolation.** (1 connections) — `tests/unit/knobs/test_policy_loader.py`
- **Import policy.py in isolation while preserving current process module state.** (1 connections) — `tests/unit/knobs/test_policy_loader.py`

## Relationships

- [[Database Management]] (24 shared connections)
- [[BO Config & Worker]] (2 shared connections)

## Source Files

- `tests/unit/knobs/test_policy_loader.py`

## Audit Trail

- EXTRACTED: 24 (92%)
- INFERRED: 2 (8%)
- AMBIGUOUS: 0 (0%)

---

*Part of the graphify knowledge wiki. See [[index]] to navigate.*