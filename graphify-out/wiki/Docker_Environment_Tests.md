# Docker Environment Tests

> 25 nodes · cohesion 0.10

## Key Concepts

- **PBTConfig** (14 connections) — `src/tuner/config/tuner_config.py`
- **test_warm_start.py** (13 connections) — `tests/unit/core/test_warm_start.py`
- **test_warm_start_accepts_tuning_session_results_json()** (5 connections) — `tests/unit/core/test_warm_start.py`
- **test_warm_start_cross_tier_core_to_minimal()** (5 connections) — `tests/unit/core/test_warm_start.py`
- **test_warm_start_cross_tier_minimal_to_core()** (5 connections) — `tests/unit/core/test_warm_start.py`
- **test_warm_start_deterministic_seed()** (5 connections) — `tests/unit/core/test_warm_start.py`
- **test_warm_start_invalid_absolute_values()** (5 connections) — `tests/unit/core/test_warm_start.py`
- **test_warm_start_provenance()** (5 connections) — `tests/unit/core/test_warm_start.py`
- **test_warm_start_rejects_malformed_tuning_session_json()** (5 connections) — `tests/unit/core/test_warm_start.py`
- **patch_pbttuner_knob_loader()** (4 connections) — `tests/unit/core/test_warm_start.py`
- **test_warm_start_graduated_perturbation()** (4 connections) — `tests/unit/core/test_warm_start.py`
- **tuner_config.py** (3 connections) — `src/tuner/config/tuner_config.py`
- **num_workers_per_quantile()** (1 connections) — `src/tuner/config/tuner_config.py`
- **PBT Configuration Parameters ============================  This module defines t** (1 connections) — `src/tuner/config/tuner_config.py`
- **Configuration for Population Based Training algorithm.      Attributes     -----** (1 connections) — `src/tuner/config/tuner_config.py`
- **Unit tests for warm start functionality in the PBT tuner, focusing on RAM relati** (1 connections) — `tests/unit/core/test_warm_start.py`
- **Test PBTTuner warm start config structure from JSON and half split.** (1 connections) — `tests/unit/core/test_warm_start.py`
- **Minimal config loaded on core tier fills missing knobs with LHS samples and warn** (1 connections) — `tests/unit/core/test_warm_start.py`
- **Core config loaded on minimal tier drops extra knobs and warns.** (1 connections) — `tests/unit/core/test_warm_start.py`
- **Reject absolute values in hardware-relative knobs.** (1 connections) — `tests/unit/core/test_warm_start.py`
- **Patch PBTTuner init-time dependencies to avoid filesystem coupling in CI.** (1 connections) — `tests/unit/core/test_warm_start.py`
- **Warm-start should extract fractions from pbt_results best_configuration.knobs.** (1 connections) — `tests/unit/core/test_warm_start.py`
- **Malformed pbt_results payloads should fail fast with clear errors.** (1 connections) — `tests/unit/core/test_warm_start.py`
- **Graduated perturbation scale correctly across variant span.** (1 connections) — `tests/unit/core/test_warm_start.py`
- **Same seed outputs identical permutation, diff seeds diverge.** (1 connections) — `tests/unit/core/test_warm_start.py`

## Relationships

- [[Evolution Algorithms]] (62 shared connections)
- [[BO Baseline & Workload]] (8 shared connections)
- [[BO Config & Worker]] (7 shared connections)
- [[Metric Config Recalibration]] (2 shared connections)
- [[PBT Literature & Papers]] (1 shared connections)
- [[Population Initialization]] (1 shared connections)
- [[Snapshot & Persistence]] (1 shared connections)
- [[Population Tests]] (1 shared connections)
- [[Database Config & Connection]] (1 shared connections)
- [[Hardware Normalization Tests]] (1 shared connections)
- [[Logger Colors Tests]] (1 shared connections)

## Source Files

- `src/tuner/config/tuner_config.py`
- `tests/unit/core/test_warm_start.py`

## Audit Trail

- EXTRACTED: 52 (60%)
- INFERRED: 34 (40%)
- AMBIGUOUS: 0 (0%)

---

*Part of the graphify knowledge wiki. See [[index]] to navigate.*