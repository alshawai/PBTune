# Knob Tiers — Detailed Reference

## Tier System

Tiers organize PostgreSQL knobs by importance for tuning efficiency.
Fewer knobs = faster convergence but potentially suboptimal.

### Tier Membership

| Tier | Count | Purpose | CSV Source |
|------|-------|---------|------------|
| `minimal` | 5 | Quick testing, debugging, CI | `data/tuner_knobs/minimal_knobs.csv` |
| `core` | 10 | Standard experiments | `data/tuner_knobs/core_knobs.csv` |
| `standard` | 20 | Comprehensive analysis | `data/tuner_knobs/standard_knobs.csv` |
| `extensive` | 40+ | Research-grade full sweep | `data/tuner_knobs/extensive_knobs.csv` |

### Expert-Defined Tier Membership (Current)

**Minimal (5):** `shared_buffers`, `work_mem`, `effective_cache_size`, `random_page_cost`, `max_connections`

**Core (10):** Minimal + `maintenance_work_mem`, `checkpoint_completion_target`, `wal_buffers`, `max_worker_processes`, `max_parallel_workers`

**Standard (20):** Core + WAL, planner, and I/O knobs

**Extensive (40+):** All tunable knobs from `pg_settings` with curated safe bounds

### Data-Driven Tiers (Future — via knob-importance-analysis)

Once sufficient experiment data exists (1000+ config-score pairs):
1. Run fANOVA importance analysis
2. Apply Jenks Natural Breaks clustering on importance scores
3. Use silhouette score to find optimal number of tiers (k=2..6)
4. Generate new tier CSVs with data-driven membership

## CSV Format

```csv
name,type,min_val,max_val,default_val,context,unit,scale,category,tier
shared_buffers,real,0.05,0.40,0.25,postmaster,fraction_of_ram,linear,memory,minimal
work_mem,real,0.005,0.10,0.02,sighup,fraction_of_ram,log,memory,minimal
```

Key columns:
- `type`: `real`, `integer`, `enum`, `bool`
- `context`: `postmaster`, `sighup`, `user`
- `scale`: `linear` or `log` (determines perturbation method)
- `unit`: `fraction_of_ram`, `fraction_of_cores`, `ms`, `bytes`, etc.

## Regenerating Tier CSVs

After modifying `src/knobs/knob_metadata.py`:
```bash
python -m src.knobs
```
This runs the full pipeline: `pg_settings → retrieval → preprocess → tier CSVs`
