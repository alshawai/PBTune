# PostgreSQL Knob Retrieval

> 32 nodes · cohesion 0.07

## Key Concepts

- **KnobSpace** (34 connections) — `src/tuner/config/knob_space.py`
- **._get_bytes_per_unit()** (5 connections) — `src/tuner/config/knob_space.py`
- **.get_knobs_by_category()** (5 connections) — `src/tuner/config/knob_space.py`
- **.fractions_to_config()** (4 connections) — `src/tuner/config/knob_space.py`
- **.config_to_fractions()** (3 connections) — `src/tuner/config/knob_space.py`
- **.create_online_view()** (3 connections) — `src/tuner/config/knob_space.py`
- **.resolve_hardware_ranges()** (3 connections) — `src/tuner/config/knob_space.py`
- **.__contains__()** (2 connections) — `src/tuner/config/knob_space.py`
- **.get_default_config()** (2 connections) — `src/tuner/config/knob_space.py`
- **.get_knob_names()** (2 connections) — `src/tuner/config/knob_space.py`
- **.get_restart_required_knobs()** (2 connections) — `src/tuner/config/knob_space.py`
- **.get_runtime_modifiable_knobs()** (2 connections) — `src/tuner/config/knob_space.py`
- **.__getitem__()** (2 connections) — `src/tuner/config/knob_space.py`
- **.__len__()** (2 connections) — `src/tuner/config/knob_space.py`
- **.split_config_by_restart_requirement()** (2 connections) — `src/tuner/config/knob_space.py`
- **Knob Space Definition for PostgreSQL Configuration Tuning ======================** (1 connections) — `src/tuner/config/knob_space.py`
- **Get default configuration (PostgreSQL defaults).          Returns         ------** (1 connections) — `src/tuner/config/knob_space.py`
- **Get list of all knob names** (1 connections) — `src/tuner/config/knob_space.py`
- **Get list of knob names in a specific category** (1 connections) — `src/tuner/config/knob_space.py`
- **Defines the search space for PostgreSQL knobs.      This class manages the colle** (1 connections) — `src/tuner/config/knob_space.py`
- **Return a filtered KnobSpace containing only runtime-safe knobs.          Filters** (1 connections) — `src/tuner/config/knob_space.py`
- **Parse unit string to bytes.** (1 connections) — `src/tuner/config/knob_space.py`
- **Override min/max for hardware-relative knobs using detected resources.** (1 connections) — `src/tuner/config/knob_space.py`
- **Convert absolute config values to fractional representation for serialization.** (1 connections) — `src/tuner/config/knob_space.py`
- **Convert fractional representation back to absolute values for this hardware.** (1 connections) — `src/tuner/config/knob_space.py`
- *... and 7 more nodes in this community*

## Relationships

- [[Knob Space Configuration]] (68 shared connections)
- [[Docker Volume Management]] (5 shared connections)
- [[Logger Colors Tests]] (4 shared connections)
- [[Logger Colors]] (3 shared connections)
- [[Cross-Module Rationale]] (2 shared connections)
- [[PBT Worker Core]] (2 shared connections)
- [[Population Initialization]] (1 shared connections)
- [[Metric Config Recalibration]] (1 shared connections)
- [[Hardware Normalization Tests]] (1 shared connections)
- [[Benchmark Validation Tests]] (1 shared connections)

## Source Files

- `src/knobs/retrieval.py`
- `src/tuner/config/knob_space.py`

## Audit Trail

- EXTRACTED: 82 (91%)
- INFERRED: 8 (9%)
- AMBIGUOUS: 0 (0%)

---

*Part of the graphify knowledge wiki. See [[index]] to navigate.*