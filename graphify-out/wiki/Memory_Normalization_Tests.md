# Memory Normalization Tests

> 6 nodes · cohesion 0.33

## Key Concepts

- **InstanceConfig** (11 connections) — `src/utils/environments/base.py`
- **_DummySchemaProvider** (10 connections) — `tests/unit/utils/test_docker_environment.py`
- **_SysbenchLikeSchemaProvider** (7 connections) — `tests/unit/utils/test_docker_environment.py`
- **Configuration for a single PostgreSQL instance.** (1 connections) — `src/utils/environments/base.py`
- **Minimal schema provider stand-in used for context payload generation.** (1 connections) — `tests/unit/utils/test_docker_environment.py`
- **Schema provider stand-in with profile-defining Sysbench attributes.** (1 connections) — `tests/unit/utils/test_docker_environment.py`

## Relationships

- [[TPC-H Query Executor]] (9 shared connections)
- [[TPC-H Benchmark Executor]] (9 shared connections)
- [[Bare Metal Environment]] (6 shared connections)
- [[Database Config & Connection]] (3 shared connections)
- [[Benchmark Executor Base]] (1 shared connections)
- [[Docker Manifest Tests]] (1 shared connections)
- [[Scoring Policies]] (1 shared connections)
- [[Cross-Module Rationale]] (1 shared connections)

## Source Files

- `src/utils/environments/base.py`
- `tests/unit/utils/test_docker_environment.py`

## Audit Trail

- EXTRACTED: 16 (52%)
- INFERRED: 15 (48%)
- AMBIGUOUS: 0 (0%)

---

*Part of the graphify knowledge wiki. See [[index]] to navigate.*