# SCALPEL — Tier Generation Architecture

> Last reviewed: 2026-06-18 · See also:
> [ADR-005](decisions/ADR-005-scalpel-tier-generation.md),
> [knob importance analysis](knob-importance-analysis.md),
> [SCALPEL rollout guide](../guides/scalpel-rollout.md),
> [SCALPEL diagnostics reference](../reference/scalpel-diagnostics.md).

SCALPEL — **S**ignificance-**C**overage-stability **A**lgorithm for
**L**ayered **PE**rformance-knob **L**abeling — is the tier-generation
pipeline that ships in [`src/analysis/scalpel.py`](../../src/analysis/scalpel.py).
It replaces a silhouette + Jenks Natural Breaks pipeline that
collapsed to two tiers on every realistic PBT trajectory and demoted
canonically important PostgreSQL knobs to the catch-all `extensive`
tier (see [ADR-005](decisions/ADR-005-scalpel-tier-generation.md) for
the failure mode in detail).

## Goals

1. Produce all four canonical tiers (`minimal`, `core`, `standard`,
   `extensive`) **non-empty** on real PBT data with a realistic budget.
2. Defend each tier assignment with **uncertainty** (BORUTA hit
   counts, BH-adjusted p-values, stability probabilities).
3. Filter display/auth/observability knobs **before** modelling so they
   cannot land in `minimal`/`core`.
4. Preserve the existing `data_driven_tiers.json` schema and the
   downstream API surface so PBT, BO, hardware validation, and the
   visualisation suite keep working.

## Pipeline overview

```text
LoadedData (config_df, scores, session_index, generation_index)
       │
       ▼
  scalpel_tier(X, y, sample_groups, hp, knob_metadata)
       │
       ├─ 1. Validate inputs (length match, finite y, alphabetised X)
       │
       ├─ 2. Nuisance filter
       │       drops display / auth / observability knobs by exact
       │       match (IMPORTANCE_NUISANCE_EXCLUSIONS) and prefix
       │       (IMPORTANCE_NUISANCE_PREFIXES). Operator overrides
       │       opt knobs back in via hp.nuisance_overrides.
       │
       ├─ 3. Preflight
       │       returns a degraded SCALPELResult (no exception) if
       │       n_samples < hp.min_samples, p < hp.min_features, or
       │       fewer than hp.min_clusters distinct clusters.
       │
       ├─ 4. Outer RF surrogate (n_estimators=500 by default,
       │       max_features='sqrt', oob_score=True). The same RF
       │       feeds Layer 1's hit-count test AND Layer 2's fANOVA.
       │
       ├─ 5. LAYER 1 — Significance gate (BORUTA + BH-FDR)
       │       n_iter rounds of:
       │         • build a shadow matrix by within-cluster column
       │           permutation (sample_groups → group_codes)
       │         • fit RF on [X | shadow]; record per-real-knob
       │           "hit" if importance > max(shadow_importance)
       │       Aggregate: per-knob two-sided binomial test against
       │       p=0.5 → BH-FDR adjustment at q=0.10 across all p
       │       knobs → confirmed / tentative / rejected.
       │
       ├─ 6. LAYER 2 — fANOVA marginals
       │       Run on the FULL cleaned feature set (NOT confirmed
       │       only — that would be circular analysis). Marginals
       │       are then renormalised within the confirmed subset
       │       in step 7.
       │
       ├─ 7. Lorenz cumulative-mass tiering
       │       Sort confirmed knobs by (importance desc, name asc).
       │       Walk cumulative mass over sum_{k in confirmed} imp[k]:
       │         coverage ≤ 0.50 → minimal
       │         coverage ≤ 0.80 → core
       │         remaining confirmed → standard
       │       Non-confirmed knobs are NOT assigned a tier
       │       (canonical extensive=null in JSON contract).
       │
       ├─ 8. LAYER 3 — Group-clustered stability
       │       B subsamples of CLUSTERS (50% rate by default).
       │       Each subsample regenerates its own shadow features
       │       and re-runs Layers 1+2 from scratch. Per-knob:
       │       stability_probability = max(tier-frequency).
       │
       ├─ 9. DBA-prior audit (report-only)
       │       Flag every expert-minimal knob whose data tier is
       │       NOT minimal. Never modifies tier_assignments.
       │
       ├─ 10. Assemble diagnostics
       │       (full_importances, confirmed_importances,
       │       cumulative_coverage, lorenz_breakpoints,
       │       boruta_hits, boruta_p_values_bh,
       │       stability_probabilities, dba_prior_violations,
       │       wall_clock_s, oob_r2, …)
       │
       └─ 11. SCALPELResult
                .to_tier_result(workload_label) → legacy TierResult
                  for hardware_validator + analyze_knob_importance
                .diagnostics_full() → full sibling JSON payload
                .diagnostics_pruned() → small payload embedded into
                  data_driven_tiers.json/metadata.diagnostics
```

