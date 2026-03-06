"""
TPC-H Benchmark Support
=======================

Provides data generation (via dbgen C-binary), schema management,
and query assets for the TPC-H analytical benchmark.

Directory Layout:
    src/benchmarks/tpch/
    ├── __init__.py          # This file
    ├── setup_dbgen.py       # Auto-compile dbgen from source
    ├── schema.sql           # 8-table DDL
    ├── indexes.sql          # FKs + secondary indexes
    └── queries/             # 22 standard TPC-H queries
        ├── 1.sql ... 22.sql
"""

from pathlib import Path

TPCH_DIR = Path(__file__).parent
SCHEMA_SQL = TPCH_DIR / "schema.sql"
INDEXES_SQL = TPCH_DIR / "indexes.sql"
QUERIES_DIR = TPCH_DIR / "queries"
