# Adding a New Tunable Knob

> Last reviewed: 2026-06-15

See also: [configuration-management](../architecture/configuration-management.md), [postgresql-connection-and-knobs](../architecture/postgresql-connection-and-knobs.md), [autotuning-knob-policy](../reference/autotuning-knob-policy.md), [knob-importance-analysis](../architecture/knob-importance-analysis.md)

This guide walks through everything required to add a PostgreSQL knob to the tunable search space — from researching its bounds to regenerating the tier CSVs and validating the result.

It's the most common contributor task that touches multiple subsystems. Everything you need to edit is in `data/`; you do **not** need to modify any Python code under `src/` to add a knob.

---

## Decision tree — should this knob be tunable?

Before adding a knob, confirm it's appropriate:

1. **Does it affect performance?** Network/locale/SSL knobs do not — they belong in the policy exclusion list, not in a tier.
2. **Is it safe to vary autonomously?** Knobs that risk data integrity (`fsync`, `wal_level`), authentication (`hba_file`), or replication (`primary_conninfo`) must be marked `unsafe` in the policy file, not added to a tier.
3. **Does PostgreSQL expose stable bounds?** Read its `pg_settings` row and confirm `min_val` / `max_val` are sensible. If they're `null` (unbounded) you must define your own tuning range from operational experience.
4. **Does it have a meaningful effect at the workload tier you're targeting?** If unsure, run the [knob-importance analysis](../architecture/knob-importance-analysis.md) on a comparable session before tier promotion.

If any of (1)-(3) fails, this knob does not belong in a tier. Add it to `data/knob_policy.json` under `AUTOTUNING_SOURCE_EXCLUSIONS` with a reason code instead.

---

## The four-file picture

A tunable knob lives in four data files:

```text
data/
├── postgresql_all_knobs.csv          # the full pg_settings dump (regenerated, not edited)
├── knob_metadata.json                # tuning bounds, scale, impact tier, hardware-relative flag
├── knob_policy.json                  # tune / freeze / unsafe + exclusion reasons
└── expert_defined_knobs/
    ├── minimal_knobs.csv             # ← regenerated from the three files above
    ├── core_knobs.csv                # ← regenerated
    ├── standard_knobs.csv            # ← regenerated
    └── extensive_knobs.csv           # ← regenerated
```

You edit the **first three** files (`knob_metadata.json` and possibly `knob_policy.json`). The four CSVs under `expert_defined_knobs/` are **regenerated** by `python -m src.scripts.analyze_knobs` — never edit them by hand.

---

## Step 1 — Confirm the knob exists in `pg_settings`

```bash
python -m src.scripts.analyze_knobs --refresh-raw    # dumps pg_settings to data/postgresql_all_knobs.csv
grep -i 'your_knob_name' data/postgresql_all_knobs.csv
```

You should see a row with the knob's `vartype`, `min_val`, `max_val`, `enumvals`, `context`, and `boot_val`. If the row is missing, the knob doesn't exist on your PostgreSQL version — bail out.

Make a note of:

- **`context`** — `internal` (immutable; reject), `postmaster` (requires restart), `sighup` (reload only), `user` (per-session SET).
- **`vartype`** — `integer`, `real`, `bool`, `enum`, or `string` (string is rejected; the search space requires numeric / enum / bool).
- **`min_val` / `max_val`** — PostgreSQL's hard bounds. Your tuning bounds must be a **subset** of these.
- **`unit`** — empty for unitless, otherwise something like `8kB` (memory pages), `kB`, `s`, `ms`, `min`. The applicator handles unit conversion automatically.

## Step 2 — Add a `TuningMetadata` entry

Edit `data/knob_metadata.json` and add a top-level entry keyed by the knob name:

```json
"jit_above_cost": {
  "tuning_min": 10000,
  "tuning_max": 500000,
  "scale": "log",
  "impact_tier": "standard",
  "tuning_priority": 2,
  "notes": "JIT-compile plans above this cost. Lower values trade compilation overhead for runtime speed.",
  "hardware_relative": false,
  "resource_type": null
}
```

Field-by-field:

