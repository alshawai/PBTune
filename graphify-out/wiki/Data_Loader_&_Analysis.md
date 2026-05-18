# Data Loader & Analysis

> 24 nodes · cohesion 0.10

## Key Concepts

- **importance.py** (16 connections) — `src/analysis/importance.py`
- **load_importance()** (8 connections) — `src/visualization/loaders/importance.py`
- **ImportanceResult** (5 connections) — `src/analysis/importance.py`
- **_run_importance_pass()** (5 connections) — `src/analysis/importance.py`
- **load_importance_from_dir()** (5 connections) — `src/visualization/loaders/importance.py`
- **_ImportancePassResult** (4 connections) — `src/analysis/importance.py`
- **ImportanceData** (4 connections) — `src/visualization/loaders/importance.py`
- **_compute_rank_correlation()** (3 connections) — `src/analysis/importance.py`
- **_drop_zero_variance_columns()** (3 connections) — `src/analysis/importance.py`
- **_get_metadata_field()** (3 connections) — `src/analysis/importance.py`
- **_ensure_fanova_numpy_aliases()** (2 connections) — `src/analysis/importance.py`
- **Knob Importance Analysis ========================  Computes marginal and pairwis** (1 connections) — `src/analysis/importance.py`
- **Internal container for one importance decomposition pass.** (1 connections) — `src/analysis/importance.py`
- **Drop constant columns that cannot contribute to importance analysis.** (1 connections) — `src/analysis/importance.py`
- **Compute Spearman correlation between fANOVA and SHAP importance vectors.** (1 connections) — `src/analysis/importance.py`
- **Safely extract a string field from the first metadata entry.** (1 connections) — `src/analysis/importance.py`
- **Run one full SHAP + fANOVA decomposition pass.** (1 connections) — `src/analysis/importance.py`
- **Patch NumPy aliases expected by older fanova versions.** (1 connections) — `src/analysis/importance.py`
- **Container for fANOVA importance variance decomposition results.      Attributes** (1 connections) — `src/analysis/importance.py`
- **Load marginal importances from a CSV file.      Args:         csv_path: Path to** (1 connections) — `src/analysis/tier_generator.py`
- **Loader/Bridge for knob importance analysis results.** (1 connections) — `src/visualization/loaders/importance.py`
- **Visualization-ready format for importance results.** (1 connections) — `src/visualization/loaders/importance.py`
- **Bridge an ImportanceResult from src.analysis.importance into the     format need** (1 connections) — `src/visualization/loaders/importance.py`
- **Convenience function that loads JSONs, runs the fANOVA analysis,     and bridges** (1 connections) — `src/visualization/loaders/importance.py`

## Relationships

- [[Feature Scoring Docs]] (56 shared connections)
- [[Snapshot Integration]] (6 shared connections)
- [[Analysis Data Pipeline]] (3 shared connections)
- [[Instance Management]] (1 shared connections)
- [[Docker Manifest Tests]] (1 shared connections)
- [[BO Config & Worker]] (1 shared connections)
- [[TPC-H Loader & Data]] (1 shared connections)
- [[TPC-H Indexes & References]] (1 shared connections)
- [[Session Management]] (1 shared connections)

## Source Files

- `src/analysis/importance.py`
- `src/analysis/tier_generator.py`
- `src/visualization/loaders/importance.py`

## Audit Trail

- EXTRACTED: 64 (90%)
- INFERRED: 7 (10%)
- AMBIGUOUS: 0 (0%)

---

*Part of the graphify knowledge wiki. See [[index]] to navigate.*