# Metric Config Schema

> 18 nodes · cohesion 0.16

## Key Concepts

- **load_knob_space_from_csv()** (11 connections) — `src/tuner/config/knob_loader.py`
- **knob_loader.py** (10 connections) — `src/tuner/config/knob_loader.py`
- **load_knob_space_for_tier()** (5 connections) — `src/tuner/config/knob_loader.py`
- **_infer_integer_step()** (4 connections) — `src/tuner/config/knob_loader.py`
- **parse_enumvals()** (4 connections) — `src/tuner/config/knob_loader.py`
- **_resolve_numeric_bounds()** (4 connections) — `src/tuner/config/knob_loader.py`
- **csv_scale_to_knob_scale()** (3 connections) — `src/tuner/config/knob_loader.py`
- **csv_type_to_knob_type()** (3 connections) — `src/tuner/config/knob_loader.py`
- **_parse_numeric_bound()** (3 connections) — `src/tuner/config/knob_loader.py`
- **CSV-Based Knob Space Loader ============================  This module loads Knob** (1 connections) — `src/tuner/config/knob_loader.py`
- **Infer integer step alignment for knobs with discrete valid grids.** (1 connections) — `src/tuner/config/knob_loader.py`
- **Load KnobSpace from preprocessed CSV file.      Parameters     ----------     cs** (1 connections) — `src/tuner/config/knob_loader.py`
- **Load KnobSpace for a specific tier.      Parameters     ----------     tier : st** (1 connections) — `src/tuner/config/knob_loader.py`
- **Convert PostgreSQL vartype to KnobType enum** (1 connections) — `src/tuner/config/knob_loader.py`
- **Convert scale string to KnobScale enum** (1 connections) — `src/tuner/config/knob_loader.py`
- **Parse enumvals from CSV string** (1 connections) — `src/tuner/config/knob_loader.py`
- **Parse a numeric bound from CSV data if present.** (1 connections) — `src/tuner/config/knob_loader.py`
- **Resolve effective numeric bounds by intersecting curated and native limits.** (1 connections) — `src/tuner/config/knob_loader.py`

## Relationships

- [[Benchmark Validation Tests]] (48 shared connections)
- [[BO Config & Worker]] (4 shared connections)
- [[Session Management]] (2 shared connections)
- [[Logger Colors Tests]] (1 shared connections)
- [[Knob Space Configuration]] (1 shared connections)

## Source Files

- `src/tuner/config/knob_loader.py`

## Audit Trail

- EXTRACTED: 50 (89%)
- INFERRED: 6 (11%)
- AMBIGUOUS: 0 (0%)

---

*Part of the graphify knowledge wiki. See [[index]] to navigate.*