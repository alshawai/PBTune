# SCALPEL Rollout Guide

> See also:
> [SCALPEL architecture](../architecture/scalpel.md),
> [SCALPEL diagnostics reference](../reference/scalpel-diagnostics.md),
> [ADR-005](../architecture/decisions/ADR-005-scalpel-tier-generation.md).

This guide walks through running SCALPEL on a real PBT trace,
inspecting the diagnostics, regenerating tier files for every
workload, and recovering when the algorithm refuses to confirm any
knobs. The full algorithmic detail lives in the
[architecture doc](../architecture/scalpel.md); this page is the
operator playbook.

## Prerequisites

- A PBT trace under
  `results/<workload>/pbt_runs/extensive/tuning_sessions/` containing
  one or more `pbt_results_*.json` files. SCALPEL is designed to run
  on the **extensive** tier so it has the broadest tunable space to
  attribute over; it walks down on `--knob-source data_driven` runs
  but expects the importance-attribution data to come from the
  extensive trace.
- `python -m pip install -e .[dev]` so `fanova`, `ConfigSpace`,
  `scipy`, and `scikit-learn` are available.
- A clear understanding that SCALPEL's output goes to
  `data/data_driven_knobs/<workload>/data_driven_tiers.json`.
  Pre-existing tier CSVs in that directory will be regenerated when
  `preprocess_knobs` is rerun.

## Generating the importance-design trace (LHS)

SCALPEL attributes importance best over a **space-filling design** where every
knob varies independently of performance feedback. A PBT trace works, but its
trajectory variance is narrow — the optimizer collapses the per-knob spread it
needs. The [`LHSDesignTuner`](../../src/tuners/lhs_design.py) produces a clean
substrate instead: a fixed Latin Hypercube design swept once, with no
evolution.

```bash
# 64-point design over the extensive tier, 4 instances in parallel
python -m src.tuners.lhs_design \
  --benchmark sysbench \
  --sysbench-workload oltp_read_write \
  --tier extensive \
  --config thorough \
  --design-size 64 \
  --parallel-workers 4
# → results/oltp/oltp_read_write/lhs_runs/extensive/tuning_sessions/lhs_results_*.json
# → an lhs_design_<ts>.html log is written alongside it, matching PBT/BO.
```

`python -m src.tuners` is an alias for the same CLI. The `--config` profile
(`rapid` / `standard` / `thorough` / `research`) sets the defaults for design
size, worker count, measurement/warmup durations, and the snapshot-restore
cadence; each is overridable by its own flag (here `--design-size` and
`--parallel-workers` override the `thorough` defaults). The session JSON carries
`tuning_strategy: "lhs"` and a `design_records` array — one entry per design
point with its config fractions, metrics, and score breakdown. Point SCALPEL
at the `lhs_runs/.../tuning_sessions` directory exactly as you would a PBT
trace:

```bash
python -m src.scripts.analyze_knob_importance \
  --algorithm scalpel \
  --results-dir results/oltp/oltp_read_write/lhs_runs/extensive/tuning_sessions \
  --workload-label oltp_read_write \
  --export-tiers auto
```

Pick `--design-size` for the budget you have: larger designs give SCALPEL more
rows to attribute over (tighter FDR control) at linear wall-clock cost. 64–128
points is a reasonable starting range for the extensive tier.

Two lifecycle details affect importance quality. First, each design batch is
restored to the pristine baseline snapshot on the per-profile cadence
(rapid=10 / standard=5 / thorough=1 / research=1 batches), so later batches do
not attribute over DB state that drifted under earlier ones; use
`--disable-snapshots` or tune `--snapshot-restore-interval N` to override.
Second, `--probe-disk` (on by default) calibrates each worker's disk I/O budget
with a short `fio` probe for realistic contention; if `fio` is not installed the
probe is skipped with a WARNING and a heuristic budget is used instead.

## Single workload

Quick run with default budgets (~5–10 min on n ≈ 2 000, p ≈ 180):

```bash
python -m src.scripts.analyze_knob_importance \
  --algorithm scalpel \
  --results-dir results/oltp/oltp_read_write/pbt_runs/extensive/tuning_sessions \
  --workload-label oltp_read_write \
  --export-tiers auto
```

