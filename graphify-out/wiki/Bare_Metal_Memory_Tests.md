# Bare Metal Memory Tests

> 16 nodes · cohesion 0.17

## Key Concepts

- **preprocess_knobs.py** (10 connections) — `src/knobs/preprocess_knobs.py`
- **preprocess_and_save_knobs()** (8 connections) — `src/knobs/preprocess_knobs.py`
- **load_raw_knobs()** (6 connections) — `src/knobs/preprocess_knobs.py`
- **_clean_enumvals()** (4 connections) — `src/knobs/preprocess_knobs.py`
- **add_tuning_metadata()** (3 connections) — `src/knobs/preprocess_knobs.py`
- **create_tier_dataframes()** (3 connections) — `src/knobs/preprocess_knobs.py`
- **_log_source_policy_exclusions()** (3 connections) — `src/knobs/preprocess_knobs.py`
- **load_knobs_for_tier()** (2 connections) — `src/knobs/preprocess_knobs.py`
- **Knob Preprocessing for Tuner ============================  This module processes** (1 connections) — `src/knobs/preprocess_knobs.py`
- **Add tuning-specific metadata to knobs dataframe.      Parameters     ----------** (1 connections) — `src/knobs/preprocess_knobs.py`
- **Create separate dataframes for each impact tier.      Parameters     ----------** (1 connections) — `src/knobs/preprocess_knobs.py`
- **Complete preprocessing pipeline.      1. Load raw knobs     2. Add tuning metada** (1 connections) — `src/knobs/preprocess_knobs.py`
- **Load preprocessed knobs for a specific tier.      Parameters     ----------** (1 connections) — `src/knobs/preprocess_knobs.py`
- **Emit aggregated audit summary for source-stage policy exclusions.** (1 connections) — `src/knobs/preprocess_knobs.py`
- **Remove environment-specific aliases and unsafe OS constraints from enums.      T** (1 connections) — `src/knobs/preprocess_knobs.py`
- **Load raw knobs from CSV or database.      Parameters     ----------     csv_path** (1 connections) — `src/knobs/preprocess_knobs.py`

## Relationships

- [[Drift Detection]] (40 shared connections)
- [[Score Normalization Tests]] (3 shared connections)
- [[BO Config & Worker]] (2 shared connections)
- [[PostgreSQL Knob Retrieval]] (1 shared connections)
- [[TPC-H Schema & Tables]] (1 shared connections)

## Source Files

- `src/knobs/preprocess_knobs.py`

## Audit Trail

- EXTRACTED: 43 (91%)
- INFERRED: 4 (9%)
- AMBIGUOUS: 0 (0%)

---

*Part of the graphify knowledge wiki. See [[index]] to navigate.*