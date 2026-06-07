# Hardware-Aware Normalization & Warm-Starting

> Last reviewed: 2026-06-07

See also: [Documentation Index](../README.md), [Configuration Management](configuration-management.md), [Environment Backends](environment-backends.md), [Cross-Workload Transfer](../research/cross-workload-transfer.md)

## 1. Overview

The PBT Database Tuner includes a hardware translation layer allowing you to **warm-start** tuning models across different heterogeneous hardware platforms.

Using pre-recorded best configurations (`best_config.json`) evaluated on arbitrary bare-metal instances or containerized nodes, the tuner uses fractional specifications to extrapolate suitable hardware ranges and dynamically restricts memory budgets to prevent PostgreSQL configuration crashes.

### The Fractional Transfer System

Rather than persisting raw absolute values, knobs tagged as `hardware_relative=True` in `TuningMetadata` (loaded from [`data/knob_metadata.json`](../../data/knob_metadata.json) via `src/knobs/knob_metadata.py`) serialize to **fractions of the node's detectable limits**, allowing configs that maximized a 2GB RAM container to effectively warm-start tuning efforts inside an 8GB container without immediate OOM (Out Of Memory) or excessive swapping.

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

---

## 6. Per-Worker Resource Slicing

The hardware translation layer also runs at **population creation time** to give every parallel worker its own resource slice. Without this, eight workers on one host would each see the full host RAM in `resolve_hardware_ranges()` and collectively recommend memory budgets that would OOM the moment the population tried to evaluate them concurrently.

`detect_worker_resources(num_parallel_workers, override_ram=None, override_cpus=None)` lives in [src/utils/hardware_info.py](../../src/utils/hardware_info.py) and produces one `WorkerResources` per worker:

```python
@dataclass(frozen=True)
class WorkerResources:
    cpu_cores: float        # cores assignable to this worker
    ram_bytes: int          # RAM budget for this worker
    disk_type: str          # "ssd" | "hdd"
```

The default policy:

1. Detect host totals (`os.cpu_count()`, cgroups RAM, rotatability flag).
2. Reserve **20% headroom** for the OS and host processes (the tuner's "80% tuning budget").
3. Divide the remaining 80% by `num_parallel_workers` to get the per-worker slice.
4. CLI overrides (`--worker-ram`, `--worker-cpus`) replace the auto-detected values; the runner emits a warning when overrides ask for more than 95% of host capacity.

`KnobSpace.resolve_hardware_ranges(worker_resources)` is then called **once per worker**, and every worker stores the same fractional configuration but is bounded by **its** slice. Two consequences:

- Memory-budget repair (`_repair_memory_budget`) applied at perturbation time uses `worker_resources.ram_bytes`, not host RAM. Workers cannot accidentally cross-saturate each other's memory budgets.
- The orchestrator's `worker_memory_budget_bytes` (used to normalise PostgreSQL RSS into `memory_utilization`) comes from the same `WorkerResources.ram_bytes`. The score's memory regularisation therefore reflects **budget pressure** rather than **host pressure** — what each worker can actually use, not what the whole machine has.

The session JSON records every worker's `worker_resources` so post-hoc tools can audit how the slice was computed.

## 7. Docker CPU Subset Enforcement

Per-worker CPU pinning is enforced at the kernel level only when the [Docker environment backend](environment-backends.md) is in use. The `DockerEnvironment` derives `--cpuset-cpus` from `worker_resources.cpu_cores` and the host CPU count:

```text
host has 16 cores
4 parallel workers, cpu_cores=3.2 each
  worker 0 → --cpuset-cpus="0-2"
  worker 1 → --cpuset-cpus="3-5"
  worker 2 → --cpuset-cpus="6-8"
  worker 3 → --cpuset-cpus="9-11"
  (cores 12-15 reserved for host)
```

Bare-metal does **not** enforce this — the OS scheduler is free to migrate any worker thread to any core. Bare-metal `WorkerResources` is therefore advisory: it correctly bounds knob-range resolution and memory normalisation, but it cannot prevent two workers' queries from time-slicing the same physical core. This is why publication-facing comparisons require Docker (see [ENVIRONMENT_BACKENDS.md §DockerEnvironment](environment-backends.md#dockerenvironment)).

When the host has fewer cores than `num_parallel_workers × cpu_cores`, the factory logs an oversubscription warning and proceeds with overlapping subsets. The user has explicitly chosen to oversubscribe by setting `--parallel-workers` higher than the host can cleanly accommodate; the warning gives them a chance to reconsider.

## 8. Related Documentation

- [Configuration Management](configuration-management.md) — `KnobSpace.resolve_hardware_ranges`, `config_to_fractions`, `fractions_to_config`.
- [Environment Backends](environment-backends.md) — Docker `--cpuset-cpus` and `--memory` enforcement.
- [PBT Core Components](pbt-core.md) — how the population consumes `WorkerResources`.
- [Cross-Workload Transfer](../research/cross-workload-transfer.md) — future work extending warm-start beyond same-workload boundaries.
- [ADR-004 — Docker CPU subset isolation](decisions/ADR-004-docker-cpu-subset-isolation.md) — design decision.