## Module layout

```text
src/analysis/
├── scalpel.py                # SCALPELHyperparameters, SCALPELResult,
│                              scalpel_tier, lorenz_tier_from_importances
├── scalpel_significance.py    # BorutaResult, boruta_with_group_perm,
│                              _fit_outer_rf, _bh_adjust
├── scalpel_stability.py       # apply_nuisance_filter,
│                              compute_fanova_marginals,
│                              assign_lorenz_tiers,
│                              group_clustered_stability,
│                              audit_dba_prior
└── tier_generator.py          # legacy-shape generate_tiers shim →
                                lorenz_tier_from_importances; carries
                                export_data_driven_tiers + agreement
                                report.

src/visualization/
├── loaders/tier_diagnostics.py
└── plots/tier_diagnostics.py  # registers `tier_diagnostics` figure
                                under the existing 'importance' category.

data/knob_policy.json
├── AUTOTUNING_SOURCE_EXCLUSIONS  (existing — runtime tuning gate)
├── IMPORTANCE_NUISANCE_EXCLUSIONS (new — exact knob names)
└── IMPORTANCE_NUISANCE_PREFIXES   (new — knob-name prefixes)
```

## Two entry points

There are two callable surfaces, intentionally:

1. **`scalpel.scalpel_tier(X, y, *, sample_groups, hp, knob_metadata)`** —
   the full pipeline. Used by
   [`src/scripts/analyze_knob_importance.py`](../../src/scripts/analyze_knob_importance.py)
   per hardware profile when `--algorithm scalpel` is set (the default).
2. **`scalpel.lorenz_tier_from_importances(marginal_importances, workload_label)`** —
   coverage-only Lorenz fallback used when only a precomputed
   importance dict is available (no raw `(X, y)`). The legacy
   [`tier_generator.generate_tiers`](../../src/analysis/tier_generator.py)
   API delegates here. The combined-hardware export from
   [`hardware_validator`](../../src/analysis/hardware_validator.py)
   takes this path because it has already collapsed `(X, y)` to a
   single importance dict by the time it would call generate_tiers.

The Lorenz fallback is **lossy**: no significance gate, no stability
audit. It is documented as such in code and in the diagnostics
metadata (`metadata.diagnostics.source = "lorenz_fallback"`).

## Key design decisions

### Why BORUTA with group permutation, not raw permutation importance

PBT samples are explicitly non-i.i.d.: exploit copies a parent
worker's config to a child worker, and explore perturbs that copy by
±20 %. Within a generation, workers are correlated; across generations,
the population's distribution drifts toward the optimum.

Row-level shadow permutation produces an anti-conservative null —
within-cluster correlations leak into the shadow distribution and
inflate the apparent significance of mediocre knobs. SCALPEL builds
shadows by permuting **within** the composite
`session_index × generation_index` cluster, which preserves the local
correlation structure inside each cluster and only randomises across
clusters where independence is closer to true. Singleton clusters
(every row in its own group) fall back to global shuffles with a
logged warning.

### Why BH-FDR at q=0.10, not Bonferroni

The original BORUTA paper recommends Bonferroni across iterations.
With p = 179 knobs, that produces a punishingly conservative gate;
adversarial review showed BH-FDR across **knobs** (not iterations) at
q=0.10 keeps the *all-relevant* set stable while not collapsing to
zero confirmed.

### Why fANOVA on the full cleaned set, then renormalise

The v0 design (now rejected) ran fANOVA *only* on the BORUTA-confirmed
subset to keep the Lorenz mass interpretable. That is a textbook
circular analysis — the same RF that selected the features attributes
their importance, and the marginals are inflated.

