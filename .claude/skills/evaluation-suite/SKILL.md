---
name: evaluation-suite
description: >
  Post-hoc comparative evaluation pipeline that compares PBT-tuned PostgreSQL configurations
  against defaults using Docker-isolated benchmarks and rigorous statistical analysis
  (Wilcoxon signed-rank, bootstrap CI, Holm correction, Cohen's d). Use this skill when
  working on the evaluation module, comparison reports, statistical testing, session loading,
  Docker evaluation containers, evaluation CLI, or any code in src/evaluation/. Also use
  when running `python -m src.evaluation`, interpreting comparison results, or debugging
  evaluation failures.
---

# Post-Hoc Evaluation Suite

The evaluation module (`src/evaluation/`) is **independent of the PBT tuning loop**.
It takes a completed tuning session JSON and runs a controlled A/B comparison:
PBT-tuned config vs. default PostgreSQL config.

## Evaluation Architecture

```
python -m src.evaluation --session <path> [--repetitions N] [--no-docker]
    │
    ├── SessionLoader.load()           → ComparisonConfig
    │   ├── Parse best_config from session JSON
    │   ├── Extract scoring metadata (policy, version)
    │   └── Detect benchmark type (sysbench/tpch)
    │
    ├── ComparisonRunner.run()
    │   ├── For each config (default, tuned) × N repetitions:
    │   │   ├── Create fresh Docker container (or bare-metal instance)
    │   │   ├── Apply configuration
    │   │   ├── Run benchmark (same as tuning)
    │   │   └── Collect PerformanceMetrics
    │   └── Return paired RunResult lists
    │
    └── StatisticalAnalyzer.analyze()
        ├── Wilcoxon signed-rank test (primary)
        ├── Bootstrap confidence intervals (BCa, 10000 resamples)
        ├── Holm-Bonferroni correction for secondary endpoints
        ├── Cohen's d effect size
        └── Generate ComparisonReport JSON
```

## Isolation Strategy

| Mode | Mechanism | Isolation Level |
|------|-----------|-----------------|
| Docker (default) | Fresh container per run, cgroup limits, tmpfs | **Full** (publication-quality) |
| Bare-metal (`--no-docker`) | Shared host, `pg_ctl` restart between runs | **Reduced** (development only) |

Docker containers use `docker/eval.Dockerfile` with pre-installed sysbench + TPC-H dbgen.

## Statistical Framework

### Primary Endpoint
- **Test**: Wilcoxon signed-rank (non-parametric, paired)
- **Metric**: Composite score (same scoring policy as tuning session)
- **α**: 0.05 (two-sided)

### Secondary Endpoints (Holm-corrected)
- Latency P95, Throughput, Error Rate
- Each gets Wilcoxon test with Holm-Bonferroni α correction

### Effect Size
- **Cohen's d**: standardized mean difference
- **Bootstrap CI**: BCa method, 10,000 resamples, 95% confidence

### Minimum Repetitions
- 5 reps (default) — minimum for Wilcoxon test validity
- 10+ reps recommended for tighter CIs

## Session Loading & Compatibility

The loader handles legacy sessions without scoring-v2 metadata by applying
compatibility defaults:
```
scoring_policy = "fixed_v1"
scoring_policy_version = "1.0"
metric_reference_version = "v1"
```

For new runs, the active default is `feature_driven_v2`; `fixed_v1` is retained
for compatibility with legacy sessions. The loader is also tolerant of the v1.1
session/timing JSON schema (see `docs/reference/session-json-schema.md`).

The evaluation CLI supports policy override for re-evaluation:
```bash
python -m src.evaluation \
    --session results/.../pbt_results_XXXX.json \
    --scoring-policy feature_driven_v2
```

## CLI Reference

```bash
python -m src.evaluation \
    --session <path>              # Required: path to tuning session JSON
    --bo-session <path>           # Optional: 3-way Default vs BO vs PBT comparison
    --benchmark {sysbench,tpch}   # Auto-detected when omitted
    --repetitions <N>             # Default: 5
    --no-docker                   # Use bare-metal instead of Docker
    --scoring-policy <id>         # Override: fixed_v1 or feature_driven_v2
    --scoring-policy-version <v>  # Override policy version
    --metric-reference-version <v># Override metric reference version
    --output-dir <path>           # Custom output directory for comparison report
    --colocate-output             # Place report next to the session file
    --seed <N>                    # RNG seed for reproducibility
    --data-dir <path>             # Override data dir
    --docker-image <image>        # Override evaluation Docker image
    --verbose {DEBUG,INFO,WARNING,ERROR}   # or -v for DEBUG
```

Benchmark parameters (auto-detected from session, override only when needed):
`--tpch-scale-factor`, `--tpch-warmup-passes`, `--sysbench-duration`,
`--sysbench-tables`, `--sysbench-table-size`, `--sysbench-workload`,
`--sysbench-warmup-seconds`.

## Output: ComparisonReport

JSON report saved to `results/{workload}/comparisons/{tier}/`:
```json
{
  "session_file": "...",
  "benchmark": "sysbench",
  "repetitions": 5,
  "environment": "docker",
  "default_results": [...],
  "tuned_results": [...],
  "statistics": {
    "primary": { "statistic": ..., "p_value": ..., "effect_size": ... },
    "secondary": { ... },
    "bootstrap_ci": { "lower": ..., "upper": ... }
  }
}
```

## Code Locations

| Component | File |
|-----------|------|
| CLI entry point | `src/evaluation/__main__.py` |
| Session loader | `src/evaluation/loader.py` |
| Comparison runner | `src/evaluation/runner.py` |
| Statistical analysis | `src/evaluation/statistics.py` |
| Type definitions | `src/evaluation/types.py` |
| Exceptions | `src/evaluation/exceptions.py` |
| Docker image | `docker/eval.Dockerfile` |
| Runbook | `docs/guides/evaluation-runbook.md` |

## Common Pitfalls

1. **Scoring policy mismatch**: If the session used `feature_driven_v2` but evaluation defaults to `fixed_v1`, scores won't be comparable — check session metadata
2. **Insufficient repetitions**: Wilcoxon requires ≥5 paired observations; <10 gives wide CIs
3. **Bare-metal noise**: Background processes inflate variance — always prefer Docker for publication results
4. **Fresh containers**: Each run MUST start from a clean state; reusing containers introduces warm-cache bias
