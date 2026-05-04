---
name: scoring-pipeline
description: >
  Feature-driven scoring pipeline (scoring-v2) including the CompositeScorer, QuantileUtilityNormalizer,
  FeatureDrivenWeightModel, scoring policies (fixed_v1, feature_driven_v2), workload feature extraction,
  reliability gating, drift detection, and saturation expansion. Use this skill whenever working on
  score computation, metric normalization, metric weighting, scoring policies, workload features,
  calibration, rescoring, normalization drift, saturation detection, or any code in src/utils/scoring/,
  src/utils/metrics.py, or src/utils/rescoring.py. Also use when debugging score values, investigating
  why a worker scored unexpectedly, or modifying the scoring contract.
---

# Scoring Pipeline (v2)

The scoring pipeline converts raw `PerformanceMetrics` into a single scalar reward
signal for the PBT evolutionary loop.

## Scoring Contract

```
S = 100 × G × Σ(w_i × u_i)
```

Where:
- **G** ∈ [0, 1] — reliability gate (0 on fatal failure or high error rate)
- **w_i** — metric weight from the active policy (Σw_i = 1)
- **u_i** ∈ [0, 1] — normalized utility from the quantile normalizer
- **S** ∈ [0, 100] — final bounded score

## Two Scoring Policies

| Policy | ID | Type | Metrics |
|--------|----|------|---------|
| Legacy | `fixed_v1` | Static weights per workload type | 6 metrics (latency_p50/p95/p99, throughput, memory_util, error_rate) |
| Feature-Driven | `feature_driven_v2` | Dynamic weights from workload features | 10 metrics (adds variance, tail_amp, pressure, scan_eff, buffer_miss) |

### fixed_v1 Weights (hardcoded)
- OLTP: latency_p95=0.50, throughput=0.30, memory=0.05, error=0.15
- OLAP: latency_p95=0.80, memory=0.05, error=0.15
- Mixed: latency_p95=0.40, throughput=0.40, memory=0.05, error=0.15

### feature_driven_v2 Weight Computation
1. **Extract features** via `WorkloadFeatureExtractor` (10 canonical features)
2. **Compute logits**: `w_i = base_i + Σ_j(M_ij × f_j)` — `working_set_millions` passes through `log1p()` to prevent softmax domination
3. **Temperature-scaled softmax** (numerically stable)
4. **Floor constraint**: `W_i = α_i + (1 - Σα) × S_i` — guarantees minimum weight for critical metrics

V2 floors: latency_p95=0.10, throughput=0.10, error_rate=0.05, latency_p99=0.05 (total=0.30)

## Normalization (QuantileUtilityNormalizer)

Uses robust p05/p95 quantile anchors (NOT min/max) to prevent single-outlier score collapse.

### Key Behaviors
- **Calibration**: `fit()` computes anchors from observed metric distributions; requires metric whitelist to avoid noisy non-scoring fields
- **Metric direction**: Heuristic from name — latency/variance/error = lower-is-better; throughput/efficiency = higher-is-better
- **Naturally bounded metrics**: error_rate, memory_pressure, buffer_miss_rate, scan_efficiency bypass quantile anchoring (already [0,1])
- **NEVER_ZERO filter**: latency_p99/p95/p50, latency_variance, tail_amplification filter out zero observations (extraction artifacts)
- **Drift detection**: Tracks out-of-support rate per metric; triggers recalibration when rate exceeds threshold (default 20%)
- **Saturation detection**: `detect_metric_saturation()` finds metrics where ≥2 workers hit the same bound
- **Anchor expansion**: `expand_metric_anchor()` widens the saturated side by 20% of current range
- **Export/import**: `export_state()` / `import_state()` for reproducible rescoring

### Calibration Flow in Population
```
Generation 0: Evaluate all workers → raw metrics collected
              fit() normalizer with metric_whitelist = policy metrics
              Calibration complete → is_calibrated = True

Generation 1+: score_vector() produces utilities
               update() tracks history + out-of-support counts
               needs_recalibration() → if True, rebuild dataset + refit
               detect_metric_saturation() → if saturated, expand_metric_anchor()
```

## Reliability Gate

```python
if failure_type is not None:     return 0.0   # Fatal failure
if error_rate >= threshold:       return 0.0   # Too many errors (default: 5%)
if error_rate > 0:                return 1.0 - (error_rate / threshold)  # Linear decay
else:                             return 1.0   # Clean run
```

## Workload Feature Extraction

10 canonical features extracted per benchmark type:

| Feature | Description | Range |
|---------|-------------|-------|
| `read_ratio` / `write_ratio` | R/W split | [0, 1] |
| `olap_complexity` | Query complexity score | [0, 1] |
| `join_intensity` | JOIN clause prevalence | [0, 1] |
| `aggregation_intensity` | GROUP BY/AGG prevalence | [0, 1] |
| `sort_intensity` | ORDER BY prevalence | [0, 1] |
| `concurrency_pressure` | threads/cpu_cores ratio | [0, 1] |
| `working_set_millions` | Total rows / 1M | [0, ∞) |
| `query_mix_entropy` | Normalized Shannon entropy | [0, 1] |
| `tail_latency_sensitivity` | Tail latency importance | [0, 1] |

Extractors: `extract_sysbench_features()`, `extract_tpch_features()`, `extract_template_features()`

## Code Locations

| Component | File |
|-----------|------|
| Composite scorer | `src/utils/scoring/scorer.py` |
| Normalizer | `src/utils/scoring/normalization.py` |
| Weight model | `src/utils/scoring/weights.py` |
| Policies | `src/utils/scoring/policies.py` |
| Workload features | `src/utils/scoring/workload_features.py` |
| Contracts | `src/utils/scoring/contracts.py` |
| Constants | `src/utils/scoring/constants.py` |
| Legacy metrics | `src/utils/metrics.py` |
| Post-hoc rescoring | `src/utils/rescoring.py` |

## Common Pitfalls

1. **Don't add non-scoring fields to normalizer calibration** — always pass `metric_whitelist` matching the active policy's `metrics` list to `fit()`
2. **Don't use raw min/max** — the normalizer intentionally clips at quantile anchors to prevent outlier collapse
3. **Check `is_calibrated` before scoring** — Generation 0 falls back to `fallback_utilities` (typically legacy scoring) before calibration completes
4. **Weight floors must sum to < 1.0** — the remaining mass is distributed by softmax
5. **Scorer caches weights at construction** — if features change, create a new `CompositeScorer` instance
