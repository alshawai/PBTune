# Workload File Loading

> 12 nodes · cohesion 0.20

## Key Concepts

- **policy.py** (6 connections) — `src/knobs/policy.py`
- **ensure_autotuning_policy_annotations()** (5 connections) — `src/knobs/policy.py`
- **filter_tunable_knobs()** (5 connections) — `src/knobs/preprocess_knobs.py`
- **annotate_autotuning_policy()** (4 connections) — `src/knobs/policy.py`
- **apply_bounds_safety_gate()** (3 connections) — `src/knobs/policy.py`
- **_load_policy()** (2 connections) — `src/knobs/policy.py`
- **Shared policy engine for PostgreSQL autotuning knob admission and exclusion.** (1 connections) — `src/knobs/policy.py`
- **Load source exclusion policy from JSON while preserving tuple-based API.** (1 connections) — `src/knobs/policy.py`
- **Annotate source-stage autotuning eligibility and exclusion reasons.** (1 connections) — `src/knobs/policy.py`
- **Ensure policy columns are present without duplicating annotation passes.** (1 connections) — `src/knobs/policy.py`
- **Exclude uncurated knobs with INT_MAX-style max bounds.      Returns     -------** (1 connections) — `src/knobs/policy.py`
- **Filter to knobs that are actually tunable.      Criteria:     1. Marked as eligi** (1 connections) — `src/knobs/preprocess_knobs.py`

## Relationships

- [[Score Normalization Tests]] (26 shared connections)
- [[Drift Detection]] (3 shared connections)
- [[PostgreSQL Knob Retrieval]] (1 shared connections)
- [[TPC-H Schema & Tables]] (1 shared connections)

## Source Files

- `src/knobs/policy.py`
- `src/knobs/preprocess_knobs.py`

## Audit Trail

- EXTRACTED: 25 (81%)
- INFERRED: 6 (19%)
- AMBIGUOUS: 0 (0%)

---

*Part of the graphify knowledge wiki. See [[index]] to navigate.*