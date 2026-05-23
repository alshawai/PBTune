## Overview

This document describes the knob importance analysis workflow used to derive
stable, hardware-aware tuning tiers. The approach combines fANOVA variance
attribution with TreeSHAP explanations and uses Jenks Natural Breaks to
translate continuous importance into discrete tiers.

## fANOVA Setup and Dependencies

The fANOVA implementation relies on `pyrfr`, a C++-backed Random Forest
library. The repository setup script handles SWIG, build tools, and the
`pyrfr` patching workflow for modern compilers.

### Recommended Setup

```bash
./setup.sh
```

The script enforces Python >=3.11,<3.14, checks for `swig` and `g++`, patches
`pyrfr` for modern GCC, and installs all Python dependencies. If you manage
your own environment, keep the same Python version range and ensure SWIG and
build tools are available.

### Analysis Modules

- `data_loader.py` loads multi-session JSON results, rescales metrics with
  global normalization, encodes categorical knobs, and resolves knob bounds
  with hardware-aware ranges when available.
- `importance.py` trains the Random Forest surrogate, runs fANOVA + TreeSHAP,
  computes pairwise interactions, and reports diagnostics such as R^2
  and fANOVA-SHAP rank correlation.
- `hardware_validator.py` groups results by hardware profile, computes
  Kendall's tau stability, derives conservative tiers, and can train a
  combined model with hardware features.
- `tier_generator.py` applies Jenks Natural Breaks with silhouette-based
  selection, reports agreement with expert tiers, and falls back to
  quantile breaks when `jenkspy` is unavailable.
- `__init__.py` exposes the analysis APIs for downstream scripts.

## Method Selection Rationale

The pipeline favors fANOVA and TreeSHAP because PostgreSQL knob effects are
non-linear and interaction-heavy.

- **Lasso regression** assumes a linear response surface, but knobs such as
  `work_mem` and `max_connections` interact to determine memory pressure.
- **PCA** loses knob identity; "component 3" does not tell us which knob to
  tune.
- **One-at-a-time ablation** ignores interactions and becomes exponentially
  expensive for pairwise testing.
- **Pearson or Spearman correlation** captures only pairwise effects and misses
  higher-order, non-linear interactions that tree ensembles model.

The architecture is explicitly two-dimensional:

- **Workload DEFINES tier boundaries** because OLTP and OLAP workloads produce
  different importance profiles.
- **Hardware VALIDATES stability** because rankings should remain consistent
  across machines for the same workload.

## fANOVA Internals

fANOVA trains a Random Forest surrogate model on knob configurations and their
scores, then applies functional ANOVA decomposition to attribute fractions of
output variance to individual knobs and their interactions. The result is a
variance-based importance score that quantifies how much each knob contributes
across the response surface [1].

## TreeSHAP Internals

TreeSHAP derives Shapley values from cooperative game theory and computes exact
attributions efficiently for tree ensembles. Local explanations for each
configuration are aggregated to global importance, revealing both main effects
and interaction-driven patterns in the Random Forest [2].

## Rescoring and Data Consistency

Raw JSON scores from tuning sessions are not stable because scoring ranges can
expand adaptively over time. The analysis pipeline therefore reloads raw
`PerformanceMetrics` and recomputes scores with consistent normalization
anchors so importance estimates are comparable across workers and sessions.

## Tier Boundary Derivation

Jenks Natural Breaks is applied as a one-dimensional optimization over the
importance distribution to produce discrete tier boundaries. The silhouette
score selects the best number of tiers. If the silhouette is weak or unstable,
expert-defined tiers are used as a fallback.

## Conservative Hardware Safety Rule

The final tier for each knob uses the most conservative assignment across
hardware profiles:

```
final_tier = max(tier_per_hardware)
```

This prevents a knob from being downgraded when it is critical on any target
machine.

## Combined RF Model With Hardware Features

A combined Random Forest is trained on the union of all hardware data. Hardware
features such as `ram_bytes`, `cpu_cores`, and `disk_type` are included to model
hardware-moderated effects. SHAP dependence plots highlight knobs whose
importance changes with these features.

## Data Pipeline

1. Extensive-tier PBT runs produce per-worker `(config, score)` pairs.
2. Knobs are fractional-normalized to a common scale.
3. The aggregated dataset trains a Random Forest surrogate.
4. fANOVA and TreeSHAP compute global importance.
5. Jenks Natural Breaks converts importance to tiers.

## References

- [1] F. Hutter, H. H. Hoos, and K. Leyton-Brown, "An efficient approach for
  assessing hyperparameter importance," in Proc. 31st Int. Conf. Machine
  Learning (ICML), 2014, pp. 754-762.
- [2] S. M. Lundberg and S.-I. Lee, "A unified approach to interpreting model
  predictions," in Proc. 31st Conf. Neural Information Processing Systems
  (NeurIPS), 2017, pp. 4765-4774.
