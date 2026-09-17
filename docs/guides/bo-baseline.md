# Run the Bayesian Optimization baseline

See also: [reference/cli](../reference/cli.md#srctuners-bo--bayesian-optimisation-baseline), [architecture/bo-baseline](../architecture/bo-baseline.md), [pbt-vs-bo-comparison](pbt-vs-bo-comparison.md)

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
python -m src.tuners bo \
    --pbt-session results/sessions/oltp_read_write/pbt/minimal/traces/trace_20260504_1825.json \
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
python -m src.tuners bo \
    --tier minimal \
    --iterations 3 \
    --benchmark sysbench \
    --duration 10 \
    --warmup 5

# Standard BO run (50 iterations, OLTP)
python -m src.tuners bo \
    --tier core \
    --iterations 50 \
    --benchmark sysbench \
    --workload oltp \
    --sysbench-workload oltp_read_write

# Comprehensive BO run (100 iterations, TPC-H)
python -m src.tuners bo \
    --tier standard \
    --iterations 100 \
    --benchmark tpch \
    --scale-factor 1.0
```

## 3. Multi-seed campaign for statistical significance

```bash
for seed in 42 123 456 789 1024; do
    python -m src.tuners bo \
        --pbt-session results/sessions/oltp_read_write/pbt/minimal/traces/trace_20260504_1825.json \
        --seed $seed
done
```

Then run the post-hoc evaluation suite against each output:

```bash
for f in results/sessions/oltp_read_write/bo/minimal/traces/trace_*.json; do
    python -m src.evaluation --session "$f" --repetitions 5
done
```

Or feed all of them to the cross-method comparison script for aggregated convergence + Pareto figures:

```bash
python -m src.scripts.pbt_vs_bo_comarison \
    --pbt results/sessions/oltp_read_write/pbt/minimal/traces/trace_*.json \
    --bo  results/sessions/oltp_read_write/bo/minimal/traces/trace_*.json \
    --output-dir analysis/oltp-rw-minimal
```

See [pbt-vs-bo-comparison](pbt-vs-bo-comparison.md).

## 4. Choose the surrogate

Random Forest (default) is robust across tier sizes:

```bash
python -m src.tuners bo --tier core --bo-surrogate rf --iterations 50
```

Gaussian Process is stronger on low-dimensional, smooth spaces — recommended for `minimal` tier only:

```bash
python -m src.tuners bo --tier minimal --bo-surrogate gp --iterations 30
```

For why these defaults exist, see [architecture/bo-baseline §Facade selection](../architecture/bo-baseline.md#facade-selection).

## 5. Run BO under PBT-matched contention

```bash
python -m src.tuners bo \
    --tier core \
    --iterations 50 \
    --cotenancy-degree 4 \
    --resource-division 4
```

`--cotenancy-degree N` reproduces PBT's single-host contention: each BO measurement window runs `N` concurrent instances (the foreground trial plus `N − 1` background-load instances), so BO is measured under the same load a PBT generation of `N` workers would create. `--resource-division` slices host RAM/CPU per instance (same role as `num_parallel_workers` for PBT). When `--pbt-session` is provided, both are inherited from the reference session — you don't need to specify them manually. BO itself still evaluates one trial at a time (single-worker ask-tell); it is not run as parallel BO trials.

## 6. Override scoring

Re-evaluate under a different scoring policy without changing the search space:

```bash
python -m src.tuners bo \
    --pbt-session results/.../traces/trace_<timestamp>.json \
    --scoring-policy feature_driven_v2 \
    --seed 42
```

Available policies: `fixed_v1` (legacy static weights), `feature_driven_v2` (workload-feature-driven). The chosen policy is recorded in the output JSON's `tuning_session.scoring_policy`.

---

## Parameter reference (most-used flags)

For the **complete** flag set, see [reference/cli §src.tuners bo](../reference/cli.md#srctuners-bo--bayesian-optimisation-baseline).

| Flag | Default | When to use |
| --- | --- | --- |
| `--pbt-session PATH` | none | **Almost always.** Copies all parity settings from a PBT session for fair comparison. |
| `--tier {minimal\|core\|standard\|extensive}` | required without `--pbt-session` | Knob search space size. |
| `--iterations N` | `50`, or `population_size × total_generations` from `--pbt-session` | Evaluation budget. |
| `--seed INT` | `42` | Master seed; recorded in output JSON. |
| `--bo-surrogate {rf\|gp}` | `rf` | RF for high-dim/mixed; GP for low-dim/smooth. |
| `--cotenancy-degree N` | `1`, or PBT `num_parallel_workers` | Concurrent instances (foreground trial + background load) per measurement window, matching PBT contention. |
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
{output_dir}/sessions/{workload_type}/bo/{tier}/traces/trace_{timestamp}.json
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

Verify the iteration budget is reasonable for the tier (see [architecture/bo-baseline](../architecture/bo-baseline.md) on why high-dim spaces need more iterations to converge). For comparisons that must match a PBT run's host contention, set `--cotenancy-degree` (or pass `--pbt-session` to inherit it).

### Tests

```bash
python -m pytest tests/unit/tuners/bo/ -v
```

Targeted tests:

```bash
python -m pytest tests/unit/tuners/bo/test_bo_config_and_search.py::TestSearchSpaceTranslation -v
```
