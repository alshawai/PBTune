---
name: postgresql-knob-tuning
description: PostgreSQL configuration parameter (knob) tuning patterns, including parameter contexts (postmaster/sighup/user), knob space management, hardware-aware fractional normalization, safe bounds enforcement, and the knob tier system. Use this skill whenever working on knob configuration, parameter application, knob metadata, hardware-aware normalization, transfer learning via warm-start, or any code in src/knobs/, or src/utils/applicator.py.
---

# PostgreSQL Knob Tuning Patterns

This skill encodes domain knowledge for PostgreSQL configuration parameter tuning in this research project.

## PostgreSQL Parameter Contexts

PostgreSQL parameters have three contexts that determine how they take effect:

| Context | Mechanism | Restart? | Code Path |
|---------|-----------|----------|-----------|
| `postmaster` | `ALTER SYSTEM SET` → `postgresql.auto.conf`, then full instance restart | **Yes** | Env backend `restart_instance()` (via orchestrator `_perform_restart`) |
| `sighup` | `ALTER SYSTEM SET` → `postgresql.auto.conf`, then reload | No | `KnobApplicator._reload_configuration()` (`SELECT pg_reload_conf()`) |
| `user` | `SET parameter = value` (session-level) | No | Direct SQL |

**Critical rule:** Batch all `postmaster` knobs together to minimize restarts. One restart for all postmaster changes per evaluation cycle.

## Knob Tier System

| Tier | Count | Use Case | CSV File |
|------|-------|----------|----------|
| `minimal` | 5 | Quick testing, debugging | `data/expert_defined_knobs/minimal_knobs.csv` |
| `core` | 13 | Standard tuning | `data/expert_defined_knobs/core_knobs.csv` |
| `standard` | 43 | Comprehensive tuning | `data/expert_defined_knobs/standard_knobs.csv` |
| `extensive` | 170 | Research-grade full analysis | `data/expert_defined_knobs/extensive_knobs.csv` |

## Hardware-Relative Fractional Representation

**All hardware-relative knobs (memory, CPU, disk) MUST be represented, sampled, mutated, and stored as fractions of detected resources.**

Examples:
- `shared_buffers = 0.25` → 25% of detected RAM
- `work_mem = 0.02` → 2% of detected RAM
- `max_parallel_workers = 0.5` → 50% of detected CPU cores

Fractions are stored in population state and only resolved to absolute values at runtime against the `WorkerResources` for the given worker. These resources are either auto-detected from host limits (divided by number of parallel workers) or manually allocated (e.g., via `--worker-ram` and `--worker-cpus`). This enables transfer learning and warm-start portability.

### Cross-Knob Aggregate Validation
After sampling, perturbation, or exploit copy, validate:
```
shared_buffers + (max_connections × work_mem) + maintenance_work_mem ≤ available_ram × 0.80
max_parallel_workers ≤ max_worker_processes
```

Repair strategy: proportionally scale down overbudget configs (preserves PBT-discovered ratios).

## Log-Scale Perturbation
For knobs marked as log-scale (identified by `KnobDefinition.scale == KnobScale.LOG`):

```python
# CORRECT: perturb in log-space
new_value = exp(log(value) + uniform(log(factor_min), log(factor_max)))

# WRONG: linear perturbation (biases toward high values)
new_value = value * uniform(factor_min, factor_max)
```

## Dangerous Knob Identification
Some PostgreSQL knobs from `pg_settings` have absurdly wide native ranges (e.g., `max_connections: 1–2,147,483,647`). These knobs have curated `TuningMetadata` entries (~80) in `data/knob_metadata.json` with safe `tuning_min`/`tuning_max` bounds; `src/knobs/knob_metadata.py` only defines the `TuningMetadata` dataclass and its JSON loader.

## Knob Metadata Pipeline
```
pg_settings → retrieval.py → raw CSV → preprocess_knobs.py (+TuningMetadata) → tier CSVs
```

To regenerate tier CSVs after metadata changes: `python -m src.scripts.analyze_knobs` (or `python -m src.knobs.preprocess_knobs`)

## Warm-Start (Transfer Learning Level 1)
```bash
python -m src.tuners pbt --warm-start results/sessions/olap/pbt/{tier}/best_configs/best_YYYYMMDD_HHMM.json
```
- Loads `best_config.json` from previous run
- Seeds 1-2 workers with loaded config (fractional representation)
- Remaining workers initialize via LHS for diversity
- Uses existing `Population.initialize(initial_configs=...)` API

## Code Locations

| Component | File |
|-----------|------|
| Knob space + LHS | `src/knobs/knob_space.py` |
| Knob CSV loading | `src/knobs/knob_loader.py` |
| Knob application | `src/utils/applicator.py` |
| Knob metadata | `src/knobs/knob_metadata.py` |
| Knob preprocessing | `src/knobs/preprocess_knobs.py` |
| pg_settings retrieval | `src/knobs/retrieval.py` |
| Hardware detection | `src/utils/hardware_info.py` |

## Reference Files
- Read `references/parameter-contexts.md` for detailed PostgreSQL parameter handling
- Read `references/knob-tiers.md` for tier membership and metadata
- Read `references/hardware-normalization.md` for fractional representation details
