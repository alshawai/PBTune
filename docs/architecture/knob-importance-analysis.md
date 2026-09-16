# Knob Importance Analysis

## Overview

This document describes the knob importance analysis workflow used to derive
stable, hardware-aware tuning tiers. The approach combines fANOVA variance
attribution with TreeSHAP explanations and uses SCALPEL (a BORUTA +
BH-FDR significance gate, Lorenz coverage cuts, and cluster-resampled
stability) to translate continuous importance into discrete tiers.

> See also: [Documentation Index](../README.md), [Configuration Management](configuration-management.md), [Visualization](../guides/visualization.md).

## Data Flow

```text
results/sessions/{workload}/pbt/extensive/traces/
    trace_*.json
                │
                ▼  data_loader.load_pbt_results(...)
                │  • parse session JSON (fixed_v1 + feature_driven_v2)
                │  • rescore raw PerformanceMetrics with global anchors
                │  • encode categorical knobs
                │  • resolve hardware-relative bounds when WorkerResources is present
                ▼
        (X, y)  ←  X: per-evaluation knob configurations (fractional or absolute)
                    y: rescored composite scores
                │
                ▼  importance.analyze_knob_importance(loaded_data)
                │  • Random Forest surrogate (default 400 trees)
                │  • report R²
                ▼
        forest, X, y
                │
                ├──► fANOVA marginals + interactions (internal to the call above)
                │       • per-knob variance attribution
                │       • pairwise interaction terms
                │
                ├──► TreeSHAP global + dependence (internal to the call above)
                │       • global SHAP importance (mean |φ_i|)
                │       • SHAP dependence values for plotting
                │
                ├──► fANOVA<->SHAP rank correlation (internal to the call above)
                │       • Spearman ρ as a method-agreement diagnostic
                │
                ▼
        ImportanceResult (per workload)
                │
                ├──► hardware_validator.validate_hardware_importance(profile_results)
                │       • per-knob ranking stability across machines
                │       • combined RF with hardware features (ram_bytes, cpu_cores, disk_type)
                │
                ▼
        tier_generator.export_data_driven_tiers(scalpel_result=…)
                │  • SCALPEL — see docs/architecture/scalpel.md
                │  • significance gate (BORUTA + BH-FDR) → coverage
                │    cuts (Lorenz @ 50 / 80 %) → cluster-resampled
                │    stability → DBA-prior audit → diagnostics
                │  • hardware_validator's combined-export path uses
                │    the lossy Lorenz fallback
                │    (lorenz_tier_from_importances) because it only
                │    has a precomputed importance dict
                ▼
        data/data_driven_knobs/{workload}/
            data_driven_tiers.json
            scalpel_diagnostics.json     # full per-knob payload
            minimal_knobs.csv
            core_knobs.csv
            standard_knobs.csv
            extensive_knobs.csv          # always present; non-extensive
                                         # CSVs are skipped when SCALPEL
                                         # confirmed nothing for them
                │
                ▼  load_data_driven_tiers() in src/knobs/knob_metadata.py
                ▼  get_knob_space(tier, source="data_driven", workload=...)
                │       walks DOWN the canonical order to the next
                │       broader tier whose CSV exists when SCALPEL
                │       left the requested tier empty
                ▼
        Next PBT/BO session uses the data-driven tiers
```

The pipeline is invoked end-to-end by [`python -m src.scripts.analyze_knob_importance`](../../src/scripts/analyze_knob_importance.py). Use `--export-tiers` to write the `data_driven_*` artifacts; without it the script only prints diagnostics and saves the importance JSON for visualization.

The tier-generation algorithm is [SCALPEL](scalpel.md). The legacy
silhouette + Jenks pipeline is gone; a thin Lorenz fallback remains
for the cross-hardware combined-export path that only retains a
precomputed importance dict. See
[ADR-005](decisions/ADR-005-scalpel-tier-generation.md) for the
failure mode the replacement addresses, and the
[rollout guide](../guides/scalpel-rollout.md) for the operator
playbook.

## `data_driven_tiers.json` Schema

For the canonical schema (including the new `metadata.algorithm`,
`metadata.scalpel_version`, and `metadata.diagnostics` fields), see
the [SCALPEL diagnostics reference](../reference/scalpel-diagnostics.md).
Below is the legacy schema captured pre-SCALPEL — every field that
follows is preserved on disk, and SCALPEL adds new ones under
`metadata`.

