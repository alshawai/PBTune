# ADR-009: Perturbation Grid-Step Fidelity and the Auto-Size Sentinel Hazard

- Status: Accepted
- Date: 2026-09-24
- Relates to:
  [`src/knobs/knob_space.py`](../../../src/knobs/knob_space.py)
  (`KnobSpace.perturb_config`, `KnobSpace._perturb_numeric_value`,
  `KnobDefinition._normalize_integer`),
  [`src/knobs/knob_loader.py`](../../../src/knobs/knob_loader.py),
  [`src/tuners/engine/orchestrator.py`](../../../src/tuners/engine/orchestrator.py)
  (`_verify_and_capture_config`, read-only for this decision).
- Tickets: #167 (grid-step fidelity, bugs B4/B5/B6), #171 (non-finite rejection,
  bug B10), and the B12 read-back-merge settle.

## Context

PBT exploration perturbs a parent's configuration to produce a neighbour. The
canonical PBT recipe (Jaderberg et al., 2017, *Population Based Training of
Neural Networks*) multiplies each hyperparameter by a **discretely chosen**
factor — the paper uses `{0.8, 1.2}` — so every perturbation is a definite step
up or down.

The implementation had drifted from that recipe in three compounding ways, all
on the numeric branches of `perturb_config`:

- **B4 — continuous factor.** The factor was drawn with
  `rng.uniform(0.8, 1.2)`. Roughly half the draws land near `1.0`, producing a
  near-zero multiplicative delta — the parameter barely moves, wasting the
  exploration step.
- **B5 — truncating normalization.** `KnobDefinition._normalize_integer` cast
  with `int(value)`, which truncates toward zero. Every integer perturbation was
  biased *downward*: an up-factor such as `4 * 1.2 = 4.8` truncated straight
  back to `4`, so a small integer could never grow.
- **B6 — absorbing zero.** A multiplicative update leaves zero fixed
  (`0 * factor == 0`), so any knob sitting at `0` (and there are many —
  `max_parallel_workers`, `effective_io_concurrency`, `bgwriter_lru_maxpages`,
  the log-scale `min_parallel_*_scan_size`, …) was frozen there for the whole
  run.

Together these made the "exploration" step systematically shrink integer knobs
and pin the zero-valued ones.

Separately, one knob carried a non-finite value (B10): `ssl_max_protocol_version`
in the extensive tier has an empty PostgreSQL boot value. `pandas` reads the
empty cell as `NaN`, and the loader's `boot_val or value` fallback preserved it
because `NaN` is truthy. Since `NaN != NaN`, the knob registered as a spurious
configuration change on *every* generation and would have reached PostgreSQL as
the literal string `'nan'`.

## Decision

### 1. Restore discrete, grid-faithful numeric perturbation (#167)

Both numeric branches now route through `KnobSpace._perturb_numeric_value`,
implementing the prototype algorithm:

```text
factor    = choice(perturbation_factors)     # discrete, per Jaderberg et al.
delta     = value * (factor - 1.0)
step      = grid quantum for this knob
if abs(delta) < step:                        # too small to move the grid
    delta = step * sign(factor - 1.0)        # force >= one grid step
new_value = normalize_value(value + delta)   # rounds (int) + clamps to bounds
```

- The factor is drawn **discretely** from the configured pair.
- `_normalize_integer` now **rounds** (`int(round(value))`) instead of
  truncating, removing the downward bias (B5).
- When the multiplicative delta is smaller than one grid step, the move is
  forced to exactly one step in the factor's direction — this escapes the
  absorbing zero (B6) and guarantees every perturbation either moves the knob by
  at least one grid step or is clamped at a bound.
- The **grid quantum** is `1` for integers (or the knob's aligned `step` when
  defined). Reals are continuous, so their quantum is a tiny fraction of the
  span (`REAL_MIN_STEP_FRACTION = 1e-3`); ordinary multiplicative deltas dwarf
  it, so only the degenerate near-zero regime is affected.
