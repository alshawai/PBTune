---
description: How to launch a PBT tuning experiment end-to-end
---

# Run PBT Experiment

## Prerequisites
1. PostgreSQL is installed and accessible via `pg_ctl` / `initdb` (bare-metal),
   or Docker is available (default backend)
2. Python environment activated with all dependencies (`./scripts/bootstrap.sh`)
3. Sysbench installed (for OLTP `--benchmark sysbench`) or TPC-H `dbgen` built
   (for OLAP `--benchmark tpch`)
4. `.env` file configured with database credentials

## Steps

### 1. Verify Environment
```bash
# Check PostgreSQL
pg_ctl --version
# Check sysbench (OLTP only)
sysbench --version
# Check Python deps
python -c "import src.tuner; print('OK')"
```

### 2. Choose Experiment Parameters

| Parameter | Options | Default |
|-----------|---------|---------|
| `--workload` / `--benchmark` | `oltp`/`olap`/`mixed` or `sysbench`/`tpch` | `oltp` |
| `--tier` | `minimal`, `core`, `standard`, `extensive` | `minimal` |
| `--population` | 4-16 | (config preset) |
| `--generations` | 5-50 | (config preset) |
| `--random-seed` | any integer | unset |
| `--config` | `rapid`, `standard`, `thorough`, `research`, `extreme` | `standard` |

Note: `--workload`, `--workload-file`, and `--benchmark` are mutually exclusive.
Use `--benchmark sysbench` (with `--sysbench-workload`) or `--benchmark tpch` for
external benchmark binaries; use `--workload {oltp,olap,mixed}` for built-in JSON
workloads.

### 3. Run Single Experiment
```bash
python -m src.tuner.main \
    --benchmark sysbench \
    --sysbench-workload oltp_read_write \
    --tier core \
    --population 8 \
    --generations 20 \
    --random-seed 42 \
    --output-dir results/oltp/oltp_read_write/pbt_runs/core/seed_42/
```

### 4. Run Multi-Seed Campaign
The five-seed standard campaign uses seeds `42, 123, 456, 789, 1024`.

```bash
for SEED in 42 123 456 789 1024; do
    python -m src.tuner.main \
        --benchmark sysbench \
        --sysbench-workload oltp_read_write \
        --tier core \
        --population 8 \
        --generations 20 \
        --random-seed $SEED \
        --output-dir results/oltp/oltp_read_write/pbt_runs/core/seed_${SEED}/
done
```

The repository also ships `scripts/run_tier{1,2,3}.sh` and `scripts/run_all.sh`
as ready-made experiment-matrix wrappers if you prefer not to script the loop
yourself.

### 5. Run Baseline (Default PG Config)
The tuner CLI does not have a `--baseline-only` flag. Default-vs-tuned baselines
are produced post-hoc by `python -m src.evaluation` against an existing PBT
session JSON. The evaluator runs both the default config and the tuned config
under identical conditions.

```bash
python -m src.evaluation \
    --session results/oltp/oltp_read_write/pbt_runs/core/seed_42/pbt_results_YYYYMMDD_HHMM.json \
    --repetitions 5
```

### 6. Warm-Start (Transfer Learning)
```bash
python -m src.tuner.main \
    --benchmark sysbench \
    --sysbench-workload oltp_read_write \
    --tier standard \
    --warm-start results/oltp/oltp_read_write/pbt_runs/core/best_configs/best_config_YYYYMMDD_HHMM.json \
    --population 8 \
    --generations 15 \
    --random-seed 42 \
    --output-dir results/oltp/oltp_read_write/pbt_runs/standard/warm_start/
```

## After the Run

1. Results saved under `--output-dir` as `pbt_results_{timestamp}.json`
2. Best config saved to `results/{olap|oltp/{workload}}/pbt_runs/{tier}/best_configs/`
3. Check `generation_history` in the results JSON for convergence
4. Use the `results-and-visualization` skill (and `python -m src.visualization`)
   for plotting
