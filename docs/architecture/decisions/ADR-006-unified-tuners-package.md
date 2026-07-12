# ADR-006: Unified `src/tuners` Package and `BaseTuner` Lifecycle

- Status: Accepted
- Date: 2026-06-19
- Relates to:
  [`src/tuner`](../../src/tuner) (PBT),
  [`src/scripts/bo_baseline`](../../src/scripts/bo_baseline) (BO),
  and the new [`src/tuners`](../../src/tuners) package (LHS-design + shared core).

## Context

The project ships two configuration-tuning strategies today:

1. **PBT** — Population-Based Training, in
   [`src/tuner/main.py`](../../src/tuner/main.py) (`PBTTuner`).
2. **BO** — a Bayesian Optimization baseline, in
   [`src/scripts/bo_baseline/runner.py`](../../src/scripts/bo_baseline/runner.py)
   (`BOBaselineRunner`).

Both classes independently re-implement an almost identical *lifecycle
scaffold* around their genuinely different optimizer cores:

- resolve per-worker hardware resources (manual override vs. auto-detect);
- build a workload executor + extract workload features (sysbench / tpch /
  custom template branch);
- bring up PostgreSQL instances and prune knobs absent from the runtime
  `pg_settings`;
- run a sequence of generations / iterations under lockstep barriers;
- tear down instances (and optionally clean up data);
- serialize a `tuning_session` JSON envelope with a shared header block plus
  `best_configuration` and `worker_resources`.

The only parts that genuinely differ between PBT and BO are **how the next
configuration(s) are proposed** and **when to stop**. Everything else is
duplicated, with the two copies already drifting (e.g. PBT degrades gracefully
when `pg_settings` is unreachable, BO re-raises).

We want to add a **third** strategy — a Latin Hypercube Sampling (LHS)
*importance-design* tuner — without tripling the duplication. The research
framing (see [`docs/guides/scalpel-rollout.md`](../../docs/guides/scalpel-rollout.md))
is that SCALPEL applied to an LHS *design* over the knob space yields
DBA-competitive tiers, whereas applied to PBT trajectory variance the signal
is too narrow. The LHS tuner must run in parallel like PBT (barriers,
per-worker resources) and emit a schema-compatible session JSON.

## Decision

Introduce a new top-level package, [`src/tuners`](../../src/tuners), that holds:

- **`BaseTuner`** ([`src/tuners/base.py`](../../src/tuners/base.py)) — an ABC
  encoding the invariant lifecycle as a concrete `run()` template method
  (Template Method pattern). It owns timing instrumentation, the generation
  loop, the teardown guard, and result assembly, and delegates the
  strategy-specific decisions to abstract hooks: `setup`,
  `propose_initial_configs`, `step`, `should_stop`, `collect_best`,
  `build_session_payload`, `teardown`.
- **`src/tuners/utils/`** — the shared scaffolding extracted from PBT and BO:
  - `types.py` — `TuningStrategy` enum (`pbt` / `bo` / `lhs`),
    `GenerationOutcome`, `TunerLifecycleConfig`.
  - `output_paths.py` — `resolve_tuner_output_root`, unifying PBT's
    `_build_output_dir` and BO's `resolve_bo_output_root` into one
    strategy-parameterized resolver (including the `@scalpel-v1` data-driven
    tier suffix).
  - `resources.py` — `resolve_worker_resources`, the manual-vs-auto dispatch.
  - `executors.py` — `build_workload_bundle`, the sysbench/tpch/custom branch.
  - `knob_filter.py` — runtime `pg_settings` knob pruning, as DB-free testable
    helpers.
  - `calibration.py` — the global score-recalibration utilities (relocated
    here from the now-deleted `src/utils/rescoring.py`; see the 2026-06-22
    addendum). [`src/tuners/utils/calibration.py`](../../src/tuners/utils/calibration.py).
  - `session_writer.py` — `convert_numpy_types`, `build_session_header`, and
    the session/best-config write helpers.

### Extraction is by **copy**, not refactor

`src/tuner` (PBT) and `src/scripts/bo_baseline` (BO) are **not modified** by
this change. The shared utilities are lifted into `src/tuners/utils` as the
*canonical* implementation that new strategies build on, but the incumbents
keep their own inline copies for now.

