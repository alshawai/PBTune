# Hardware-Aware Normalization & Warm-Starting

## 1. Overview

The PBT Database Tuner includes a hardware translation layer allowing you to **warm-start** tuning models across different heterogeneous hardware platforms.

Using pre-recorded best configurations (`best_config.json`) evaluated on arbitrary bare-metal instances or containerized nodes, the tuner uses fractional specifications to extrapolate suitable hardware ranges and dynamically restricts memory budgets to prevent PostgreSQL configuration crashes.

### The Fractional Transfer System

Rather than persisting raw absolute values, knobs tagged as `hardware_relative=True` in `TuningMetadata` serialize to **fractions of the node's detectable limits**, allowing configs that maximized a 2GB RAM container to effectively warm-start tuning efforts inside an 8GB container without immediate OOM (Out Of Memory) or excessive swapping.

---

## 2. Resource Detection Layer (`hardware_info.py`)

At PBT initialization, the `PBTTuner` invokes `detect_worker_resources()` to determine the node's true available hardware.

- **CPU Cores**: Accounts for `os.cpu_count()`, `cgroups` (Linux containers), and `sched_getaffinity`.
- **System Memory (RAM)**: Gathers absolute limits, factoring in `cgroups/memory` limits in container environments (e.g. Docker with `--memory="4g"`).
- **Disk Type**: Checks rotatability flags to classify as SSD/NVMe or HDD.

> **Note on Resource Budgets:** The tuner assumes an 80% tuning budget, subtracting an immediate 20% penalty to leave underlying host processes / OS essentials enough stability headroom.

---

## 3. Dynamic Knob Specifications (`knob_space.py`)

A set of 13 key knobs are mapped as hardware-dependent:

- **RAM-Dependent**: `shared_buffers`, `effective_cache_size`, `work_mem`, `maintenance_work_mem`, `temp_buffers`, `wal_buffers`
- **CPU-Dependent**: `max_worker_processes`, `max_parallel_workers`, `max_parallel_maintenance_workers`
- **Disk-Dependent**: `random_page_cost`, `seq_page_cost`, `effective_io_concurrency`

### Normalization Flow

1. `resolve_hardware_ranges()` determines absolute lower and upper scalar bounds for a knob based on its `HARDWARE_RELATIVE_SPECS` conversion logic.
2. During the PBT run, worker exploration treats these fractions mathematically across bounded continuous/discrete spaces.
3. At the end of training `config_to_fractions()` evaluates final bounds explicitly back into independent % variants for storage.

---

## 4. Aggregate Memory Validation (Budget Repairs)

To prevent cross-knob combinatorial explosions (e.g. maxing out both `shared_buffers` and `work_mem`), `repair_config_dependencies()` applies an aggregate formula across configurations evaluated during generation bounds:

Total Memory Consumed = `shared_buffers` + (`max_connections` × `work_mem`) + `maintenance_work_mem`

If `Total > Budget` (80% of node's per-worker RAM limit constraint), the framework dynamically scales down memory parameters proportionally by an equal scalar multiplier `Budget / Total`, enforcing stability without destroying the tuned dimensional ratio relationships between the configurations.

---

## 5. Warm-Starting

You can resume PBT optimizations from any previous state mapping via the CLI:

```bash
python -m src.tuner.main --warm-start ../results/best_config.json
```

**What Happens:**

- **Config Conversion**: Decodes fraction entries back into active hardware limits.
- **Partial Seeding**: Generates **N/2** warm-started configs. 1 base intact variant + graduated variations (e.g., perturbed at ±20%, ±35%, ±50%).
- **LHS Fill**: Samples the remaining worker slots globally with Latin Hypercube Sampling.
- **Resilience**: The tuner will LHS-fill new knobs absent from the JSON spec or seamlessly drop dead JSON knobs no longer existing in the selected `knob_tier`.
