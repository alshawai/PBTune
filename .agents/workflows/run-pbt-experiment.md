---
description: How to launch a PBT tuning experiment end-to-end
---

# Run PBT Experiment

## Prerequisites
1. PostgreSQL is installed and accessible via `pg_ctl` / `initdb`
2. Python environment activated with all dependencies
3. Sysbench installed (for OLTP) or TPC-H `dbgen` built (for OLAP)
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
| `--workload` | `oltp`, `olap`, `mixed` | `oltp` |
| `--tier` | `minimal`, `core`, `standard`, `extensive` | `core` |
| `--population-size` | 4-16 | 8 |
| `--generations` | 5-50 | 20 |
| `--seed` | any integer | 42 |

### 3. Run Single Experiment
```bash
python -m src.tuner.main \
    --workload oltp \
    --tier core \
    --population-size 8 \
    --generations 20 \
    --seed 42 \
    --output results/sysbench/pbt_runs/core/seed_42/
```

### 4. Run Multi-Seed Campaign
Run with each of the 5 standard seeds: `42, 123, 456, 789, 1024`

```bash
for SEED in 42 123 456 789 1024; do
    python -m src.tuner.main \
        --workload oltp \
        --tier core \
        --population-size 8 \
        --generations 20 \
        --seed $SEED \
        --output results/sysbench/pbt_runs/core/seed_${SEED}/
done
```

### 5. Run Baseline (Default PG Config)
```bash
python -m src.tuner.main \
    --workload oltp \
    --tier minimal \
    --baseline-only \
    --repetitions 5 \
    --output results/sysbench/baselines/
```

### 6. Warm-Start (Transfer Learning)
```bash
python -m src.tuner.main \
    --workload oltp \
    --tier standard \
    --warm-start results/best_configs/best_config_XXXX.json \
    --population-size 8 \
    --generations 15 \
    --seed 42 \
    --output results/sysbench/pbt_runs/standard/warm_start/
```

## After the Run

1. Results saved to `--output` directory as `results_{timestamp}.json`
2. Best config saved to `results/best_configs/`
3. Check `generation_history` in results JSON for convergence
4. Use `results-and-visualization` skill for plotting