Rationale:

- **Risk isolation.** PBT and BO are the two strategies the paper's headline
  numbers depend on. Retrofitting them onto a new ABC in the same change that
  introduces the ABC would put those numbers at risk for no immediate benefit.
- **Reviewability.** The diff for "add a third strategy" stays additive: new
  files, no behavioral change to existing runs.
- **Reversibility.** If the `BaseTuner` abstraction turns out to fit LHS but
  chafe against PBT/BO, we can iterate on it against LHS alone before
  committing to a migration.

A follow-up ADR will decide whether (and how) to migrate PBT and BO onto
`BaseTuner` once the abstraction has proven itself on LHS.

### `tuning_strategy` is orthogonal to `benchmark_name`

The session JSON keeps `benchmark_name` as the **workload driver**
(`"sysbench"` / `"tpch"` / custom) and adds `tuning_strategy`
(`"pbt"` / `"bo"` / `"lhs"`) as a separate discriminator (see the prior
`tuning_strategy` field migration). A session has exactly one of each. Loaders
in [`src/analysis/data_loader.py`](../../src/analysis/data_loader.py) and
[`src/evaluation/loader.py`](../../src/evaluation/loader.py) read the explicit
field and fall back to a path heuristic (`/pbt_runs/`, `/bo_runs/`,
`/lhs_runs/`) for legacy files.

## Consequences

**Positive**

- A third strategy can be added as a single `BaseTuner` subclass plus a CLI,
  reusing the shared lifecycle, output layout, and serialization.
- The shared helpers are individually unit-tested without a database
  (`tests/unit/tuners/`), closing the PBT/BO drift gaps (e.g. graceful
  `pg_settings` degradation is now the documented default).
- Output paths, numpy serialization, and the session header are guaranteed
  consistent across strategies because they flow through one implementation.

**Negative / accepted costs**

- **Temporary duplication.** Until the follow-up migration, the lifecycle
  scaffold exists in three places (PBT inline, BO inline, `src/tuners/utils`).
  This is a deliberate, time-boxed cost taken to de-risk the paper's incumbents.
- The `BaseTuner` API is provisional: it is validated only against LHS in this
  change, so its hook surface may shift before PBT/BO adopt it.

## Alternatives considered

1. **Refactor PBT and BO onto `BaseTuner` now.** Rejected: couples a risky
   refactor of the headline strategies to the introduction of a new one.
2. **Put LHS inside `src/tuner` alongside PBT.** Rejected: `src/tuner` is
   PBT-specific (population, workers, evolution, barriers); LHS is not a
   population method, and overloading the package would muddy both.
3. **No shared base; copy the whole PBT scaffold into the LHS tuner.**
   Rejected: that is exactly the duplication this ADR exists to bound, and it
   would immediately drift like PBT/BO already have.

## Addendum (2026-06-19): `LHSDesignTuner` — the first concrete strategy

[`src/tuners/lhs_design/tuner.py`](../../src/tuners/lhs_design/tuner.py) is the first
concrete `BaseTuner`. It evaluates a **fixed** Latin Hypercube Sampling design
over the knob space — no evolution, no exploit/explore, no perturbation:

