# Evolution Strategies

> 13 nodes · cohesion 0.27

## Key Concepts

- **test_knob_metadata_loader.py** (9 connections) — `tests/unit/knobs/test_knob_metadata_loader.py`
- **_load_metadata()** (8 connections) — `src/knobs/knob_metadata.py`
- **_load_knob_metadata_module()** (8 connections) — `tests/unit/knobs/test_knob_metadata_loader.py`
- **test_impact_tiers_derive_correctly_from_loaded_metadata()** (3 connections) — `tests/unit/knobs/test_knob_metadata_loader.py`
- **test_knob_tuning_metadata_loads_from_json_with_expected_count()** (3 connections) — `tests/unit/knobs/test_knob_metadata_loader.py`
- **test_missing_json_file_raises_actionable_filenotfounderror()** (3 connections) — `tests/unit/knobs/test_knob_metadata_loader.py`
- **test_round_trip_dict_json_loaded_dict_values_identical()** (3 connections) — `tests/unit/knobs/test_knob_metadata_loader.py`
- **test_tuning_metadata_fields_match_json_keys()** (3 connections) — `tests/unit/knobs/test_knob_metadata_loader.py`
- **test_get_knobs_by_tier_returns_same_derived_results()** (2 connections) — `tests/unit/knobs/test_knob_metadata_loader.py`
- **Load knob tuning metadata from JSON and coerce values to TuningMetadata.** (1 connections) — `src/knobs/knob_metadata.py`
- **Tests for JSON-backed knob metadata loading and tier derivation.** (1 connections) — `tests/unit/knobs/test_knob_metadata_loader.py`
- **Load knob_metadata.py directly to avoid package import side effects.** (1 connections) — `tests/unit/knobs/test_knob_metadata_loader.py`
- **Load canonical knob metadata JSON used by runtime loader tests.** (1 connections) — `tests/unit/knobs/test_knob_metadata_loader.py`

## Relationships

- [[Session Tests]] (42 shared connections)
- [[BO Config & Worker]] (2 shared connections)
- [[Knob Metadata]] (1 shared connections)
- [[Query Pattern Analysis]] (1 shared connections)

## Source Files

- `src/knobs/knob_metadata.py`
- `tests/unit/knobs/test_knob_metadata_loader.py`

## Audit Trail

- EXTRACTED: 44 (96%)
- INFERRED: 2 (4%)
- AMBIGUOUS: 0 (0%)

---

*Part of the graphify knowledge wiki. See [[index]] to navigate.*