---
name: knob-importance-analysis
description: >
  Post-hoc knob importance analysis using fANOVA and TreeSHAP, data-driven tier
  generation via SCALPEL (group-permutation BORUTA + Benjamini-Hochberg FDR gate,
  Lorenz coverage cuts, cluster-resampled stability), and hardware-aware importance
  validation across multiple physical machines. Use this skill when working on knob
  importance analysis, fANOVA, SHAP values, tier generation, importance ranking,
  cross-hardware validation, or designing the analysis pipeline for determining which
  PostgreSQL knobs matter most for performance tuning.
---

# Knob Importance Analysis

## Purpose

After collecting enough PBT experiment data, this pipeline answers: "Which knobs
actually matter for performance?" This drives the knob tier system — instead of
expert guesses, tiers are defined by measured importance.

## fANOVA Pipeline (Primary Method)

fANOVA (functional ANOVA) decomposes the variance of the scoring function into
contributions from individual knobs and their interactions.

### Steps
1. **Collect data:** Extract `(config, score)` pairs from extensive-tier PBT results
   - Each worker's config + score per generation = one data point
   - Aggregate across multiple multi-seed campaigns for reliability
   - Target: 1000+ pairs minimum for stable importance estimates
2. **Fit random forest:** Train on `config → score` mapping
3. **Decompose variance:** fANOVA partitions total variance into:
   - Per-knob marginal contributions (% of variance explained)
   - Pairwise interaction importance (knob A × knob B)
4. **Output:** Ranked importance table + interaction heatmap

### Dependencies
```
fanova    # Functional ANOVA (requires pyrfr backend)
pyrfr     # Random forest implementation required by fANOVA
```

### Key Implementation Detail
All knob values MUST be in fractional representation (not absolute values) because
the model needs hardware-independent features for cross-hardware comparison.

## TreeSHAP Pipeline (Complementary Method)

Uses the same random forest from fANOVA to compute Shapley values — a game-theoretic
measure of each knob's contribution to individual predictions.

### Why Both?
- **fANOVA** answers: "How much variance does this knob explain globally?"
- **SHAP** answers: "For this specific config, how much did each knob contribute?"
- Cross-validating rankings from both methods increases confidence

### Outputs
- **Beeswarm plot:** Global importance with per-config directionality
- **Dependence plots:** How each knob's value maps to its SHAP value
- **Ranking comparison:** Kendall's τ between fANOVA and SHAP rankings

### Dependency
```
shap      # SHAP values (TreeSHAP for random forests)
```

## Data-Driven Tier Generation

### Current State: Expert-Defined Tiers
Minimal (5) → Core (13) → Standard (43) → Extensive (170) with boundaries
set by domain expertise. These serve as the default until data-driven tiers
are validated.

### Data-Driven Tiers via SCALPEL
1. Run fANOVA + TreeSHAP importance analysis → fused, sorted importance scores
2. Confirm the signal-carrying knobs with a group-permutation BORUTA significance gate (Benjamini-Hochberg FDR, q = 0.10)
3. Partition the confirmed knobs with Lorenz coverage cuts (50 % / 80 % of cumulative importance), validated by a cluster-resampled stability pass
4. **Canonical Projection:** the export projects the resulting tiers onto the 4 canonical names (`minimal`, `core`, `standard`, `extensive`) expected by the tuner.
5. Export to `data/data_driven_knobs/{workload_type}/data_driven_tiers.json` via `--export-tiers [PATH]` option on the CLI.  Each workload writes to its own subdirectory, so runs for different workloads never overwrite each other.

SCALPEL is implemented and live in `src/analysis/scalpel.py` (+ `scalpel_significance.py`, `scalpel_stability.py`); it superseded the earlier Jenks Natural Breaks + silhouette pipeline. Tier slugs carry the `@scalpel-v1` suffix. See [ADR-005](../../../docs/architecture/decisions/ADR-005-scalpel-tier-generation.md).


## Two-Dimensional Analysis Architecture

### Dimension 1: Workload DEFINES Tiers
- Per-workload fANOVA ranking → SCALPEL tiering (BORUTA + BH-FDR gate → Lorenz coverage cuts → cluster-resampled stability) → tier definitions
- OLTP and OLAP WILL produce different tier memberships
- Example: `random_page_cost` may be critical for OLAP but irrelevant for OLTP

### Dimension 2: Hardware VALIDATES Stability
- Per-hardware fANOVA rankings compared via Kendall's τ
- Stable knobs (same tier across hardware) = high confidence
- Shifting knobs (different tier on different hardware) = hardware-dependent

### Conservative Safety Rule
A knob's final tier = **highest importance tier across any hardware**.
If `shared_buffers` is tier-1 on machine A but tier-2 on machine B,
it gets tier-1. This prevents accidentally excluding important knobs.

## Data Requirements

| Requirement | Value | Rationale |
|-------------|-------|-----------|
| Min data points | 1000+ pairs | Stable RF importance estimates |
| Same tier | All extensive | Consistent search space |
| Same scoring | Identical `MetricConfig` | Comparable scores |
| Knob representation | Fractional | Hardware-independent features |
| Per-worker logging | Required | Task 2.18 in work plan |

## Code Locations

| Component | File |
|-----------|------|
| Analysis script | `src/scripts/analyze_knob_importance.py` (full pipeline) |
| Knob metadata | `src/knobs/knob_metadata.py` |
| Tier CSVs | `data/expert_defined_knobs/{tier}_knobs.csv` or `data/data_driven_knobs/{workload_type}/{tier}_knobs.csv` |
| Tier export bridge | Run analysis script with `--export-tiers` to generate `data/data_driven_knobs/{workload_type}/data_driven_tiers.json` |