Reduced-budget smoke run (~3–5 min):

```bash
python -m src.scripts.analyze_knob_importance \
  --algorithm scalpel \
  --results-dir results/oltp/oltp_read_write/pbt_runs/extensive/tuning_sessions \
  --workload-label oltp_read_write \
  --skip-shap \
  --scalpel-boruta-iter 30 \
  --scalpel-stability-b 10 \
  --scalpel-rf-trees 200 \
  --export-tiers auto
```

Outputs:

- `data/data_driven_knobs/oltp_read_write/data_driven_tiers.json` —
  the canonical four-tier file consumed by the tuner CLI / BO baseline.
- `data/data_driven_knobs/oltp_read_write/scalpel_diagnostics.json` —
  full per-knob diagnostics (BORUTA hits, BH p-values, stability
  probabilities, DBA-prior violations).
- `results/analysis/oltp_read_write/importance_results.json` — the
  importance + tier-generation block, now carrying SCALPEL metadata
  under `tier_generation.metadata`.

## All workloads in one shot

```bash
python -m src.scripts.analyze_knob_importance \
  --algorithm scalpel \
  --all-workloads results \
  --export-tiers auto
```

The script globs
`results/*/pbt_runs/extensive/tuning_sessions` (override via
`--results-glob`), discovers `(workload_label, results_dir)` pairs
from the path, and runs the per-workload pipeline. Failures on any
single workload are logged at WARNING level and skipped — they never
abort the rollout. Per-workload seeds are derived deterministically
from `--scalpel-base-seed` × workload label so cross-workload
comparisons stay independent.

## Tuner / BO consumption

`--knob-source data_driven` runs from the tuner and the BO baseline
suffix the tier slug with `@scalpel-v1` so post-SCALPEL artifacts do
not collide with pre-SCALPEL ones:

```bash
python -m src.tuner.main \
  --workload oltp_read_write \
  --tier core \
  --knob-source data_driven
# → results/oltp/oltp_read_write/pbt_runs/core@scalpel-v1/
```

Expert-source paths are unchanged. When SCALPEL leaves an
intermediate tier empty (e.g., `core` confirmed nothing),
[`knob_loader`](../../src/tuner/config/knob_loader.py) walks DOWN
the canonical order to the next broader tier whose CSV exists with
at least one knob, logging a warning. The tuner does not crash on
empty tiers any more.

## Inspecting the output

```bash
python - <<'PY'
import json
d = json.load(open("data/data_driven_knobs/oltp_read_write/data_driven_tiers.json"))
print("algorithm:", d["metadata"]["algorithm"])
print("minimal:", d["tiers"]["minimal"])
print("core:", d["tiers"]["core"])
print("standard:", d["tiers"]["standard"])
print("extensive:", d["tiers"]["extensive"])
print()
diag = d["metadata"]["diagnostics"]
print("nuisance_dropped:", len(diag["nuisance_dropped"]))
print("n_confirmed / tentative / rejected:",
      diag["n_confirmed"], diag["n_tentative"], diag["n_rejected"])
print("DBA-prior violations:", diag["dba_prior_violations"])
print("OOB R²:", diag["oob_r2"])
print("wall_clock_s:", diag["wall_clock_s"])
PY
```

Then visualise the diagnostics with the new figure:

```bash
python -m src.visualization \
  --figure tier_diagnostics \
  --data-dir results/analysis/oltp_read_write \
  --output-dir docs/figures/scalpel
```

The figure has four panels: tier counts, BORUTA hit counts (top 25
confirmed knobs), Lorenz cumulative-mass curve with the 50 / 80 cuts
marked, and stability-probability strip plot per tier.

## Smoke test gate

Before promoting a regenerated `data_driven_tiers.json` to a paper
table or an automated tuning run, verify three invariants:

