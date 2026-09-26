# ADR-008: PBT Readiness Cooldown (recurring, not one-shot)

- Status: Accepted
- Date: 2026-09-24
- Cross-links:
  [ADR-002](ADR-002-feature-driven-scoring-v2.md),
  [ADR-009](ADR-009-perturbation-grid-step-fidelity.md),
  [ADR-010](ADR-010-asymmetric-normalizer-anchoring.md),
  [ADR-011](ADR-011-postfix-invariant-corrections.md) (epic #162 siblings).

## Context

Population-Based Training gates exploit/explore on a per-member *ready*
criterion. Jaderberg et al. (2017), §4.1 (`ready(p, t, P)`) measure readiness as
the number of optimisation steps elapsed **since that population member last
became ready** — after a member exploits (copies another member's weights and
hyperparameters), its readiness clock restarts, so it must serve a full
`ready_interval` again before it can exploit once more. The ready interval is a
*cooldown*, not a one-time warm-up.

Our implementation diverged from the paper. `PBTWorker.is_ready()` returns
`step_count >= ready_interval`, and `step_count` was only ever incremented (by
`update_metrics`) and never reset. `clone_from` — the exploit operator — copied
the elite's `knob_config` but left `step_count` intact (its docstring even said
`step_count (maintained)`). The consequence:

- Once any worker's `step_count` crossed the threshold it stayed ready **for the
  rest of the run**. The ready gate degenerated into a single population-wide
  warm-up period, after which it was permanently open.
- A worker that adopted an elite configuration kept the donor-era `step_count`,
  so it was immediately eligible again and re-exploited on the very next
  generation.

In the reference run `trace_20260916_0007` (8 workers, `ready_interval=3`), this
produced exploitation on **every** generation from generation 2 through 11 —
gaps of 1 where the interval demanded ≥ 3. The regression is catalogued as **bug
B1** and detected by the `exploit_cadence` invariant
(`src/analysis/pbt_invariants.py`). Back-to-back exploitation collapses search
diversity: combined with a one-member elite bucket it meant 96 evaluations
explored only 18 distinct configurations.

Documentation compounded the confusion: `docs/architecture/pbt-core.md` claimed
`clone_from` "resets `step_count`" (it did not) and claimed the dead-worker
rescue path left `step_count` running *so that* a rescued worker "cannot
immediately be re-ranked as poor" — the opposite of what *not* resetting a
counter does.

## Decision

Restore the paper's recurring-cooldown semantics at the worker seam:

1. `PBTWorker.clone_from()` resets `step_count` to `0` when a worker adopts a new
   configuration. Adoption re-arms the readiness cooldown; the worker must
   complete `ready_interval` fresh evaluations before `is_ready()` is true again.
2. `step_count` is redefined as "evaluations completed since this worker last
   adopted a configuration" — the readiness cooldown — rather than a lifetime
   evaluation count. Lifetime measurement history remains available via
   `performance_history`.
3. Documentation (`pbt-core.md`) is corrected so the `clone_from` behaviour and
   the dead-worker-rescue narrative match the code: the alive-donor rescue
   branch re-arms the cooldown through `clone_from`; the no-alive-donor resample
   branch leaves `step_count` untouched (moot, because that branch runs only when
   the whole population is dead and dead workers bypass the readiness gate).

The eligibility gate itself (`truncation_selection(require_ready=...)`) and the
whole-population quantile basis are unchanged; this ADR only fixes *when the
readiness clock restarts*.

## Consequences

Positive:

- The exploit cadence honours `ready_interval`: consecutive exploit generations
  for a given member are now at least `ready_interval` apart, satisfying the
  `exploit_cadence` (B1) invariant at the code seam.
- Diversity is preserved across generations — a just-exploited worker gets a full
  interval to be measured under its new configuration before it can be judged
  poor again, which is exactly the noise-cascade guard the paper intends.
- `step_count` now means what its readiness use requires, removing a latent trap
  for future changes.

Trade-offs / scope:

- This is a behavioural change to the tuning loop: post-fix runs will exploit
  less often than pre-fix runs at the same `ready_interval`. Historical traces
  recorded before the fix (e.g. `trace_20260916_0007`) are immutable evidence and
  are characterised by the frozen `test_trace_regression.py` suite, which stays
  as-is; the fresh-trace end-to-end replay is ticket #172's responsibility.
- The correction overlaps issue #161 (broader `clone_from`/doc drift, including
  the stale `environment=`/`exclude_knobs=` signature in the docs). This ADR
  closes only the `step_count`/readiness facet; #161 remains open.

## Rejected Alternatives

1. **Reset `step_count` inside `truncation_selection` / `execute_exploit_explore`
   instead of `clone_from`.** Rejected: readiness is worker state, and every
   adoption path (exploit, alive-donor rescue) already funnels through
   `clone_from`. Resetting there keeps the invariant local and prevents a future
   caller from adopting a config without re-arming the cooldown.
2. **Introduce a separate `last_ready_step` field and leave `step_count` as a
   lifetime counter.** Rejected as unnecessary state: nothing in the tuning loop
   consumes a lifetime per-worker evaluation count (`total_evaluations` is derived
   from `population_size × rounds_completed`), and `performance_history` already
   preserves the full measurement record.
3. **Change only the documentation to say the cooldown is one-shot by design.**
   Rejected: the one-shot behaviour is a defect against the cited algorithm, not
   an intentional deviation, and it measurably degraded search.

## References

- Jaderberg et al. (2017), *Population Based Training of Neural Networks*, §4.1
  (the `ready` criterion measured since a member last became ready).
- Bug ledger B1; `exploit_cadence` invariant — `src/analysis/pbt_invariants.py`.
- Code seam — `src/tuners/pbt/worker.py` (`PBTWorker.clone_from`, `is_ready`).
- Seam tests — `tests/unit/tuners/pbt/test_ready_cooldown_cadence.py`.
- Frozen symptom characterisation — `tests/unit/tuners/pbt/test_trace_regression.py`.
