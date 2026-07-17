# SCALPEL Diagnostics Reference

> See also:
> [SCALPEL architecture](../architecture/scalpel.md),
> [SCALPEL rollout guide](../guides/scalpel-rollout.md),
> [ADR-005](../architecture/decisions/ADR-005-scalpel-tier-generation.md),
> [session JSON schema](session-json-schema.md).

This page documents every JSON field SCALPEL writes. Three files are
involved:

1. **`data/data_driven_knobs/<workload>/data_driven_tiers.json`** — the
   canonical tier file every downstream consumer reads. Schema is
   preserved from the pre-SCALPEL pipeline; only `metadata.*` gains
   new keys.
2. **`data/data_driven_knobs/<workload>/scalpel_diagnostics.json`** —
   optional sibling carrying full per-knob diagnostics. Written when
   the analyze CLI is invoked with `--algorithm scalpel` and the
   per-profile pipeline produces a non-degenerate result.
3. **`results/analysis/<workload>/importance_results.json`** — the
   importance + tier-generation block; the `tier_generation` slot
   gains a new `metadata` sub-block.

All file writes are atomic via `os.replace(<path>.tmp, <path>)`.

## `data_driven_tiers.json`

```json
{
  "metadata": {
    "workload_type": "oltp_read_write",
    "generated_at": "2026-06-18T20:01:41+00:00",
    "algorithm": "scalpel-v1",
    "scalpel_version": "1.0",
    "source_results": "results_temp/oltp/oltp_read_write/pbt_runs/extensive/tuning_sessions",
    "diagnostics": {
      "nuisance_dropped": ["array_nulls", "IntervalStyle", "..."],
      "oob_r2": 0.69,
      "n_confirmed": 1,
      "n_tentative": 2,
      "n_rejected": 141,
      "dba_prior_violations": ["shared_buffers", "work_mem", "..."],
      "lorenz_cutoffs": [0.5, 0.8],
      "boruta_iter": 100,
      "fdr_q": 0.10,
      "n_stability_subsamples": 100,
      "wall_clock_s": 305.83,
      "preflight_reason": null,
      "is_degenerate": false,
      "stable_knobs_semantics": "intersection_of_confirmed_sets"
    }
  },
  "tiers": {
    "minimal": ["default_statistics_target"],
    "core": [],
    "standard": [],
    "extensive": null
  }
}
```

### `metadata`

| Field | Type | Notes |
|---|---|---|
| `workload_type` | string | Granular workload label (e.g., `oltp_read_write`). Used by `preprocess_knobs` to resolve the output directory; required by every reader. |
| `generated_at` | ISO-8601 string (UTC) | When SCALPEL produced this file. |
| `algorithm` | `"scalpel-v1"` or `"lorenz_fallback"` | Distinguishes the full pipeline from the lossy fallback (used by `hardware_validator` cross-hardware exports). |
| `scalpel_version` | string (semver-ish) | Bumps when SCALPEL semantics change. `1.0` for the initial release. |
| `source_results` | string | Provenance — the directory the importance attribution was run against. |
| `diagnostics` | object | Pruned diagnostics block; full payload is in the sibling `scalpel_diagnostics.json`. See below. |

### `metadata.diagnostics`

The pruned block exists so reviewers can read it inline without
chasing a sibling file. It NEVER contains per-knob arrays — those live
in `scalpel_diagnostics.json`.