SCALPEL fits the outer RF once on the FULL cleaned feature set,
runs fANOVA on that single fit, then renormalises the cumulative mass
within the confirmed subset for the tier-assignment cuts. The
non-confirmed knobs still influence the surrogate's tree splits (they
participate in candidate-feature draws), but they cannot inflate the
confirmed knobs' relative ranking. We surface a
`diagnostics.contamination_sensitivity_rho` slot for paper reviewers
who want to verify this empirically by refitting RF on confirmed-only
and checking Spearman ρ vs. the primary marginals.

### Why stability subsamples cluster, not rows

Same principle as group-permutation BORUTA. A 50 % row-subsample of a
PBT trace stays correlated with the full trace because clusters are
preserved; the resulting stability score is anti-conservatively
inflated. Subsampling at the cluster level (drop half the
session × generation tuples wholesale) gives a stability score that
actually answers "would this tier hold up if we reran PBT?".

### Why the nuisance filter, and why it is a hard prerequisite

Knobs like `array_nulls`, `IntervalStyle`, `bytea_output`,
`syslog_facility`, and the `track_*`/`log_*` families are not real
performance knobs — they are display, observability, or auth flags.
On the `oltp_read_write` workload, several of them landed in the
top 20 of fANOVA marginal importance under the legacy pipeline,
purely because BORUTA noticed they correlated with score variance
across sessions. Any tiering algorithm that does not filter them in
advance will produce headline tiers a DBA reviewer rejects.

The exclusion + prefix lists live in
[`data/knob_policy.json`](../../data/knob_policy.json) and are
loadable by [`src/knobs/policy.py`](../../src/knobs/policy.py).
Operators can opt a knob back in via `hp.nuisance_overrides` (or the
`--scalpel-nuisance-overrides` CLI flag) when investigating a
specific workload — e.g., `default_toast_compression` is conservatively
excluded but may matter on OLAP. The filter is logged in the run
diagnostics so the reviewer always sees what was removed.

### Why the DBA-prior audit is report-only

A DBA reviewer expects to see `shared_buffers`, `work_mem`,
`effective_cache_size` in `minimal`. SCALPEL routinely reports them
in `not_confirmed` on PBT traces — not because they are unimportant in
general, but because PBT converges on a near-optimal value early and
then perturbs it within a tight band, leaving very little marginal
variance for the surrogate to attribute.

We could enforce the DBA prior (force expert-minimal knobs into
`minimal`), but that defeats the point of a data-driven tier. Instead
we report every violation alongside the tier output. The paper
methodology section then has two principled responses:

1. Justify the demotion empirically (e.g., a tier-level utility
   ablation showing PBT performs equally well without the demoted
   knob in the search subset), or
2. Acknowledge the limitation and use a dedicated LHS/Sobol design
   for importance attribution rather than reusing the PBT trace.

The audit makes both responses possible. Silently enforcing the prior
would make neither.

### Why per-workload seeds

`--all-workloads` discovers every `tuning_sessions/` directory under
a results root and runs SCALPEL on each. With a global seed, every
workload would inherit the **same** RF / BORUTA shadow draws, which
inflates apparent inter-workload tier agreement and produces a
correlated set of "results" that look more confirmatory than they
are. SCALPEL derives a per-workload seed as
`hash((args.scalpel_base_seed, workload_label)) & 0xFFFFFFFF`, so
each workload gets independent randomness.

### Why @scalpel-v1 in tier paths for data-driven runs

A `core` PBT run on a pre-SCALPEL `data_driven_tiers.json` contained
a different set of knobs than a post-SCALPEL `core` run. Aggregating
both via [`load_pbt_results`](../../src/analysis/data_loader.py)
raises "Knob set mismatch detected" mid-load.

For `--knob-source data_driven`, the tuner and BO baseline now
suffix the tier slug with `@scalpel-v1` so post-SCALPEL artifacts
land at `results/<workload>/pbt_runs/core@scalpel-v1/` while
legacy ones stay at `results/<workload>/pbt_runs/core/`. Expert-
source paths are unchanged. The version slug propagates naturally
when SCALPEL bumps to v2 in the future.

## Hyperparameter defaults

See [`SCALPELHyperparameters`](../../src/analysis/scalpel.py).
The defaults below are tuned for a typical PBT trace at
n ≈ 2 000 / p ≈ 180:

| Parameter | Default | Why |
|---|---|---|
| `rf_n_estimators` | 500 | Enough for stable fANOVA + BORUTA on ~180 features. |
| `rf_max_features` | `"sqrt"` | Standard RF choice for high-dimensional regression. |
| `rf_min_samples_leaf` | 3 | Smooths fANOVA on PBT samples that cluster around the optimum. |
| `boruta_iter` | 100 | BORUTA convergence floor for ~180 features. |
| `fdr_q` | 0.10 | Plan default; tightens the all-relevant set without collapsing it. |
| `coverage_minimal` | 0.50 | Pareto / Juran "vital few" cut. |
| `coverage_core` | 0.80 | 80/20 cut on the confirmed subset. |
| `interaction_alpha` | 0.5 | Fused-signal weight: Lorenz input is `marginal + alpha * max_interaction`. |
| `interaction_top_k` | 20 | Search frontier for pairwise interactions (caps cost at O(K * p)). |
| `n_stability_subsamples` | 50 | Layer 3 budget (Meinshausen & Bühlmann 2010, lower end of B ∈ [50, 100]). |
| `stability_subsample_frac` | 0.5 | Drop half the clusters per subsample. |
| `stability_boruta_iter` | `None` → falls through to `boruta_iter` | BORUTA iter count *inside* each stability subsample. Match the primary or BH-FDR null is mis-calibrated. |
| `stability_jobs` | 4 | Parallel `ProcessPoolExecutor` workers for stability subsamples. |
| `min_samples` | 200 | Below this the surrogate is unstable. |
| `min_clusters` | 4 | Cluster-permutation null is degenerate below this. |
| `seed` | 42 (per-workload-derived) | See "per-workload seeds" above. |

A budget-constrained smoke run can drop `boruta_iter` to ~30 and
`n_stability_subsamples` to ~10 (see the
[rollout guide](../guides/scalpel-rollout.md) for examples).

## Output schema

`data_driven_tiers.json` (the file every downstream consumer reads):

```json
{
  "metadata": {
    "workload_type": "oltp_read_write",
    "generated_at": "2026-06-18T20:01:41+00:00",
    "algorithm": "scalpel-v1",
    "scalpel_version": "1.0",
    "source_results": "results_temp/.../tuning_sessions",
    "diagnostics": {
      "nuisance_dropped": ["array_nulls", "IntervalStyle", "..."],
      "oob_r2": 0.69,
      "n_confirmed": 1,
      "n_tentative": 2,
      "n_rejected": 141,
      "dba_prior_violations": ["shared_buffers", "work_mem", "..."],
      "lorenz_cutoffs": [0.5, 0.8],
      "boruta_iter": 30,
      "fdr_q": 0.10,
      "n_stability_subsamples": 10,
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

`scalpel_diagnostics.json` (sibling, written when
`write_diagnostics=True`): full per-knob payload with BORUTA hits,
BH-adjusted p-values, stability probabilities, full + confirmed
importances, cumulative coverage curve, Lorenz breakpoints, DBA-prior
violations, hyperparameters used, and the wall-clock breakdown.
See the [SCALPEL diagnostics reference](../reference/scalpel-diagnostics.md)
for the field-by-field schema.

## Data flow

```text
results/<workload>/pbt_runs/<tier>/tuning_sessions/
    pbt_results_*.json
                │
                ▼  data_loader.load_pbt_results
                │  • parse session JSON, rescore globally,
                │    encode categorical knobs
                │  • record per-row session_index + generation_index
                │
                ▼
        LoadedData(config_df, scores, session_index,
                   generation_index, knob_bounds)
                │
                ▼  analyze_knob_importance
                │  (import-side computation, unchanged)
                │
                ▼  scalpel_tier(X, y, sample_groups, hp, knob_metadata)
                │
                ▼
        SCALPELResult
                │
                ├──► tier_generator.export_data_driven_tiers(scalpel_result=…)
                │       data/data_driven_knobs/<workload>/data_driven_tiers.json
                │       data/data_driven_knobs/<workload>/scalpel_diagnostics.json
                │
                ├──► analyze_knob_importance._save_analysis_results
                │       results/analysis/<workload>/importance_results.json
                │       (with tier_generation.metadata.algorithm = "scalpel-v1")
                │
                ▼
        knob_loader.load_knob_space_for_tier
                │  • walks DOWN [minimal, core, standard, extensive]
                │    when SCALPEL leaves an intermediate tier empty
                │
                ▼
        PBTTuner.knob_space  /  BO baseline knob_space
                │  • output paths suffixed with @scalpel-v1 when
                │    knob_source == "data_driven"
                ▼
        results/<workload>/pbt_runs/<tier>@scalpel-v1/
        results/<workload>/bo_runs/<tier>@scalpel-v1/
