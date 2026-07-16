# Bayesian Optimization Baseline

See also: [Documentation index](../README.md), [pbt-core](pbt-core.md), [feature-driven-scoring](feature-driven-scoring.md), [environment-backends](environment-backends.md), [guides/bo-baseline](../guides/bo-baseline.md), [guides/pbt-vs-bo-comparison](../guides/pbt-vs-bo-comparison.md)

## Overview

The BO baseline is a [SMAC3](https://github.com/automl/SMAC3)-based Bayesian-Optimisation tuner that runs against the same `KnobSpace`, the same `WorkloadOrchestrator`, and the same `DatabaseEnvironment` backends as PBT. It exists as a **controlled comparison baseline** for academic peer review — to defend the claim that PBT is competitive against the most widely-cited alternative for database configuration tuning.

This document covers **how the baseline is shaped and why**. The runbook for actually launching BO runs lives in [guides/bo-baseline](../guides/bo-baseline.md). For its CLI flags see [reference/cli §src.scripts.bo_baseline](../reference/cli.md#srcscriptsbo_baseline--bayesian-optimisation-baseline).

The design priorities are:

1. **Fair comparison parity with PBT** — same scoring engine, same environment backend, same per-worker resource slicing.
2. **Surrogate-model integrity** — keep the cost surface stable enough for the GP / RF surrogate to learn meaningful gradients.
3. **Reproducible methodology** — every parity decision recorded in the output JSON so reviewers can audit it.

---

## Table of Contents

1. [Module layout](#module-layout)
2. [Facade selection](#facade-selection)
3. [Pilot + Freeze normalisation](#pilot--freeze-normalisation)
4. [Snapshot restoration parity](#snapshot-restoration-parity)
5. [Quantisation and read-back parity](#quantisation-and-read-back-parity)
6. [Parallel BO and resource equalisation](#parallel-bo-and-resource-equalisation)
7. [Ask-tell execution model](#ask-tell-execution-model)
8. [Implementation notes](#implementation-notes)
9. [Design decisions](#design-decisions)
10. [Related documentation](#related-documentation)

---

## Module layout

| File | Responsibility |
| --- | --- |
| [`config.py`](../../src/scripts/bo_baseline/config.py) | `BOConfig` dataclass — all tuning parameters in a frozen record. |
| [`search_space.py`](../../src/scripts/bo_baseline/search_space.py) | Translates `KnobSpace` ↔ `ConfigSpace` (SMAC3's parameter format). |
| [`objective.py`](../../src/scripts/bo_baseline/objective.py) | SMAC3-compatible objective function (returns cost = `100 - score`). |
| [`result_writer.py`](../../src/scripts/bo_baseline/result_writer.py) | Serialises results into PBT-compatible session JSON. |
| [`runner.py`](../../src/scripts/bo_baseline/runner.py) | `BOBaselineRunner` — orchestrates the optimisation, owns the ask-tell loop. |
| [`__main__.py`](../../src/scripts/bo_baseline/__main__.py) | CLI entry point. |

Every component except `runner.py` is small and pure (the runner is where the integration with the rest of the codebase happens). This split lets the search-space translation and result-writer be tested independently of SMAC3.

---

## Facade selection

SMAC3 exposes BO behaviour through "facades" that bundle a surrogate model, an acquisition function, and an initial-design strategy. The runner picks one of two:

- **`HyperparameterOptimizationFacade` (`--bo-surrogate rf`, default)** — Random Forest surrogate. Handles high-dimensional, mixed-type spaces (continuous + integer + categorical) and is robust to flat penalty regions where a configuration consistently fails. Includes 20% random interleaving (`ProbabilityRandomDesign`) to prevent the surrogate from over-exploiting a local optimum.
- **`BlackBoxFacade` (`--bo-surrogate gp`)** — Gaussian Process surrogate with Matérn 5/2 kernel and Expected Improvement acquisition. Stronger than RF on low-dimensional, smooth, continuous spaces. Less appropriate for high-dimensional knob spaces because the GP's computational cost grows with sample count and the kernel becomes uninformative at high dimensionality.

Both facades are configured with two non-default settings:

- `deterministic=False` — database benchmarks have inherent measurement variance from concurrent host activity, scheduling, and PostgreSQL background work. Setting `deterministic=True` would tell SMAC each `(config, seed)` pair has a fixed cost; that would prevent SMAC from re-evaluating incumbents and lead to overconfident decisions on noisy observations.
- `SobolInitialDesign` — quasi-random initial points instead of pure-random. Sobol sequences cover the search space more uniformly during the pilot phase, which matters because the pilot observations are what calibrate the normaliser.

The default of `rf` reflects the `extensive` tier's high dimensionality (~80 knobs) where GP behaviour degrades.

---

## Pilot + Freeze normalisation

This is the largest design choice that distinguishes BO from PBT in this codebase.

The scoring function (`feature_driven_v2`) requires normalisation ranges (e.g. max TPS, min latency) to convert raw `PerformanceMetrics` into `[0, 1]` utilities. Dynamically expanding those ranges as new observations arrive — what PBT does — is fine for a population-based optimiser because no single worker's score is held against rescaled history. **It is fatal for a surrogate-based optimiser.**

The reason: SMAC's surrogate model is trained on the cumulative `(config, cost)` history. If observation `t=10`'s cost was `0.6` under early calibration anchors, but the same raw metrics under later anchors would have produced cost `0.7`, the surrogate is now training on **inconsistent labels for the same underlying physical state**. Its predictions become noise.

The runner solves this with a **Pilot + Freeze** strategy:

1. **Pilot Phase** (first `--range-update-interval` iterations, default 10): SMAC's Sobol initial design evaluates diverse configurations. Raw `PerformanceMetrics` are recorded; the normaliser uses fallback anchors from `MetricConfig` defaults.
2. **Freeze Event** (exactly once, at the end of the pilot): `metric_config.expand_ranges_for_metrics()` calibrates anchors from the union of pilot observations. The normaliser is then locked.
3. **Frozen Phase** (remaining iterations): Anchors are immutable. The surrogate trains on a stable cost surface.

The frozen anchors are not optimal — they were calibrated from only the pilot observations and will look conservative compared to anchors a long PBT run could produce. Two safeguards address this:

- The pilot uses Sobol coverage, not random, so the anchors capture more of the space than a similarly-sized random sample would.
- Post-hoc cross-method comparison via [`rescore_metrics_globally()`](../../src/tuners/utils/calibration.py) pools raw metrics from both PBT and BO runs and rescales using globally-calibrated anchors, so the frozen in-run BO scores are not what the comparison is judged against. See [pbt-vs-bo-comparison guide](../guides/pbt-vs-bo-comparison.md).

---

## Snapshot restoration parity

Long tuning campaigns accumulate data drift: writes to `sbtest1` change row counts and index statistics; OLAP runs update `pg_class.relpages`; the buffer cache stabilises into shapes specific to configurations seen so far. PBT mitigates this by restoring worker instances to a clean baseline snapshot every N generations. For BO to be a fair comparison, it must use the same drift-mitigation mechanism — otherwise a comparison late in the BO run is against a meaningfully different database state than late in the PBT run.

The runner implements this:

- `--enable-snapshots` activates periodic restoration via the `DatabaseEnvironment.restore_snapshot()` interface (see [environment-backends](environment-backends.md)).
- `--snapshot-restore-interval N` controls how many BO **iterations** elapse between restorations.
- **PBT session sync**: when `--pbt-session` is provided, BO extracts `enable_snapshots` and `snapshot_restore_interval` from the reference session and **scales the interval correctly across optimiser semantics**. PBT measures intervals in generations; one generation evaluates `population_size` configurations. BO measures intervals in iterations; one iteration evaluates one configuration. The scale factor is `population_size`, applied automatically at session sync time. The result is that the same number of *configuration evaluations* occur between restorations in both algorithms — the methodologically meaningful unit.

---

## Quantisation and read-back parity

PostgreSQL silently quantises many knob values to internal alignment boundaries. `shared_buffers=134217729` becomes `134217728` (rounded down to the nearest 8 kB page). For PBT this is mostly a recording problem — the session JSON should record what PostgreSQL actually ran. For BO this is **a correctness problem**: the surrogate model's gradients depend on the input space being faithfully observed.

Without read-back, the BO surrogate sees:

```text
suggestion 134217729 → cost 0.62
suggestion 134217730 → cost 0.62
suggestion 134217731 → cost 0.62
... 8000 more identical suggestions ...
suggestion 134225921 → cost 0.61   (next 8kB page)
```

This is a step function in the suggestion space, but the surrogate doesn't know that — it sees a flat region with one sharp discontinuity at an arbitrary location, and learns spurious cliffs that are entirely artefacts of PostgreSQL's quantisation rather than performance.

The runner solves this with the **Read-Back Abstraction** in `evaluate_config`:

1. The BO agent suggests a continuous configuration.
2. The orchestrator applies the configuration via `KnobApplicator.apply()`.
3. After application (and any necessary restart), the orchestrator calls `KnobApplicator.verify()`, which queries `pg_settings` for the actually-applied typed values.
4. `evaluate_worker` returns these quantised values alongside the performance metrics.
5. The runner merges the quantised values back into the configuration dictionary **before** returning it to SMAC3.

The surrogate now sees:

```text
suggestion 134217728 → cost 0.62
suggestion 134217728 → cost 0.62  (different raw suggestions but same applied)
suggestion 134225920 → cost 0.61
```

— exactly the data-generating process the surrogate is supposed to model.

PBT does the same merge for its lineage tracking and session JSON; the difference is that for PBT the merge is a correctness nicety, while for BO it is required for the surrogate to converge.

---

## Parallel BO and resource equalisation

The runner supports parallel BO evaluation, mirroring PBT's parallel-worker model so cross-method comparisons are wall-clock fair as well as evaluation-budget fair.

- `--batched-bo` enables the parallel ask-tell loop (see next section).
- `--resource-division N` is the denominator for per-worker resource slicing — same role as `num_parallel_workers` for PBT in [hardware-aware-normalization](hardware-aware-normalization.md).
- When `--pbt-session` is provided, BO copies `num_parallel_workers` from the reference session and applies it as `--resource-division`, ensuring per-worker RAM/CPU budgets match.
- If the reference session includes `worker_resources` (a per-worker resource record), BO uses **that** for knob-range resolution rather than dividing fresh local host resources. This handles the case where the comparison is run on different hardware than the original session.
- The result JSON records `iterations`, `num_parallel_workers`, and `resource_equalization` so downstream comparison tools can confirm parity. BO never records `population_size` — it is not population-based, and pretending otherwise would mislead consumers.

If the reference PBT session is missing any of `population_size`, `total_generations`, or `num_parallel_workers`, BO falls back to its CLI defaults rather than guessing. The fall-through is logged.

---

## Ask-tell execution model

The runner has two execution paths:

- **Sequential path (default)** uses SMAC3's standard `facade.optimize()` loop with the objective function as a closure.
- **Parallel path (`--batched-bo`)** uses explicit ask-tell control inside [`runner.py`](../../src/scripts/bo_baseline/runner.py).

In ask-tell mode each batch follows this cycle:

1. `ask()` — request `num_parallel_workers` `TrialInfo` objects for the current batch.
2. Configurations are evaluated concurrently using a local `ThreadPoolExecutor`.
3. Each completed trial is returned via `tell(trial_info, TrialValue(cost=...))`.
4. After the whole batch is told, the surrogate is updated before the next `ask()` call.

The design has three intentional constraints:

- **`Scenario(n_workers=1)`** — SMAC's own parallel mode uses Dask process workers, which would force pickling of the orchestrator + environment objects (Docker clients in particular don't pickle cleanly). Keeping `n_workers=1` and parallelising in-process via threads avoids the pickling problem entirely.
- **In-process ThreadPoolExecutor** — the database environment lives in the runner's process; threads share its handle without any serialisation.
- **Per-worker previous-config tracking** — the restart-detection logic in `objective.py` needs to know "did *this worker's* previous config require a restart-on-change?" Since multiple workers may suggest different configs concurrently, that state is kept per-worker rather than globally.

---

## Implementation notes

A few specifics worth knowing when modifying the BO baseline:

### Search space translation

[`search_space.py`](../../src/scripts/bo_baseline/search_space.py) translates `KnobSpace` into a SMAC3 `ConfigSpace`:

- Integer knobs with `scale=log` clamp `min` to 1 (log(0) is undefined).
- Float knobs with `scale=log` clamp `min` to `1e-9`.
- Degenerate ranges (`min == max`) become `Constant` parameters.
- Default values are validated against bounds; if out of range, default is set to `None` (SMAC will use the midpoint).

### Objective function

[`objective.py`](../../src/scripts/bo_baseline/objective.py) returns a cost on a `[0, 100]` scale (SMAC minimises cost):

```text
cost = 100 - score                   # normal evaluation
cost = 99.0                           # benchmark timeout
cost = 99.5                           # dead PostgreSQL instance
cost = 100.0                          # unexpected exception
```

The penalty hierarchy ensures unstable configurations rank above complete failures, but both rank below any successful evaluation. The `100 - score` mapping uses score in `[0, 1]` for `feature_driven_v2` and `[0, 100]` for legacy `fixed_v1`; both produce cost in `[0, 100]`.

Restart detection runs before each evaluation and triggers a restart only when a `postmaster`-context knob changed since the last evaluation on that worker. This avoids restarting on every iteration when only `sighup` knobs changed.

### Result serialisation

[`result_writer.py`](../../src/scripts/bo_baseline/result_writer.py) emits session JSON with the same schema as PBT (see [reference/session-json-schema](../reference/session-json-schema.md)). Differences:

- `generation_history[]` has one element per BO iteration; `worker_scores` and `worker_configs` are length-1 arrays. This keeps downstream tooling (visualization loaders, evaluation suite, comparison script) workload-agnostic across PBT and BO.
- The best configuration is extracted from SMAC's incumbent at the end of optimisation, not from the maximum-score evaluation in history (those can differ if SMAC's surrogate believes a particular point is the predicted optimum even though it was not the empirically-best evaluation).
- Convergence is tracked through the per-iteration `generation_history` rather than a separate convergence record.

---

## Design decisions

### 1. SMAC3 over BoTorch / Optuna / Hyperopt

SMAC3 is the most widely-cited BO library in the systems-tuning literature (OtterTune builds on it; CDBTune compares against it; LlamaTune extends it). Using a different library would require defending why and would invite reviewers to discount the comparison.

### 2. Random Forest as the default surrogate

GP surrogates are the textbook BO choice but degrade at the dimensionality of `extensive` tier (~80 knobs) and don't handle the mixed continuous/integer/categorical search space without kernel engineering. RF is more robust to both and is the default. Users tuning low-dimensional spaces (`minimal` tier, ~5 knobs) can switch to GP via `--bo-surrogate gp`.

### 3. Pilot + Freeze instead of online recalibration

PBT's online recalibration is incompatible with surrogate-model training (see [Pilot + Freeze normalisation](#pilot--freeze-normalisation)). The freeze-after-pilot strategy is the simplest workaround that preserves the surrogate's training signal without sacrificing the ability to use feature-driven scoring.

### 4. Read-back merge required, not optional

Surrogate gradients depend on faithful input observation. Without the merge, the BO baseline would systematically underperform any optimiser that doesn't have to guess the quantisation grid — biasing the comparison against BO. The merge is non-optional.

### 5. In-process threads for parallel BO

The Dask-based parallelism path that SMAC offers natively can't pickle the Docker client. Keeping parallelism in-process via threads sidesteps the issue and matches PBT's `ThreadPoolExecutor` model, which makes the per-worker accounting (resource slicing, restart detection) straightforward to share between the two optimisers.

### 6. Cost in `[0, 100]`, not `[0, 1]`

SMAC's penalty regions (timeouts, failures) are easier to reason about with concrete cost values like 99.5 than with tiny floats like 0.005. The internal score is in `[0, 1]` for `feature_driven_v2`; the `100 - score` mapping spreads the same information across SMAC's natural numeric range.

---

## Related documentation

- **[guides/bo-baseline](../guides/bo-baseline.md)** — runbook for launching BO sessions, parameter reference, troubleshooting.
- **[guides/pbt-vs-bo-comparison](../guides/pbt-vs-bo-comparison.md)** — cross-method comparison script using BO and PBT session JSONs.
- **[pbt-core](pbt-core.md)** — the optimiser this baseline is being compared against.
- **[feature-driven-scoring](feature-driven-scoring.md)** — the scoring engine BO consumes.
- **[environment-backends](environment-backends.md)** — Docker / bare-metal backends BO shares with PBT.
- **[hardware-aware-normalization](hardware-aware-normalization.md)** — `WorkerResources` slicing, applied identically across optimisers.
- **[reference/cli](../reference/cli.md#srcscriptsbo_baseline--bayesian-optimisation-baseline)** — full BO CLI flag reference.
- **[reference/session-json-schema §BO session schema](../reference/session-json-schema.md#bo-session-schema)** — output JSON shape.
