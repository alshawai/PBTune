# Scoring Formula — Detailed Reference

> This file documents the legacy `fixed_v1` scoring math implemented in
> `MetricConfig.compute_score()`. The current default for new runs is
> `feature_driven_v2` — see the `scoring-pipeline` skill (canonical contract:
> `S = 100 × G × Σ(w_i × u_i) / (1 − w_error)`). `fixed_v1` is retained for compatibility with
> legacy sessions.

## Core Computation (`MetricConfig.compute_score()`)

Located in `src/utils/metrics.py`.

### Step-by-Step

1. **Dead worker check**: If `metrics.failure_type is not None` → return 0.0 immediately
2. **Latency normalization** (lower is better):
   ```
   clamped = clip(latency_p95, latency_min, latency_max)
   normalized = (latency_max - clamped) / (latency_max - latency_min)  → [0, 1]
   ```
3. **Throughput normalization** (higher is better):
   ```
   clamped = clip(throughput, throughput_min, throughput_max)
   normalized = (clamped - throughput_min) / (throughput_max - throughput_min)  → [0, 1]
   ```
4. **Memory normalization** (lower is better, already in [0,1]):
   ```
   normalized = 1.0 - clip(memory_utilization, 0, 1)
   ```
5. **Error normalization** (lower is better, already in [0,1]):
   ```
   normalized = 1.0 - clip(error_rate, 0, 1)
   ```
6. **Weighted sum**:
   ```
   score = Σ(weight_i × normalized_i) × 100
   ```

### Workload Preset Constants

| Preset | Latency | Throughput | Memory | Error | Latency Metric |
|--------|---------|------------|--------|-------|----------------|
| OLTP   | 0.50    | 0.40       | 0.05   | 0.05  | p95            |
| OLAP   | 0.55    | 0.30       | 0.10   | 0.05  | p99            |
| MIXED  | 0.40    | 0.35       | 0.15   | 0.10  | p95            |

### Fallback Ranges

These are used ONLY until adaptive normalization kicks in (once ≥ max(20, 5·population_size) valid samples have accrued — typically around generation 5, not a fixed generation index):

| Preset | lat_min | lat_max | thr_min | thr_max |
|--------|---------|---------|---------|---------|
| OLTP   | 10ms    | 200ms   | 10 TPS  | 1000 TPS |
| OLAP   | 100ms   | 20000ms | 10 QphH | 1000 QphH |
| MIXED  | 100ms   | 20000ms | 10 TPS  | 1000 TPS |

## Adaptive Normalization (`update_ranges()`)

Activates once ≥ max(20, 5·population_size) valid samples have accrued across workers (`Population.update_metric_ranges_if_needed`), not at a fixed generation index. `MetricConfig.update_ranges()` additionally requires at least 3 samples before fitting.

```python
# Uses 5th/95th percentiles (robust to outliers)
lat_p05, lat_p95 = np.percentile(latencies, [5, 95])
thr_p05, thr_p95 = np.percentile(throughputs, [5, 95])

# Adds 20% padding for headroom
latency_min = max(0.1, lat_p05 - 0.2 * range)
latency_max = lat_p95 + 0.2 * range
```

## Saturation Detection (`detect_saturation()`)

Checks if normalized component ≥ 0.95. When detected:

```python
expand_ranges_for_metrics(metrics_list, expansion_factor=0.25)
# PBT passes expansion_factor=0.25 (population.py). NOTE: this parameter is
# retained for API compatibility but ignored — the normalizer widens each
# saturated anchor by 20% of its current range via expand_metric_anchor().
```

## Edge Cases

- **Zero latency**: Component contributes 0.0 (not normalized)
- **Zero throughput**: Same — component is 0.0
- **All metrics zero**: Score = memory + error components only
- **Baseline normalization**: If enabled, final score is divided by baseline score
- **Score floor**: `max(0.0, score)` prevents negative scores
- **Detailed decomposition**: `compute_detailed_scores()` returns per-component breakdown
