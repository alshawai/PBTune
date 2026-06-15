# Hardware-Aware Fractional Normalization — Detailed Reference

## Why Fractions?

Database knob values are hardware-dependent. `shared_buffers = 4GB` is optimal on a 16GB
machine but wasteful on a 64GB one. To enable transfer learning (warm-start) across
different hardware, all hardware-relative knobs are stored as fractions.

## Fraction Types

| Unit | Base Resource | Example |
|------|--------------|---------|
| `fraction_of_ram` | Detected total RAM | `shared_buffers=0.25` → 25% of RAM |
| `fraction_of_cores` | Detected CPU cores | `max_parallel_workers=0.5` → 50% of cores |

## Resolution Flow

```
Fraction (stored in population)
    → HardwareInfo.detect() captures RAM, cores
    → KnobSpace.resolve_to_absolute(fraction, hardware)
    → Absolute value (written to postgresql.conf)
```

Example:
```python
# Stored: shared_buffers = 0.25
# Hardware: 16GB RAM
# Resolved: shared_buffers = 4GB (written to postgresql.conf as "4096MB")

# Same config on 64GB machine:
# Resolved: shared_buffers = 16GB
```

## Cross-Knob Memory Budget Validation

After any config mutation (sampling, perturbation, exploit copy):

```python
def repair_config_dependencies(config):
    total_memory = (
        config['shared_buffers'] +                      # fraction of RAM
        config['max_connections'] * config['work_mem'] + # scaled fraction
        config['maintenance_work_mem']                   # fraction of RAM
    )
    
    if total_memory > 0.80:  # 80% of RAM ceiling
        # Proportionally scale down (preserves PBT-discovered ratios)
        scale = 0.80 / total_memory
        config['shared_buffers'] *= scale
        config['work_mem'] *= scale
        config['maintenance_work_mem'] *= scale
    
    # Also enforce: max_parallel_workers ≤ max_worker_processes
    if config['max_parallel_workers'] > config['max_worker_processes']:
        config['max_parallel_workers'] = config['max_worker_processes']
```

## HardwareInfo Detection

`src/utils/hardware_info.py` captures:
- Total RAM (bytes)
- CPU core count (physical)
- Disk type (SSD/HDD)
- PostgreSQL version
- OS info
- All saved to results JSON for reproducibility
