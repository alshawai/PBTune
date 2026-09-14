---
name: pbt-algorithm-patterns
description: Population-Based Training algorithm implementation patterns, evolutionary optimization conventions, and PBT-specific coding standards for the database tuning research project. Use this skill whenever working on PBT core logic, evolution, worker management, population management, scoring, normalization, convergence detection, exploit-explore mechanics, or any code in src/tuners/pbt/.
---

# PBT Algorithm Patterns

This skill encodes the domain knowledge for implementing and modifying the Population-Based Training algorithm in this research project.

## PBT Lifecycle

The PBT algorithm follows this exact lifecycle per generation:

```
INITIALIZE population P = {w₁, ..., wₙ} via Latin Hypercube Sampling (LHS)
FOR generation g = 1, ..., G:
    FOR each worker wᵢ ∈ P (in parallel via ThreadPoolExecutor):
        Apply wᵢ.config to PostgreSQL instance i
        Run workload benchmark, measure metrics
        wᵢ.score = CompositeScorer.compute_breakdown(metrics, features).final_score
    
    Once ≥ max(20, 5·population_size) non-failure samples accrued (one-time):
        Calibrate normalization ranges from observed data (adaptive normalization)
    
    IF any metric saturated (normalized component ≥ 0.95):
        Widen the saturated anchor (~20% of current range), rescore
    
    Truncation selection:
        bottom = workers in bottom Q% by score
        top = workers in top Q% by score
    
    FOR each (bad, good) in zip(bottom, top):
        IF bad.is_ready(ready_interval):
            bad.clone_from(good)          # Exploit: copy config + score
            bad.perturb(knob_space)       # Explore: randomly perturb config
```

## Key Invariants

### Evolution Module is Stateless
All functions in `evolution.py` are **pure functions** operating on worker lists. They take workers as input and return results — no side effects, no state mutation within the module itself. State lives in `Worker` objects and `Population` class.

### Worker State Machine
```
Worker lifecycle:
  init (via LHS) → evaluate → update_metrics → is_ready check
                                                     ├── not ready → evaluate again
                                                     └── ready → truncation selection
                                                                    ├── top Q% → unchanged
                                                                    └── bottom Q% → clone_from(top) → perturb()
```

### Scoring Formula

The canonical scoring contract is:

```
S = 100 × G × Σ(w_i × u_i) / (1 − w_error)
```

Where:
- **G** ∈ [0, 1] — reliability gate (0 on fatal failure or high error rate)
- **w_i** — metric weight from the active scoring policy (Σw_i = 1)
- **u_i** ∈ [0, 1] — normalized utility from the quantile normalizer
- **S** ∈ [0, 100] — final bounded score
- **w_error** — the `error_rate` weight; `error_rate` is excluded from Σ and the `(1 − w_error)` denominator renormalizes the remaining weights to sum to 1

Active scoring policies (see `src/utils/scoring/policies.py`):
- `feature_driven_v2` — default for new runs (dynamic weights from workload features)
- `fixed_v1` — compatibility-only static weights

See the `scoring-pipeline` skill for full details on `CompositeScorer`,
`QuantileUtilityNormalizer`, and feature-driven weights.

### Adaptive Normalization
- Activates once ≥ max(20, 5·population_size) non-failure samples accrue (one-time, guarded by `_ranges_calibrated`; ≈ gen 5 — not a fixed generation index)
- Uses 5th/95th percentiles of observed latency and throughput
- Scoring flows through `CompositeScorer` (via `create_scoring_engine`); `MetricConfig.compute_score()` no longer exists
- Purpose: prevent scoring from being dominated by arbitrary initial range estimates

### Saturation Detection
- Checks if any normalized metric component ≥ 0.95
- When detected: widen the saturated anchor by ~20% of current range via `expand_metric_anchor` (PBT passes `expansion_factor=0.25`, but that parameter is deprecated/ignored)
- Purpose: restore discrimination power when metrics hit normalization ceiling

### Convergence Detection
- Formula: `std(scores) / mean(scores) < threshold`
- Triggers early stopping when population has converged

### Dead Worker Rescue
When a worker scores 0.0 (PostgreSQL crash or benchmark failure), it gets rescued
with a diversity-preserving resampling strategy:
- `_choose_diverse_resample_config()` picks config maximizing distance from alive workers
- Uses `_config_change_ratio()` — fraction of knobs that differ significantly
- Worker's `step_count` resets to 0 (must re-earn readiness)
- Purpose: prevent dead workers from being selected in truncation and wasting exploit slots

### Population Diversity
- `GenerationResult.std_score` tracks score standard deviation per generation
- Used for convergence detection AND diversity monitoring
- `record_generation()` captures: best, mean, std, median, all worker scores
- Exploit events are logged with source/target worker IDs for lineage tracking

## Code Locations

| Component | File | Key Class/Function |
|-----------|------|-------------------|
| Orchestrator | `src/tuners/pbt/tuner.py` | `PBTTuner` |
| Population | `src/tuners/pbt/population.py` | `Population`, `PopulationConfig`, `GenerationResult` |
| Evolution | `src/tuners/pbt/evolution.py` | `truncation_selection()`, `execute_exploit_explore()`, `get_best_worker()`, `check_convergence()` |
| Generation barriers | `src/tuners/engine/barriers.py` | B1..B17 lockstep barriers |
| Worker | `src/tuners/pbt/worker.py` | `Worker` |
| Scoring | `src/utils/scoring/scorer.py`, `src/utils/metrics.py` | `CompositeScorer`, `PerformanceMetrics`, `WorkloadType` |

## Design Decisions (Deviations from Original PBT Paper)

1. **Resampling omitted:** Original PBT paper includes resampling-when-stuck. We omit this because exploit (clone from top) + explore (perturb) already addresses stuck configurations via truncation selection. Documented as potential future enhancement.

2. **Adaptive normalization:** Not in original PBT paper. Added because database benchmarks have unknown metric ranges a priori (unlike neural network loss which has known bounds).

3. **Saturation detection:** Extension to handle metrics approaching normalization ceiling, which is specific to the multi-objective scoring formula used in database tuning.

## Reference Files
- Read `references/pbt-lifecycle.md` for detailed generation-by-generation data flow
- Read `references/scoring-formula.md` for metric computation edge cases and clamping behavior
