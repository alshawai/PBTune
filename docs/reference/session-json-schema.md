# Session JSON Schema

> Last reviewed: 2026-06-15

See also: [evaluation-suite](../architecture/evaluation-suite.md), [feature-driven-scoring](../architecture/feature-driven-scoring.md), [pbt-core](../architecture/pbt-core.md), [bo-baseline guide](../guides/bo-baseline.md), [timing instrumentation contributor guide](timing-instrumentation.md)

Every tuning run, evaluation comparison, and analysis pass emits or consumes one of three JSON shapes:

- **PBT session** — produced by `python -m src.tuner.main`
- **BO session** — produced by `python -m src.scripts.bo_baseline`
- **Comparison report** — produced by `python -m src.evaluation`

This page is the schema reference for tooling authors and reviewers. The session loader in [`src/evaluation/loader.py`](../../src/evaluation/loader.py) is the authoritative implementation; this doc summarises what it expects.

All three schemas have evolved over the project's lifetime. The loader is **schema-tolerant** — sessions written under older versions still load with default values for fields added later. Fields described below as `*added in vX*` are absent in older artefacts.

---

## Conventions

- Times are ISO 8601 strings (UTC) unless otherwise noted.
- Durations are floats in seconds.
- Memory sizes follow the originating layer's unit — `ram_bytes` is bytes, `shared_buffers` is whatever unit PostgreSQL reported (typically `8kB` pages).
- Knob values are stored at the **resolved (post-`verify()`) granularity**, not the suggested granularity — see [configuration-management §Verifying applied config](../architecture/configuration-management.md#verifying-applied-config).
- `null` is used for "field absent / not applicable"; missing keys are equivalent to `null` for downstream tooling.

---

## Timing instrumentation (v1.1)

Sessions written from 2026-06 onward carry `tuning_session.timing_schema_version`. v1.0 introduced per-component wall-clock instrumentation; v1.1 (current) is a non-breaking refinement that strips the redundant `summary` field at non-aggregating layers — see [Changes from v1.0](#changes-from-v10) below.

**Clock.** All durations come from `time.monotonic()` via the `TimingRecorder` / `TimingRecord` primitives in [`src/utils/timing.py`](../../src/utils/timing.py). Wall-clock timestamps (filenames, log lines, ordering) come from `session_timestamp()` in [`src/utils/session_clock.py`](../../src/utils/session_clock.py). Durations and wall-clock timestamps are deliberately decoupled — durations are immune to NTP drift, leap seconds, and DST.

**Hierarchy.** Timing data lives at three nested layers:

1. **Session-level** — `bootstrap_breakdown` (one-shot setup costs incurred before the gen loop) and `timing_summary` (mean/std/n/min/max/total per component, aggregated across every `(generation, worker)` tuple in the session). Both carry a `summary` field — this is where aggregation matters.
2. **Generation-level** — `generation_history[i].timing` is a per-generation block for whole-generation work that is not attributable to any single worker (currently only `evolve`). Shape: `{ "records": [...] }` — no `summary` because each component appears once per gen (n=1).
3. **Worker-level** — `generation_history[i].worker_scores[j].timing` is a per-worker block covering everything from configuration apply through scoring. Shape: `{ "records": [...] }` — no `summary` because each component appears once per worker per gen (n=1).

The `records` field is the ordered list of `TimingRecord` entries (component name, duration, optional metadata) at every layer.

The `summary` field — present only at session level (`timing_summary` and `bootstrap_breakdown.summary`) — is `aggregate()` output keyed by component name: `{n, mean, std, min, max, total}`. `std` is population standard deviation (`statistics.pstdev`), `0.0` when `n == 1`.

### Changes from v1.0

v1.0 emitted `summary` at every layer uniformly. At per-worker / per-gen scope each component appeared exactly once per evaluation, so the summary block had `n=1`, `std=0.0`, and `mean=min=max=total=record.seconds` — pure JSON noise (~5x the bytes of the records block) without information beyond `records[i].seconds`. v1.1 emits `summary` only at the session-level layers where pooling actually produces non-trivial aggregates (`timing_summary` over all (gen, worker) tuples; `bootstrap_breakdown.summary` over the bootstrap component set).

Backwards compatibility: v1.0 readers that walk `worker_scores[*].timing.summary` will see a missing key. The analysis script ([`src/analysis/timing_breakdown.py`](../../src/analysis/timing_breakdown.py)) already pulls from `records` (and falls back to flattening when `timing_summary` is absent), so it consumes both v1.0 and v1.1 unchanged.

### Component reference

The components currently emitted by the orchestrator, population, and tuner bootstrap. The source-file pointer is where the bracket is opened; new components are added by following the [contributor guide](timing-instrumentation.md).

| Component | Layer | Semantics | Source |
| --- | --- | --- | --- |
| `setup_instances` | bootstrap | Bring PostgreSQL workers up (containers, datadirs, schema load). | `src/tuner/main.py` — `PBTTuner.run` bootstrap block |
| `verify_instances` | bootstrap | Probe each worker for liveness, version, capability. | `src/tuner/main.py` — `PBTTuner.run` bootstrap block |
| `prune_knobs` | bootstrap | Drop knobs unsupported by the resolved PG server version. | `src/tuner/main.py` — `_prune_unsupported_runtime_knobs` |
| `setup_snapshots` | bootstrap | Create per-worker baseline snapshots for fast restart. | `src/tuner/main.py` — `PBTTuner.run` bootstrap block |
| `apply_only` | worker | `ALTER SYSTEM` writes only — no reload, no restart. Sets `restart_required`. | `src/utils/applicator.py` — `KnobApplicator.apply_only`, invoked from `WorkloadOrchestrator.apply_configuration` |
| `activate_reload` | worker | `pg_reload_conf()` for SIGHUP / user / superuser-context knobs. Metadata: `strategy="reload"`. | `src/utils/applicator.py` — `KnobApplicator.activate` |
| `activate_restart` | worker | `restart_instance()` for postmaster-context knobs. Metadata: `strategy="restart"`. | `src/utils/applicator.py` — `KnobApplicator.activate` |
| `snapshot_restore` | worker | `env.restore_snapshot(worker_id)` when `restore_due=True`. Replaces `activate_*` on restore-interval generations (the restore IS the restart). | `src/tuner/benchmark/orchestrator.py` — `WorkloadOrchestrator.evaluate_worker` |
| `knob_verify` | worker | Read-back via `KnobApplicator.verify()` — confirms PostgreSQL accepted and quantised the values. | `src/tuner/benchmark/orchestrator.py` — `WorkloadOrchestrator.evaluate_worker` |
| `workload` | worker | Full workload execution (warmup + measurement). Metadata: `executor="internal"` or `executor="benchmark"` (sysbench / tpch). | `src/tuner/benchmark/orchestrator.py` — `WorkloadOrchestrator.evaluate_worker` |
| `score` | worker | `engine.compute_breakdown()` over the captured metrics. | `src/tuner/benchmark/orchestrator.py` — `WorkloadOrchestrator.evaluate_worker` |
| `evolve` | generation | `execute_exploit_explore` + `env.clone_instances` — the PBT step itself. | `src/tuner/core/population.py` — `Population.train_generation` |

Component names are snake_case and stable: they are the dimension key in the cost-decomposition table, so renaming one silently breaks reproducibility of older sessions through the analysis script.

### Observed vs. configured

The `workload` component's semantics depend on the executor metadata:

- `executor="benchmark"` (sysbench, tpch): warmup and measurement are not separately bracketed — the C-binary owns the warmup/measurement boundary internally. The reported `seconds` is the wall-clock duration of the subprocess call, which dominates both phases plus any process startup overhead. The configured `sysbench_warmup_seconds` / `sysbench_duration_seconds` (or `tpch_warmup_passes` / measurement-pass count) live in `tuning_session` and give the configured-vs-observed perspective.
- `executor="internal"` (template-driven JSON workloads): warmup and measurement are **not yet** separately bracketed in the v1.0 schema. Splitting them is a follow-up to Phase 2C.10 of the timing instrumentation plan ([`docs/research/timing-instrumentation-plan.md`](../research/timing-instrumentation-plan.md)) and will land in a later schema bump. Until then, the bracket reports the combined wall-clock of both phases together.

For sysbench specifically, the audit's [Phase 2C.10 note](../research/timing-instrumentation-plan.md) records that warmup / measurement durations can be reported as the configured values with `observed=False` metadata. The v1.0 emitter does not yet add that metadata; downstream tools that need the breakdown should consult the `tuning_session` configuration fields and treat the bracket as a single-block total.

### Worker timing JSON example

```json
{
  "timing": {
    "records": [
      {"component": "apply_only", "seconds": 0.124},
      {"component": "activate_reload", "seconds": 0.087, "metadata": {"strategy": "reload"}},
      {"component": "knob_verify", "seconds": 0.205},
      {"component": "workload", "seconds": 300.45, "metadata": {"executor": "benchmark"}},
      {"component": "score", "seconds": 0.012}
    ]
  }
}
```

On a restore-interval generation, the record list contains a single `snapshot_restore` entry instead of `activate_reload` / `activate_restart`. A per-generation `timing` block is identical in shape but typically contains only an `evolve` record. Neither layer carries a `summary` field in v1.1 — see [Changes from v1.0](#changes-from-v10).

The corresponding aggregate lives at the session level — `timing_summary[component]` accumulates each component's durations across every `(gen, worker)` tuple and emits `{n, mean, std, min, max, total}`. For the worker example above, the workload entry contributes one sample to `timing_summary["workload"]`.

### Time accounting

`tuning_session` carries three duration fields:

| Field | Meaning |
| --- | --- |
| `total_time_seconds` | End-to-end session wall-clock (bootstrap + tuning loop). Kept for backwards compatibility with v0.0 sessions. |
| `tuning_time_seconds` | Measurement-loop time only — captured from immediately before the gen loop to its end. This is what the paper's wall-clock-to-deployment-ready-config plot uses. *new in v1.0* |
| `bootstrap_seconds` | `total_time_seconds - tuning_time_seconds`. The cost paid before the algorithm starts learning. *new in v1.0* |

Legacy sessions (`timing_schema_version` absent) carry only `total_time_seconds`; the loader treats `tuning_time_seconds` / `bootstrap_seconds` / `bootstrap_breakdown` / `timing_summary` as `None` for those sessions and the analysis script must handle that case explicitly.

---

## PBT session schema

File location: `results/{workload_dir}/pbt_runs/{tier}/tuning_sessions/pbt_results_{timestamp}.json`

### Top-level layout

```json
{
  "tuning_session":          { ... session metadata ... },
  "scoring_policy":          "feature_driven_v2",
  "scoring_policy_version":  "2.0",
  "metric_reference_version":"v2",
  "workload_features":       { ... feature vector ... },
  "normalization_metadata":  { ... normaliser state ... },
  "score_breakdown":         { ... }  (best worker's breakdown)
  "best_configuration":      { ... }
  "worker_resources":        { "ram_bytes": ..., "cpu_cores": ..., "disk_type": ... },
  "warm_start":              { "enabled": false, ... },
  "generation_history":      [ ... per-generation snapshots ... ],
  "convergence":             { "converged": bool, "generations_without_improvement": int },
  "system_info":             { ... host metadata ... }
}
```

The five scoring-related top-level keys (`scoring_policy`, `scoring_policy_version`, `metric_reference_version`, `workload_features`, `normalization_metadata`) are duplicated from the equivalent fields under `tuning_session` for downstream-tool convenience. Older sessions only have the nested copy; the loader handles both layouts.

### `tuning_session`

| Field | Type | Notes |
| --- | --- | --- |
| `knob_tier` | str | One of `minimal` / `core` / `standard` / `extensive`. |
| `num_knobs` | int | Tier size at session start. |
| `workload_type` | str | `oltp` / `olap` / `mixed`. |
| `benchmark_name` | str \| null | `sysbench`, `tpch`, or null for custom workload files. |
| `scale_factor` | float \| null | TPC-H scale factor. |
| `sysbench_tables`, `sysbench_table_size` | int \| null | Sysbench schema. |
| `sysbench_workload` | str \| null | `oltp_read_only` / `oltp_read_write` / `oltp_write_only`. |
| `sysbench_duration_seconds`, `sysbench_warmup_seconds` | float \| null | Per-evaluation timing. |
| `tpch_warmup_passes` | int \| null | TPC-H query-pass warmup count. |
| `tuning_mode` | str | `online` / `offline` / `adaptive`. |
| `population_size` | int | Workers in the population. |
| `total_generations` | int | Generations actually run (may be < `max_generations` due to early stopping). |
| `num_parallel_workers` | int | Workers run concurrently. *added in v2* |
| `enable_snapshots`, `snapshot_restore_interval` | bool, int | Baseline-snapshot policy. *added in v2* |
| `seed` | int | Master random seed. |
| `total_time_seconds` | float | Wall-clock duration (bootstrap + tuning). |
| `tuning_time_seconds` | float | Measurement-loop wall-clock only (excludes bootstrap). *added in timing-schema v1.0* |
| `bootstrap_seconds` | float | Bootstrap wall-clock only. *added in timing-schema v1.0* |
| `timing_schema_version` | str | `"1.0"` from 2026-06 onwards; absent in legacy sessions. |
| `timestamp` | str | `YYYYMMDD_HHMM`. |
| `workload_features` | object | Workload feature vector (mirrored at top level). |

### `best_configuration`

```json
{
  "score": 0.9164,
  "knobs": {
    "shared_buffers": 65536,
    "effective_cache_size": 262144,
    ...
  },
  "metrics": {
    "latency_p50": 12.3,
    "latency_p95": 38.1,
    "latency_p99": 84.0,
    "throughput": 4250.7,
    ...
  },
  "score_breakdown": { ... }
}
```

`score` is in `[0, 1]` for v2 sessions, on `[0, 100]` for legacy `fixed_v1` sessions with the historical scaling factor. Always check `scoring_policy` before interpreting the magnitude.

`knobs` keys are the post-`verify()` quantised values PostgreSQL actually ran with — not the optimiser's suggestion.

`metrics` is a `PerformanceMetrics.to_dict()` payload — the full field list lives in [`src/utils/metrics.py`](../../src/utils/metrics.py).

`score_breakdown` is a [`ScoreBreakdown`](../../src/utils/scoring/contracts.py) serialised dict — resolved weights, per-metric utility scores, reliability gate value, and policy version.

### `workload_features`

The feature vector consumed by `FeatureDrivenWeightModel`. Standard fields:

| Field | Range | Meaning |
| --- | --- | --- |
| `read_ratio` | [0, 1] | Fraction of read queries. |
| `write_ratio` | [0, 1] | Fraction of write queries. |
| `olap_complexity` | [0, 1] | Multi-table-scan / aggregation density. |
| `join_intensity` | [0, 1] | Cross-table joins per query. |
| `aggregation_intensity` | [0, 1] | GROUP BY / window-function density. |
| `sort_intensity` | [0, 1] | ORDER BY / sort-driven operator density. |
| `concurrency_pressure` | [0, 1] | Effective concurrent worker count. |
| `working_set_millions` | ≥ 0 | Approx working-set rows (millions). |
| `query_mix_entropy` | [0, log(N)] | Shannon entropy over the query weights. |
| `tail_latency_sensitivity` | [0, 1] | Heuristic weight for p99 over p50. |

Older sessions may have an empty `workload_features: {}` — the loader falls back to default features in that case.

### `normalization_metadata`

Snapshot of the `QuantileUtilityNormalizer` state at the end of the session, used by the post-hoc rescoring helper in [`src/tuners/utils/calibration.py`](../../src/tuners/utils/calibration.py).

| Field | Type | Notes |
| --- | --- | --- |
| `normalizer` | str | `"QuantileUtilityNormalizer"` |
| `metric_reference_version` | str | `v1` / `v2`. |
| `anchors` | dict[str, [low, high]] | Per-metric calibration anchors. |
| `n_calibration_samples` | int | Sample count used for anchors. |
| `out_of_support_rates` | dict[str, float] | Drift detection signal. |
| `latency_metric` | str | Active percentile (`p95` for OLTP, `p99` for OLAP). |
| `padding_factor` | float | Per-policy safety factor. |
| `ranges_calibrated` | bool | Whether the normaliser left fallback bounds. |

### `worker_resources`

```json
{
  "ram_bytes": 4294967296,
  "cpu_cores": 2.0,
  "disk_type": "ssd"
}
```

The per-worker resource slice computed by [`detect_worker_resources`](../../src/utils/hardware_info.py). See [hardware-aware-normalization](../architecture/hardware-aware-normalization.md). Reviewers can check that the slice matches the `system_info` host totals divided by `num_parallel_workers × 0.8`.

### `generation_history[]`

One element per generation. Each is:

```json
{
  "generation": 0,
  "best_score": 0.79,
  "mean_score": 0.74,
  "std_score": 0.04,
  "num_exploited": 0,
  "best_worker_id": 1,
  "converged": false,
  "restart_count": 1,
  "timestamp": "2026-03-26T00:46:48.849150",
  "wall_clock_seconds": 287.3,
  "generation_elapsed_seconds": 287.3,
  "worker_scores": [
    { "worker_id": 0, "score": 0.71, "metrics": { ... } },
    { "worker_id": 1, "score": 0.79, "metrics": { ... } }
  ],
  "worker_configs": [
    { "worker_id": 0, "config": { ... } },
    { "worker_id": 1, "config": { ... } }
  ]
}
```

`worker_scores[].metrics` is a `PerformanceMetrics.to_dict()`. `worker_configs[].config` is the post-verify quantised configuration for that worker at that generation. `restart_count` records how many workers triggered PostgreSQL restarts in the generation (a function of the tuning mode + the postmaster-context knobs touched).

### `convergence`

```json
{
  "converged": true,
  "generations_without_improvement": 7
}
```

Drives the `should_stop()` decision in [`Population`](../architecture/pbt-core.md).

### `system_info`

```json
{
  "cpu_model": "AMD Ryzen 9 5950X 16-Core Processor",
  "cpu_cores": 16,
  "ram": "62.7 GiB",
  "disk_type": "ssd",
  "pg_version": "16.2",
  "os": "Linux 6.5.0-15-generic"
}
```

The first six fields are required; later additions (e.g. `python_version`, `docker_image`) are present in newer sessions and absent in older ones.

---

## BO session schema

File location: `results/{workload_dir}/bo_runs/{tier}/tuning_sessions/bo_results_{timestamp}.json` (or `baseline_sessions/` under some historical paths).

The schema is **structurally identical** to the PBT session schema with one optimiser-specific addition under `tuning_session`:

| Field | Type | Notes |
| --- | --- | --- |
| `optimizer` | str | `"bo_smac3"` |
| `bo_library` | str | `"smac"` |
| `bo_surrogate` | str | `rf` (Random Forest) or `gp` (Gaussian Process). |
| `bo_acquisition` | str | `"EI"` or facade default. |
| `iterations` | int | Total iterations completed. |
| `num_parallel_workers` | int | When `--batched-bo` is used. |
| `resource_equalization` | bool | Whether BO inherited per-worker resource slices from `--pbt-session`. |
| `reference_pbt_session` | str \| null | Path to the reference PBT session. |
| `reference_pbt_knobs` | list[str] \| null | Knob names copied from the reference session. |
| `pilot_size` | int | Sobol pilot iterations before `expand_ranges_for_metrics()` froze the normaliser. |

`generation_history[]` for BO is a per-iteration list (each iteration is a single-worker "generation"), so `worker_scores` and `worker_configs` are length-1 arrays. This keeps the consuming code (visualization, evaluation suite, comparison script) workload-agnostic across PBT and BO.

The `population_size` field is **absent** for BO (it's not population-based); consuming code should treat its absence as "single-worker iteration."

---

## Comparison JSON schema

File location: `results/{workload_dir}/comparisons/{tier}/comparison_{timestamp}.json`

Produced by `python -m src.evaluation`. The full schema:

```json
{
  "comparison_metadata":   { ... },
  "session_info":          { ... },
  "session_scoring_metadata": { ... },
  "scoring_metadata":      { ... },
  "tuned_knobs":           { ... },
  "default_runs":          [ RunResult, ... ],
  "tuned_runs":            [ RunResult, ... ],
  "statistics":            { ... },
  "power_warning":         "..." | null,
  "system_info":           { ... }
}
```

### `comparison_metadata`

| Field | Type | Notes |
| --- | --- | --- |
| `timestamp` | str | `YYYYMMDD_HHMMSS`. |
| `tuning_session_path` | str | Source PBT/BO session JSON. |
| `evaluation_log_path` | str | Path to the HTML log. |
| `benchmark` | str | Sysbench / TPC-H. |
| `repetitions` | int | Number of paired (default, tuned) runs. |
| `pair_seed_base` | int | Repetition `i` uses `pair_seed_base + i - 1`. |
| `evaluation_environment` | str | `"docker"` or `"bare-metal-fallback"`. |
| `resource_constraints` | object | Per-worker `ram_bytes`, `cpu_cores`, `disk_type`. |
| `scoring_policy_override` | str \| null | If the user passed `--scoring-policy` to override the session's policy. |
| `reproducibility` | object | `python_version`, `postgres_version`, `docker_image`, `python_package_versions`, `benchmark_binary_paths`. |

### `default_runs[]` and `tuned_runs[]`

Each element is a `RunResult` ([`src/evaluation/types.py`](../../src/evaluation/types.py)):

```json
{
  "repetition": 0,
  "seed": 50000,
  "metrics": { "latency_p50": ..., "latency_p95": ..., "throughput": ..., ... },
  "score": 0.7821,
  "wall_clock_seconds": 91.4,
  "score_breakdown": { ... }
}
```

The arrays are length-`repetitions`. Pair `i` of `default_runs[i]` and `tuned_runs[i]` shares its seed; the paired statistical tests in `statistics` are valid only because of this seed pairing.

### `statistics`

```json
{
  "alpha": 0.05,
  "primary_endpoint": "score",
  "primary_significant": true,
  "secondary_endpoints": ["latency_p95", "throughput", "memory_utilization"],
  "secondary_correction_method": "holm",
  "metrics": {
    "score": MetricComparison,
    "latency_p95": MetricComparison,
    "throughput": MetricComparison,
    "memory_utilization": MetricComparison
  }
}
```

Each `MetricComparison`:

```json
{
  "default": { "mean": ..., "std": ..., "median": ..., "p25": ..., "p75": ..., "n": 5 },
  "tuned":   { "mean": ..., "std": ..., "median": ..., "p25": ..., "p75": ..., "n": 5 },
  "delta_mean": ...,
  "delta_median": ...,
  "pct_change": ...,
  "wilcoxon_p": 0.0234,
  "wilcoxon_p_corrected": 0.0468,
  "bootstrap_ci_median": [low, high],
  "cohens_d": 1.23,
  "significant": true
}
```

The primary endpoint's `wilcoxon_p` is uncorrected. Secondary endpoints' `wilcoxon_p_corrected` is Holm-adjusted across the secondary family. See [evaluation-suite §Statistical analysis](../architecture/evaluation-suite.md#statistical-analysis) for the methodology.

### `scoring_metadata` vs `session_scoring_metadata`

These two blocks look similar but answer different questions:

- **`session_scoring_metadata`** — what the source tuning session was scored under. Carries the session's `scoring_policy`, `scoring_policy_version`, `metric_reference_version`, `workload_features`, `normalization_metadata`, and best `score_breakdown`. Use this to attribute scores in the source session correctly.
- **`scoring_metadata`** — what *this evaluation* used to compute the comparison scores. May differ from the session metadata if `--scoring-policy` was used to rescore historical sessions under a newer policy. Use this to interpret the `default_runs[].score` and `tuned_runs[].score` values.

If they match (the common case), there's no ambiguity. If they differ, downstream tooling should display both versions to avoid misleading interpretation.

---

## Common patterns for consumers

### "Load a session and get the best config"

```python
import json
with open(path) as f:
    data = json.load(f)
best_config = data["best_configuration"]["knobs"]
best_score = data["best_configuration"]["score"]
```

### "Load a session via the canonical loader"

For anything beyond trivial inspection, use [`load_tuning_session`](../../src/evaluation/loader.py):

```python
from src.evaluation.loader import load_tuning_session
session = load_tuning_session(Path("path/to/session.json"))
# Returns a typed TuningSessionData with version compatibility applied.
```

The loader handles all schema-version differences, populates default fields, infers `benchmark` / `workload_type` from the file path when the JSON omits them, and warns on metric-reference-version mismatches without blocking.

### "Convergence curve across multiple sessions"

```python
history = data["generation_history"]
best_so_far = []
running = float("-inf")
for gen in history:
    running = max(running, gen["best_score"])
    best_so_far.append((gen["generation"], running))
```

This is what the `pbt_vs_bo_comarison.py` script does for its convergence plot, with the additional step of using `wall_clock_seconds` for the time-axis and `rescore_metrics_globally()` for cross-method score parity.

### "Per-knob lineage"

```python
# Which generation did the best worker's config first appear in?
history = data["generation_history"]
best_worker_id = data["best_configuration"]  # might not be present in older sessions
# walk worker_configs backwards looking for the last divergence
```

There's no direct lineage field; lineage has to be reconstructed from `worker_configs` across generations. For automation, prefer the visualization loaders in [`src/visualization/loaders/session.py`](../../src/visualization/loaders/session.py).

---

## Field-presence cheat sheet

| Field | First version | Notes |
| --- | --- | --- |
| `tuning_session.scoring_policy` | `fixed_v1` era | Always present. |
| Top-level `workload_features` | feature_driven_v2 | Empty `{}` in older sessions. |
| Top-level `normalization_metadata` | feature_driven_v2 | Empty `{}` in older sessions. |
| `worker_resources` | hardware-aware-normalization | Absent in earliest sessions. |
| `tuning_session.num_parallel_workers` | parallel-worker isolation | Absent in earliest sessions. |
| `tuning_session.enable_snapshots` | snapshot lifecycle | Absent in earliest sessions. |
| `comparison_metadata.scoring_policy_override` | scoring policy override flag | Absent in older comparisons. |
| `comparison_metadata.reproducibility` | reproducibility checklist | Absent in older comparisons. |
| `tuning_session.timing_schema_version` | timing-instrumentation v1.0 (current value `"1.1"`) | Absent in pre-2026-06 sessions; loader treats absence as `"0.0"`. |
| `tuning_session.tuning_time_seconds`, `bootstrap_seconds` | timing-instrumentation v1.0 | Absent in pre-2026-06 sessions. |
| `bootstrap_breakdown`, `timing_summary` | timing-instrumentation v1.0 | Top-level. Absent in pre-2026-06 sessions. Both carry `records` + `summary`. |
| `generation_history[].timing`, `generation_history[].worker_scores[].timing` | timing-instrumentation v1.0 | Absent in pre-2026-06 sessions. v1.0 carried `records` + `summary`; v1.1 drops the redundant `summary` here (each component has n=1 at this scope). |

When in doubt, run the file through `load_tuning_session()` — the loader's compatibility branches are the source of truth on what older sessions look like and how to interpret missing fields.
