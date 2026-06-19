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
  - `calibration.py` — a tuner-facing adapter over the existing
    [`src/utils/rescoring`](../../src/utils/rescoring.py) global recalibration.
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
