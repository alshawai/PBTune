# Feature-Driven Scoring

> Last reviewed: 2026-06-07

See also: [Documentation Index](./README.md), [Performance Evaluation](./PERFORMANCE_EVALUATION.md), [Metrics Validation](./METRICS_VALIDATION.md), [Workload Orchestrator](./WORKLOAD_ORCHESTRATOR.md)

## Overview

This document describes the migration from the older fixed scoring policy to the current feature-driven scoring system used by tuning, post-hoc rescoring, and evaluation. The objective is still a single scalar reward for Population-Based Training, but workload features now influence metric importance instead of relying on benchmark name alone.

The runtime pipeline is composed of four parts:

1. `WorkloadFeatures` capture static and runtime workload characteristics.
2. `QuantileUtilityNormalizer` maps raw metrics to monotonic utilities in $[0, 1]$.
3. `FeatureDrivenWeightModel` computes metric weights from workload features and the active policy.
4. `CompositeScorer` combines weighted utilities with a reliability gate and emits the final score.

## What Changed

The older design used fixed, workload-specific weights. The current design keeps a compatibility policy for historical sessions, but shifts new runs to feature-conditioned weights. The score now reacts to workload shape rather than just benchmark label.

| Aspect | Fixed policy (`fixed_v1`) | Feature-driven policy (`feature_driven_v2`) |
| --- | --- | --- |
| Weight source | Static per workload type | Computed from workload features |
| Metric set | `latency_p50`, `latency_p95`, `latency_p99`, `throughput`, `memory_utilization`, `error_rate` | `latency_p95`, `latency_p99`, `latency_variance`, `tail_amplification`, `throughput`, `throughput_variance`, `error_rate`, `memory_pressure`, `scan_efficiency`, `buffer_miss_rate` |
| Compatibility | Default for legacy sessions | Default for new feature-aware runs |
| Weight behavior | Preset and deterministic | Floor-constrained softmax over feature-conditioned logits |

## Scoring Contract

The runtime score is computed as:

$$
S = G \cdot \sum_{i=1}^{n} w_i \cdot u_i
$$

where:

- $G \in [0, 1]$ is the reliability gate.
- $w_i$ is the active weight for metric $i$.
- $u_i \in [0, 1]$ is the normalized utility for metric $i$.
- The active weights produced by `FeatureDrivenWeightModel` sum to $1$ and each metric receives a configured floor.

The score is therefore bounded by the gate and the normalized utilities rather than by an artificial $100$-point scaling factor.

## Workload Features

`src/utils/scoring/workload_features.py` defines the extraction layer that produces the feature vector consumed by the weight model. The canonical feature groupings are:

- `read_ratio` and `write_ratio`
- `olap_complexity`
- `join_intensity`
- `aggregation_intensity`
- `sort_intensity`
- `concurrency_pressure`
- `working_set_millions`
- `query_mix_entropy`
- `tail_latency_sensitivity`

The extractor is deterministic for Sysbench, TPC-H, and template workloads.

Sysbench encodes read/write mode, concurrency pressure, and working-set scale. TPC-H emphasizes OLAP complexity, joins, aggregation, and tail sensitivity. Template workloads derive feature values from query text and schema metadata.

## Weight Model

`FeatureDrivenWeightModel` converts workload features into weights using a floor-constrained softmax:

$$
z_i = b_i + \sum_j M_{ij} f_j
$$

$$
w_i = \alpha_i + \left(1 - \sum_k \alpha_k\right) \cdot \mathrm{softmax}\left(\frac{z_i}{T}\right)
$$

where:

- $b_i$ is the per-metric base weight.
- $M_{ij}$ is the feature coefficient matrix.
- $f_j$ is the workload feature value.
- $\alpha_i$ is the floor for metric $i$.
- $T$ is the softmax temperature.

The current implementation applies logarithmic compression to `working_set_millions` before multiplication so large benchmarks do not dominate the score.

The floor set must satisfy $\sum_i \alpha_i < 1$. That keeps every metric in the score while allowing the remaining weight mass to move with the workload.

## Normalization

`QuantileUtilityNormalizer` maps raw performance metrics to $[0, 1]$ using robust quantile anchors instead of single-point min/max bounds. This keeps normalization monotonic while reducing sensitivity to outliers and one-off failures.

The normalizer:

- uses calibrated anchors when history is available,
- falls back to sensible anchors before calibration,
- keeps naturally bounded metrics clamped to $[0, 1]$,
- tracks drift through out-of-support rates,
- recalibrates when drift persists,
- expands anchors when saturation is detected.

This is what keeps the scoring signal stable across generations while preserving discrimination between candidate configurations.

## Reliability Gate

The reliability gate prevents unstable runs from dominating ranking.

- Fatal failures set the gate to $0$.
- Error rates at or above the fatal threshold set the gate to $0$.
- Non-zero error rates below the threshold decay the gate linearly toward $0$.
- Successful runs keep the gate at $1$.

The gate is applied before score aggregation, so a failed worker cannot win because of incidental utility values on unrelated metrics.

## Serialization and Reproducibility

The scoring layer defines explicit contracts for auditability:

