# ADR-010: Asymmetric, Direction-Aware Normalizer Anchoring

- Status: Accepted
- Date: 2026-09-25
- Relates to:
  [`src/utils/scoring/normalization.py`](../../../src/utils/scoring/normalization.py)
  (`QuantileUtilityNormalizer.fit`, `QuantileUtilityNormalizer._fit_metric_anchor`).
- Cross-links: [ADR-002 — Feature-Driven Scoring v2](ADR-002-feature-driven-scoring-v2.md)
  (the scoring-v2 stack whose normalizer this decision corrects);
  [ADR-008](ADR-008-pbt-readiness-cooldown.md),
  [ADR-009](ADR-009-perturbation-grid-step-fidelity.md),
  [ADR-011](ADR-011-postfix-invariant-corrections.md) (epic #162 siblings).
- Tickets: #170 (bug B9), epic #162. Builds on the direction-aware anchor
  mapping introduced for #169 (bug B8) in `expand_metric_anchor`.

## Context

Scoring-v2 (ADR-002) maps each raw metric to a `[0, 1]` utility through
`QuantileUtilityNormalizer`, then combines the utilities into the composite
score `S = 100 · G · Σ(wᵢ · uᵢ) / (1 − w_error)`. The utility mapping is
governed by three per-metric anchors `(direction, q_low, q_high)`: a value is
clamped into `[q_low, q_high]`, linearly normalized, and inverted for
`LOWER_IS_BETTER` / `ZERO_IS_BEST` metrics.

`fit()` calibrated those anchors **symmetrically**: it ran a two-sided
`iqr_filter(k=2.5)` and then took the `p05` / `p95` quantiles of the filtered
sample for *both* ends. Trimming both tails discards the single best (elite)
observation in the population. Because the elite then sits *outside* the fitted
range on the good side, it clamps to the good-end anchor and scores exactly the
maximum utility — and so does every future observation that improves on it. The
scorer goes blind to progress exactly where selection pressure matters most.

This is bug **B9** in epic #162. The regression fixture
`trace_20260916_0007.json` shows it concretely: the fitted throughput anchor
stopped at `q_high = 1342.61` while the observed elite was `1791.48`, so **17 of
96** throughput observations clamped at utility `≥ 0.999` and roughly the top of
the objective weight became invisible. The `check_normalizer_support` invariant
flags the same effect for the latency metrics, which clamped 27–32 % of their
observations.

The `iqr_filter` trim is still wanted on the **bad** end: a single hung query
producing a 600 ms → multi-second latency, or a broken configuration producing
near-zero throughput, must not drag the bad-end anchor out to it and collapse
score variance. The defect is specifically the *symmetry* — trimming the good
tail as well.

## Decision

Calibrate anchors **asymmetrically and direction-aware**. Every metric has a
*good* end (the optimization target) and a *bad* end. The bad end keeps its
robust treatment; the good end is anchored to cover the best observation.

`_fit_metric_anchor(values, direction)` computes, per metric:

1. **Bad tail — robust, one-sided.** Apply the existing `iqr_filter(k=2.5)`
   bound to the bad tail *only* (drop values below `Q1 − k·IQR` for a
   `HIGHER_IS_BETTER` metric, above `Q3 + k·IQR` for a `LOWER`/`ZERO` one), then
   take the `p05` / `p95` quantile of the retained sample. This is the bad-end
   anchor (`u = 0`). Its robustness is unchanged from the pre-fix path.
2. **Good tail — never discarded.** Take the best observation from the *full*
   (untrimmed) sample — `max` for `HIGHER_IS_BETTER`, `min` for
   `LOWER`/`ZERO` — and place the good-end anchor (`u = 1`) strictly beyond it
   with headroom.

Direction decides which numeric anchor is good:

| Direction | Good end | Bad end |
|-----------|----------|---------|
| `HIGHER_IS_BETTER` | `q_high = max(obs) + headroom` | `q_low` = robust low quantile |
| `LOWER_IS_BETTER` / `ZERO_IS_BEST` | `q_low = min(obs) − headroom` | `q_high` = robust high quantile |

This reuses the same utility↔raw-end mapping that `expand_metric_anchor` was
made direction-aware for in #169 (bug B8), so calibration and saturation-driven
expansion now agree on which raw anchor is the good end.

### Headroom

```text
span     = |best − bad_anchor|
headroom = max(GOOD_END_HEADROOM_FRACTION · span,
               GOOD_END_HEADROOM_FRACTION · |best|,
               MIN_GOOD_END_HEADROOM)
```

with `GOOD_END_HEADROOM_FRACTION = 0.05` and `MIN_GOOD_END_HEADROOM = 1e-6`.
The `|best|` term keeps the headroom meaningful when the sample is tightly
clustered; the absolute floor keeps it positive when the best value is `~0`. A
lone breakout elite therefore scores just under `1.0` (`≈ 0.88` for the fixture's
`1791.48`), leaving room for further improvement to keep rising.

The headroom may push the good-end anchor slightly past the metric's physical
range — for example a hair below `0` for a non-negative metric such as
`throughput_variance`. This is intentional and harmless: no real observation can
reach it, and it only guarantees the best observation is strictly interior
(never clamped at the good end). It deliberately does **not** clamp the good-end
anchor back to `0`, because doing so would re-clamp genuine zero-valued optima
and re-introduce good-end saturation.

### Degenerate (near-constant) metrics

When the sample has no spread (`span ≈ 0`), there is nothing to discriminate.
`_fit_metric_anchor` falls back to the legacy neutral window (`[0.9·v, 1.1·v]`,
or `[0, 1e-6]` at `v = 0`) so a uniform population scores `~0.5` rather than
being pinned at an arbitrary extreme. This preserves the pre-fix behaviour for
constant metrics (e.g. `throughput_variance` on single-stream TPC-H).

## Consequences

- The population's current champion is never clamped at maximum utility by
  construction, so the scorer stays sensitive to improvement throughout the run.
  On the regression fixture, throughput observations clamped at utility `≥ 0.999`
  drop from **17/96 to 0/96**, and the good-end anchor moves from `1342.61`
  (below the elite) to `1881.05` (above it).
- `check_normalizer_support` turns green on corrected data: covering the good
  end removes the good-end clamping that dominated the latency breaches, so
  total clamping falls below the 25 % ceiling. The immutable characterization
  test `test_trace_regression.py` keeps the **old** violation, because a
  recorded trace is immutable evidence; the invariant is driven red→green at the
  normalizer seam in this ticket's tests instead.
- Absolute utility levels shift down relative to the buggy path (the buggy path
  inflated them by clamping good ends to `1.0`). On the fixture the mean
  equal-weight composite utility moves from `0.546` to `0.310`. This is a
  rescaling of the ruler, not a regression: relative ranking and — crucially —
  the ability to *track* improvement across generations are what the fix
  restores. Historical score values are not comparable across this change; the
  session metadata records the anchors so post-hoc rescoring is exact.
- Bad-tail robustness is unchanged: pathological-but-not-failed measurements are
  still trimmed off the bad end before the quantile is taken. Failure-tagged
  measurements are excluded upstream by the reliability gate and are not
  re-handled here.

## Alternatives Considered

1. **Widen both symmetric anchors (larger `k`, or `p01`/`p99`).** Rejected: it
   only postpones the clamp. Any fixed quantile still discards the single best
   observation, so a breakout elite eventually saturates again.
2. **Anchor the good end at the best observation exactly (no headroom).**
   Rejected: the best worker would score exactly `1.0`, so an improvement over
   it would be invisible until the next recalibration — a weaker form of the
   same blindness.
3. **Drop robust trimming entirely and anchor both ends at min/max.** Rejected:
   it reinstates the single-outlier fragility that motivated the quantile
   normalizer in the first place; a lone hung query would collapse score
   variance for a dozen generations.
