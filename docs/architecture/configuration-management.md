# Configuration Management

See also: [Documentation Index](../README.md), [Hardware-Aware Normalization](hardware-aware-normalization.md), [PostgreSQL Connection and Knobs](postgresql-connection-and-knobs.md), [Autotuning Knob Policy](../reference/autotuning-knob-policy.md)

## Overview

The configuration management layer is responsible for **defining what may be tuned** and **applying tuned values to a live PostgreSQL** safely. Two components do this:

1. **[`KnobSpace`](../../src/tuner/config/knob_space.py)** — the search space. Loaded from per-tier CSVs (or from a data-driven manifest), exposes per-knob bounds, scale, default, restart context, and **hardware-relative fractional encoding**. Owns sampling, perturbation, dependency repair, and warm-start translation.
2. **[`KnobApplicator`](../../src/utils/applicator.py)** — the runtime applier. Validates against `pg_settings`, writes via `ALTER SYSTEM` (persistent) or `SET` (session), respects parameter contexts (`postmaster` / `sighup` / `user`), and exposes a `verify()` read-back that returns the **actually quantised** values PostgreSQL is running with.

These two layers are independent. `KnobSpace` is the contract PBT and the BO baseline both consume; `KnobApplicator` is what the orchestrator calls per evaluation.

---

## Table of Contents

