# PostgreSQL Connection and Knob Retrieval

See also: [Documentation Index](../README.md), [Configuration Management](configuration-management.md), [Autotuning Knob Policy](../reference/autotuning-knob-policy.md), [Knob Importance Analysis](knob-importance-analysis.md)

## Overview

This document describes the foundation layer that backs every other module: how the project connects to PostgreSQL, retrieves knob metadata from `pg_settings`, overlays tuning-specific metadata, and exports curated tier CSVs that the tuner consumes. The layer spans four packages:

- **[src/config/](../../src/config/)** — environment-derived database credentials and resolved data-root path.
- **[src/database/](../../src/database/)** — psycopg2 connections, SQLAlchemy engines, lifecycle management, CSV loaders.
- **[src/knobs/](../../src/knobs/)** — `pg_settings` retrieval, tuning metadata overlay, knob policy filter, preprocessing pipeline.
- **[src/scripts/](../../src/scripts/)** — CLI entry points (`setup_database`, `analyze_knob_importance`, `analyze_knobs`, `cleanup_instances`).

---

## Table of Contents

1. [Architecture](#architecture)
2. [Configuration layer](#configuration-layer-srcconfig)
3. [Database layer](#database-layer-srcdatabase)
4. [Knobs layer](#knobs-layer-srcknobs)
5. [Scripts layer](#scripts-layer-srcscripts)
6. [Knob policy and tuning metadata](#knob-policy-and-tuning-metadata)
7. [Data files](#data-files)
8. [Related documentation](#related-documentation)

---

## Architecture

```text
                          ┌─────────────────────────┐
                          │   .env (DB_PASSWORD,    │
                          │   DB_HOST, DB_PORT, …)  │
                          └────────────┬────────────┘
                                       │
                                       ▼
┌──────────────────────────────────────────────────────────────────┐
│                          src/config/                             │
│   DatabaseConfig.from_env(), get_db_config(), resolve_data_root()│
└────────────┬─────────────────────────────────┬───────────────────┘
             │                                 │
             ▼                                 ▼
┌────────────────────────────┐  ┌────────────────────────────────────┐
│      src/database/         │  │            src/knobs/              │
│   get_connection()         │  │  PostgreSQLKnobRetriever           │
│   get_engine()             │  │   • get_all_parameters()           │
│   create/drop/reset_db()   │  │   • get_tunable_knobs()            │
│   load_csv_to_table()      │  │  knob_metadata.py                  │
└────────────┬───────────────┘  │   • TuningMetadata overlay         │
             │                  │   • get_knobs_by_tier()            │
             │                  │  policy.py                         │
             │                  │   • annotate_autotuning_policy()   │
             │                  │   • apply_bounds_safety_gate()     │
             │                  │  preprocess_knobs.py               │
             │                  │   • create_tier_dataframes()       │
             │                  │   • preprocess_and_save_knobs()    │
             │                  └────────────┬───────────────────────┘
             │                               │
             ▼                               ▼
        ┌─────────────────────────────────────────┐
        │           PostgreSQL (live)             │
        │   pg_settings, current_setting(),       │
        │   ALTER SYSTEM SET, pg_reload_conf()    │
        └─────────────────────────────────────────┘
```

The two most important things this layer produces:

- **A `DatabaseConfig` singleton** every other component imports for connections.
- **The tier CSVs under `data/expert_defined_knobs/`** that drive [`KnobSpace`](configuration-management.md#knobspace).

---

## Configuration layer (`src/config/`)

### `DatabaseConfig`

**Location**: [src/config/database.py](../../src/config/database.py)

Single source of truth for PostgreSQL credentials. Loaded once via `get_db_config()`, populated from `DB_USER`, `DB_PASSWORD` (required), `DB_HOST`, `DB_PORT`, `DB_NAME`. Password is masked in `__repr__` and in `get_connection_string(hide_password=True)`. See [ENVIRONMENT_SETUP.md](../getting-started/setup.md) for the full env-var reference.

### `resolve_data_root()`

**Location**: [src/config/data_root.py](../../src/config/data_root.py)

Returns the absolute path to the `data/` directory regardless of working directory. The CSVs and JSON manifests under `data/` are referenced by both production code and notebooks; the resolver keeps the path stable.

### `loopback.py`

**Location**: [src/config/loopback.py](../../src/config/loopback.py)

Helpers for resolving `localhost` semantics correctly across host/Docker/WSL boundaries. Used by the environment factory when deciding whether `host="localhost"` should become `host="host.docker.internal"` for a containerised orchestrator talking to a host PostgreSQL.

---

## Database layer (`src/database/`)

### `connection.py`

```python
def get_connection(
    config: Optional[DatabaseConfig] = None,
    dbname: Optional[str] = None,
) -> psycopg2.extensions.connection: ...

def get_engine(config: Optional[DatabaseConfig] = None) -> sqlalchemy.Engine: ...
```

`get_connection()` returns a raw psycopg2 connection — used by `KnobApplicator`, the `PostgreSQLKnobRetriever`, and the orchestrator. `dbname` overrides `DB_NAME` for administrative work (e.g. connecting to `postgres` to create another database). `get_engine()` returns a SQLAlchemy engine with connection pooling, used by pandas-based loaders and notebooks.

### `management.py`

```python
create_database(config=None)
drop_database(config=None)
reset_database(config=None)
```

Lifecycle helpers that connect to the `postgres` administrative database with `ISOLATION_LEVEL_AUTOCOMMIT`, terminate active connections, then create/drop/recreate the target. **Destructive — `drop_database` and `reset_database` cannot be undone.** Used by [`src/scripts/setup_database.py`](../../src/scripts/setup_database.py) and by the cleanup utility [`src/scripts/cleanup_instances.py`](../../src/scripts/cleanup_instances.py).

### `data_loader.py`

```python
load_csv_to_table(csv_path, table_name, if_exists="fail", config=None, engine=None)
load_products_dataset(config=None)
load_leads_dataset(config=None)
```

Pandas-backed bulk loaders. `if_exists` accepts `"fail"`, `"replace"`, or `"append"`. The `load_*_dataset` helpers are convenience wrappers around two illustrative datasets shipped under `data/` — they are not used during a tuning run, only during initial environment setup.

---

## Knobs layer (`src/knobs/`)

This is the layer that turns "PostgreSQL has 350 parameters" into "here are 36 parameters with sensible bounds and metadata for tuning."

### `retrieval.py`

`PostgreSQLKnobRetriever` is the live-database query layer.

```python
class KnobCategory(Enum):
    MEMORY = "memory"
    QUERY_PLANNER = "query_planner"
    WAL = "wal"
    CHECKPOINT = "checkpoint"
    AUTOVACUUM = "autovacuum"
    CONNECTIONS = "connections"
    PARALLELISM = "parallelism"
    STATISTICS = "statistics"
    LOCKS = "locks"
    OTHER = "other"

@dataclass
class ConfigParameter:
    name: str
    value: str
    unit: Optional[str]
    category: str           # custom category from KnobCategory
    context: str            # 'internal'|'postmaster'|'sighup'|'user'|'superuser'
    vartype: str            # 'bool'|'integer'|'real'|'string'|'enum'
    source: str
    min_val: Optional[str]
    max_val: Optional[str]
    enumvals: Optional[List[str]]
    boot_val: Optional[str]
    reset_val: Optional[str]
    description: Optional[str]
```

Key methods on `PostgreSQLKnobRetriever`:

| Method | Purpose |
| --- | --- |
| `get_all_parameters()` | Pull every row from `pg_settings`. Returns a DataFrame. |
| `get_tunable_knobs(categories=None)` | Filter to the curated set of commonly-tuned knobs, optionally by category. |
| `get_numeric_knobs()` | Restrict to `integer` / `real` types. |
| `get_modifiable_knobs()` | Exclude `internal` and `postmaster` contexts. |
| `get_knobs_by_context(ctx)` / `get_knobs_by_category(cat)` | Slicing helpers. |
| `get_all_knobs_with_metadata()` | All rows plus `is_predefined_tunable`, `is_runtime_modifiable`, custom category. |
| `save_all_knobs(filepath, include_metadata=True)` | Persist to CSV — primary input to the preprocessing pipeline. |
| `get_current_values_dict(names=None)` | Map of name → current value. |
| `get_knob_details(name)` | Single-knob `ConfigParameter`. |
| `normalize_value(value, unit)` | Best-effort conversion of `pg_settings` raw values to standard units (MB / seconds). For *applying* configurations use `KnobApplicator.verify()` instead — it handles quantisation correctly. |

### `knob_metadata.py`

Adds tuning-specific metadata that is **not** in `pg_settings` (scale, hardware-relative flag, default ranges, tier assignment).

```python
@dataclass
class TuningMetadata:
    name: str
    scale: str                     # 'linear' | 'log'
    tier: str                      # 'minimal' | 'core' | 'standard' | 'extensive'
    hardware_relative: bool
    min_value: Optional[float]
    max_value: Optional[float]
    notes: Optional[str]
    # ... see source for full set
```

The metadata source is `data/knob_metadata.json` (loaded once via `_load_metadata`). Two public entry points:

```python
get_knobs_by_tier(tier: str, source: str = "expert") -> list[str]
load_data_driven_tiers(json_path: Optional[str] = None) -> None
```

`load_data_driven_tiers` swaps in the analysis-pipeline output (`data/data_driven_knobs/{workload}/data_driven_tiers.json`) when a workload-specific tier set is preferred over the expert one. See [KNOB_IMPORTANCE_ANALYSIS.md](knob-importance-analysis.md).

### `policy.py`

Filters which knobs are *eligible* for tuning at all, regardless of tier.

```python
annotate_autotuning_policy(df) -> df         # adds policy/justification columns
ensure_autotuning_policy_annotations(df) -> df
apply_bounds_safety_gate(df) -> (df_safe, df_unsafe)
```

Backed by `data/knob_policy.json`. Each entry is a `(policy, justification)` tuple — `policy` is one of `tune` / `freeze` / `unsafe`. Knobs marked `freeze` or `unsafe` never enter the search space. The full per-knob rationale lives in [AUTOTUNING_KNOB_POLICY.md](../reference/autotuning-knob-policy.md).

### `preprocess_knobs.py`

The pipeline that takes raw `pg_settings` output and produces the per-tier CSVs the tuner reads.

```python
load_raw_knobs(csv_path=None) -> df
add_tuning_metadata(df) -> df              # overlay TuningMetadata
filter_tunable_knobs(df) -> df             # apply policy + bounds gate
create_tier_dataframes(df) -> dict[str, df]
preprocess_and_save_knobs(...)             # end-to-end driver
load_knobs_for_tier(tier, source="expert", workload=None) -> df
```

`preprocess_and_save_knobs` is the function called by [`src/scripts/analyze_knobs.py`](../../src/scripts/analyze_knobs.py) to refresh `data/expert_defined_knobs/{tier}_knobs.csv`. After running it, the tuner sees the updated tiers automatically.

---

## Scripts layer (`src/scripts/`)

| Script | Entry point | Purpose |
| --- | --- | --- |
| `setup_database.py` | `python -m src.scripts.setup_database` | Create + populate the operational database. Has interactive and `setup`/`reset` subcommands. |
| `analyze_knobs.py` | `python -m src.scripts.analyze_knobs` | Run the preprocessing pipeline and refresh tier CSVs. |
| `analyze_knob_importance.py` | `python -m src.scripts.analyze_knob_importance` | Run fANOVA + TreeSHAP + tier generation across PBT session results. See [KNOB_IMPORTANCE_ANALYSIS.md](knob-importance-analysis.md). |
| `cleanup_instances.py` | `python -m src.scripts.cleanup_instances` | Tear down stale Docker containers / bare-metal data dirs from prior runs. |
| `bo_baseline/` | `python -m src.scripts.bo_baseline` | The Bayesian-Optimisation baseline. Documented separately in [BO_BASELINE.md](../guides/bo-baseline.md). |
| `pbt_vs_bo_comarison.py` | `python -m src.scripts.pbt_vs_bo_comarison` | Cross-method comparison. Documented in [PBT_VS_BO_COMPARISON.md](../guides/pbt-vs-bo-comparison.md). |

---

## Knob policy and tuning metadata

Tier membership is the result of layering three filters:

1. **`pg_settings`** — produced by PostgreSQL at runtime; defines what *exists* and what's *runtime-modifiable*.
2. **`data/knob_metadata.json`** — defines what is *worth* tuning and how (scale, hardware relativity, default range).
3. **`data/knob_policy.json`** — defines what is *safe* to tune (filters out anything hazardous to data integrity or replication).

The analysis pipeline can additionally produce **data-driven tier overlays** stored under `data/data_driven_knobs/{workload}/`, used when the workload-specific importance ranking differs meaningfully from the expert-curated tiers.

The reasoning behind every freeze/unsafe decision lives in [AUTOTUNING_KNOB_POLICY.md](../reference/autotuning-knob-policy.md). For the contributor recipe — how to actually promote a knob into a tier — see [guides/adding-knobs](../guides/adding-knobs.md).

---

## Data files

```text
data/
├── knob_metadata.json                    # TuningMetadata overlay
├── knob_policy.json                      # tune / freeze / unsafe
├── postgresql_all_knobs.csv              # full pg_settings dump (regenerated)
├── expert_defined_knobs/
│   ├── minimal_knobs.csv                 # ~5 knobs
│   ├── core_knobs.csv                    # ~13 knobs
│   ├── standard_knobs.csv                # ~36 knobs
│   └── extensive_knobs.csv               # 80+ knobs
└── data_driven_knobs/
    └── {workload_label}/                 # e.g. oltp_read_write
        ├── minimal_knobs.csv
        ├── core_knobs.csv
        ├── standard_knobs.csv
        ├── extensive_knobs.csv
        └── data_driven_tiers.json
```

Counts depend on the PostgreSQL version of the source database; on PG 14+ the `extensive` tier currently lands around 80 knobs after policy filtering.

---

## Related documentation

- **[Environment Setup](../getting-started/setup.md)** — installing dependencies, configuring `.env`.
- **[Configuration Management](configuration-management.md)** — how the tier CSVs become a `KnobSpace`.
- **[Autotuning Knob Policy](../reference/autotuning-knob-policy.md)** — per-knob rationale.
- **[Knob Importance Analysis](knob-importance-analysis.md)** — how data-driven tiers are derived.
- **[Hardware-Aware Normalization](hardware-aware-normalization.md)** — the fractional encoding used by hardware-relative knobs.

### File locations

- `DatabaseConfig`, `get_db_config`: [src/config/database.py](../../src/config/database.py)
- Connection helpers: [src/database/connection.py](../../src/database/connection.py)
- DB lifecycle: [src/database/management.py](../../src/database/management.py)
- CSV loaders: [src/database/data_loader.py](../../src/database/data_loader.py)
- `PostgreSQLKnobRetriever`: [src/knobs/retrieval.py](../../src/knobs/retrieval.py)
- Tuning metadata: [src/knobs/knob_metadata.py](../../src/knobs/knob_metadata.py)
- Knob policy filter: [src/knobs/policy.py](../../src/knobs/policy.py)
- Preprocessing pipeline: [src/knobs/preprocess_knobs.py](../../src/knobs/preprocess_knobs.py)
- Tests: [tests/unit/knobs/](../../tests/unit/knobs/)
