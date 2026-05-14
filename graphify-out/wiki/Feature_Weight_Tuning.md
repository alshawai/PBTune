# Feature Weight Tuning

> 8 nodes · cohesion 0.32

## Key Concepts

- **create_database()** (5 connections) — `src/database/management.py`
- **reset_database()** (5 connections) — `src/database/management.py`
- **management.py** (5 connections) — `src/database/management.py`
- **drop_database()** (4 connections) — `src/database/management.py`
- **Database Management Utilities ==============================  Provides utilities** (1 connections) — `src/database/management.py`
- **Drop and recreate the database.      This provides a clean slate by removing all** (1 connections) — `src/database/management.py`
- **Create the database if it does not exist.      Parameters     ----------     con** (1 connections) — `src/database/management.py`
- **Drop the database if it exists.      This will terminate all connections to the** (1 connections) — `src/database/management.py`

## Relationships

- [[Database Operations]] (18 shared connections)
- [[Metric Config & Composite]] (2 shared connections)
- [[Worker Scoring Tests]] (2 shared connections)
- [[TPC-H Schema & Tables]] (1 shared connections)

## Source Files

- `src/database/management.py`

## Audit Trail

- EXTRACTED: 19 (83%)
- INFERRED: 4 (17%)
- AMBIGUOUS: 0 (0%)

---

*Part of the graphify knowledge wiki. See [[index]] to navigate.*