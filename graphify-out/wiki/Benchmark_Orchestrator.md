# Benchmark Orchestrator

> 29 nodes · cohesion 0.12

## Key Concepts

- **PostgreSQLKnobRetriever** (26 connections) — `src/knobs/retrieval.py`
- **.get_all_parameters()** (12 connections) — `src/knobs/retrieval.py`
- **.get_tunable_knobs()** (10 connections) — `src/knobs/retrieval.py`
- **.get_current_values()** (7 connections) — `src/utils/applicator.py`
- **.get_all_knobs_with_metadata()** (6 connections) — `src/knobs/retrieval.py`
- **.save_all_knobs()** (5 connections) — `src/knobs/retrieval.py`
- **.export_to_csv()** (4 connections) — `src/knobs/retrieval.py`
- **.get_numeric_knobs()** (4 connections) — `src/knobs/retrieval.py`
- **.get_all_categories()** (3 connections) — `src/knobs/retrieval.py`
- **.get_all_contexts()** (3 connections) — `src/knobs/retrieval.py`
- **.get_knobs_by_context()** (3 connections) — `src/knobs/retrieval.py`
- **.get_knobs_summary()** (3 connections) — `src/knobs/retrieval.py`
- **.get_memory_knobs()** (3 connections) — `src/knobs/retrieval.py`
- **.get_modifiable_knobs()** (3 connections) — `src/knobs/retrieval.py`
- **.get_query_planner_knobs()** (3 connections) — `src/knobs/retrieval.py`
- **Retrieve all PostgreSQL configuration parameters.          Returns         -----** (2 connections) — `src/knobs/retrieval.py`
- **Get list of all PostgreSQL configuration categories.          Returns         --** (2 connections) — `src/knobs/retrieval.py`
- **Retrieve commonly tuned parameters for ML-based optimization.          Parameter** (1 connections) — `src/knobs/retrieval.py`
- **Get only numeric knobs (integer and real) suitable for ML optimization.** (1 connections) — `src/knobs/retrieval.py`
- **Get current values as a dictionary (useful for ML feature vectors).          Par** (1 connections) — `src/knobs/retrieval.py`
- **Get memory-related configuration parameters.** (1 connections) — `src/knobs/retrieval.py`
- **Get query planner configuration parameters.** (1 connections) — `src/knobs/retrieval.py`
- **Export knobs to CSV for analysis or ML training.          Parameters         ---** (1 connections) — `src/knobs/retrieval.py`
- **Get knobs that can be modified without restarting PostgreSQL.          Returns** (1 connections) — `src/knobs/retrieval.py`
- **Save ALL PostgreSQL knobs to a CSV file.          This saves every single parame** (1 connections) — `src/knobs/retrieval.py`
- *... and 4 more nodes in this community*

## Relationships

- [[PostgreSQL Knob Retrieval]] (96 shared connections)
- [[Docker Volume Management]] (3 shared connections)
- [[Logger Colors]] (2 shared connections)
- [[Knob Space Configuration]] (2 shared connections)
- [[Scoring & Weight Policies]] (2 shared connections)
- [[Metric Config & Composite]] (1 shared connections)
- [[Cross-Module Rationale]] (1 shared connections)
- [[Database Config & Connection]] (1 shared connections)
- [[Drift Detection]] (1 shared connections)
- [[Docker Manifest Tests]] (1 shared connections)
- [[Score Normalization Tests]] (1 shared connections)

## Source Files

- `src/knobs/retrieval.py`
- `src/utils/applicator.py`

## Audit Trail

- EXTRACTED: 107 (96%)
- INFERRED: 4 (4%)
- AMBIGUOUS: 0 (0%)

---

*Part of the graphify knowledge wiki. See [[index]] to navigate.*