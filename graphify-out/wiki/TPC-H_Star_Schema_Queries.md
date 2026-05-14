# TPC-H Star Schema Queries

> 11 nodes · cohesion 0.31

## Key Concepts

- **generate_tiers()** (13 connections) — `src/analysis/tier_generator.py`
- **test_tier_generator.py** (9 connections) — `tests/unit/analysis/test_tier_generator.py`
- **_build_importances()** (4 connections) — `tests/unit/analysis/test_tier_generator.py`
- **get_tier_names()** (4 connections) — `src/analysis/tier_generator.py`
- **test_all_equal_importances_fallback()** (3 connections) — `tests/unit/analysis/test_tier_generator.py`
- **test_bimodal_scores_split_into_two_tiers()** (3 connections) — `tests/unit/analysis/test_tier_generator.py`
- **test_optimal_k_selected_for_three_clusters()** (3 connections) — `tests/unit/analysis/test_tier_generator.py`
- **test_single_knob_single_tier()** (2 connections) — `tests/unit/analysis/test_tier_generator.py`
- **test_tier_names_for_k3_and_k4()** (2 connections) — `tests/unit/analysis/test_tier_generator.py`
- **Return tier labels for the requested tier count.      Args:         k: Number of** (1 connections) — `src/analysis/tier_generator.py`
- **Generate tier assignments from marginal importance scores.      Args:         ma** (1 connections) — `src/analysis/tier_generator.py`

## Relationships

- [[Metric Validation Docs]] (34 shared connections)
- [[Instance Management]] (6 shared connections)
- [[Query Pattern Analysis]] (4 shared connections)
- [[Docker Manifest Tests]] (1 shared connections)

## Source Files

- `src/analysis/tier_generator.py`
- `tests/unit/analysis/test_tier_generator.py`

## Audit Trail

- EXTRACTED: 35 (78%)
- INFERRED: 10 (22%)
- AMBIGUOUS: 0 (0%)

---

*Part of the graphify knowledge wiki. See [[index]] to navigate.*