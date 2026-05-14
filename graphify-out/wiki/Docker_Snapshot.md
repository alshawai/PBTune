# Docker Snapshot

> 13 nodes · cohesion 0.19

## Key Concepts

- **.normalize_value()** (12 connections) — `src/tuner/config/knob_space.py`
- **.repair_config_dependencies()** (9 connections) — `src/tuner/config/knob_space.py`
- **.perturb_config()** (5 connections) — `src/tuner/config/knob_space.py`
- **.sample_diverse_configs()** (5 connections) — `src/tuner/config/knob_space.py`
- **._repair_memory_budget()** (4 connections) — `src/tuner/config/knob_space.py`
- **.get_normalized_features()** (4 connections) — `src/knobs/retrieval.py`
- **Normalize a candidate value into this knob's valid domain.** (1 connections) — `src/tuner/config/knob_space.py`
- **Proportionally scale down memory allocations to fit budget.** (1 connections) — `src/tuner/config/knob_space.py`
- **Repair configuration to satisfy known dependencies and constraints between knobs** (1 connections) — `src/tuner/config/knob_space.py`
- **Sample diverse configurations using Latin Hypercube Sampling (LHS).          LHS** (1 connections) — `src/tuner/config/knob_space.py`
- **Perturb a configuration (PBT exploration step).          For numerical knobs: Mu** (1 connections) — `src/tuner/config/knob_space.py`
- **Normalize a knob value to a standard unit (useful for ML).          Converts mem** (1 connections) — `src/knobs/retrieval.py`
- **Get normalized numeric features for ML models.          Returns         -------** (1 connections) — `src/knobs/retrieval.py`

## Relationships

- [[Docker Volume Management]] (30 shared connections)
- [[Knob Space Configuration]] (5 shared connections)
- [[Logger Colors Tests]] (4 shared connections)
- [[PostgreSQL Knob Retrieval]] (3 shared connections)
- [[BO Config & Worker]] (3 shared connections)
- [[Benchmark Orchestrator]] (1 shared connections)

## Source Files

- `src/knobs/retrieval.py`
- `src/tuner/config/knob_space.py`

## Audit Trail

- EXTRACTED: 42 (91%)
- INFERRED: 4 (9%)
- AMBIGUOUS: 0 (0%)

---

*Part of the graphify knowledge wiki. See [[index]] to navigate.*