```bash
python - <<'PY'
import json
d = json.load(open("data/data_driven_knobs/oltp_read_write/data_driven_tiers.json"))
assert d["metadata"]["algorithm"] == "scalpel-v1"
mc = d["tiers"]["minimal"] + (d["tiers"]["core"] or [])

# 1. Canonical knobs reach minimal or core (only required if SCALPEL ran
#    with a healthy budget — see the next section if this fails).
print("shared_buffers in minimal/core?", "shared_buffers" in mc)
print("work_mem in minimal/core?", "work_mem" in mc)

# 2. Nuisance knobs are absent from every tier.
forbidden = {"array_nulls", "IntervalStyle", "syslog_facility", "bytea_output"}
all_assigned = (
    set(d["tiers"]["minimal"])
    | set(d["tiers"]["core"] or [])
    | set(d["tiers"]["standard"] or [])
)
leak = forbidden & all_assigned
assert not leak, f"nuisance knobs leaked: {leak}"

# 3. extensive is null (means "all tunable knobs").
assert d["tiers"]["extensive"] is None
print("SMOKE OK")
PY
```

## When SCALPEL refuses to confirm canonical knobs

If `shared_buffers` / `work_mem` / `effective_cache_size` land in
`dba_prior_violations` (data tier `not_confirmed`), this is a real
finding — not a bug. PBT converges on a near-optimal value for those
knobs early in the trajectory and then perturbs them within a tight
band, leaving very little marginal variance for the surrogate to
attribute. Three responses:

1. **Verify it.** Refit RF on the confirmed-only subset and check
   Spearman ρ vs the primary marginals. The
   `diagnostics.contamination_sensitivity_rho` slot exists for this
   sensitivity check; populate it manually if you want a defensible
   number for the paper.
2. **Acknowledge it in the methodology.** SCALPEL surfaces the gap;
   the paper section reports the data-driven tier alongside the
   expert-defined tier and notes that PBT-trajectory importance
   under-attributes knobs that converge early.
3. **Switch the importance design.** Long-term, run a dedicated
   LHS / Sobol design with a fixed budget for importance attribution
   only (no PBT trajectory bias). This is logged as a v2 follow-up
   in [ADR-005](../architecture/decisions/ADR-005-scalpel-tier-generation.md).

## When SCALPEL confirms zero knobs

The most common cause is too-aggressive nuisance filtering or too-low
BORUTA budget. Check `diagnostics.preflight_reason` first — it
distinguishes preflight rejection (too few samples / clusters) from
"no knob beat the shadow ceiling" (`is_degenerate=False` with
`n_confirmed=0`). For the latter:

1. Bump `--scalpel-boruta-iter` to 200.
2. Inspect `nuisance_dropped`. If the operator suspects a nuisance
   knob is actually load-bearing for this workload, override:
   `--scalpel-nuisance-overrides default_toast_compression,track_io_timing`.
3. If `OOB R²` is below 0.30, the surrogate itself is too weak.
   Either collect more PBT samples or reduce `--scalpel-rf-min-samples-leaf`.

## Recovering from a bad rollout

Every export goes through `os.replace(<path>.tmp, <path>)`, so a
mid-run crash leaves the prior good `data_driven_tiers.json`
untouched. To revert a SCALPEL-generated file to a Jenks-era backup,
git restore the file from before the SCALPEL commit
(`scalpel-v1`-suffixed paths under `pbt_runs/<tier>/` will not be
affected — they live alongside legacy paths, not on top of them).

## CI integration

Add a smoke gate to your CI:

```yaml
- name: SCALPEL smoke gate
  run: |
    python -m src.scripts.analyze_knob_importance \
      --algorithm scalpel \
      --results-dir tests/fixtures/scalpel_pbt_smoke \
      --workload-label scalpel_smoke \
      --skip-shap \
      --scalpel-boruta-iter 20 \
      --scalpel-stability-b 5 \
      --scalpel-rf-trees 100 \
      --output-dir /tmp/scalpel_smoke \
      --export-tiers /tmp/scalpel_smoke/data_driven_tiers.json
```

The end-to-end pytest (`tests/unit/analysis/test_scalpel.py::test_scalpel_tier_end_to_end_on_synthetic_signal`)
is marked `slow` and runs separately from the unit-test sweep. Run it
explicitly when SCALPEL itself is being modified:

```bash
pytest tests/unit/analysis/test_scalpel.py -m slow -v
```
