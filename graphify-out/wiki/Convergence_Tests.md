# Convergence Tests

> 14 nodes · cohesion 0.18

## Key Concepts

- **load_csv_to_table()** (6 connections) — `src/database/data_loader.py`
- **reset_existing_database()** (6 connections) — `src/scripts/setup_database.py`
- **setup_fresh_database()** (6 connections) — `src/scripts/setup_database.py`
- **load_leads_dataset()** (5 connections) — `src/database/data_loader.py`
- **load_products_dataset()** (5 connections) — `src/database/data_loader.py`
- **setup_database.py** (5 connections) — `src/scripts/setup_database.py`
- **setup_sysbench_table()** (4 connections) — `src/scripts/setup_database.py`
- **Load the leads dataset (convenience function).      Parameters     ----------** (1 connections) — `src/database/data_loader.py`
- **Load data from a CSV file into a PostgreSQL table.      Parameters     ---------** (1 connections) — `src/database/data_loader.py`
- **Load the products dataset (convenience function).      Parameters     ----------** (1 connections) — `src/database/data_loader.py`
- **Database Setup Script =====================  Sets up the PostgreSQL database for** (1 connections) — `src/scripts/setup_database.py`
- **Create the sbtest1 table required for OLTP workloads.** (1 connections) — `src/scripts/setup_database.py`
- **Create and populate the database from scratch.** (1 connections) — `src/scripts/setup_database.py`
- **Reset the database (WARNING: Destroys all data).** (1 connections) — `src/scripts/setup_database.py`

## Relationships

- [[Worker Scoring Tests]] (32 shared connections)
- [[Docker Manifest Tests]] (4 shared connections)
- [[PostgreSQL Knob Tests]] (3 shared connections)
- [[Database Operations]] (2 shared connections)
- [[PostgreSQL Knob Retrieval]] (1 shared connections)
- [[BO Config & Worker]] (1 shared connections)
- [[Metric Config & Composite]] (1 shared connections)

## Source Files

- `src/database/data_loader.py`
- `src/scripts/setup_database.py`

## Audit Trail

- EXTRACTED: 31 (70%)
- INFERRED: 13 (30%)
- AMBIGUOUS: 0 (0%)

---

*Part of the graphify knowledge wiki. See [[index]] to navigate.*