1. **One design, drawn once.** `KnobSpace.sample_diverse_configs(design_size)`
   produces a space-filling sample; slot 0 is anchored to the PostgreSQL
   default config (mirroring PBT/BO's pilot-seed convention).
2. **Swept in parallel batches.** The design is sliced into batches of
   `num_parallel_workers`. Each batch is evaluated concurrently under the same
   `GenerationBarrier` lockstep PBT uses, so every configuration's measurement
   window experiences identical contention. `max_generations` is therefore
   `ceil(design_size / num_parallel_workers)` — each "generation" in the
   `BaseTuner` loop is one batch.
3. **No feedback.** Because there is no optimizer reacting to scores, every
   knob varies independently of performance. That independence is exactly what
   SCALPEL needs: applied to this design the per-knob importance signal is
   wide enough to tier; applied to PBT's optimization *trajectory* the variance
   collapses (see [`docs/guides/scalpel-rollout.md`](../../docs/guides/scalpel-rollout.md)).

The tuner *composes* PBT's environment, orchestrator, and `Worker` machinery
for the actual apply→run→measure step, but drives them through the
strategy-agnostic `BaseTuner` lifecycle rather than PBT's `Population`
evolution loop. PBT and BO remain unmodified.

A run is launched via either entry point:

```bash
python -m src.tuners.lhs_design --tier core --benchmark sysbench \
    --sysbench-workload oltp_read_write --design-size 64 --parallel-workers 4
# equivalently:
python -m src.tuners --tier core --benchmark sysbench --design-size 64
```

Output lands at
`results/{workload}/[{sysbench_workload}/]lhs_runs/{tier}/tuning_sessions/lhs_results_*.json`,
carrying `tuning_strategy: "lhs"` and a `design_records` array (one entry per
design point with its config fractions, metrics, and score breakdown) that the
SCALPEL pipeline consumes.

## Addendum (2026-06-22): shared CLI, profiles, and PBT/BO parity

After `LHSDesignTuner` proved the `BaseTuner` abstraction, the strategy grew the
remaining surface needed to be a first-class peer of PBT/BO. None of it touched
`src/tuner/` or `src/scripts/bo_baseline/`, so the copy-not-refactor invariant
still holds.

### Strategy-agnostic CLI + profile registry

[`src/tuners/cli.py`](../../src/tuners/cli.py) now owns the full strategy-agnostic
flag surface (Tuning Configuration, Workload Settings, Instance Management,
Per-Worker Resources, Scoring & Normalization, Output & Logging). A new strategy
entry point shrinks to *just* its own knobs (for LHS, only `--design-size`) plus a
call to `add_common_groups`.

The profile system mirrors PBT's two-layer model. `PROFILES` in
[`src/tuners/utils/profiles.py`](../../src/tuners/utils/profiles.py) maps
`--config` to a `TunerProfile` carrying the default worker count and a matched
`BenchmarkConfig`; individual flags then override the profile under the
"`None` means keep the profile default" convention. The profiles are
`rapid` / `standard` / `thorough` / `research` (worker counts 2 / 4 / 8 / 12) —
PBT's `extreme` is intentionally omitted, as it is population-scale specific and
LHS is not a population method. Each strategy additionally owns a small
`{profile: scalar}` map for the one hyperparameter it adds; LHS uses
`LHS_DESIGN_SIZE_BY_PROFILE` (8 / 32 / 512 / 1024).

### Per-batch baseline-snapshot restoration

Each LHS design batch now restores the shared read-only baseline snapshot on the
per-profile cadence (`snapshot_restore_interval`: rapid=10 / standard=5 /
thorough=1 / research=1, numerically identical to PBT's restart cadence), so a
fresh batch no longer inherits the drifted DB state left by the previous one. The
CLI/profile choice is combined with the workload bundle's own decision via a
logical AND in [`src/tuners/base.py`](../../src/tuners/base.py): a forced
read-only / TPC-H auto-disable still wins, but otherwise the operator's
`--enable-snapshots` / `--disable-snapshots` / `--snapshot-restore-interval`
selection is honored.

### HTML-log parity

The CLI attaches `add_html_file_logging` and writes a timestamped
`lhs_design_<ts>.html` under the resolved output root, matching the HTML logs
PBT and BO already produce.

### Probe-disk diagnostics

`--probe-disk` (default on) calibrates the per-worker disk I/O budget with a short
`fio` probe. [`src/utils/hardware_info.py`](../../src/utils/hardware_info.py) now
emits a WARNING when probing was requested but `fio` is absent, instead of
silently falling back to the heuristic budget. That file is shared with PBT/BO but
sits *outside* the copy-not-refactor boundary (it is not under `src/tuner/` or
`src/scripts/bo_baseline/`), so the additive diagnostic improves all three
strategies.

### Rescoring relocation

The global score-recalibration utilities moved from the now-deleted
`src/utils/rescoring.py` into
[`src/tuners/utils/calibration.py`](../../src/tuners/utils/calibration.py), with
all consumers repointed. PBT and BO remain unmodified.
