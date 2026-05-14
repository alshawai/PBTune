# Database Management

> 8 nodes · cohesion 0.32

## Key Concepts

- **LoadedData** (12 connections) — `src/analysis/data_loader.py`
- **InsufficientDataError** (7 connections) — `src/analysis/importance.py`
- **MockFANOVA** (6 connections) — `tests/unit/analysis/test_importance.py`
- **MockTreeExplainer** (6 connections) — `tests/unit/analysis/test_importance.py`
- **Container for processed PBT results.      Attributes     ----------     config_d** (1 connections) — `src/analysis/data_loader.py`
- **Raised when there are fewer than 30 samples available for importance analysis.** (1 connections) — `src/analysis/importance.py`
- **.quantify_importance()** (1 connections) — `tests/unit/analysis/test_importance.py`
- **.shap_values()** (1 connections) — `tests/unit/analysis/test_importance.py`

## Relationships

- [[Analysis Data Pipeline]] (18 shared connections)
- [[Snapshot Integration]] (4 shared connections)
- [[DB Connection Reuse]] (3 shared connections)
- [[Feature Scoring Docs]] (3 shared connections)
- [[Cross-Module Rationale]] (2 shared connections)
- [[PostgreSQL Knob Tests]] (1 shared connections)
- [[TPC-H Loader & Data]] (1 shared connections)
- [[Evaluator Fault Injection]] (1 shared connections)
- [[Hardware Normalization Tests]] (1 shared connections)
- [[Bare Metal Memory Tests]] (1 shared connections)

## Source Files

- `src/analysis/data_loader.py`
- `src/analysis/importance.py`
- `tests/unit/analysis/test_importance.py`

## Audit Trail

- EXTRACTED: 17 (49%)
- INFERRED: 18 (51%)
- AMBIGUOUS: 0 (0%)

---

*Part of the graphify knowledge wiki. See [[index]] to navigate.*