| Field | Type | Notes |
|---|---|---|
| `nuisance_dropped` | string[] | Knobs filtered out by `IMPORTANCE_NUISANCE_*` rules before modelling. |
| `oob_r2` | float \| null | Out-of-bag R² of the outer Random Forest surrogate. `null` when bootstrap was disabled. |
| `n_confirmed` | int | Count of knobs that beat the shadow null at BH-FDR `q`. |
| `n_tentative` | int | Knobs that did not reach the confirmation threshold and were not rejected. |
| `n_rejected` | int | Knobs significantly below chance. |
| `dba_prior_violations` | string[] | Expert-`minimal` knobs that did NOT land in data-driven `minimal`. Names only; full violation records are in the sibling file. |
| `lorenz_cutoffs` | float[2] | The `[coverage_minimal, coverage_core]` cuts used. Defaults to `[0.5, 0.8]`. |
| `boruta_iter` | int | Number of BORUTA iterations actually run. |
| `fdr_q` | float | BH-FDR target. |
| `n_stability_subsamples` | int | Layer 3 budget. |
| `wall_clock_s` | float | Full-pipeline wall-clock. |
| `preflight_reason` | string \| null | Set when SCALPEL rejected the input at the preflight gate (too few samples, too few clusters, too few features after nuisance filter). `null` on a successful run. |
| `is_degenerate` | bool | `true` iff the result carries no confirmed knobs. |
| `stable_knobs_semantics` | `"intersection_of_confirmed_sets"` \| `"intersection_of_labelled_sets"` | Reminds reviewers that under SCALPEL, `hardware_validation.stable_knobs` intersects *confirmed* sets across hardware profiles (not *labelled* sets, as it did under the legacy Lorenz fallback). |

### `tiers`

| Field | Type | Notes |
|---|---|---|
| `minimal` | string[] | Knobs whose cumulative renormalised mass over the confirmed subset is ≤ `coverage_minimal` (default 50%). Knob names are sorted ASC by name (not by importance). |
| `core` | string[] | Knobs whose cumulative renormalised mass is between `coverage_minimal` and `coverage_core` (default 80%). |
| `standard` | string[] | The rest of the BORUTA-confirmed subset. |
| `extensive` | `null` | Canonical sentinel meaning "all tunable knobs"; resolved at read time by [`get_knobs_by_tier`](../../src/knobs/knob_metadata.py). |

**Empty lists** are valid for `core` and `standard` (SCALPEL confirmed
few knobs). The downstream
[`preprocess_knobs`](../../src/knobs/preprocess_knobs.py) skips writing
CSVs for empty tiers, and
[`knob_loader`](../../src/knobs/knob_loader.py) walks down to
the next broader tier with a warning.

**Non-cumulative on disk.** A knob that lands in `minimal` does NOT
re-appear in `core` or `standard`. The cumulative semantics are
applied at read time by `get_knobs_by_tier(tier, source="data_driven")`
in [`src/knobs/knob_metadata.py`](../../src/knobs/knob_metadata.py),
which walks the canonical order and unions earlier tiers' knobs into
the requested tier. This preserves the pre-SCALPEL on-disk shape so
downstream consumers do not need a migration.

## `scalpel_diagnostics.json`

Written next to `data_driven_tiers.json` whenever the analyze CLI
calls `export_data_driven_tiers(..., write_diagnostics=True,
scalpel_result=…)`. The payload mirrors `SCALPELResult.diagnostics_full()`:

```json
{
  "workload_label": "oltp_read_write",
  "algorithm": "scalpel-v1",
  "scalpel_version": "1.0",
  "is_degenerate": false,
  "preflight_reason": null,
  "hyperparameters": {
    "rf_n_estimators": 500,
    "rf_max_features": "sqrt",
    "rf_min_samples_leaf": 3,
    "boruta_iter": 100,
    "fdr_q": 0.10,
    "coverage_minimal": 0.50,
    "coverage_core": 0.80,
    "n_stability_subsamples": 100,
    "stability_subsample_frac": 0.5,
    "min_samples": 200,
    "min_features": 2,
    "min_clusters": 4,
    "min_obs_per_cluster": 1,
    "seed": 2891133612,
    "workload_label": "oltp_read_write",
    "nuisance_overrides": []
  },
  "summary": { /* same shape as metadata.diagnostics in data_driven_tiers.json */ },
  "tier_assignments": {
    "default_statistics_target": "minimal"
  },
  "confirmed": ["default_statistics_target"],
  "tentative": ["autovacuum_vacuum_insert_scale_factor", "bgwriter_lru_multiplier"],
  "rejected": ["..."],
  "nuisance_dropped": ["array_nulls", "IntervalStyle", "..."],
  "full_importances": {
    "default_statistics_target": 0.04347,
    "autovacuum_vacuum_insert_scale_factor": 0.03442,
    "bgwriter_lru_multiplier": 0.01712,
    "..."
  },
  "confirmed_importances": {
    "default_statistics_target": 0.04347
  },
  "cumulative_coverage": {
    "default_statistics_target": 1.0
  },
  "lorenz_breakpoints": {
    "minimal": 1.0,
    "core": 1.0,
    "standard": 1.0
  },
  "boruta_hits": {
    "default_statistics_target": 100,
    "autovacuum_vacuum_insert_scale_factor": 53
  },
  "boruta_p_values": {
    "default_statistics_target": 0.0,
    "autovacuum_vacuum_insert_scale_factor": 0.41
  },
  "boruta_p_values_bh": {
    "default_statistics_target": 0.0,
    "autovacuum_vacuum_insert_scale_factor": 0.66
  },
  "stability_probabilities": {
    "default_statistics_target": 1.0,
    "bgwriter_lru_multiplier": 0.55
  },
  "stability_tier_distribution": {
    "default_statistics_target": {"minimal": 1.0}
  },
  "dba_prior_violations": [
    {
      "knob": "shared_buffers",
      "expert_tier": "minimal",
      "data_tier": "not_confirmed"
    },
    {
      "knob": "work_mem",
      "expert_tier": "minimal",
      "data_tier": "not_confirmed"
    }
  ],
  "diagnostics": {
    "oob_r2": 0.69,
    "wall_clock_s": 305.83,
    "n_samples": 2067,
    "n_features_after_nuisance": 144,
    "n_unique_clusters": 227,
    "n_stability_successful": 100
  }
}
```

### Per-knob coverage

The keys `confirmed`, `tentative`, `rejected`, `nuisance_dropped`
partition the input feature set: every knob the loader produced
belongs to exactly one of them. `tier_assignments` is a strict
**subset** of `confirmed` (or empty when the result is degenerate).

`full_importances` and `confirmed_importances`:

- `full_importances` covers every knob the outer RF saw (post-nuisance
  filter, pre-BORUTA gate). These are the raw fANOVA marginals on the
  shared outer surrogate — useful for the contamination-sensitivity
  check.
- `confirmed_importances` is the same dict restricted to the BORUTA-
  confirmed subset. Lorenz cuts are computed over its renormalised
  cumulative mass.

`cumulative_coverage` records, for each confirmed knob in
`(importance desc, name asc)` order, the cumulative renormalised mass
through and including that knob. `lorenz_breakpoints` then records the
coverage values at the tier boundaries; if SCALPEL never crosses
`coverage_minimal`, the `minimal` breakpoint equals 1.0 and every
confirmed knob lands in `minimal`.

`boruta_hits[k]` is the number of BORUTA iterations (out of
`hyperparameters.boruta_iter`) in which knob `k`'s RF importance
strictly exceeded `max(shadow_importance)`. `boruta_p_values[k]` is
the two-sided binomial p-value of that hit count against `p=0.5`;
`boruta_p_values_bh[k]` is the BH-FDR-adjusted p-value across the
full feature set.

`stability_probabilities[k]` is the max tier-assignment frequency
over the `n_stability_subsamples` cluster subsamples. Knobs that did
not get assigned in any subsample do not appear in either
`stability_probabilities` or `stability_tier_distribution`. Knobs
with `stability_probabilities[k] ≥ 0.80` are reportable as "stably
tiered" in paper tables; below 0.60 the boundary is unstable and the
knob should be flagged.

### DBA-prior violations

Each entry has `knob`, `expert_tier`, and `data_tier`:

- `expert_tier` is always `"minimal"` in v1 (we only audit against
  expert-`minimal` because that is the headline reviewer expectation).
