# PBT Tuner Main & Config

> 47 nodes · cohesion 0.05

## Key Concepts

- **QuantileUtilityNormalizer** (33 connections)
- **test_normalizer.py** (10 connections) — `tests/unit/scoring/test_normalizer.py`
- **.fit()** (5 connections) — `src/utils/scoring/normalization.py`
- **._get_metric_direction()** (4 connections) — `src/utils/scoring/normalization.py`
- **.score_metric()** (4 connections) — `src/utils/scoring/normalization.py`
- **.score_vector()** (4 connections) — `src/utils/scoring/normalization.py`
- **test_normalizer_clipping()** (4 connections) — `tests/unit/scoring/test_normalizer.py`
- **test_normalizer_score_vector()** (4 connections) — `tests/unit/scoring/test_normalizer.py`
- **test_normalizer_state_serialization()** (4 connections) — `tests/unit/scoring/test_normalizer.py`
- **test_normalizer_uncalibrated_metric()** (4 connections) — `tests/unit/scoring/test_normalizer.py`
- **test_normalizer_update_and_drift()** (4 connections) — `tests/unit/scoring/test_normalizer.py`
- **.build_recalibration_dataset()** (3 connections) — `src/utils/scoring/normalization.py`
- **.detect_metric_saturation()** (3 connections) — `src/utils/scoring/normalization.py`
- **.update()** (3 connections) — `src/utils/scoring/normalization.py`
- **test_normalizer_initialization()** (3 connections) — `tests/unit/scoring/test_normalizer.py`
- **.clear_drift_events()** (2 connections) — `src/utils/scoring/normalization.py`
- **.expand_metric_anchor()** (2 connections) — `src/utils/scoring/normalization.py`
- **.export_state()** (2 connections) — `src/utils/scoring/normalization.py`
- **.get_drift_events()** (2 connections) — `src/utils/scoring/normalization.py`
- **.import_state()** (2 connections) — `src/utils/scoring/normalization.py`
- **.needs_recalibration()** (2 connections) — `src/utils/scoring/normalization.py`
- **.out_of_support_rate()** (2 connections) — `src/utils/scoring/normalization.py`
- **.record_drift_event()** (2 connections) — `src/utils/scoring/normalization.py`
- **Return out-of-support rate for one metric since last calibration.** (1 connections) — `src/utils/scoring/normalization.py`
- **Build a history-aware dataset for robust recalibration.          Combines curren** (1 connections) — `src/utils/scoring/normalization.py`
- *... and 22 more nodes in this community*

## Relationships

- [[Quantile Utility Normalizer]] (105 shared connections)
- [[DBGEN Setup & Build]] (11 shared connections)
- [[Evaluator Fault Injection]] (8 shared connections)
- [[BO Normalization Strategy]] (2 shared connections)
- [[Cross-Module Rationale]] (1 shared connections)
- [[Metric Instrumentation]] (1 shared connections)
- [[DB Connection Reuse]] (1 shared connections)
- [[Scoring Normalization Base]] (1 shared connections)
- [[TPC-H Star Schema Queries]] (1 shared connections)

## Source Files

- `src/utils/scoring/normalization.py`
- `tests/unit/scoring/test_normalizer.py`

## Audit Trail

- EXTRACTED: 103 (78%)
- INFERRED: 29 (22%)
- AMBIGUOUS: 0 (0%)

---

*Part of the graphify knowledge wiki. See [[index]] to navigate.*