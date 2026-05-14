# Workload README

> 8 nodes · cohesion 0.32

## Key Concepts

- **EnvironmentFactory** (7 connections) — `src/utils/environments/factory.py`
- **create()** (5 connections) — `src/utils/environments/factory.py`
- **factory.py** (5 connections) — `src/utils/environments/factory.py`
- **_resolve_docker_image()** (4 connections) — `src/utils/environments/factory.py`
- **_extract_pg_major()** (4 connections) — `src/evaluation/runner.py`
- **Environment Factory ===================  Handles environment instantiation with** (1 connections) — `src/utils/environments/factory.py`
- **Factory for creating execution environments.** (1 connections) — `src/utils/environments/factory.py`
- **Extract the major PostgreSQL version number from version strings.      Examples:** (1 connections) — `src/evaluation/runner.py`

## Relationships

- [[Environment Factory]] (18 shared connections)
- [[TPC-H Query Executor]] (2 shared connections)
- [[Bare Metal Environment]] (2 shared connections)
- [[Logger Banners]] (1 shared connections)
- [[Benchmark Executor Base]] (1 shared connections)
- [[Database Config & Connection]] (1 shared connections)
- [[Metric Config & Composite]] (1 shared connections)
- [[Hardware Detection & Info]] (1 shared connections)
- [[Workload Orchestrator]] (1 shared connections)

## Source Files

- `src/evaluation/runner.py`
- `src/utils/environments/factory.py`

## Audit Trail

- EXTRACTED: 19 (68%)
- INFERRED: 9 (32%)
- AMBIGUOUS: 0 (0%)

---

*Part of the graphify knowledge wiki. See [[index]] to navigate.*