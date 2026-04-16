---
name: knob-importance-analysis
description: >
  Post-hoc knob importance analysis using fANOVA and TreeSHAP, data-driven tier
  generation via Jenks Natural Breaks, and hardware-aware importance validation
  across multiple physical machines. Use this skill when working on knob importance
  analysis, fANOVA, SHAP values, tier generation, importance ranking, cross-hardware
  validation, or designing the analysis pipeline for determining which PostgreSQL
  knobs matter most for performance tuning.
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
Minimal (5) → Core (10) → Standard (20) → Extensive (40+) with boundaries
set by domain expertise. These serve as the default until data-driven tiers
are validated.

### Future State: Data-Driven Tiers
1. Run fANOVA importance analysis → sorted importance scores
2. Apply silhouette score across k = 2..6 to find optimal number of tiers
3. Apply Jenks Natural Breaks on importance scores for optimal boundaries
4. **Key insight:** Tier sizes are NOT fixed — data determines both the
   number of tiers and the membership cutoffs

```python
from jenkspy import JenksNaturalBreaks
from sklearn.metrics import silhouette_score

# Find optimal k
best_k = max(range(2, 7), key=lambda k: silhouette_score(
    importance_scores.reshape(-1, 1),
    JenksNaturalBreaks(k).fit(importance_scores).labels_
))

# Apply Jenks with optimal k
jnb = JenksNaturalBreaks(best_k)
jnb.fit(importance_scores)
tier_labels = jnb.labels_
```

## Two-Dimensional Analysis Architecture

### Dimension 1: Workload DEFINES Tiers
- Per-workload fANOVA ranking → Jenks clustering → tier definitions
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
| Analysis script | `src/scripts/analyze_knobs.py` (existing, basic) |
| Knob metadata | `src/knobs/knob_metadata.py` |
| Tier CSVs | `data/tuner_knobs/{tier}_knobs.csv` |
