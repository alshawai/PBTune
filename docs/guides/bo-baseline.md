# Run the Bayesian Optimization baseline

> Last reviewed: 2026-06-07

See also: [reference/cli](../reference/cli.md#srcscriptsbo_baseline--bayesian-optimisation-baseline), [architecture/bo-baseline](../architecture/bo-baseline.md), [pbt-vs-bo-comparison](pbt-vs-bo-comparison.md)

This guide is for someone who wants to **run** the BO baseline. For the architecture and design rationale of the baseline, read [architecture/bo-baseline](../architecture/bo-baseline.md).

The most common use case is producing a BO session that can be compared head-to-head against a PBT session — for that, jump to [Match a PBT session](#1-match-a-pbt-session-recommended).

---

## Quick checks before launching

Confirm dependencies:

```bash
python -c "import smac, ConfigSpace; print(smac.__version__, ConfigSpace.__version__)"
# Expected: smac >= 2.2.0, ConfigSpace >= 1.1.0
```

Confirm Docker is reachable (recommended for publication-grade comparisons):

```bash
docker info >/dev/null && echo OK
```

If Docker isn't reachable, every command on this page accepts `--no-docker` to fall back to bare-metal with reduced isolation.

---

## 1. Match a PBT session (recommended)

The single most useful command — runs BO with all comparable settings copied from a PBT session, ensuring a fair head-to-head:

```bash
python -m src.scripts.bo_baseline \
    --pbt-session results/oltp/oltp_read_write/pbt_runs/minimal/tuning_sessions/pbt_results_20260504_1825.json \
    --seed 42
```

What gets copied automatically:

- knob tier, benchmark, workload type, tuning mode
- sysbench / TPC-H runtime parameters
- `population_size × total_generations` becomes the BO iteration budget
- `num_parallel_workers` becomes `--resource-division` (per-worker resource slicing)
- snapshot settings (`enable_snapshots`, `snapshot_restore_interval`) with iteration scaling

You only need `--seed` (and `--no-docker` if applicable). Override anything by passing it explicitly.

## 2. Run BO independently

Without a reference session you must specify the search space and runtime parameters explicitly:

```bash
# Smallest possible smoke test
python -m src.scripts.bo_baseline \
    --tier minimal \
    --iterations 3 \
    --benchmark sysbench \
    --duration 10 \
    --warmup 5

# Standard BO run (50 iterations, OLTP)
python -m src.scripts.bo_baseline \
    --tier core \
    --iterations 50 \
    --benchmark sysbench \
    --workload oltp \
    --sysbench-workload oltp_read_write

# Comprehensive BO run (100 iterations, TPC-H)
python -m src.scripts.bo_baseline \
    --tier standard \
    --iterations 100 \
    --benchmark tpch \
    --scale-factor 1.0
```

## 3. Multi-seed campaign for statistical significance

```bash
for seed in 42 123 456 789 1024; do
    python -m src.scripts.bo_baseline \
        --pbt-session results/oltp/oltp_read_write/pbt_runs/minimal/tuning_sessions/pbt_results_20260504_1825.json \
        --seed $seed
done
```

Then run the post-hoc evaluation suite against each output:

```bash
for f in results/oltp/oltp_read_write/bo_runs/minimal/tuning_sessions/bo_results_*.json; do
    python -m src.evaluation --session "$f" --repetitions 5
done
```

Or feed all of them to the cross-method comparison script for aggregated convergence + Pareto figures:

```bash
python -m src.scripts.pbt_vs_bo_comarison \
    --pbt results/oltp/oltp_read_write/pbt_runs/minimal/tuning_sessions/pbt_results_*.json \
    --bo  results/oltp/oltp_read_write/bo_runs/minimal/tuning_sessions/bo_results_*.json \
    --output-dir analysis/oltp-rw-minimal
```

See [pbt-vs-bo-comparison](pbt-vs-bo-comparison.md).

## 4. Choose the surrogate

Random Forest (default) is robust across tier sizes:

```bash
python -m src.scripts.bo_baseline --tier core --bo-surrogate rf --iterations 50
```

Gaussian Process is stronger on low-dimensional, smooth spaces — recommended for `minimal` tier only:

```bash
python -m src.scripts.bo_baseline --tier minimal --bo-surrogate gp --iterations 30
```

For why these defaults exist, see [architecture/bo-baseline §Facade selection](../architecture/bo-baseline.md#facade-selection).

## 5. Run BO in parallel

```bash
python -m src.scripts.bo_baseline \
    --tier core \
    --iterations 50 \
    --batched-bo \
    --resource-division 4
```

`--batched-bo` enables ask-tell parallel evaluation; `--resource-division` slices host RAM/CPU across the parallel workers (same role as `num_parallel_workers` for PBT). When `--pbt-session` is provided, the resource division is inherited from the reference session — you don't need to specify it manually.

## 6. Override scoring

Re-evaluate under a different scoring policy without changing the search space:

```bash
python -m src.scripts.bo_baseline \
    --pbt-session results/.../pbt_results_<timestamp>.json \
    --scoring-policy feature_driven_v2 \
    --seed 42
```

Available policies: `fixed_v1` (legacy static weights), `feature_driven_v2` (workload-feature-driven). The chosen policy is recorded in the output JSON's `tuning_session.scoring_policy`.

---

## Parameter reference (most-used flags)

For the **complete** flag set, see [reference/cli §src.scripts.bo_baseline](../reference/cli.md#srcscriptsbo_baseline--bayesian-optimisation-baseline).

| Flag | Default | When to use |
| --- | --- | --- |
| `--pbt-session PATH` | none | **Almost always.** Copies all parity settings from a PBT session for fair comparison. |
| `--tier {minimal\|core\|standard\|extensive}` | required without `--pbt-session` | Knob search space size. |
| `--iterations N` | `50`, or `population_size × total_generations` from `--pbt-session` | Evaluation budget. |
| `--seed INT` | `42` | Master seed; recorded in output JSON. |
| `--bo-surrogate {rf\|gp}` | `rf` | RF for high-dim/mixed; GP for low-dim/smooth. |
| `--batched-bo` | off | Parallel ask-tell mode. |
| `--resource-division N` | `1`, or PBT `num_parallel_workers` | Denominator for slicing host resources. |
| `--scoring-policy {fixed_v1\|feature_driven_v2}` | per-workload default | Override the active scoring policy. |
| `--enable-snapshots` | off, or PBT `enable_snapshots` | Periodic snapshot restoration to combat data drift. |
| `--snapshot-restore-interval N` | `1`, or scaled PBT interval | Iterations between restorations. |
| `--no-docker` | off | Bare-metal fallback (reduced isolation; tagged in output JSON). |
| `--force-recreate-instances` / `--force-recreate-baseline` | off | Reset state before launching. |

---

## Output

Results are written to:

```text
{output_dir}/{workload_type}/bo_runs/{tier}/tuning_sessions/bo_results_{timestamp}.json
```

The schema is identical to the PBT session schema with one optimiser-specific addition (`optimizer: "bo_smac3"`, `bo_surrogate`, etc.). Full schema in [reference/session-json-schema §BO session schema](../reference/session-json-schema.md#bo-session-schema).

When `--pbt-session` was provided, the output JSON additionally records:

- `reference_pbt_session` — path to the source PBT session
- `reference_pbt_knobs` — knob names copied from `best_configuration.knobs`
- `num_parallel_workers` — parallel BO worker count
- `resource_equalization` — whether per-worker resource slices came from the reference session

These are what the cross-method comparison script consumes to verify parity.

---

## Troubleshooting

### `ConfigSpace` / `smac` import errors

```bash
pip install 'ConfigSpace>=1.1.0' 'smac>=2.2.0'
```

Note the import path is `from ConfigSpace import …` (capital `C`), not `from configspace`.

### Connection errors

- Verify PostgreSQL instances on ports 5440+ are reachable.
- Check `.env` credentials.
- `python -m src.scripts.cleanup_instances` to reset stale state.
- Re-launch with `--force-recreate-instances` if cleanup didn't help.

### Memory pressure

Reduce in this order:

1. `--iterations` — fewer evaluations means smaller surrogate model.
2. `--tier minimal` — fewer knobs means a smaller `ConfigSpace`.
3. `--duration` — shorter measurement window means smaller PostgreSQL working set per evaluation.

### Long runtimes

Verify the iteration budget is reasonable for the tier (see [architecture/bo-baseline](../architecture/bo-baseline.md) on why high-dim spaces need more iterations to converge). For wall-clock comparisons, prefer `--batched-bo` over sequential when the host has spare cores.

### Tests

```bash
python -m pytest tests/test_bo_baseline.py -v
```

Targeted tests:

```bash
python -m pytest tests/test_bo_baseline.py::TestSearchSpaceTranslation -v
```
