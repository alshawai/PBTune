# Scoring Formula — Detailed Reference

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

These are used ONLY until adaptive normalization kicks in (generation ≥ 2):

| Preset | lat_min | lat_max | thr_min | thr_max |
|--------|---------|---------|---------|---------|
| OLTP   | 10ms    | 200ms   | 10 TPS  | 1000 TPS |
| OLAP   | 100ms   | 20000ms | 10 QphH | 1000 QphH |
| MIXED  | 100ms   | 20000ms | 10 TPS  | 1000 TPS |

## Adaptive Normalization (`update_ranges()`)

Activates at generation ≥ 2 when at least 3 valid metrics exist.

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
expand_ranges_for_metrics(metrics_list, expansion_factor=0.5)
# Expands range by 50% to restore discrimination power
```

## Edge Cases

- **Zero latency**: Component contributes 0.0 (not normalized)
- **Zero throughput**: Same — component is 0.0
- **All metrics zero**: Score = memory + error components only
- **Baseline normalization**: If enabled, final score is divided by baseline score
- **Score floor**: `max(0.0, score)` prevents negative scores
- **Detailed decomposition**: `compute_detailed_scores()` returns per-component breakdown
