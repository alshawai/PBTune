# Docker Environment Management

> 51 nodes · cohesion 0.05

## Key Concepts

- **KnobApplicator** (24 connections) — `src/utils/applicator.py`
- **.connect()** (11 connections) — `src/utils/applicator.py`
- **._apply_locked()** (10 connections) — `src/utils/applicator.py`
- **PBTuneTheme** (10 connections) — `src/visualization/theme.py`
- **.disconnect()** (8 connections) — `src/utils/applicator.py`
- **applicator.py** (7 connections) — `src/utils/applicator.py`
- **._connect_internal()** (5 connections) — `src/utils/applicator.py`
- **apply()** (5 connections) — `src/visualization/theme.py`
- **._disconnect_internal()** (4 connections) — `src/utils/applicator.py`
- **._load_parameter_info()** (4 connections) — `src/utils/applicator.py`
- **._validate_parameter()** (4 connections) — `src/utils/applicator.py`
- **.verify()** (4 connections) — `src/utils/applicator.py`
- **KnobContext** (4 connections) — `src/utils/applicator.py`
- **ParameterInfo** (4 connections) — `src/utils/applicator.py`
- **.figure()** (4 connections) — `src/visualization/theme.py`
- **.get_figure_size()** (4 connections) — `src/visualization/theme.py`
- **.subplots()** (4 connections) — `src/visualization/theme.py`
- **theme.py** (3 connections) — `src/visualization/theme.py`
- **._apply_parameter()** (3 connections) — `src/utils/applicator.py`
- **.__enter__()** (3 connections) — `src/utils/applicator.py`
- **.__exit__()** (3 connections) — `src/utils/applicator.py`
- **._reload_configuration()** (3 connections) — `src/utils/applicator.py`
- **.reset_parameter()** (3 connections) — `src/utils/applicator.py`
- **.rc_params()** (3 connections) — `src/visualization/theme.py`
- **FigureSize** (3 connections) — `src/visualization/types.py`
- *... and 26 more nodes in this community*

## Relationships

- [[Scoring & Weight Policies]] (136 shared connections)
- [[Benchmark Orchestrator]] (7 shared connections)
- [[Database Config & Connection]] (5 shared connections)
- [[Metric Config & Composite]] (3 shared connections)
- [[BO Config & Worker]] (3 shared connections)
- [[Logger Colors]] (2 shared connections)
- [[Performance Metrics]] (2 shared connections)
- [[Cross-Module Rationale]] (2 shared connections)
- [[PostgreSQL Knob Retrieval]] (2 shared connections)
- [[Comparison Runner]] (1 shared connections)
- [[Population Initialization]] (1 shared connections)
- [[Visualization Types]] (1 shared connections)

## Source Files

- `src/tuner/benchmark/orchestrator.py`
- `src/utils/applicator.py`
- `src/visualization/theme.py`
- `src/visualization/types.py`

## Audit Trail

- EXTRACTED: 149 (89%)
- INFERRED: 18 (11%)
- AMBIGUOUS: 0 (0%)

---

*Part of the graphify knowledge wiki. See [[index]] to navigate.*