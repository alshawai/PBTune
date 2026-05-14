# Architecture Documentation

> 3 nodes · cohesion 0.67

## Key Concepts

- **get_engine()** (6 connections) — `src/database/connection.py`
- **Create a SQLAlchemy engine for pandas and ORM operations.      Parameters     --** (1 connections) — `src/database/connection.py`
- **Create or return SQLAlchemy engine for pandas operations.** (1 connections) — `src/knobs/retrieval.py`

## Relationships

- [[PostgreSQL Knob Retrieval]] (6 shared connections)
- [[Worker Scoring Tests]] (1 shared connections)
- [[TPC-H Schema & Tables]] (1 shared connections)

## Source Files

- `src/database/connection.py`
- `src/knobs/retrieval.py`

## Audit Trail

- EXTRACTED: 7 (88%)
- INFERRED: 1 (12%)
- AMBIGUOUS: 0 (0%)

---

*Part of the graphify knowledge wiki. See [[index]] to navigate.*