| Field | Required | Purpose |
| --- | --- | --- |
| `tuning_min` / `tuning_max` | yes (numeric/real knobs) | The PBT/BO search range. Must be a subset of `pg_settings.min_val`/`max_val`. |
| `scale` | yes (numeric) | `linear` for additive effects (`max_connections`), `log` for multiplicative effects (memory buffers, costs). |
| `impact_tier` | yes | `minimal` / `core` / `standard` / `extensive`. Each tier is a superset of the previous. Promote conservatively — knobs without empirical importance evidence belong in `extensive`. |
| `tuning_priority` | yes | Numeric priority within the tier (1 = highest). Used for sorting and reporting only. |
| `notes` | yes | One-sentence rationale. **Read by maintainers**, so it's worth writing well — explain why this knob matters and what trade-off it represents. |
| `hardware_relative` | yes | `true` if the bounds should be reinterpreted as fractions of detected resources. Memory and parallelism knobs are typically `true`; cost-model and time-out knobs are `false`. See [hardware-aware-normalization](../architecture/hardware-aware-normalization.md). |
| `resource_type` | when `hardware_relative=true` | One of `ram`, `cpu`, `disk`. Drives which `WorkerResources` field the fractional encoding refers to. |

For **enum** knobs, replace the numeric bounds with an `enum_values` list (the loader recognises the difference). For **boolean** knobs, no bounds are needed — `KnobLoader` infers `[False, True]` from the type.

## Step 3 — Confirm or add the policy entry

`data/knob_policy.json` is the safety filter. **Most knobs need no policy entry**; the default is "tunable" if a `TuningMetadata` entry exists.

You only edit `knob_policy.json` in two cases:

1. **The knob is unsafe to tune** — add it under `AUTOTUNING_SOURCE_EXCLUSIONS` with a reason code:

   ```json
   "your_unsafe_knob": [
     "data_integrity",
     "Disabling this can corrupt the WAL and prevent recovery."
   ]
   ```

   Reason codes already in use (use the closest match):
   - `maintenance_only` — affects only post-workload maintenance.
   - `os_alignment` — depends on OS limits, can crash the backend.
   - `applicator_dependency` — the tuner needs this knob set a specific way to operate.
   - `network_discovery`, `network_binding` — networking, not performance.
   - `session_semantics` — per-session toggle, not a stable global parameter.
   - `security_transport` — TLS/SSL configuration.
   - `data_integrity` — risks data loss if mistuned.
   - `replication_invariant` — affects replication, not tuneable in isolation.

2. **The knob needs an explicit safety bound** that overrides `pg_settings`. Add it under `BOUNDS_OVERRIDES` (see existing entries for the shape).

If neither case applies, leave `knob_policy.json` untouched.

## Step 4 — Regenerate the tier CSVs

```bash
python -m src.scripts.analyze_knobs
```

This runs the preprocessing pipeline:

1. Loads `data/postgresql_all_knobs.csv`.
2. Overlays `TuningMetadata` from `data/knob_metadata.json`.
3. Applies the policy filter from `data/knob_policy.json`.
4. Splits by `impact_tier` and writes the four tier CSVs under `data/expert_defined_knobs/`.

The script prints a diff at the end:

```text
+ jit_above_cost     standard  log  hardware_relative=False
- old_unused_knob    (dropped: not in current pg_settings)
```

Confirm your knob appears in the right tier CSV:

```bash
grep 'your_knob_name' data/expert_defined_knobs/<tier>_knobs.csv
```

## Step 5 — Smoke-test with a quick PBT run

```bash
python -m src.tuners pbt \
    --tier <the-tier-you-promoted-to> \
    --config rapid \
    --population 2 \
    --generations 3
```

What you're looking for:

- The startup banner should log `Knob space: <N> knobs loaded` with `<N>` matching your tier CSV row count.
- The first generation should successfully apply the new knob — look for `KnobApplicator: applied <name>=...` in the HTML log.
- The session JSON's `best_configuration.knobs` should include your knob with a value inside `[tuning_min, tuning_max]`.

If application fails ("ERROR: parameter "your_knob" cannot be changed without restarting the server"), the knob is `postmaster`-context but you didn't set `requires_restart: true` in the metadata — fix and regenerate.

