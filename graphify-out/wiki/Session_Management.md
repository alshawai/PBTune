# Session Management

> 23 nodes · cohesion 0.14

## Key Concepts

- **detect_worker_resources()** (14 connections) — `src/utils/hardware_info.py`
- **get_knob_space()** (13 connections) — `src/tuner/config/knob_loader.py`
- **_build_config_space()** (11 connections) — `src/analysis/importance.py`
- **TestSearchSpaceTranslation** (11 connections) — `tests/test_bo_baseline.py`
- **.test_configspace_to_knobs_conversion()** (7 connections) — `tests/test_bo_baseline.py`
- **.test_configspace_validation()** (6 connections) — `tests/test_bo_baseline.py`
- **.test_build_configspace_core()** (5 connections) — `tests/test_bo_baseline.py`
- **.test_build_configspace_minimal()** (5 connections) — `tests/test_bo_baseline.py`
- **.test_configspace_sampling_reproducibility()** (5 connections) — `tests/test_bo_baseline.py`
- **configspace_to_knobs()** (4 connections) — `src/scripts/bo_baseline/search_space.py`
- **search_space.py** (4 connections) — `src/scripts/bo_baseline/search_space.py`
- **Create ConfigSpace definitions matching encoded dataframe columns.** (1 connections) — `src/analysis/importance.py`
- **Search space translation between KnobSpace and ConfigSpace.** (1 connections) — `src/scripts/bo_baseline/search_space.py`
- **Convert a ConfigSpace Configuration back to a knob config dict.      Parameters** (1 connections) — `src/scripts/bo_baseline/search_space.py`
- **Translate KnobSpace into a ConfigSpace ConfigurationSpace.      Parameters     -** (1 connections) — `src/scripts/bo_baseline/search_space.py`
- **Get or load KnobSpace for a tier (cached).      Parameters     ----------     ti** (1 connections) — `src/tuner/config/knob_loader.py`
- **Test ConfigSpace translation.** (1 connections) — `tests/test_bo_baseline.py`
- **Test building ConfigSpace for minimal tier.** (1 connections) — `tests/test_bo_baseline.py`
- **Test building ConfigSpace for core tier.** (1 connections) — `tests/test_bo_baseline.py`
- **Test converting ConfigSpace config back to knob dict.** (1 connections) — `tests/test_bo_baseline.py`
- **Test that ConfigSpace sampling is reproducible with same seed.** (1 connections) — `tests/test_bo_baseline.py`
- **Test that sampled configs pass knob space validation.** (1 connections) — `tests/test_bo_baseline.py`
- **Detect per-worker hardware resources.      If in a container, uses cgroups via p** (1 connections) — `src/utils/hardware_info.py`

## Relationships

- [[Population Tests]] (6 shared connections)
- [[Hardware Detection & Info]] (4 shared connections)
- [[Cross-Module Rationale]] (2 shared connections)
- [[Benchmark Validation Tests]] (2 shared connections)
- [[BO Config & Worker]] (2 shared connections)
- [[Hardware Normalization Tests]] (2 shared connections)
- [[BO Baseline & Workload]] (1 shared connections)
- [[Feature Scoring Docs]] (1 shared connections)
- [[Snapshot Integration]] (1 shared connections)
- [[Workload Orchestrator]] (1 shared connections)
- [[PostgreSQL Knob Tests]] (1 shared connections)
- [[TPC-H Indexes & References]] (1 shared connections)

## Source Files

- `src/analysis/importance.py`
- `src/scripts/bo_baseline/search_space.py`
- `src/tuner/config/knob_loader.py`
- `src/utils/hardware_info.py`
- `tests/test_bo_baseline.py`

## Audit Trail

- EXTRACTED: 47 (48%)
- INFERRED: 50 (52%)
- AMBIGUOUS: 0 (0%)

---

*Part of the graphify knowledge wiki. See [[index]] to navigate.*