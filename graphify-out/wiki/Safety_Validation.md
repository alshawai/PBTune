# Safety Validation

> 14 nodes · cohesion 0.33

## Key Concepts

- **analyze_knob_importance()** (18 connections) — `src/analysis/importance.py`
- **test_importance.py** (13 connections) — `tests/unit/analysis/test_importance.py`
- **create_mock_loaded_data()** (11 connections) — `tests/unit/analysis/test_importance.py`
- **test_insufficient_data()** (4 connections) — `tests/unit/analysis/test_importance.py`
- **test_config_space_uses_bounds()** (3 connections) — `tests/unit/analysis/test_importance.py`
- **test_correlation_warning()** (3 connections) — `tests/unit/analysis/test_importance.py`
- **test_dominant_knob_and_marginal_sum()** (3 connections) — `tests/unit/analysis/test_importance.py`
- **test_fanova_shap_correlation()** (3 connections) — `tests/unit/analysis/test_importance.py`
- **test_pairwise_interaction_top_k()** (3 connections) — `tests/unit/analysis/test_importance.py`
- **test_shap_global_importance()** (3 connections) — `tests/unit/analysis/test_importance.py`
- **test_shap_values_matrix_shape()** (3 connections) — `tests/unit/analysis/test_importance.py`
- **test_zero_variance_dropped()** (3 connections) — `tests/unit/analysis/test_importance.py`
- **Train a Random Forest and perform fANOVA decomposition to measure knob importanc** (1 connections) — `src/analysis/importance.py`
- **Tests for Knob Importance Analysis using fANOVA.** (1 connections) — `tests/unit/analysis/test_importance.py`

## Relationships

- [[Snapshot Integration]] (60 shared connections)
- [[Feature Scoring Docs]] (6 shared connections)
- [[Analysis Data Pipeline]] (4 shared connections)
- [[Session Management]] (1 shared connections)
- [[BO Config & Worker]] (1 shared connections)

## Source Files

- `src/analysis/importance.py`
- `tests/unit/analysis/test_importance.py`

## Audit Trail

- EXTRACTED: 51 (71%)
- INFERRED: 21 (29%)
- AMBIGUOUS: 0 (0%)

---

*Part of the graphify knowledge wiki. See [[index]] to navigate.*