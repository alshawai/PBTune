# Sysbench Core

> 15 nodes · cohesion 0.14

## Key Concepts

- **KnobDefinition** (9 connections) — `src/tuner/config/knob_space.py`
- **mock_knob_space()** (7 connections) — `tests/unit/core/test_warm_start.py`
- **.sample_random_value()** (6 connections) — `src/tuner/config/knob_space.py`
- **._normalize_integer()** (4 connections) — `src/tuner/config/knob_space.py`
- **.sample_random_config()** (4 connections) — `src/tuner/config/knob_space.py`
- **.validate_value()** (3 connections) — `src/tuner/config/knob_space.py`
- **.validate_config()** (3 connections) — `src/tuner/config/knob_space.py`
- **Clamp and align integer values to the valid discrete grid.** (1 connections) — `src/tuner/config/knob_space.py`
- **Validate if a value is valid for this knob.          Parameters         --------** (1 connections) — `src/tuner/config/knob_space.py`
- **Sample a random valid value for this knob.          Parameters         ---------** (1 connections) — `src/tuner/config/knob_space.py`
- **Validate a configuration.          Parameters         ----------         config** (1 connections) — `src/tuner/config/knob_space.py`
- **Sample a random configuration.          Parameters         ----------         se** (1 connections) — `src/tuner/config/knob_space.py`
- **Definition of a single PostgreSQL configuration knob.      Attributes     ------** (1 connections) — `src/tuner/config/knob_space.py`
- **Create a mock KnobSpace with hardware-relative knobs for testing.** (1 connections) — `tests/unit/config/test_hardware_normalization.py`
- **Provides a mocked KnobSpace for testing warm starts with RAM relative specs.** (1 connections) — `tests/unit/core/test_warm_start.py`

## Relationships

- [[Logger Colors Tests]] (30 shared connections)
- [[Knob Space Configuration]] (4 shared connections)
- [[Docker Volume Management]] (4 shared connections)
- [[Hardware Normalization Tests]] (3 shared connections)
- [[Benchmark Validation Tests]] (1 shared connections)
- [[BO Config & Worker]] (1 shared connections)
- [[Evolution Algorithms]] (1 shared connections)

## Source Files

- `src/tuner/config/knob_space.py`
- `tests/unit/config/test_hardware_normalization.py`
- `tests/unit/core/test_warm_start.py`

## Audit Trail

- EXTRACTED: 37 (84%)
- INFERRED: 7 (16%)
- AMBIGUOUS: 0 (0%)

---

*Part of the graphify knowledge wiki. See [[index]] to navigate.*