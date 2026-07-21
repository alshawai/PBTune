# CLI Reference


See also: [getting-started/quickstart](../getting-started/quickstart.md), [guides/evaluation-runbook](../guides/evaluation-runbook.md), [guides/bo-baseline](../guides/bo-baseline.md), [guides/pbt-vs-bo-comparison](../guides/pbt-vs-bo-comparison.md), [guides/scalpel-rollout](../guides/scalpel-rollout.md)

Consolidated reference for every command-line flag across all six user-facing entry points. Use the per-guide docs for narrative context — this page is for **lookup**.

```text
python -m src.tuners pbt                 # tuning sessions (PBT)
python -m src.tuners                      # LHS-design importance-sampling tuner
python -m src.evaluation                 # post-hoc default-vs-tuned comparison
python -m src.scripts.bo_baseline        # SMAC3 Bayesian-Optimisation baseline
python -m src.scripts.pbt_vs_bo_comarison  # cross-method comparison
python -m src.visualization              # publication figure generation
```

For the canonical authority on any flag's exact semantics, run the entry point with `--help`. This page reflects the flag set as of 2026-06-22.

---

## Table of contents

1. [`src.tuners pbt` — PBT tuning](#srctuners-pbt--pbt-tuning)
2. [`src.tuners` — LHS-design tuning](#srctuners--lhs-design-tuning)
3. [`src.evaluation` — default-vs-tuned comparison](#srcevaluation--default-vs-tuned-comparison)
4. [`src.scripts.bo_baseline` — Bayesian-Optimisation baseline](#srcscriptsbo_baseline--bayesian-optimisation-baseline)
5. [`src.scripts.pbt_vs_bo_comarison` — cross-method comparison](#srcscriptspbt_vs_bo_comarison--cross-method-comparison)
6. [`src.visualization` — publication figures](#srcvisualization--publication-figures)
7. [Common cross-tool flags](#common-cross-tool-flags)

---

## `src.tuners pbt` — PBT tuning

The primary entry point. Two equivalent invocations: the routed form `python -m src.tuners pbt` and the direct form `python -m src.tuners.pbt`. See [getting-started/quickstart](../getting-started/quickstart.md) for a walkthrough and [pbt-core](../architecture/pbt-core.md) for what the flags actually do.

### Search space

| Flag | Default | Purpose |
| --- | --- | --- |
| `--tier {minimal\|core\|standard\|extensive}` | `minimal` | Knob tier (~5 / 13 / 36 / 80+ knobs). See [adding-knobs](../guides/adding-knobs.md). |
| `--knob-source {expert\|data_driven}` | `expert` | `expert` reads `data/expert_defined_knobs/`; `data_driven` reads `data/data_driven_knobs/{workload}/`. |
| `--warm-start <path>` | none | Load `best_config.json` from a previous session for fractional warm-start. See [hardware-aware-normalization](../architecture/hardware-aware-normalization.md). |

### PBT configuration

| Flag | Default | Purpose |
| --- | --- | --- |
| `--config {rapid\|standard\|thorough\|research\|extreme}` | `standard` | Pre-configured `PBTConfig` profile bundling population/generations/durations. |
| `--random-seed <int>` | `42` | Master seed for population init, LHS, perturbation. |
| `--population <int>` | from profile | Override the profile's worker count. |
| `--generations <int>` | from profile | Override the profile's generation count. |
| `--parallel-workers <int>` | population size | Number of workers running concurrently. Limits resource division — see [hardware-aware-normalization §7](../architecture/hardware-aware-normalization.md#7-docker-cpu-subset-enforcement). |
| `--worker-ram <str>` | auto | RAM per worker (e.g. `3G`, `512M`, `1073741824`). Bypasses auto-detection; total across all workers must not exceed host RAM. |
| `--worker-cpus <int>` | auto | CPU cores per worker. Bypasses auto-detection; total across all workers must not exceed host cores. |
| `--tuning-mode {online\|offline\|adaptive}` | `offline` | Restart policy. `online` = runtime knobs only, no restarts; `offline` = all knobs, restart every generation; `adaptive` = all knobs, restart every N generations. See [workload-orchestrator §Restart policy](../architecture/workload-orchestrator.md#restart-policy-and-tuning-modes). |
| `--perturbation-factor <float>` | `0.2` | Perturbation spread factor for knob exploration. Range is `[1-X, 1+X]`. |
| `--disable-early-stopping` | off | Run the full `--generations` even after the early-stopping patience expires. |
| `--no-sync` | off | Disable lockstep barriers. **Reduces measurement fairness** — use only for single-worker debugging. See [generation-barriers](../architecture/generation-barriers.md). |

### Scoring

| Flag | Default | Purpose |
| --- | --- | --- |
| `--scoring-policy {fixed_v1\|feature_driven_v2}` | falls back to PBT config (`feature_driven_v2` for new runs) | Score formula. See [feature-driven-scoring](../architecture/feature-driven-scoring.md). |
| `--scoring-policy-version <str>` | from policy | Pinned policy version recorded in session JSON. |
| `--metric-reference-version <str>` | from policy | Metric semantics version recorded in session JSON. |
| `--scoring-calibration-evals <int>` | `5` | Evaluations before the quantile normaliser's first calibration. |

### Workload

| Flag | Default | Purpose |
| --- | --- | --- |
| `--workload {oltp\|olap\|mixed}` | `oltp` | Built-in workload type when neither `--benchmark` nor `--workload-file` is given. |
| `--workload-file <path>` | none | Custom JSON/YAML workload template. See [adding-workloads](../guides/adding-workloads.md). |
| `--benchmark {sysbench\|tpch}` | none | External C-binary benchmark. See [benchmarking](benchmarking.md). |
| `--duration <float>` | `30.0` | Measurement window seconds. |
| `--warmup <float>` | `10.0` | Warmup window seconds before measurement. |

#### Sysbench-specific

| Flag | Default | Purpose |
| --- | --- | --- |
| `--sysbench-workload {oltp_read_only\|oltp_read_write\|oltp_write_only}` | `oltp_read_write` | Sysbench OLTP mode. |
| `--sysbench-tables <int>` | `10` | Number of `sbtest{N}` tables. |
| `--sysbench-table-size <int>` | `100000` | Rows per table. |

#### TPC-H-specific

| Flag | Default | Purpose |
| --- | --- | --- |
| `--scale-factor <float>` | `1.0` | TPC-H scale factor (SF=1 → ~1 GB, ~6M lineitem rows). |

### Instance management

| Flag | Default | Purpose |
| --- | --- | --- |
| `--data-dir <path>` | `./.instances` | Per-worker PostgreSQL data directory root. |
| `--no-docker` | off | Use bare-metal PostgreSQL instead of Docker. **Reduces isolation** — see [environment-backends](../architecture/environment-backends.md). |
| `--docker-image <image>` | auto | Override the auto-resolved PostgreSQL image. |
| `--force-recreate-instances` | off | Tear down and recreate worker instances before starting. |
| `--cleanup-instances` | off | Remove all worker instances and exit (no tuning). Same as `python -m src.scripts.cleanup_instances`. |
| `--skip-schema-init` | off | Skip schema initialisation (assumes data already loaded). |
| `--force-recreate-baseline` | off | Tear down and recreate the baseline snapshot before starting. |

### Output

| Flag | Default | Purpose |
| --- | --- | --- |
| `--output-dir <path>` | `results` | Root of the result tree. |
| `--colocate-output` | off | Place HTML logs alongside session JSONs in the same directory. |
| `--ablation-variable <str>` | none | Tag the session with an ablation variable name (recorded in JSON metadata). |
| `--ablation-value <str>` | none | The value of the ablation variable (recorded in JSON metadata). |
| `--verbose {DEBUG\|INFO\|WARNING\|ERROR\|TRACE}` | `INFO` | Logging verbosity. |
| `--no-color` | off | Disable ANSI colour in console output. |

---

## `src.tuners` — LHS-design tuning

The strategy-unified tuner package. `python -m src.tuners` and `python -m src.tuners.lhs_design` are aliases for the same entry point: a fixed Latin Hypercube Sampling (LHS) *importance-design* sweep over the knob space, swept once with no evolution. The session JSON it writes (`tuning_strategy: "lhs"`, plus a `design_records` array) is the substrate the SCALPEL importance pipeline consumes. See [scalpel](../architecture/scalpel.md), [guides/scalpel-rollout](../guides/scalpel-rollout.md), and [ADR-006](../architecture/decisions/ADR-006-unified-tuners-package.md).

Only `--design-size` is LHS-specific; every other group below is the shared strategy-agnostic surface (`src/tuners/cli.py`) that future strategies reuse.

### Design configuration

| Flag | Default | Purpose |
| --- | --- | --- |
| `--design-size <int>` | profile-derived (`rapid`=8 / `standard`=32 / `thorough`=512 / `research`=1024) | Number of LHS design points to evaluate. Larger designs give SCALPEL more rows to attribute over at linear wall-clock cost. |

### Tuning configuration

| Flag | Default | Purpose |
| --- | --- | --- |
| `--config {rapid\|standard\|thorough\|research}` | `standard` | Execution profile supplying default worker count, benchmark settings, design size, and snapshot cadence — each overridable by individual flags. Note: **no `extreme`** (that profile is PBT population-scale-specific). |
| `--tier {minimal\|core\|standard\|extensive}` | `minimal` | Knob-space tier. SCALPEL designs are typically run on `extensive`. |
| `--knob-source {expert\|data_driven}` | `expert` | `expert` reads `data/expert_defined_knobs/`; `data_driven` reads `data/data_driven_knobs/{workload}/`. |
| `--parallel-workers <int>` | profile-derived (`rapid`=2 / `standard`=4 / `thorough`=8 / `research`=12) | Number of PostgreSQL instances evaluated concurrently. The design is swept in batches of this size. |
| `--random-seed <int>` | `42` | Seed for reproducible LHS sampling. |

### Workload settings

| Flag | Default | Purpose |
| --- | --- | --- |
| `--benchmark {sysbench\|tpch}` | `sysbench` | Benchmark driver. |
| `--workload {oltp\|olap\|mixed}` | `oltp` | Workload type for custom workloads. |
| `--workload-file <path>` | none | Custom workload file (non-sysbench/tpch only). |
| `--duration <float>` | profile default | Measurement-window seconds. |
| `--warmup <float>` | profile default | Warmup seconds before measurement. |
| `--scale-factor <float>` | profile default | TPC-H / template scale factor. |
| `--sysbench-tables <int>` | profile default | Number of sysbench tables. |
| `--sysbench-table-size <int>` | profile default | Rows per sysbench table. |
| `--sysbench-workload {oltp_read_only\|oltp_read_write\|oltp_write_only}` | `oltp_read_write` | Sysbench OLTP mode. |

### Per-worker resources

| Flag | Default | Purpose |
| --- | --- | --- |
| `--worker-ram <str>` | auto | RAM per worker (e.g. `3G`, `512M`, `1073741824`). Total across workers must not exceed host RAM. |
| `--worker-cpus <int>` | auto | CPU cores per worker. Total across workers must not exceed host cores. |
| `--worker-disk-read-bps <int>` | auto | Per-worker disk read bandwidth (bytes/sec, cgroup blkio / io.max). |
| `--worker-disk-write-bps <int>` | auto | Per-worker disk write bandwidth (bytes/sec). |
| `--worker-disk-read-iops <int>` | auto | Per-worker disk read IOPS ceiling. |
| `--worker-disk-write-iops <int>` | auto | Per-worker disk write IOPS ceiling. |
| `--probe-disk` / `--no-probe-disk` | `--probe-disk` | Run a short fio probe at startup to calibrate per-worker disk I/O budget. **Requires `fio`**; when fio is absent the run logs a WARNING and falls back to the disk-class heuristic. |

### Scoring & normalization

| Flag | Default | Purpose |
| --- | --- | --- |
| `--scoring-policy {fixed_v1\|feature_driven_v2}` | engine default | Performance-score aggregation policy. |
| `--scoring-policy-version <str>` | from policy | Frozen policy version recorded in the session JSON. |
| `--metric-reference-version <str>` | from policy | Frozen normalizer-metadata reference version. |

### Instance management

| Flag | Default | Purpose |
| --- | --- | --- |
| `--tuning-mode {online\|offline\|adaptive}` | `offline` | Restart policy. `online` = runtime knobs only, no restarts; `offline` = all knobs, restart every generation; `adaptive` = all knobs, restart every N generations. |
| `--no-docker` | off | Run on bare-metal PostgreSQL instead of Docker. |
| `--force-recreate-instances` | off | Force recreation of worker instances before starting. |
| `--force-recreate-baseline` | off | Force recreation of the shared baseline snapshot every per-worker instance is cloned from. |
| `--enable-snapshots` / `--disable-snapshots` | enabled | Restore each worker to the pristine baseline snapshot on the per-profile cadence so every design batch starts from identical DB state (no drift carried between batches). |
| `--snapshot-restore-interval N` | profile-derived (`rapid`=10 / `standard`=5 / `thorough`=1 / `research`=1) | Baseline-snapshot restore cadence in generations (one generation = one design batch). |
| `--cleanup-instances` | off | Remove PostgreSQL instance data after completion. |
| `--data-dir <path>` | `$PBT_DATA_ROOT` | Base directory for PostgreSQL instances (overrides `PBT_DATA_ROOT`). |

### Output & logging

| Flag | Default | Purpose |
| --- | --- | --- |
| `--output-dir <path>` | `results` | Base results directory. Session JSON lands under `{output-dir}/{workload}/[{sysbench_workload}/]lhs_runs/{tier}/tuning_sessions/`. |
| `--colocate-output` | off | Place results/logs under the data directory (`{data-root}/results`) instead of `./results/`. |
| `--verbose {DEBUG\|INFO\|WARNING\|ERROR\|TRACE}` | `INFO` | Logging verbosity. |
| `--no-color` | off | Disable ANSI colour in console output. |

A timestamped `lhs_design_<ts>.html` log is written under the resolved output root, matching the HTML-log parity PBT and BO already provide.

---

## `src.evaluation` — default-vs-tuned comparison

Post-hoc evaluation suite. See [guides/evaluation-runbook](../guides/evaluation-runbook.md) for the runbook and [evaluation-suite](../architecture/evaluation-suite.md) for the architecture.

| Flag | Default | Purpose |
| --- | --- | --- |
| `--session <path>` | required | PBT (or BO) session JSON to evaluate. |
| `--bo-session <path>` | none | BO session JSON for 3-way Default vs BO vs PBT comparison alongside `--session`. |
| `--benchmark {sysbench\|tpch}` | from session | Override the benchmark recorded in the session. |
| `--repetitions <int>` | `5` | Number of paired (default, tuned) runs. |
| `--seed <int>` | `50000` | Base seed; repetition `i` uses `seed + i - 1` for both default and tuned. |
| `--sysbench-workload` | from session | Override the Sysbench mode. |
| `--sysbench-tables`, `--sysbench-table-size`, `--sysbench-duration`, `--sysbench-warmup-seconds` | from session | Override Sysbench runtime parameters. |
| `--tpch-scale-factor`, `--tpch-warmup-passes` | from session | Override TPC-H runtime parameters. |
| `--scoring-policy` | from session | Override the scoring policy. Use to rescore a `fixed_v1` session under `feature_driven_v2`. |
| `--scoring-policy-version`, `--metric-reference-version` | from session | Pinned policy/metric versions. |
| `--no-docker` | off | Bare-metal evaluation (reduced isolation; tagged in output JSON). |
| `--data-dir` | `./.instances` | Worker PostgreSQL data directory root. |
| `--docker-image <image>` | `pbt-eval` | Docker image name/tag for evaluation containers. |
| `--output-dir` | `results` | Comparison artefact root. |
| `--colocate-output` | off | Place comparison HTML alongside the JSON. |
| `--verbose {DEBUG\|INFO\|WARNING\|ERROR}` | `INFO` | Logging verbosity. |
| `-v` | off | Shortcut for `--verbose DEBUG`. |

---

## `src.scripts.bo_baseline` — Bayesian-Optimisation baseline

SMAC3-based BO runner. See [guides/bo-baseline](../guides/bo-baseline.md) for the full runbook.

### PBT-session parity

| Flag | Default | Purpose |
| --- | --- | --- |
| `--pbt-session <path>` | none | Reference PBT session. BO copies `tier`, `benchmark`, `workload_type`, runtime params, and `num_parallel_workers`. Without it, you must specify `--tier` and friends. |

### Search space

| Flag | Default | Purpose |
| --- | --- | --- |
| `--tier` | required if no `--pbt-session` | Knob tier. |
| `--knob-source {expert\|data_driven}` | `expert` | Same semantics as PBT. |
| `--config <profile>` | none | Same as PBT's `--config`; mainly carries timing defaults. |

### BO control

| Flag | Default | Purpose |
| --- | --- | --- |
| `--iterations <int>` | `50` or `population_size × total_generations` from `--pbt-session` | Total BO iterations. |
| `--seed <int>` | `42` | SMAC RNG seed. |
| `--bo-surrogate {rf\|gp}` | `rf` | `rf` = Random Forest (HyperparameterOptimizationFacade); `gp` = Gaussian Process (BlackBoxFacade). |
| `--range-update-interval <int>` | `10` | Pilot phase size: iterations before normalisation ranges freeze. |
| `--batched-bo` | off | Parallel ask-tell mode using `ThreadPoolExecutor`. |
| `--resource-division <int>` | `1` (or `num_parallel_workers` from `--pbt-session`) | Denominator for dividing host resources across parallel BO workers. |
| `--scoring-policy` | per-workload default | Same as PBT. |
| `--enable-snapshots` | off | Periodic snapshot restoration. |
| `--snapshot-restore-interval <int>` | `1` (scaled from PBT) | Restore baseline snapshot every N iterations. |

### Benchmark

| Flag | Default | Purpose |
| --- | --- | --- |
| `--benchmark {sysbench\|tpch}` | `sysbench` (or from `--pbt-session`) | External benchmark. |
| `--workload {oltp\|olap\|mixed}` | `oltp` | Workload-type tag for scoring. |
| `--duration <float>` | `30` | Measurement window seconds. |
| `--warmup <float>` | `10` | Warmup seconds. |
| `--sysbench-workload`, `--sysbench-tables`, `--sysbench-table-size`, `--scale-factor`, `--tpch-warmup-passes` | same defaults as PBT | Per-benchmark overrides. |
| `--benchmark-config <path>` | none | YAML override for benchmark-specific defaults. |

### Instance management

| Flag | Default | Purpose |
| --- | --- | --- |
| `--data-dir`, `--no-docker`, `--docker-image`, `--force-recreate-instances`, `--force-recreate-baseline`, `--tuning-mode` | same as PBT | |

### Output

| Flag | Default | Purpose |
| --- | --- | --- |
| `--output-dir` | `results` | Result tree root. |
| `--colocate-output` | off | Place HTML logs alongside JSON. |
| `--verbose` | `INFO` | Logging level. |

---

## `src.scripts.pbt_vs_bo_comarison` — cross-method comparison

Aggregates multiple PBT and BO sessions into publication-ready convergence plots, Pareto plots, and a statistical-significance table. See [guides/pbt-vs-bo-comparison](../guides/pbt-vs-bo-comparison.md).

| Flag | Default | Purpose |
| --- | --- | --- |
| `--pbt <path> [<path> …]` | required | One or more PBT session JSONs. |
| `--bo <path> [<path> …]` | required | One or more BO session JSONs. |
| `--output-dir <path>` | `analysis` | Output directory for CSV summaries and PDF figures. |

The script does not validate that the PBT and BO sessions match settings — `--pbt-session` on the BO baseline is the recommended way to ensure parity before reaching this script.

---

## `src.visualization` — publication figures

Figure generation against a result tree. See [guides/visualization](../guides/visualization.md).

| Flag | Default | Purpose |
| --- | --- | --- |
| `--list` | off | Print every registered figure and exit. |
| `--figure <fig_id>` | none | Generate one figure by ID. |
| `--category <name>` | none | Generate all figures in a category. |
| `--venue {pvldb\|springer\|preview}` | `pvldb` | Sizing + typography preset. |
| `--data-dir <path>` | `results` | Result tree root. |
| `--output-dir <path>` | `figures` | Output directory for generated artefacts. |
| `--format {pdf\|png\|svg}` | per-figure preference | Override the registered output format. |
| `--importance-top-k <int>` | `20` | Top-K knobs in the importance plot. |
| `--dependence-top-k <int>` | `8` | Top-K knobs in the SHAP dependence plot grid. |
| `--interaction-top-k <int>` | `12` | Top-K knobs in the pairwise interaction heatmap. |

---

## Common cross-tool flags

Several flags appear in multiple entry points with identical semantics:

| Flag | Where | Notes |
| --- | --- | --- |
| `--no-docker` | tuner, evaluation, bo, tuners | Reduced-isolation fallback. Tagged in output metadata. |
| `--docker-image` | tuner, evaluation, bo | Override the auto-resolved image. |
| `--data-dir` | tuner, evaluation, bo, tuners, viz | Worker data directory root. |
| `--output-dir` | every entry point | Result tree root. |
| `--scoring-policy` | tuner, evaluation, bo, tuners | Same semantics; the value pinned in the output JSON. |
| `--seed` (bo) / `--random-seed` (tuner, tuners) / `--seed` (evaluation) | various | Master deterministic seed. Names differ across CLIs for historical reasons; semantics are equivalent. |
| `--colocate-output` | tuner, evaluation, bo, tuners | Co-locate HTML logs with JSON artefacts instead of using a separate `logs/` subdirectory. |
| `--probe-disk` / `--no-probe-disk` | tuners | Calibrate per-worker disk I/O budget with a short `fio` probe (default on). When `fio` is absent the probe is skipped with a WARNING and the heuristic budget is used. |
| `--enable-snapshots` / `--snapshot-restore-interval` | bo, tuners | Baseline-snapshot restoration cadence. Both default to enabled; the unset interval is profile-derived — bo and tuners share the rapid=10 / standard=5 / thorough=1 / research=1 schedule. |
| `--verbose` | every entry point | Logging level. |
