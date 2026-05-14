# Evolution Algorithms

> 24 nodes · cohesion 0.11

## Key Concepts

- **WorkerResources** (36 connections) — `src/utils/hardware_info.py`
- **test_hardware_normalization.py** (15 connections) — `tests/unit/config/test_hardware_normalization.py`
- **test_resolve_hardware_ranges_ram()** (4 connections) — `tests/unit/config/test_hardware_normalization.py`
- **test_worker_clone_memory_budget_repair()** (4 connections) — `tests/unit/config/test_hardware_normalization.py`
- **test_config_to_fractions_cpu_knob()** (3 connections) — `tests/unit/config/test_hardware_normalization.py`
- **test_fractions_to_config_units()** (3 connections) — `tests/unit/config/test_hardware_normalization.py`
- **test_perturbation_bound_exceed_repairs()** (3 connections) — `tests/unit/config/test_hardware_normalization.py`
- **test_worker_resources_creation()** (3 connections) — `tests/unit/config/test_hardware_normalization.py`
- **test_config_fractions_conversion_roundtrip()** (2 connections) — `tests/unit/config/test_hardware_normalization.py`
- **test_memory_budget_repair_ratios()** (2 connections) — `tests/unit/config/test_hardware_normalization.py`
- **test_repair_config_dependencies_triggers_budget()** (2 connections) — `tests/unit/config/test_hardware_normalization.py`
- **Unit tests for hardware normalization logic in the KnobSpace class.  These tests** (1 connections) — `tests/unit/config/test_hardware_normalization.py`
- **Test dynamic range resolution for CPU bounds.** (1 connections) — `tests/unit/config/test_hardware_normalization.py`
- **Test that config dependency repair respects total memory budget overrides.** (1 connections) — `tests/unit/config/test_hardware_normalization.py`
- **Test disk unknown resolution.** (1 connections) — `tests/unit/config/test_hardware_normalization.py`
- **Test unit conversions from fractions to absolute.** (1 connections) — `tests/unit/config/test_hardware_normalization.py`
- **Test that extreme memory budgets preserve relative ratios between knobs.** (1 connections) — `tests/unit/config/test_hardware_normalization.py`
- **Test Worker clone_from enforces bounds AND budget repair.** (1 connections) — `tests/unit/config/test_hardware_normalization.py`
- **Test that perturbations that exceed bounds are clamped properly.** (1 connections) — `tests/unit/config/test_hardware_normalization.py`
- **Test fractions extraction for a CPU relative knob.** (1 connections) — `tests/unit/config/test_hardware_normalization.py`
- **Test WorkerResources initialization bounds and constraints.** (1 connections) — `tests/unit/config/test_hardware_normalization.py`
- **test_memory_budget_repair_exceeds_budget()** (1 connections) — `tests/unit/config/test_hardware_normalization.py`
- **test_memory_budget_repair_within_budget()** (1 connections) — `tests/unit/config/test_hardware_normalization.py`
- **Per-worker hardware resources for hardware-aware knob ranges.** (1 connections) — `src/utils/hardware_info.py`

## Relationships

- [[Hardware Normalization Tests]] (60 shared connections)
- [[Comparison Runner]] (5 shared connections)
- [[Logger Colors Tests]] (3 shared connections)
- [[Hardware Detection & Info]] (3 shared connections)
- [[Population Tests]] (3 shared connections)
- [[Evaluation Statistics]] (2 shared connections)
- [[Logger Colors]] (2 shared connections)
- [[Session Management]] (2 shared connections)
- [[Population Initialization]] (1 shared connections)
- [[Performance Metrics]] (1 shared connections)
- [[Evaluation Types]] (1 shared connections)
- [[Docker Environment Management]] (1 shared connections)

## Source Files

- `src/utils/hardware_info.py`
- `tests/unit/config/test_hardware_normalization.py`

## Audit Trail

- EXTRACTED: 49 (54%)
- INFERRED: 41 (46%)
- AMBIGUOUS: 0 (0%)

---

*Part of the graphify knowledge wiki. See [[index]] to navigate.*