- **Log-scale intent is preserved.** With a discrete factor the additive and
  multiplicative forms coincide (`value + value*(factor-1) == value*factor`), so
  the update stays geometric/symmetric while also being able to leave zero. The
  previous special-cased log branch (which itself barely moved and required
  `value > 0`) is subsumed.

This is behaviour-preserving wherever the multiplicative delta already exceeds
one grid step; only the degenerate small-value regime changes. Existing
bound-clamping and `repair_config_dependencies` behaviour are unchanged.

### 2. Reject non-finite knob values at load and construction (#171)

- **Source fix (`knob_loader`).** Default resolution no longer relies on the
  truthiness of `NaN`. It takes `boot_val` when finite, else `value`, and for
  ENUM knobs falls back to the first enum member (the canonical `''`
  no-op value for `ssl_max_protocol_version`) when the value is missing/`NaN`.
- **Construction guard (`KnobSpace.__init__`).** Construction now rejects any
  knob whose `min_value`, `max_value` or `default` is a floating-point `NaN`/
  `inf`, with an actionable `ValueError`. Ints, strings, booleans and `None`
  remain valid.

### 3. B12 read-back merge — DROPPED (neutralised upstream)

`orchestrator._verify_and_capture_config` merges the values PostgreSQL reports
back into `worker.knob_config`. The concern was that for an auto-size knob whose
request is a sentinel (`wal_buffers = -1`, `*_buffers = 0`), PostgreSQL reports
the *resolved* concrete value, so the merge could overwrite the sentinel and
ratchet the knob across generations.

Investigation at the knob-space seam shows the sentinels never reach the merge:
the tuned sentinel knobs have bounds that clamp their auto-sentinel to a
concrete in-domain value at normalization time, before any evaluation.

| Knob | Auto sentinel | Bound | `normalize_value(sentinel)` |
|------|---------------|-------|-----------------------------|
| `wal_buffers` | `-1` | min 64 | `64` |
| `commit_timestamp_buffers` | `0` | min 16, step 16 | `16` |
| `subtransaction_buffers` | `0` | min 16, step 16 | `16` |
| `transaction_buffers` | `0` | min 16, step 16 | `16` |
| `io_max_concurrency` | `-1` | min −1 | `-1` (retained) |

So the merge only ever sees, and re-persists, an already concrete value, which
is stable generation-to-generation. This matches the empirical #163 result of
**zero cross-generation ratchets across 73 clean worker-generations**.
`io_max_concurrency` retains `-1`, but that *is* the value PostgreSQL also
resolves to (auto), so it too is stable. **B12 is therefore dropped: it is not a
live defect in the current configuration.** No orchestrator change is made.

### The "-1 means auto-tune" hazard (recorded for the future)

The disposition above depends on bounds clamping the sentinels away. The general
hazard remains real for any *future* code: a knob value of `-1` or `0` may be a
PostgreSQL "auto-tune / derive it for me" sentinel, not a literal setting. Any
code that persists a read-back value over such a sentinel (for example, a future
change that relaxes `wal_buffers`' lower bound to expose the auto value, or a new
read-back-caching path) would silently destroy the auto-tune semantics and pin
the derived value. Sentinel-valued knobs must be handled explicitly (skip
persisting their read-back, or model the sentinel as a distinct categorical
choice) rather than round-tripped as ordinary integers.

## Consequences

- Perturbation is a genuine, unbiased exploration step again: discrete factors,
  no downward truncation bias, and no absorbing zero. Convergence behaviour on
  small integer and zero-valued knobs is materially different from the buggy
  path (this is the intended correction, not a regression).
- No non-finite value can enter a `KnobSpace`; the spurious per-generation diff
  for `ssl_max_protocol_version` is gone.
- B12 requires no code change; the hazard is documented so future read-back
  persistence is designed with sentinels in mind.
- Rounding rather than truncating in `_normalize_integer` slightly shifts
  resolved integer values elsewhere (for example hardware-relative
  fraction→absolute conversions round to the nearest unit instead of down); this
  is the more correct behaviour and is intended.