1. [Architecture](#architecture)
2. [KnobSpace](#knobspace)
3. [Knob tiers and data sources](#knob-tiers-and-data-sources)
4. [Hardware-relative fractional encoding](#hardware-relative-fractional-encoding)
5. [Sampling, perturbation, and dependency repair](#sampling-perturbation-and-dependency-repair)
6. [Warm-start serialisation](#warm-start-serialisation)
7. [KnobApplicator](#knobapplicator)
8. [Verifying applied config](#verifying-applied-config)
9. [Two-layer validation](#two-layer-validation)
10. [Design decisions](#design-decisions)
11. [Related documentation](#related-documentation)

---

## Architecture

```text
                        ┌──────────────────────┐
                        │      KnobSpace       │
                        │  (search space)      │
   data/expert_         │                      │
   defined_knobs/       │  load_knob_space_    │
   {tier}.csv ───────►  │     for_tier()       │
                        │                      │
   data/data_driven_    │  resolve_hardware_   │
   knobs/{workload}/    │     ranges()         │
   {tier}.csv ───────►  │                      │
                        │  sample / perturb /  │
                        │  repair / fractions  │
                        └──────────┬───────────┘
                                   │ dict[str, Any]
                                   ▼
                        ┌──────────────────────┐
                        │   Worker.knob_config │
                        └──────────┬───────────┘
                                   │
                                   │ orchestrator.evaluate_worker()
                                   ▼
                        ┌──────────────────────┐
                        │    KnobApplicator    │
                        │                      │
                        │  apply()  → ALTER    │
                        │           SYSTEM/SET │
                        │  verify() → read-    │
                        │           back from  │
                        │           current_   │
                        │           setting()  │
                        └──────────┬───────────┘
                                   │
                                   ▼
                        ┌──────────────────────┐
                        │      PostgreSQL      │
                        └──────────────────────┘
```

---

## KnobSpace

**Location**: [src/tuner/config/knob_space.py](../../src/tuner/config/knob_space.py)

A `KnobSpace` is a typed collection of `KnobDefinition` records. It is the single source of truth for *which* knobs are tunable in this session and *with what bounds*.

```python
class KnobType(Enum):
    INTEGER = "integer"
    REAL = "real"
    BOOLEAN = "bool"
    ENUM = "enum"

class KnobScale(Enum):
    LINEAR = "linear"
    LOG = "log"

@dataclass
class KnobDefinition:
    name: str
    knob_type: KnobType
    min_value: Optional[Union[int, float]]
    max_value: Optional[Union[int, float]]
    scale: KnobScale
    default: Any
    unit: Optional[str]
    enum_values: Optional[List[str]]
    description: str
    category: str
    restart_required: bool
    # ... see source for full list
```

### `KnobSpace` public API

| Method | Purpose |
| --- | --- |
| `__getitem__(name)`, `__contains__(name)`, `__len__()` | Mapping-style access. |
| `get_restart_required_knobs()` | Names of `postmaster`-context knobs. |
| `get_runtime_modifiable_knobs()` | Names of `sighup` / `user` knobs. |
| `split_config_by_restart_requirement(config)` | Splits a config dict into `(restart_required, runtime)` for the orchestrator's restart policy. |
| `validate_config(config)` | Returns `(is_valid, errors)` against tuning bounds. |
| `resolve_hardware_ranges(resources)` | Rewrites bounds for `hardware_relative=True` knobs against a `WorkerResources` slice. |
| `repair_config_dependencies(config, worker_id, budget_ram_bytes)` | Aggregate memory-budget repair (see below). |
| `sample_random_config(seed)`, `sample_diverse_configs(n, seed)` | LHS-style population seeding. |
| `perturb_config(config, factors)` | The explore step. |
| `config_to_fractions(config)`, `fractions_to_config(fractions)` | Warm-start serialisation. |
| `create_online_view()` | A `KnobSpace` filtered to runtime-modifiable knobs only (for `ONLINE` tuning mode). |

The full file lives at [src/tuner/config/knob_space.py](../../src/tuner/config/knob_space.py) — every method has a Google-style docstring.

---

## Knob tiers and data sources

**Location**: [src/tuner/config/knob_loader.py](../../src/tuner/config/knob_loader.py)

There are four canonical tiers: `minimal`, `core`, `standard`, `extensive`. Each tier corresponds to a CSV under `data/`. Two layouts are supported:

```text
data/
├── expert_defined_knobs/                 # canonical, hand-curated
│   ├── minimal_knobs.csv
│   ├── core_knobs.csv
│   ├── standard_knobs.csv
│   └── extensive_knobs.csv
└── data_driven_knobs/                    # optional, derived from analysis
    └── {workload}/                       # e.g. oltp_read_write
        ├── minimal_knobs.csv
        ├── core_knobs.csv
        ├── standard_knobs.csv
        ├── extensive_knobs.csv
        └── data_driven_tiers.json
```

`get_knob_space(tier, workload=None, source="expert")` picks the right CSV. When `source="data_driven"` and `workload` is set, the loader prefers `data/data_driven_knobs/{workload}/{tier}_knobs.csv` — these are the tier CSVs produced by [`src/analysis/tier_generator.py`](../../src/analysis/tier_generator.py) from fANOVA+TreeSHAP importance (see [KNOB_IMPORTANCE_ANALYSIS.md](knob-importance-analysis.md)).

CSV format constants live in [`knob_loader.py`](../../src/tuner/config/knob_loader.py):

```python
EXPERT_KNOBS_DIR = "data/expert_defined_knobs"
DATA_DRIVEN_KNOBS_DIR = "data/data_driven_knobs"
```

The CSV columns mirror `KnobDefinition` fields (`name`, `vartype`, `min_value`, `max_value`, `scale`, `default`, `unit`, `enumvals`, `category`, `restart_required`, `hardware_relative`, `tuning_metadata`). The `tuning_metadata` JSON column carries fields not present in `pg_settings` — see [src/knobs/knob_metadata.py](../../src/knobs/knob_metadata.py).

### Knob policy file

`data/knob_policy.json` selects which knobs are *eligible* for tuning at all (vs frozen for safety) regardless of tier. The policy is loaded by [`src/knobs/policy.py`](../../src/knobs/policy.py) and applied as a filter at CSV-load time. See [AUTOTUNING_KNOB_POLICY.md](../reference/autotuning-knob-policy.md) for the reasoning behind each frozen knob.

---

## Hardware-relative fractional encoding

For knobs flagged `hardware_relative=True` in metadata (RAM-dependent knobs like `shared_buffers`, `work_mem`; CPU-dependent like `max_parallel_workers`; disk-dependent like `random_page_cost`), the bounds in the CSV are not absolute — they are **fractions of the worker's available resources**.

At session start, the tuner calls [`detect_worker_resources()`](../../src/utils/hardware_info.py) to get a `WorkerResources` slice for each worker (host total ÷ parallel-worker count, less an 80 % budget headroom). Then:

```python
knob_space.resolve_hardware_ranges(worker_resources)
```

rewrites each hardware-relative knob's `min_value` and `max_value` to absolute integers/floats appropriate for that worker.

This is what makes **warm-start across hardware** work: a `best_config.json` produced on a 2 GB container can be re-resolved against an 8 GB container without OOM, because the stored configuration is fractional and gets converted to absolute values against the new resource ceiling.

Details and the list of all hardware-relative knobs: [HARDWARE_AWARE_NORMALIZATION.md](hardware-aware-normalization.md).

---

## Sampling, perturbation, and dependency repair

### Sampling

`sample_random_config(seed)` samples each knob independently. Numeric knobs use either linear or log sampling per `KnobScale`. `sample_diverse_configs(n, seed)` uses a Latin Hypercube design to seed the initial population with better space coverage than independent sampling.

### Perturbation (explore)

`perturb_config(config, factors=(0.8, 1.2))` is the explore step from the PBT paper. For numeric knobs it multiplies by `U(factors[0], factors[1])` and clamps to bounds. Booleans flip with a configurable probability. Enums probabilistically jump to a neighbour. The implementation correctly handles log-scale knobs (perturbation in log space, not linear space) so a `+20%` move on `shared_buffers` does what you expect.

### Memory-budget repair

`repair_config_dependencies(config, worker_id, budget_ram_bytes)` runs the **aggregate memory check** before a config ever reaches PostgreSQL:

```text
total_bytes = shared_buffers
            + max_connections × work_mem
            + maintenance_work_mem
```

If `total_bytes` exceeds the worker's RAM budget, every memory knob is **scaled down by the same multiplier** `budget / total`. This preserves the ratios the perturbation chose while bringing total memory back under the budget. Without this, the explore step could easily produce configurations that fail to start (`shared_buffers` alone larger than container RAM).

Implementation: [`src/tuner/config/knob_space.py:_repair_memory_budget`](../../src/tuner/config/knob_space.py).

---

## Warm-start serialisation

`config_to_fractions(config)` and `fractions_to_config(fractions)` are the round-trip used by `--warm-start`. Hardware-relative knobs are serialised as fractions of their resolved bounds; absolute knobs are kept as-is. The resulting JSON survives transport across hardware.

The full warm-start flow lives in [`src/tuner/main.py`](../../src/tuner/main.py) and is described in [HARDWARE_AWARE_NORMALIZATION.md §5](hardware-aware-normalization.md). Notably, since commit `858d482` the warm-start serialiser now writes the **full knob space**, not just the best configuration — this lets the loader gracefully drop knobs that no longer exist in the target tier and LHS-fill new ones.

---

## KnobApplicator

**Location**: [src/utils/applicator.py](../../src/utils/applicator.py)

`KnobApplicator` applies a `dict[str, Any]` of knob values to a live PostgreSQL instance. It is the only component that talks to PostgreSQL's configuration surface.

### `ApplicatorConfig`

```python
@dataclass
class ApplicatorConfig:
    persist: bool = True              # ALTER SYSTEM (persisted) vs SET (session)
    auto_reload: bool = True          # pg_reload_conf() for sighup knobs
    validate: bool = True             # check against pg_settings before applying
    dry_run: bool = False
    rollback_on_error: bool = True
    allow_restart_params: bool = True
```

### `apply(knob_config) -> ApplicationResult`

The flow:

1. **Connect** — establishes a connection (cached if pooled).
2. **Load parameter info** — single `SELECT … FROM pg_settings WHERE name IN (…)` for every knob in `knob_config`.
3. **Validate** each knob against `pg_settings.vartype` / `min_val` / `max_val` / `enumvals` / context.
4. **Apply** in a single transaction. `persist=True` ⇒ `ALTER SYSTEM SET`, else `SET`.
5. **Reload** via `pg_reload_conf()` if any `sighup` knob changed and `auto_reload=True`.
6. **Commit or rollback** — atomic. On any per-knob failure with `rollback_on_error=True`, the whole transaction rolls back via psycopg2 — there is no per-knob undo path.

Return value:

```python
@dataclass
class ApplicationResult:
    success: bool
    applied: dict[str, Any]
    failed: dict[str, str]
    restart_required: set[str]
    applied_count: int
    failed_count: int
    message: str
```

`restart_required` is populated when a `postmaster`-context knob was applied (via `ALTER SYSTEM`) but PostgreSQL has not yet been restarted — the orchestrator uses this to decide whether to trigger a restart at barrier B3.

### Context manager

`KnobApplicator` is a context manager; `__enter__` connects and `__exit__` disconnects unconditionally, including under exception. The orchestrator uses both the context-manager form for short-lived operations and the explicit `connect()` / `disconnect()` form when it needs to keep a connection alive across multiple barrier sub-steps.

---

## Verifying applied config

`verify(expected_config) -> VerificationResult` reads back the **actually applied** values from PostgreSQL via `current_setting(name)` for each knob and returns:

```python
@dataclass
class VerificationResult:
    matches: dict[str, bool]
    db_config: dict[str, Any]       # the quantised values PostgreSQL is using
```

Two problems make this method non-trivial:

1. **Quantisation** — PostgreSQL rounds many values to internal block boundaries (e.g. `shared_buffers` to the nearest 8 kB page). A suggested 134217729 becomes 134217728 internally. Without `verify()` the optimiser believes its raw suggestion is what's running.
2. **Unit conversion** — `pg_settings.setting` is a raw number plus a separate `unit` column. `current_setting()` returns PostgreSQL's normalised typed value directly, avoiding manual unit math.

The orchestrator calls `verify()` at barrier B5 and **merges `db_config` back into the worker's `knob_config`** before scoring. This makes session JSON faithful to what the database actually ran. The BO baseline does the same to keep its surrogate model gradients honest — see [BO_BASELINE.md §Quantization & Read-Back Parity](../guides/bo-baseline.md).

---

## Two-layer validation

Validation happens twice for a reason:

| Layer | Component | Purpose | Failure mode |
| --- | --- | --- | --- |
| **Design-time** | `KnobSpace.validate_config()` | Keep PBT inside reasonable tuning bounds. | Reject + clamp + log. |
| **Runtime** | `KnobApplicator._validate_parameter()` | Catch anything PostgreSQL would reject (context, type, hard min/max, enum). | Rollback whole apply. |

The design-time range is always a strict subset of the PostgreSQL-allowed range. The design-time bounds are where we *want* PBT to explore; the runtime bounds are where PostgreSQL allows. Two checks let us tighten exploration without losing PostgreSQL-level safety.

---

## Design decisions

### 1. KnobSpace owns hardware resolution

`resolve_hardware_ranges` lives on `KnobSpace`, not on a separate hardware layer, because every consumer of `KnobSpace` (PBT, BO baseline, warm-start) needs the same resolved view. Putting it on the space avoids the surface area for "I forgot to resolve and got OOMed" bugs.

### 2. CSV-tiered loading instead of code-defined sets

Tiers were once hard-coded lists in Python. They are now CSVs because:

- analysis (`src/analysis/tier_generator.py`) can write new tiers without code edits,
- expert-defined and data-driven tiers can coexist (`data/expert_defined_knobs/` vs `data/data_driven_knobs/{workload}/`),
- the same file is the source of truth for the BO baseline and any tooling that has to enumerate the search space.

### 3. Transaction-level rollback only

`KnobApplicator` does not implement a per-knob undo log. PostgreSQL's transaction semantics already guarantee atomicity on `ALTER SYSTEM SET`. A custom undo would duplicate that guarantee and add a failure mode.

### 4. `verify()` returns the read-back, the caller merges

The caller decides whether the quantised values replace the suggested values. PBT merges (so lineage tracking is honest); BO merges (so surrogate gradients are honest). A future analyzer that wants to diff suggested-vs-applied can leave the suggestion intact and use `verification.db_config` separately.

### 5. Knob policy is a filter, not a fork

`data/knob_policy.json` filters which knobs are eligible for tuning. It is applied after CSV load, so the tier CSVs can list every plausible knob and the policy decides which are safe to tune in a given environment. This avoids forking the CSVs per policy.

---

## Related documentation

- **[Hardware-Aware Normalization](hardware-aware-normalization.md)** — fractional encoding, `WorkerResources`, warm-start across hardware.
- **[PostgreSQL Connection and Knobs](postgresql-connection-and-knobs.md)** — `pg_settings` retrieval, `PostgreSQLKnobRetriever`, knob policy.
- **[Autotuning Knob Policy](../reference/autotuning-knob-policy.md)** — per-knob tuning rationale and safety classification.
- **[Knob Importance Analysis](knob-importance-analysis.md)** — how `data_driven_knobs/` CSVs are generated.
- **[Workload Orchestrator](workload-orchestrator.md)** — how the applicator is driven during an evaluation.
- **[PBT Core Components](pbt-core.md)** — how `KnobSpace` is consumed by the worker and population.

### File locations

- `KnobSpace`, `KnobDefinition`, `KnobType`, `KnobScale`: [src/tuner/config/knob_space.py](../../src/tuner/config/knob_space.py)
- Tier CSV loader: [src/tuner/config/knob_loader.py](../../src/tuner/config/knob_loader.py)
- `KnobApplicator`, `ApplicatorConfig`, `ApplicationResult`, `VerificationResult`: [src/utils/applicator.py](../../src/utils/applicator.py)
- Knob metadata overlay: [src/knobs/knob_metadata.py](../../src/knobs/knob_metadata.py)
- Knob policy filter: [src/knobs/policy.py](../../src/knobs/policy.py)
- Tier CSVs: [data/expert_defined_knobs/](../../data/expert_defined_knobs/), [data/data_driven_knobs/](../../data/data_driven_knobs/)
- Tests: [tests/unit/knobs/](../../tests/unit/knobs/), [tests/unit/config/test_hardware_normalization.py](../../tests/unit/config/test_hardware_normalization.py)
