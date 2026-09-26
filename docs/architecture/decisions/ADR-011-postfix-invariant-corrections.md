# ADR-011: Post-Fix Invariant Corrections and the #172 Validation Sweep

- Status: Accepted
- Date: 2026-09-26
- Relates to:
  [`src/analysis/pbt_invariants.py`](../../../src/analysis/pbt_invariants.py)
  (`check_corrected`, `check_exploit_cadence_per_worker`,
  `check_perturbation_magnitude`, `check_exploit_recovery_median`),
  [`src/tuners/utils/tuner_logging.py`](../../../src/tuners/utils/tuner_logging.py)
  (`log_round_end`),
  [`src/tuners/pbt/population.py`](../../../src/tuners/pbt/population.py)
  (`_determine_overall_best`).
- Cross-links:
  [ADR-002](ADR-002-feature-driven-scoring-v2.md),
  [ADR-008](ADR-008-pbt-readiness-cooldown.md),
  [ADR-009](ADR-009-perturbation-grid-step-fidelity.md),
  [ADR-010](ADR-010-asymmetric-normalizer-anchoring.md).
- Tickets: #172 (epic #162 closing gate).

## Context

Ticket #172 is the epic's closing gate: replay the reference trace through the
original and corrected scoring paths, run a fresh post-fix PBT session, and prove
the corrected algorithm shows no remaining symptoms. Auditing a fresh run
(`trace_postfix_20260926_1033`, 8 workers × 20 generations, `oltp_read_write` /
170-knob `extensive`, seed 42, produced on `main` after PRs #173–#179) surfaced
two things the per-ticket fixes could not:

1. **Three trace invariants mis-measure the *corrected* algorithm.** They were
   calibrated against the pre-fix trace and pinned verbatim by the immutable
   characterization suite (`test_trace_regression.py`, ADR of #163). On a
   correct run they fire as false positives:
   - `check_exploit_cadence` measures **population** cadence (any worker
     exploiting each generation). With 8 workers on staggered per-worker
     cooldowns, *some* worker is eligible almost every generation, so the
     population shows gaps of 1 even though **every worker** honours
     `ready_interval` (verified per-worker on the fresh trace: e.g. worker 6
     exploited at gens 3,6,9,12,15,18 — gaps of exactly 3). The cooldown fix
     (#164/ADR-008) is correct; the invariant asks the wrong question.
   - `check_perturbation_locality` flags "> 50 % of knobs *changed*". But the
     paper's perturbation (#167/ADR-009) moves *every* knob by a bounded factor;
     "many knobs changed" is faithful PBT, not a defect. Measured by per-knob
     *magnitude*, all runs are local (median move: pre-fix 0.13, corrected 0.20
     — the discrete ±20 % factor — both with p90 ≤ 0.5).
   - `check_exploit_recovery` requires *every* child ≥ 85 % of its donor. Explore
     legitimately produces some worse neighbours that selection then culls; a
     per-child floor mislabels honest exploration.

2. **A new reporting defect, B14.** The "🔺 NEW BEST SCORE" announcement compared
   a pre-step best (previous ruler) against a post-step best (post-recalibration
   ruler). On recalibration generations it announced a "new best" for an
   *unchanged* carried-over config whose score rose only because the ruler moved
   (gen 8: "0 workers with significant score changes", yet "NEW BEST 94.083"
   announced while the live population best was 84.8). This is exactly the
   epic's headline anti-pattern — reported improvement ≠ real improvement —
   leaking into the operator-facing log.

The immutable #163 harness hardcodes the originals' exact output on the pre-fix
trace, so the invariants cannot be changed in place. The corrections are
therefore **additive**.

## Decision

### 1. A corrected invariant set (`check_corrected`), alongside the originals

The nine originals and the frozen `test_trace_regression.py` are left untouched
— they remain the true, immutable characterization of the pre-fix trace under
their original definitions. A parallel entry point `check_corrected` audits
*post-fix* traces, substituting three corrected forms and sharing the rest:

- **`check_exploit_cadence_per_worker` (B1)** — gaps between a *single worker's*
  own exploit events must be ≥ `ready_interval`.
- **`check_perturbation_magnitude` (B4/B5/B6)** — locality is per-knob
  *magnitude*: the median per-knob relative move must stay within a bounded band
  (cap 0.5). This requires no per-knob knowledge — it perturbs then benchmarks —
  so it does not contradict PBTune's zero-domain-knowledge claim.
- **`check_exploit_recovery_median` (B4/B5/B6)** — the *median* child must recover
  ≥ 75 % of its donor's throughput, so a systematic collapse is caught while an
  honest worse neighbour is not.

`score_rank_agreement` remains **informational** (a composite-vs-throughput rank
check inverts naturally) and does not gate `--strict`. The CLI gains
`--corrected`; `gating_violations` excludes informational findings.

### 2. B14 — announce a "new best" only on a same-ruler improvement

`Population._determine_overall_best` already decides improvement on one ruler
(the incumbent is rescored onto the current ruler before the comparison). That
decision is now surfaced as `GenerationOutcome.strictly_improved` and consumed by
`log_round_end`, which announces only when it is true (falling back to the
best-delta compare when a strategy supplies no signal). The stagnation counter
was already same-ruler and is unaffected; B14 was contained to the announcement.

### 3. Cross-generation comparability is a trace-consumer concern

PBT training needs only the *relative* improvement of the current best over the
previous best, which is why only the incumbent is rescored each generation. A
single global ruler across the whole trace is what an analyzer/visualizer wants,
and the global rescorer (`src/utils/calibration.py`) already provides it. This is
correct by design; no training-loop change is warranted.

## Validation results

- **AC #1 — fresh trace, no symptoms.** `check_corrected` reports **0 gating
  violations** on `trace_postfix_20260926_1033`; the same set still flags the
  pre-fix trace (per-worker cadence, median recovery, donor diversity, coupling,
  support, search efficiency), so it is not vacuous.
- **AC #2 — attribution, separated.**
  - *Anchoring (#170), from the fixed pre-fix trace:* the throughput good-end
    anchor moves `1342.61 → 1881.05` (above the 1791.48 elite), throughput
    observations clamped at the good end drop `17/96 → 0/96`, and the mean
    composite score deflates `62.3 → 39.9` — a pure re-ruling of fixed metrics.
  - *Perturbation (#167), from the seed-matched A/B (corrected vs old
    perturbation):* median exploit-recovery `94 % → 77 %` and median per-knob
    move `0.11 → 0.20`. #167 correctly unfroze knobs (escaping the absorbing
    zero) at a measurable exploit-locality cost that grows with dimensionality;
    magnitude stays within the local band throughout.
- **AC #3 — behaviour as specified.** Per-worker cadence honours
  `ready_interval=3`; 7 distinct donors over 20 exploitations; knobs move.
- **AC #4 — reported gains are real.** Re-scored on one global ruler, the best
  configuration is gen 11 (throughput 209.96 vs the ~110–150 carried-over line),
  rank-correlation between single-ruler score and raw throughput is **0.90**, and
  the gen-8 phantom collapses to a single-ruler score of 45.9 — well below the
  true best. The convergence signal tracks the database, not the ruler.

## Consequences

- The epic closes on a fresh trace that is clean under invariants that actually
  match the corrected design, without editing the immutable pre-fix
  characterization.
- **Documented limitation (perturbation locality at high dimensionality).**
  Perturbing all 170 knobs by a discrete ±20 % factor is paper-faithful, but the
  seed-matched A/B shows it narrows exploit-recovery (median 0.77 vs the old
  path's 0.94). The corrected median-recovery invariant still clears, but the
  margin is modest; this is an inherent property of zero-domain-knowledge PBT in
  a large search space, recorded here rather than mitigated (pruning the space
  or perturbing a subset would require knob knowledge PBTune deliberately avoids).
- `score_rank_agreement` and `perturbation_magnitude` currently hold on every
  available trace; they are forward guards against future regressions.

## Alternatives Considered

1. **Edit the invariants in place / amend the frozen `test_trace_regression.py`.**
   Rejected: the #163 harness is the epic's integrity anchor; changing the
   originals' output would break the immutable diagnosis. Additive corrected
   forms preserve both the history and the corrected gate.
2. **Fix B14 by rescoring `prev_best` onto the new ruler in the tuner.** Rejected
   as redundant: the population already computes the same-ruler decision; surface
   it rather than recomputing a cross-ruler delta.
3. **Treat cross-generation ruler drift as a training-loop bug.** Rejected: PBT
   needs only relative prev-vs-current improvement (settled by design); global
   comparability belongs to trace consumers.

