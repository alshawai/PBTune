# Scoring Normalization Base

> 6 nodes · cohesion 0.33

## Key Concepts

- **iqr_filter()** (4 connections) — `src/utils/scoring/outlier_filtering.py`
- **.update_ranges()** (4 connections) — `src/utils/metrics.py`
- **outlier_filtering.py** (2 connections) — `src/utils/scoring/outlier_filtering.py`
- **IQR-based outlier filtering for normalization calibration.** (1 connections) — `src/utils/scoring/outlier_filtering.py`
- **Filter outliers using Interquartile Range (IQR) method.      Removes values outs** (1 connections) — `src/utils/scoring/outlier_filtering.py`
- **Update normalization ranges based on observed performance data.          This im** (1 connections) — `src/utils/metrics.py`

## Relationships

- [[DBGEN Setup & Build]] (11 shared connections)
- [[DB Connection Reuse]] (1 shared connections)
- [[Quantile Utility Normalizer]] (1 shared connections)

## Source Files

- `src/utils/metrics.py`
- `src/utils/scoring/outlier_filtering.py`

## Audit Trail

- EXTRACTED: 9 (69%)
- INFERRED: 4 (31%)
- AMBIGUOUS: 0 (0%)

---

*Part of the graphify knowledge wiki. See [[index]] to navigate.*