- `data_tier` is either one of the canonical SCALPEL tiers or the
  sentinel `"not_confirmed"` when SCALPEL did not assign the knob to
  any tier.

The audit is **report-only** — SCALPEL never modifies its tier
assignments based on the prior. See the rationale in
[ADR-005](../architecture/decisions/ADR-005-scalpel-tier-generation.md#why-the-dba-prior-audit-is-report-only).

## `importance_results.json` (the `tier_generation` block)

The analyze CLI also writes a per-workload
`results/analysis/<workload>/importance_results.json`. Its
`tier_generation` block now carries SCALPEL metadata alongside the
legacy shape:

```json
{
  "tier_generation": {
    "metadata": {
      "algorithm": "scalpel-v1",
      "scalpel_version": "1.0",
      "diagnostics": { /* same pruned dict as data_driven_tiers.json */ }
    },
    "optimal_k": 4,
    "silhouette_scores": {},
    "tier_assignments": {
      "default_statistics_target": "minimal"
    },
    "jenks_breaks": [0.5, 0.8],
    "agreement_report": {
      "agreements": [],
      "promotions": [],
      "demotions": []
    },
    "workload_label": "oltp_read_write"
  }
}
```

- `optimal_k` is always `4` on a successful SCALPEL run and `1` in
  the degenerate path. The legacy `k ∈ {2, 3, 5, 6}` values from
  silhouette + Jenks no longer occur.
- `silhouette_scores` is always `{}`. The field is kept for JSON
  schema compatibility.
- `jenks_breaks` carries the Lorenz cutoffs `[coverage_minimal,
  coverage_core]`, defaulting to `[0.5, 0.8]`. The field is kept for
  JSON schema compatibility.
- `tier_assignments` contains **only** the knobs SCALPEL placed in
  `minimal`, `core`, or `standard`. Non-confirmed knobs are absent
  (canonical `extensive=null` in `data_driven_tiers.json`).
- `agreement_report` continues to compare against
  `EXPERT_TIER_ORDER`. Knobs absent from `tier_assignments` are
  silently skipped — they appear in `dba_prior_violations` via
  `metadata.diagnostics.dba_prior_violations` instead.

## Reading the output programmatically

Use [`load_tier_diagnostics`](../../src/visualization/loaders/tier_diagnostics.py)
to get a typed dataclass that handles both the SCALPEL primary path
and the legacy / Lorenz-fallback path uniformly:

```python
from src.visualization.loaders import load_tier_diagnostics

diag = load_tier_diagnostics(
    "results/analysis/oltp_read_write/importance_results.json"
)
print(diag.algorithm)                  # "scalpel-v1" or "legacy"
print(diag.scalpel_version)            # "1.0" or None
print(diag.tier_assignments)
print(diag.boruta_hits)                # {} on legacy / missing-sibling
print(diag.dba_prior_violations)
print(diag.lorenz_breakpoints)
```

The loader probes for the sibling `scalpel_diagnostics.json` next to
the supplied `importance_results.json` AND under
`data/data_driven_knobs/<workload>/`. Missing sibling → minimal
payload (only the `metadata.diagnostics` inline block is populated).
Missing primary `importance_results.json` → `FileNotFoundError`.

## Backwards compatibility

Pre-SCALPEL `importance_results.json` files (no
`tier_generation.metadata` block, `silhouette_scores` populated,
`jenks_breaks` carrying actual Jenks break values, `optimal_k ∈
{2,3,5,6}` possible) continue to load:

- The visualisation loader returns minimal payload
  (`algorithm = "legacy"`, no SCALPEL-specific fields).
- `_print_comparison_report` reads tier assignments through
  `.get(knob, "not_confirmed")` so absent-knob inputs do not crash.

No migration script is required. To regenerate every workload's
`data_driven_tiers.json` under SCALPEL, run the rollout command from
the [rollout guide](../guides/scalpel-rollout.md#all-workloads-in-one-shot).
