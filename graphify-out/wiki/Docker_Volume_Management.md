# Docker Volume Management

> 12 nodes · cohesion 0.18

## Key Concepts

- **data_loader.py** (12 connections) — `src/database/data_loader.py`
- **_coerce_worker_resources()** (5 connections) — `src/analysis/data_loader.py`
- **_extract_knob_bounds()** (5 connections) — `src/analysis/data_loader.py`
- **_encode_dataframe_features()** (4 connections) — `src/analysis/data_loader.py`
- **_build_session_metadata()** (3 connections) — `src/analysis/data_loader.py`
- **test_encode_dataframe_features_maps_booleans_and_enums_correctly()** (2 connections) — `tests/unit/analysis/test_data_loader.py`
- **PBT Analysis Data Loader ========================  This module provides loaders** (1 connections) — `src/analysis/data_loader.py`
- **Normalize serialized worker resources into WorkerResources dataclass.** (1 connections) — `src/analysis/data_loader.py`
- **Determine continuous/discrete bounds for fANOVA ConfigSpace using KnobSpecs.** (1 connections) — `src/analysis/data_loader.py`
- **Build normalized metadata payload for one tuning session file.** (1 connections) — `src/analysis/data_loader.py`
- **Encode DataFrame configuration parameters inplace for ML compatibility.      Con** (1 connections) — `src/analysis/data_loader.py`
- **Data Loading Utilities =======================  Utilities for loading data from** (1 connections) — `src/database/data_loader.py`

## Relationships

- [[PostgreSQL Knob Tests]] (24 shared connections)
- [[TPC-H Loader & Data]] (5 shared connections)
- [[Worker Scoring Tests]] (3 shared connections)
- [[Hardware Normalization Tests]] (1 shared connections)
- [[BO Config & Worker]] (1 shared connections)
- [[Session Management]] (1 shared connections)
- [[TPC-H Schema & Tables]] (1 shared connections)
- [[Analysis Data Pipeline]] (1 shared connections)

## Source Files

- `src/analysis/data_loader.py`
- `src/database/data_loader.py`
- `tests/unit/analysis/test_data_loader.py`

## Audit Trail

- EXTRACTED: 32 (86%)
- INFERRED: 5 (14%)
- AMBIGUOUS: 0 (0%)

---

*Part of the graphify knowledge wiki. See [[index]] to navigate.*