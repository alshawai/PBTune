# ADR-005: SCALPEL Tier Generation

- Status: Accepted
- Date: 2026-06-18
- Supersedes: silhouette + Jenks tier generation in
  [`src/analysis/tier_generator.py`](../../src/analysis/tier_generator.py).

## Context

Until June 2026, [`tier_generator.generate_tiers`](../../src/analysis/tier_generator.py)
partitioned PostgreSQL knobs into the canonical
`{minimal, core, standard, extensive}` tier system by:

1. Sweeping `k ∈ {2, 3, 4, 5, 6}` Jenks Natural Breaks on the raw 1D
   fANOVA marginal-importance vector.
2. Selecting the optimal k via silhouette score.
3. Projecting the chosen k onto the canonical four-tier system.

On the headline `oltp_read_write` workload (n = 2 067 PBT samples,
p = 179 knobs, Gini = 0.78), this pipeline reliably collapsed to
`optimal_k = 2`, projected `{tier_1, tier_2} → {minimal, extensive}`,
and left the canonical `core` and `standard` tiers **empty**.
Canonically important PostgreSQL knobs (`shared_buffers`, `work_mem`,
`effective_cache_size`, `max_parallel_workers_per_gather`,
`random_page_cost`) were demoted to the catch-all `extensive`. Mean-
while nuisance knobs (`array_nulls`, `IntervalStyle`,
`syslog_facility`) climbed into the top 20 by fANOVA marginal mass.

Root cause: silhouette score on raw 1D heavy-tailed importance is a
known degenerate optimization. The single large gap between the head
and the tail dominates the silhouette numerator, so silhouette
systematically prefers `k = 2` regardless of the true importance
structure. Jenks then concentrates its breaks in the head, leaving the
tail without resolution.

A multi-lens research effort (statistics, database tuning, ML feature
importance) plus an adversarial verifier panel (statistical-rigor,
engineering, DB-tuning) converged on the same conclusion: a clustering
frame is the wrong tool for a heavy-tailed ranking-with-uncertainty
problem. The right tool is significance-first, coverage-structured,
stability-audited.

## Decision

Replace silhouette + Jenks with **SCALPEL** —
**S**ignificance-**C**overage-stability **A**lgorithm for **L**ayered
**PE**rformance-knob **L**abeling. SCALPEL runs in
[`src/analysis/scalpel.py`](../../src/analysis/scalpel.py) and consists
of three layers wrapped around a shared Random Forest surrogate:

1. **Layer 1 — Significance gate.** A BORUTA-style selector with shadow
   features permuted **within** `sample_groups` clusters (the composite
   `session_index × generation_index` cluster id from PBT) so the null
   respects PBT's non-i.i.d. structure. Per-knob hit counts feed a
   two-sided binomial test, then **Benjamini–Hochberg FDR** at q = 0.10
   across the p knobs (not Bonferroni per iteration). Outputs:
   `confirmed`, `tentative`, `rejected`.
2. **Layer 2 — Coverage tiering.** Within the BORUTA-confirmed subset,
   sort by `(importance desc, knob_name asc)` (deterministic
   tie-break), renormalize cumulative mass over the **confirmed**
   subset (NOT the full feature set, eliminating circular analysis),
   and walk the curve. Knobs up to 50 % cumulative mass are `minimal`,
   the next slice up to 80 % is `core`, the rest of confirmed is
   `standard`.
3. **Layer 3 — Stability audit.** Resample at the **cluster** level
   (not the row level) for B = 100 iterations at 50 % cluster
   subsampling. Each subsample re-runs Layers 1+2 from scratch
   (regenerated shadow features per subsample — no cached null) and we
   record per-knob tier-assignment selection probability.

Two upstream defenses keep the algorithm honest:

- A **nuisance filter** drops display/auth/observability knobs from
  consideration before any modelling. The exact-name and prefix lists
  live in [`data/knob_policy.json`](../../data/knob_policy.json) under
  `IMPORTANCE_NUISANCE_EXCLUSIONS` and `IMPORTANCE_NUISANCE_PREFIXES`,
  with an operator-overridable allow-list.
- A **DBA-prior audit** flags expert-`minimal` knobs that did not land
  in data-driven `minimal`. The audit is **report-only**: SCALPEL never
  modifies its tier assignments based on the prior. The diagnostic is
  emitted alongside the data-driven tiers so reviewers can investigate
  whether the demotion is empirically supported.

Every JSON-shape and downstream-API constraint inherited from the
silhouette + Jenks pipeline is preserved (see Migration Notes).

## Consequences

Positive:

- The four canonical tiers are non-empty on real PBT data with a
  realistic budget. The headline `oltp_read_write` workload at
  default settings exposes a useful `core` and `standard` tier.
- Tier assignments carry uncertainty. Each knob exports BORUTA hit
  counts, BH-adjusted p-values, and a stability probability. Reviewers
  can challenge the algorithm at the per-knob level.
- Display/auth/observability knobs (`array_nulls`, `IntervalStyle`,
  `syslog_facility`) cannot land in `minimal`/`core` regardless of
  what the surrogate surfaces.
- Per-workload seeds prevent the `--all-workloads` rollout from
  inflating apparent inter-workload tier agreement via shared shadow
  draws.