```json
{
  "workload_label": "oltp_read_write",
  "generated_at": "2026-05-30T18:42:11Z",
  "expert_tiers_baseline": {
    "minimal":  ["shared_buffers", "effective_cache_size", ...],
    "core":     [...],
    "standard": [...],
    "extensive": [...]
  },
  "data_driven_tiers": {
    "minimal":  ["shared_buffers", "work_mem", ...],
    "core":     [...],
    "standard": [...],
    "extensive": [...]
  },
  "agreement_with_expert": {
    "minimal":  0.83,
    "core":     0.71,
    "standard": 0.65,
    "extensive": 0.92
  },
  "importance_per_knob": {
    "shared_buffers": { "fanova": 0.214, "shap": 0.198, "rank": 1 },
    ...
  },
  "tier_generation": {
    "metadata": {
      "algorithm": "scalpel-v1",
      "scalpel_version": "1.0",
      "diagnostics": { "...": "BORUTA / BH-FDR / stability diagnostics" }
    },
    "optimal_k": 4,
    "silhouette_scores": {},
    "jenks_breaks": [0.5, 0.8],
    "tier_assignments": { "shared_buffers": "minimal" },
    "workload_label": "oltp_read_write"
  }
}
```

`optimal_k`, `silhouette_scores` and `jenks_breaks` are retained only for JSON
schema compatibility: under SCALPEL, `optimal_k` is always `4` (or `1` in the
degenerate path), `silhouette_scores` is always `{}`, and `jenks_breaks` now
carries the Lorenz cutoffs `[coverage_minimal, coverage_core]`. See
[scalpel-diagnostics](../reference/scalpel-diagnostics.md) for the full block.

The `agreement_with_expert` block reports the fraction of expert-tier members that survived into the data-driven tier of the same name. Low agreement on a tier is a signal that the expert categorisation may benefit from review for that workload — not that the data-driven tier is automatically correct. The conservative hardware safety rule is applied before this comparison.

## fANOVA dependency note

The fANOVA implementation relies on `pyrfr`, a C++-backed Random Forest library that requires SWIG and a C++ toolchain to build. For installation steps see [getting-started/setup](../getting-started/setup.md), which handles the SWIG / `pyrfr` patching workflow for modern GCC. The Python version requirement is the project-wide `>=3.11,<3.14`.

## Analysis modules

- `data_loader.py` loads multi-session JSON results, rescales metrics with
  global normalization, encodes categorical knobs, and resolves knob bounds
  with hardware-aware ranges when available.
- `importance.py` trains the Random Forest surrogate, runs fANOVA + TreeSHAP,
  computes pairwise interactions, and reports diagnostics such as R^2
  and fANOVA-SHAP rank correlation.
- `hardware_validator.py` groups results by hardware profile, computes
  Kendall's tau stability, derives conservative tiers, and can train a
  combined model with hardware features.
- `tier_generator.py` carries the legacy-shape `generate_tiers` wrapper
  (delegates to SCALPEL's Lorenz fallback for callers with only a
  precomputed importance dict) and the canonical
  `export_data_driven_tiers` writer.
- `scalpel.py`, `scalpel_significance.py`, `scalpel_stability.py`
  implement the full SCALPEL pipeline — see
  [scalpel.md](scalpel.md).
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

SCALPEL derives tier boundaries in three layers over the confirmed-knob
subset. A group-permutation BORUTA significance gate with a
Benjamini-Hochberg FDR correction (q = 0.10) first confirms which knobs
carry real signal. Lorenz coverage cuts (cumulative-importance
breakpoints at 50 % / 80 %) then partition the confirmed knobs into the
tuner's canonical 4-tier system (`minimal`, `core`, `standard`,
`extensive`), and a group-clustered stability pass re-runs BORUTA per
subsample to flag unstable assignments. The result is written to
`data/data_driven_knobs/{workload_type}/data_driven_tiers.json`. When
SCALPEL confirms nothing for a tier its CSV is skipped and callers fall
back to the next broader tier; if the data is too weak overall,
expert-defined tiers remain the fallback.

## Conservative Hardware Safety Rule

The final tier for each knob uses the most conservative assignment across
hardware profiles:

```text
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
5. SCALPEL converts importance to tiers (BORUTA + BH-FDR gate -> Lorenz coverage cuts -> cluster-resampled stability).
6. A second pass projects the importances onto canonical tiers and exports them to `data/data_driven_knobs/{workload_type}/data_driven_tiers.json` via `--export-tiers`.

## References

- [1] F. Hutter, H. H. Hoos, and K. Leyton-Brown, "An efficient approach for
  assessing hyperparameter importance," in Proc. 31st Int. Conf. Machine
  Learning (ICML), 2014, pp. 754-762.
- [2] S. M. Lundberg and S.-I. Lee, "A unified approach to interpreting model
  predictions," in Proc. 31st Conf. Neural Information Processing Systems
  (NeurIPS), 2017, pp. 4765-4774.