If the value is silently rounded to a different number, that's PostgreSQL's quantisation. The orchestrator's `KnobApplicator.verify()` read-back at barrier B5 captures the actually-applied value; check the session JSON for the **quantised** value, not the suggested one. See [configuration-management §Verifying applied config](../architecture/configuration-management.md#verifying-applied-config).

## Step 6 — Run the test suite

```bash
make test
```

Two test files matter for this change:

- [tests/unit/knobs/test_knob_metadata_loader.py](../../tests/unit/knobs/test_knob_metadata_loader.py) — verifies the metadata loader still parses cleanly.
- [tests/unit/knobs/test_policy_loader.py](../../tests/unit/knobs/test_policy_loader.py) — verifies the policy filter still applies cleanly.

If your knob is `hardware_relative=True`, also run [tests/unit/config/test_hardware_normalization.py](../../tests/unit/config/test_hardware_normalization.py) — it asserts the fractional encoding round-trips.

---

## Worked example — adding `jit_above_cost`

`jit_above_cost` controls when PostgreSQL's JIT compiler activates. It's a `real` knob, `user`-context, with `pg_settings.min_val=0` and `pg_settings.max_val=1.79e+308`. It's mid-impact (relevant only on OLAP-ish workloads), not hardware-relative, and safe to tune.

**Step 1 — confirm it exists:**

```bash
grep '^jit_above_cost' data/postgresql_all_knobs.csv
# jit_above_cost,100000,,Query Tuning / Other Planner Options,user,real,...
```

**Step 2 — add metadata** in `data/knob_metadata.json`:

```json
"jit_above_cost": {
  "tuning_min": 10000,
  "tuning_max": 500000,
  "scale": "log",
  "impact_tier": "standard",
  "tuning_priority": 3,
  "notes": "JIT activation threshold. Lower values trade compile overhead for runtime speed on complex plans.",
  "hardware_relative": false,
  "resource_type": null
}
```

**Step 3 — no policy entry needed** (safe + performance-relevant + has metadata).

**Step 4 — regenerate:**

```bash
python -m src.scripts.analyze_knobs
grep '^jit_above_cost' data/expert_defined_knobs/standard_knobs.csv
# jit_above_cost,...,standard,3.0,"JIT activation threshold...",False,
```

**Step 5 — smoke test:**

```bash
python -m src.tuners pbt --tier standard --config rapid --population 2 --generations 3
```

Inspect the resulting `pbt_results_*.json` for `best_configuration.knobs.jit_above_cost`. Done.

---

## Promoting a knob to a higher tier

If empirical analysis ([knob-importance-analysis](../architecture/knob-importance-analysis.md)) shows that a knob currently in `extensive` matters consistently across hardware profiles for the workload you care about, you can promote it:

1. Edit `data/knob_metadata.json` — change `impact_tier` to the target tier.
2. Re-run `python -m src.scripts.analyze_knobs`.
3. Verify the diff is the expected single-knob promotion.

The fANOVA + TreeSHAP pipeline can also produce **data-driven tier overlays** under `data/data_driven_knobs/{workload}/` — these are workload-specific and live alongside the expert-defined tiers without overwriting them. Use `--knob-source data_driven --workload <label>` on the tuner CLI to load those instead.

## Common mistakes

| Mistake | Symptom | Fix |
| --- | --- | --- |
| `tuning_min` outside PostgreSQL's `min_val` | Apply fails on first generation. | Tighten the tuning range. |
| Memory knob with `hardware_relative: false` | OOM on small-RAM hosts. | Set `hardware_relative: true`, `resource_type: "ram"`. |
| Wrong scale (`linear` for a memory knob) | Sampling clusters at the upper bound. | Switch to `scale: "log"`; memory and cost knobs almost always want log. |
| Missing `notes` | Reviewers / future-you can't tell why this knob is in the tier. | Write one sentence explaining the trade-off. |
| Editing the tier CSVs by hand | Edits get clobbered on next regenerate. | Edit `knob_metadata.json` and re-run `analyze_knobs`. |