- The output schema gains a new `metadata.algorithm = "scalpel-v1"`
  slug + `metadata.scalpel_version` + `metadata.diagnostics` block,
  plus an optional sibling `scalpel_diagnostics.json` carrying full
  per-knob payload.

Trade-offs:

- Default-budget runs (BORUTA iter = 100, stability B = 100) take
  ~5–10 minutes per workload on n ≈ 2k, p ≈ 180. The legacy pipeline
  ran in seconds.
- BORUTA on the full feature set introduces a real chance that all
  knobs land in `tentative` or `rejected` on a noisy workload. SCALPEL
  treats this as a degenerate-result signal and emits empty
  `core`/`standard` tiers; downstream
  [`knob_loader`](../../src/tuner/config/knob_loader.py) walks down to
  the next broader tier with a warning rather than crashing the tuner.
- Output paths under `results/<workload>/pbt_runs/<tier>/` for
  `--knob-source data_driven` runs are now suffixed with
  `@scalpel-v1` to prevent collisions with pre-SCALPEL artifacts that
  contained a different knob set under the same canonical tier name.
  Legacy expert-source paths are unchanged.

## Alternatives Considered

1. **Lower the silhouette acceptance threshold and force k = 3 or 4.**
   Rejected: a hard-coded k bypasses the bug but does not fix the
   underlying ranking-vs-clustering category error and still demotes
   canonical knobs.
2. **Replace Jenks with a different univariate clustering algorithm
   (e.g. gap statistic, mixture-model BIC).**
   Rejected: every clustering frame imposes a geometric assumption on
   what is fundamentally a Pareto-shaped ranking. Adversarial review
   showed clustering-on-ranks loses to significance-then-coverage on
   every realistic input distribution.
3. **Tier purely by cumulative coverage thresholds, no significance
   gate.**
   Rejected: without a significance gate, nuisance knobs and noise
   features land in `minimal` whenever their fANOVA mass happens to
   beat the cutoff. The DBA-prior audit cannot fix this — it is
   report-only by design.
4. **BORUTA without group-aware permutation.**
   Rejected: PBT samples are explicitly not i.i.d. (exploit copies a
   parent config to a child; explore perturbs by ±20 % around it).
   Row-level shadow permutation produces an anti-conservative null and
   inflates the confirmed set.
5. **Cumulative-on-disk JSON (flip the read-time accumulation in
   `knob_metadata.get_knobs_by_tier`).**
   Rejected for v1: the migration risk is high (every existing
   `data_driven_tiers.json` file would need a coordinated rewrite) and
   the audit found no consumer that benefits from the flip. v1 keeps
   the on-disk format non-cumulative.

## Migration Notes

The downstream JSON schema and Python API surface are preserved:

- `data_driven_tiers.json` keeps the canonical four-tier keys
  (`minimal`, `core`, `standard`, `extensive`), the non-cumulative
  on-disk format, and `extensive: null`. New fields:
  `metadata.algorithm = "scalpel-v1"`, `metadata.scalpel_version`,
  `metadata.diagnostics`. The `extensive: null` convention still means
  "every tunable knob"; downstream
  [`get_knobs_by_tier`](../../src/knobs/knob_metadata.py) continues to
  accumulate `minimal ⊂ core ⊂ standard` at read time.
- `tier_generator.generate_tiers(marginal_importances, workload_label)`
  keeps its legacy signature. Internally it now delegates to the
  Lorenz fallback (`scalpel.lorenz_tier_from_importances`). The full
  pipeline that runs on `(X, y)` is exposed as
  `scalpel.scalpel_tier`.
- `TierResult.silhouette_scores` is always `{}`. `TierResult.jenks_breaks`
  carries the Lorenz cutoffs `[0.50, 0.80]`. Both fields stay in the
  schema for compatibility with existing readers.
- `TierResult.tier_assignments` only contains BORUTA-confirmed knobs
  (no `extensive` label). Non-confirmed knobs are absent. This makes
  `hardware_validator.stable_knobs` an intersection of *confirmed*
  sets across hardware profiles rather than *labelled* sets — the
  shift is explicit and surfaced via the new
  `hardware_validation.stable_knobs_semantics` field.
- `preprocess_knobs.preprocess_and_save_knobs` skips writing CSVs for
  empty intermediate tiers. `knob_loader.load_knob_space_for_tier`
  walks DOWN the canonical order
  `[minimal, core, standard, extensive]` to the next broader tier
  whose CSV exists with at least one knob, logging a warning at every
  step.
- For `--knob-source data_driven`, output paths gain a `@scalpel-v1`
  suffix on the tier slug to avoid colliding with pre-SCALPEL
  artifacts. The expert-source path is unchanged.

The legacy silhouette + Jenks code is removed. The `jenkspy` runtime
import is also gone. CI sees the same public symbol set; legacy tests
in `tests/unit/analysis/test_tier_generator.py` were rewritten to
assert SCALPEL invariants.

For the rollout playbook, see
[../../docs/guides/scalpel-rollout.md](../guides/scalpel-rollout.md)
and the reference at
[../../docs/reference/scalpel-diagnostics.md](../reference/scalpel-diagnostics.md).