- `WorkloadFeatures` stores the feature vector, source, and version.
- `MetricSnapshot` stores per-metric raw and normalized values, weights, and contributions.
- `ScoreBreakdown` captures the final score, policy, reliability gate, and component list.
- `NormalizationState` stores anchors and normalization metadata for reproducible rescoring.

Legacy sessions continue to deserialize with `fixed_v1`, policy version `1.0`, and metric reference version `v1` through the defaults in `src/utils/scoring/constants.py`.

## Migration Guidance

Use `fixed_v1` when you need historical comparability or want to replay old sessions exactly. Use `feature_driven_v2` when you want the score to reflect workload shape rather than benchmark name alone.

When enabling the feature-driven policy:

1. Keep the feature extractor deterministic for the benchmark family you are running.
2. Ensure the floor sum remains below $1$.
3. Allow the normalizer to calibrate before interpreting small score deltas.
4. Persist `ScoreBreakdown` and `NormalizationState` so rescoring remains reproducible.

For debugging, inspect the resolved weights and component breakdown rather than only the final scalar. That makes it much easier to confirm that feature shifts are influencing the intended metrics.

## Validation Checklist

The migration is complete when the following hold:

- workload feature extraction is deterministic,
- the feature-driven weight model returns weights that sum to $1$,
- every configured metric respects its floor constraint,
- the normalizer produces stable utilities after calibration,
- drift and saturation handling preserve score variance,
- legacy sessions still load under the compatibility policy,
- the targeted unit tests pass.

## Scoring Engine Factory

The scoring stack is reached through a single factory:

```python
from src.utils.scoring import create_scoring_engine
scorer: CompositeScorer = create_scoring_engine(metric_config)
```

`create_scoring_engine(metric_config)` lives in [src/utils/scoring/__init__.py](../src/utils/scoring/__init__.py) and is the only place that wires the four scoring layers together. Given a `MetricConfig`, it:

1. Reuses an existing `QuantileUtilityNormalizer` from `metric_config._normalizer` if one was attached (e.g. by post-hoc rescoring); otherwise constructs a fresh normalizer.
2. For `fixed_v1`, builds a per-metric weight override dict from `metric_config.weight_latency` / `weight_throughput` / `weight_memory` / `weight_error` so legacy sessions remain bit-identical.
3. For `feature_driven_v2`, lets `FeatureDrivenWeightModel` derive weights at scoring time from the workload features.
4. Returns a `CompositeScorer` configured with the resolved `policy_id`, workload type, normalizer, and weight overrides.

The orchestrator constructs the engine __lazily under a lock__ so the first per-worker thread to call `evaluate_worker` doesn't race with peers building their own engine — see [WORKLOAD_ORCHESTRATOR.md §Lazy thread-safe scoring engine](./WORKLOAD_ORCHESTRATOR.md#3-lazy-thread-safe-scoring-engine). Every subsequent worker thread shares the same engine.

The post-hoc evaluation suite uses the same factory: when `--scoring-policy feature_driven_v2` overrides a session originally tagged `fixed_v1`, the suite calls `create_scoring_engine` with a re-tagged `MetricConfig`, then rescores the persisted raw `PerformanceMetrics`.

## Outlier Filtering

__Location__: [src/utils/scoring/outlier_filtering.py](../src/utils/scoring/outlier_filtering.py)

Calibrating the normalizer's quantile anchors against raw observations is sensitive to extreme outliers — a single hung query producing a 600-second latency would push the 95th-percentile anchor far above the typical operating range and collapse score variance for the next dozen generations.

`iqr_filter(values, k=2.5)` removes observations outside `[Q1 - k·IQR, Q3 + k·IQR]` before they enter the calibration set. The choice of `k=2.5` is a compromise:

- __Classic 1.5×__ — too aggressive for noisy database metrics; rejects legitimate variance from autovacuum bursts and checkpoint writes.
- __3.0×__ — too lenient; lets a single hung query past the gate.
- __2.5×__ (chosen) — rejects only the tails that demonstrably distort calibration on real PBT/BO traces.

The filter returns a `(filtered_array, metadata_dict)` pair where the metadata records `n_removed`, `original_size`, the bounds used, and a `fallback_used` flag. When the input has fewer than 4 observations or `IQR == 0`, the filter falls back to the unfiltered values rather than producing degenerate bounds; both cases are surfaced in the metadata so post-hoc analysis can audit when calibration was unfiltered.

The filter is applied inside `QuantileUtilityNormalizer.expand_ranges_for_metrics()` immediately before quantile estimation, and inside the global rescoring helper [`rescore_metrics_globally()`](../src/utils/rescoring.py) used by the [PBT vs BO comparison script](./PBT_VS_BO_COMPARISON.md). The filter is __not__ applied at scoring time — only at calibration time — because individual scoring calls must remain monotonic in their inputs.

## Source References

- [Scoring policies](../src/utils/scoring/policies.py)
- [Weight model](../src/utils/scoring/weights.py)
- [Workload features](../src/utils/scoring/workload_features.py)
- [Composite scorer](../src/utils/scoring/scorer.py)
- [Utility normalization](../src/utils/scoring/normalization.py)
- [Typed scoring contracts](../src/utils/scoring/contracts.py)