```

## v1.1 — q-sensitivity sweep

Layer 1's confirmed set depends on a single FDR target (`fdr_q=0.10`).
A reviewer who wants to know "how stable is the tier boundary if we
tighten or loosen that knob?" historically had to re-run the whole
pipeline at multiple `q` values. In v1.1, the primary BORUTA pass
records its per-knob hit counts once, and the sweep partitions those
same counts at `q ∈ {0.05, 0.10, 0.20, 0.30}`. Because BH-adjusted
p-values are q-independent (only the verdict at a given threshold
changes), the sweep costs four Lorenz partitions — milliseconds total
on top of the multi-minute primary pass.

The sweep guarantees a **monotone-nested** confirmed set:
`confirmed(q=0.05) ⊆ confirmed(q=0.10) ⊆ confirmed(q=0.20) ⊆ confirmed(q=0.30)`.
A knob present at the tightest threshold is always present at the looser
ones, so the sweep gives reviewers a clear "as we relax FDR, these
additional knobs become candidates" diagnostic without re-fitting any RFs.

Output shape:

- `data_driven_tiers.json` `metadata.diagnostics.q_sensitivity_summary`
  carries `{q_str: n_confirmed}` — the lightweight at-a-glance counts.
- `scalpel_diagnostics.json` `q_sensitivity[q_str]` carries the full
  partition: `confirmed`, `tentative`, `rejected`, `tier_assignments`,
  `n_confirmed` for each `q ∈ Q_SWEEP`. The primary pipeline's tier
  assignment is the one at `fdr_q = hp.fdr_q` (the default 0.10).

The sweep is operator-invisible: there is no CLI flag to disable it
because the cost is negligible and the diagnostic is high-value. If
the primary pass returns no confirmed knobs (the degenerate path), the
sweep is skipped and `q_sensitivity` is empty.

## v1.1 — pairwise-interaction fused signal

fANOVA's `quantify_importance((i, j))` returns the interaction
contribution above the two marginals. SCALPEL v1.0 used only the
marginals to drive the Lorenz partition — a knob whose marginal mass
was modest but whose strongest interaction lifted the response surface
was demoted unfairly. v1.1 fuses the two:

```text
lorenz_input[k] = marginal[k] + alpha * max_interaction[k]
max_interaction[k] = max_j fanova((k, j)).individual_importance
```

Only the top-K marginal knobs (K=20 default) get pairwise queries.
The full O(p²) interaction matrix on ~180 knobs would dominate runtime,
and in practice the fused signal only helps knobs whose marginal is
already competitive — a knob with zero marginal and zero shadow mass
is not going to be promoted by an interaction term alone.

Hyperparameters: `interaction_alpha` (default 0.5) controls the mix.
The default lets a unit-mass interaction matter only when the marginal
is at least half its size. `--scalpel-interaction-alpha 0.0` recovers
the pure marginal-only baseline; `--scalpel-interaction-top-k 0` skips
interaction computation entirely.

`SCALPELResult` persists three new fields:

- `marginal_importances` — the unmixed fANOVA marginals (what v1.0
  used as the Lorenz input).
- `max_interactions` — per-knob max pairwise interaction (zero for
  knobs outside the top-K).
- `lorenz_input_importances` — the fused vector that actually drove
  the Lorenz partition.
- `top_k_marginals` — the search frontier (knobs whose pairwise
  interactions were queried).

`compute_fanova_marginals` is now a thin shim around the new
`compute_fanova_importance` returning `FanovaImportance`. The shim
preserves backward compatibility for the stability-layer closure and
for `hardware_validator`'s cross-hardware export, neither of which
benefit from the interaction signal.

## Cross-references

- [Knob importance analysis (canonical importance pipeline)](knob-importance-analysis.md)
- [Configuration management](configuration-management.md)
- [Visualization architecture](visualization.md)
- [SCALPEL rollout guide (operator playbook)](../guides/scalpel-rollout.md)
- [SCALPEL diagnostics reference (JSON schema)](../reference/scalpel-diagnostics.md)
- [ADR-005: SCALPEL tier generation](decisions/ADR-005-scalpel-tier-generation.md)
