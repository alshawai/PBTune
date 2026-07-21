# MySQL Integration — Comprehensive Implementation Guide


See also: [Documentation Index](../README.md)

> **Purpose:** Complete reference for integrating MySQL as a second supported DBMS.  
> **Date created:** March 2026  
> **Status:** Future work — deferred from current scope. This document captures ALL required changes, research, and implementation steps so that the work can be picked up at any time with full context.
>
> ⚠️ **Stale layout warning (added 2026-07-17):** This guide was written in March 2026 against the pre-refactor codebase, before the tuners unification (ADR-006). Its File-by-File Change Map and phase sections still reference the old `src/tuner/` package — including modules that no longer exist as separate files (e.g. `src/tuner/utils/instance_manager.py`, `snapshot_manager.py`, `restart_manager.py`, `postgres_instance.py`, `src/tuner/evaluator/evaluator.py`). The current layout is: PBT strategy under `src/tuners/pbt/`, the shared eval engine (orchestrator, barriers, restart_policy, worker) under `src/tuners/engine/`, environment/instance lifecycle under `src/utils/environments/`, knob code under `src/knobs/`, and workload code under `src/benchmarks/`. Before picking up this work, re-derive every target path against the current tree; the architecture is unchanged in intent but the file boundaries have moved.

---

## Table of Contents

1. [Executive Summary](#1-executive-summary)
2. [Architecture: Database Adapter Pattern](#2-architecture-database-adapter-pattern)
3. [Phase-by-Phase Implementation Plan](#3-phase-by-phase-implementation-plan)
4. [Phase R1 — Abstract Adapter Layer](#4-phase-r1--abstract-adapter-layer)
5. [Phase R2 — MySQL Connection & Config](#5-phase-r2--mysql-connection--config)
6. [Phase R3 — MySQL Knob System](#6-phase-r3--mysql-knob-system)
7. [Phase R4 — MySQL Instance Management](#7-phase-r4--mysql-instance-management)
8. [Phase R5 — MySQL Knob Application](#8-phase-r5--mysql-knob-application)
9. [Phase R6 — MySQL Benchmark Integration](#9-phase-r6--mysql-benchmark-integration)
10. [Phase R7 — MySQL Metrics & Evaluator](#10-phase-r7--mysql-metrics--evaluator)
11. [Phase R8 — MySQL Knob Metadata Research](#11-phase-r8--mysql-knob-metadata-research)
12. [Phase R9 — CLI, Config & Orchestrator](#12-phase-r9--cli-config--orchestrator)
13. [Phase R10 — Testing](#13-phase-r10--testing)
14. [Phase R11 — Documentation](#14-phase-r11--documentation)
15. [Conceptual Differences: PostgreSQL vs MySQL](#15-conceptual-differences-postgresql-vs-mysql)
16. [File-by-File Change Map](#16-file-by-file-change-map)
17. [MySQL Knob Quick Reference](#17-mysql-knob-quick-reference)
18. [Dependencies & Requirements](#18-dependencies--requirements)
19. [Paper Framing (If Deferred as Future Work)](#19-paper-framing-if-deferred-as-future-work)

---

## 1. Executive Summary

Adding MySQL support requires changes across **~35-50 files**, creation of **~8-10 new files**, and research into **~100+ MySQL system variables**. The recommended approach is a **Database Adapter Pattern** that abstracts all DBMS-specific operations behind a common interface, allowing the PBT core algorithm, scoring formula, and analysis tools (fANOVA, SHAP, visualization) to remain completely DBMS-agnostic.

**Impact breakdown:**

| Category | Files Affected | Effort |
|----------|---------------|--------|
| Hard dependencies (substantial rewrite/new code) | ~18 files | Large |
| Soft dependencies (config/naming changes) | ~10 files | Small-Medium |
| No changes needed (already DBMS-agnostic) | ~7 files | None |
| New files to create | ~8-10 files | Large |
| New data files (knob CSVs) | ~5+ files | Medium |
| Research (MySQL knob metadata) | N/A | Large |

---

## 2. Architecture: Database Adapter Pattern

### 2.1 Abstract Base Class

Create `src/database/adapter.py`:

```python
from abc import ABC, abstractmethod
from dataclasses import dataclass
from typing import Any, Dict, List, Optional, Tuple
from pathlib import Path


@dataclass
class KnobMetadataRecord:
    """DBMS-agnostic knob metadata from the system catalog."""
    name: str
    value: str
    var_type: str           # 'integer', 'real', 'boolean', 'enum', 'string'
    min_val: Optional[str]
    max_val: Optional[str]
    enum_vals: Optional[List[str]]
    requires_restart: bool
    unit: Optional[str]
    category: str
    description: str


class DatabaseAdapter(ABC):
    """Abstract interface for DBMS-specific operations.
    
    All DBMS-specific code is encapsulated behind this interface.
    The PBT core, scoring, and analysis tools interact ONLY with this ABC.
    """

    # --- Connection ---
    @abstractmethod
    def connect(self, **kwargs) -> Any:
        """Return a native database connection object."""

    @abstractmethod
    def get_sqlalchemy_url(self, config) -> str:
        """Return SQLAlchemy connection URL string."""

    @abstractmethod
    def get_admin_connection(self, config) -> Any:
        """Return a connection to the admin/system database."""

    # --- Database Management ---
    @abstractmethod
    def database_exists(self, conn, dbname: str) -> bool:
        """Check if a database/schema exists."""

    @abstractmethod
    def create_database(self, conn, dbname: str) -> None:
        """Create a new database."""

    @abstractmethod
    def drop_database(self, conn, dbname: str) -> None:
        """Drop a database (with force-disconnect of other sessions)."""

    # --- Knob Metadata ---
    @abstractmethod
    def get_all_knob_metadata(self, conn) -> List[KnobMetadataRecord]:
        """Retrieve metadata for ALL tunable knobs from the system catalog."""

    @abstractmethod
    def get_knob_metadata(self, conn, knob_name: str) -> KnobMetadataRecord:
        """Retrieve metadata for a single knob."""

    @abstractmethod
    def normalize_knob_value(self, raw_value: str, unit: Optional[str]) -> float:
        """Convert DBMS-specific raw value + unit to a normalized numeric value."""

    # --- Knob Application ---
    @abstractmethod
    def apply_knob_persistent(self, conn, name: str, value: Any) -> None:
        """Apply a knob value persistently (survives restart)."""

    @abstractmethod
    def apply_knob_runtime(self, conn, name: str, value: Any) -> None:
        """Apply a knob value at runtime (lost on restart)."""

    @abstractmethod
    def reset_knob(self, conn, name: str) -> None:
        """Reset a knob to its default value."""

    @abstractmethod
    def reload_config(self, conn) -> None:
        """Signal the DBMS to reload its configuration file."""

    @abstractmethod
    def get_current_knob_value(self, conn, name: str) -> str:
        """Read the current value of a knob from the running instance."""

    # --- Statistics ---
    @abstractmethod
    def reset_stats(self, conn) -> None:
        """Reset internal performance counters/statistics."""

    @abstractmethod
    def get_cache_hit_ratio(self, conn) -> float:
        """Read the buffer cache hit ratio."""

    @abstractmethod
    def get_io_stats(self, conn) -> Dict[str, float]:
        """Read I/O statistics (blocks read, blocks hit, etc.)."""

    @abstractmethod
    def get_backend_pid(self, conn) -> int:
        """Get the connection's backend process ID."""

    # --- Maintenance ---
    @abstractmethod
    def vacuum_equivalent(self, conn, table_name: str) -> None:
        """Run VACUUM ANALYZE (PG) or ANALYZE TABLE (MySQL) or equivalent."""

    @abstractmethod
    def bulk_load(self, conn, table_name: str, file_path: str, 
                  delimiter: str = '|') -> None:
        """Bulk-load data from a file into a table."""

    # --- Instance Management ---
    @abstractmethod
    def initialize_data_directory(self, data_dir: Path, **kwargs) -> None:
        """Create a new, empty data directory (initdb / mysqld --initialize)."""

    @abstractmethod
    def write_config_file(self, data_dir: Path, port: int, 
                          extra_settings: Dict[str, str] = None) -> None:
        """Write the DBMS config file (postgresql.conf / my.cnf) into data_dir."""

    @abstractmethod
    def start_instance(self, data_dir: Path, log_file: Path = None) -> int:
        """Start a DBMS instance. Return the PID."""

    @abstractmethod
    def stop_instance(self, data_dir: Path, mode: str = 'fast') -> None:
        """Stop a running DBMS instance."""

    @abstractmethod
    def is_instance_running(self, data_dir: Path) -> bool:
        """Check if an instance is running from the given data directory."""

    @abstractmethod
    def validate_data_directory(self, data_dir: Path) -> bool:
        """Check if data_dir contains a valid DBMS data directory."""

    @abstractmethod
    def get_excluded_snapshot_files(self) -> List[str]:
        """Return list of files to exclude from data directory snapshots."""

    @abstractmethod
    def get_process_name(self) -> str:
        """Return the process name to search for (e.g., 'postgres', 'mysqld')."""

    @abstractmethod
    def detect_data_directory(self, conn) -> str:
        """Query the running instance for its data directory path."""

    @abstractmethod
    def find_binaries(self) -> Dict[str, Optional[str]]:
        """Locate DBMS-specific binaries on the system. Return {name: path}."""

    # --- Connection / User Management ---
    @abstractmethod
    def user_exists(self, conn, username: str) -> bool:
        """Check if a database user/role exists."""

    @abstractmethod
    def create_user(self, conn, username: str, password: str, 
                    superuser: bool = False) -> None:
        """Create a database user with appropriate privileges."""

    # --- Defaults ---
    @abstractmethod
    def default_port(self) -> int:
        """Return the DBMS default port (5432 / 3306)."""

    @abstractmethod
    def default_admin_user(self) -> str:
        """Return the DBMS default admin username (postgres / root)."""

    @abstractmethod
    def identifier_quote(self) -> str:
        """Return the identifier quote character ('"' for PG, '`' for MySQL)."""

    # --- Recovery / Health ---
    @abstractmethod
    def is_in_recovery(self, conn) -> bool:
        """Check if the instance is in recovery/standby mode."""
```

### 2.2 PostgreSQL Adapter

Create `src/database/postgresql_adapter.py` — refactor existing code into a `PostgreSQLAdapter(DatabaseAdapter)` class. This is a **reorganization**, not a rewrite. Every method maps to existing code:

| Adapter Method | Current Code Location |
|---|---|
| `connect()` | `src/database/connection.py:get_connection()` (L64) |
| `get_sqlalchemy_url()` | `src/config/database.py:DatabaseConfig.get_sqlalchemy_url()` (L106) |
| `get_admin_connection()` | `src/database/management.py:create_database()` (L37) — connect to `postgres` DB |
| `database_exists()` | `src/database/management.py` (L43) — `SELECT 1 FROM pg_database WHERE datname = %s` |
| `create_database()` | `src/database/management.py` (L48) — `CREATE DATABASE "{dbname}"` |
| `drop_database()` | `src/database/management.py` (L85-93) — `pg_terminate_backend()` + `DROP DATABASE` |
| `get_all_knob_metadata()` | `src/knobs/retrieval.py` (L181-196) — `SELECT ... FROM pg_settings` |
| `get_knob_metadata()` | `src/knobs/retrieval.py` (L300-314) — `SELECT ... FROM pg_settings WHERE name = %s` |
| `normalize_knob_value()` | `src/knobs/retrieval.py:normalize_value()` (L349-380) — PG unit handling |
| `apply_knob_persistent()` | `src/tuner/utils/applicator.py` (L417-432) — `ALTER SYSTEM SET` |
| `apply_knob_runtime()` | `src/tuner/utils/applicator.py` (L436) — `SET {name} = %s` |
| `reset_knob()` | `src/tuner/utils/applicator.py` (L670) — `ALTER SYSTEM RESET {name}` |
| `reload_config()` | `src/tuner/utils/applicator.py` (L453) — `SELECT pg_reload_conf()` |
| `get_current_knob_value()` | `src/tuner/utils/applicator.py` (L638-648) — `SELECT ... FROM pg_settings` |
| `reset_stats()` | `src/tuner/evaluator/evaluator.py` (L907) — `SELECT pg_stat_reset()` |
| `get_cache_hit_ratio()` | `src/tuner/evaluator/evaluator.py` (L1253-1258) — `pg_stat_database` |
| `get_io_stats()` | `src/tuner/evaluator/evaluator.py` (L1398-1440) — `pg_stat_database` |
| `get_backend_pid()` | `src/tuner/evaluator/evaluator.py` (L1037) — `SELECT pg_backend_pid()` |
| `vacuum_equivalent()` | Multiple files — `VACUUM ANALYZE {table}` |
| `bulk_load()` | `src/benchmarks/tpch/executor.py` (L115-118) — `psycopg2.copy_expert()` |
| `initialize_data_directory()` | `src/tuner/utils/instance_manager.py` (L267) — `initdb -D ...` |
| `write_config_file()` | `src/tuner/utils/instance_manager.py` (L557) — write `postgresql.conf` |
| `start_instance()` | `src/tuner/utils/instance_manager.py` (L579) — `pg_ctl start` |
| `stop_instance()` | `src/tuner/utils/instance_manager.py` (L700-730) — `pg_ctl stop` |
| `is_instance_running()` | `src/tuner/utils/instance_manager.py` (L172-195) — check `postmaster.pid` |
| `validate_data_directory()` | `src/tuner/utils/instance_manager.py` (L145-170) — check PG-specific files |
| `get_excluded_snapshot_files()` | `src/tuner/utils/snapshot_manager.py` (L131-138): return PG config file list |
| `get_process_name()` | Returns `"postgres"` |
| `detect_data_directory()` | `src/tuner/utils/restart_manager.py` (L229) — `SHOW data_directory` |
| `find_binaries()` | `src/tuner/utils/instance_manager.py` (L104) — search for `pg_ctl`, `initdb`, etc. |
| `user_exists()` | `src/tuner/utils/instance_manager.py` (L527) — `SELECT 1 FROM pg_roles` |
| `create_user()` | `src/tuner/utils/instance_manager.py` (L527) — `CREATE USER ... WITH SUPERUSER` |
| `default_port()` | Returns `5432` |
| `default_admin_user()` | Returns `"postgres"` |
| `identifier_quote()` | Returns `'"'` |
| `is_in_recovery()` | `src/tuner/utils/restart_manager.py` (L700) — `SELECT pg_is_in_recovery()` |

### 2.3 MySQL Adapter

Create `src/database/mysql_adapter.py` — new `MySQLAdapter(DatabaseAdapter)` class (detailed in Phase R2-R7).

### 2.4 Adapter Factory

Create `src/database/adapter_factory.py`:

```python
from src.database.adapter import DatabaseAdapter
from src.database.postgresql_adapter import PostgreSQLAdapter
from src.database.mysql_adapter import MySQLAdapter


def create_adapter(dbms: str, **kwargs) -> DatabaseAdapter:
    """Factory to create the appropriate database adapter.
    
    Args:
        dbms: 'postgresql' or 'mysql'
    """
    adapters = {
        'postgresql': PostgreSQLAdapter,
        'mysql': MySQLAdapter,
    }
    if dbms not in adapters:
        raise ValueError(f"Unsupported DBMS: {dbms}. Supported: {list(adapters.keys())}")
    return adapters[dbms](**kwargs)
```

---

## 3. Phase-by-Phase Implementation Plan

| Phase | Description | Effort | Depends On |
|-------|-------------|--------|------------|
| R1 | Define `DatabaseAdapter` ABC + refactor PostgreSQL code behind it | Large | — |
| R2 | MySQL connection, config, driver | Small | R1 |
| R3 | MySQL knob retrieval + metadata pipeline | Large | R2, R8 |
| R4 | MySQL instance management (start/stop/init) | Large | R2 |
| R5 | MySQL knob application (SET GLOBAL/PERSIST) | Medium | R2, R3 |
| R6 | MySQL benchmark integration (Sysbench + TPC-H) | Medium | R2, R4 |
| R7 | MySQL metrics collection + evaluator | Medium | R2 |
| R8 | MySQL knob metadata research | Large (research) | — |
| R9 | CLI + config: `--dbms` flag, DBMS-aware orchestrator | Medium | R1-R7 |
| R10 | Testing: adapter tests, MySQL-specific tests | Large | R1-R9 |
| R11 | Documentation | Medium | R1-R10 |

**Critical path:** R8 (research) can start immediately and in parallel with R1. R1 must complete before R2-R7. R9 depends on all R2-R7.

---

## 4. Phase R1 — Abstract Adapter Layer

### 4.1 Tasks

1. **Create `src/database/adapter.py`** — the `DatabaseAdapter` ABC as defined in Section 2.1 above.

2. **Create `src/database/postgresql_adapter.py`** — move all PostgreSQL-specific code from across the codebase into this single adapter. Every method delegates to existing code. The existing files (`connection.py`, `management.py`) can be kept internally but wrapped by the adapter.

3. **Create `src/database/adapter_factory.py`** — factory function as defined in Section 2.4.

4. **Refactor consumers to use the adapter interface.** Each consumer currently imports PostgreSQL-specific code directly; change them to accept a `DatabaseAdapter` instance:

    **Files to refactor:**

    | File | Current PG-Specific Code | Change To |
    |---|---|---|
    | `src/tuner/evaluator/evaluator.py` | Imports `psycopg2`, uses PG SQL directly | Accept `DatabaseAdapter`, call `adapter.get_cache_hit_ratio()`, `adapter.reset_stats()`, etc. |
    | `src/tuner/utils/applicator.py` | `ALTER SYSTEM SET`, queries `pg_settings` | Accept `DatabaseAdapter`, call `adapter.apply_knob_persistent()`, `adapter.get_knob_metadata()` |
    | `src/tuner/utils/restart_manager.py` | `pg_ctl`, `SHOW data_directory` | Accept `DatabaseAdapter`, call `adapter.stop_instance()`, `adapter.start_instance()`, `adapter.detect_data_directory()` |
    | `src/tuner/utils/instance_manager.py` | `initdb`, `pg_ctl`, writes `postgresql.conf` | Accept `DatabaseAdapter`, call `adapter.initialize_data_directory()`, `adapter.write_config_file()`, etc. |
    | `src/tuner/utils/postgres_instance.py` | Queries `pg_settings` for context | Accept `DatabaseAdapter`, call `adapter.get_knob_metadata()` |
    | `src/tuner/utils/snapshot_manager.py` | Hardcoded PG excluded files | Accept `DatabaseAdapter`, call `adapter.get_excluded_snapshot_files()` |
    | `src/knobs/retrieval.py` | `SELECT ... FROM pg_settings` | Accept `DatabaseAdapter`, call `adapter.get_all_knob_metadata()` |
    | `src/database/management.py` | `pg_database`, `pg_terminate_backend()` | Wrapped by adapter; consumer code calls `adapter.create_database()` etc. |
    | `src/benchmarks/sysbench/executor.py` | `--db-driver=pgsql`, `VACUUM ANALYZE` | Accept `DatabaseAdapter`, call `adapter.vacuum_equivalent()`, build flags from adapter |
    | `src/benchmarks/tpch/executor.py` | `psycopg2.copy_expert()`, `VACUUM ANALYZE` | Accept `DatabaseAdapter`, call `adapter.bulk_load()`, `adapter.vacuum_equivalent()` |
    | `src/tuners/pbt/tuner.py` | `PostgresInstanceManager`, hardcoded port `5440` | Accept DBMS config, use `adapter_factory.create_adapter()` |
    | `src/tuners/pbt/population.py` | References `PostgresInstanceManager` | Use generic `InstanceManager` (rename or interface) |

5. **Rename PostgreSQL-branded classes** to generic names (or keep PG-branded as the adapter implementation):
    - `PostgresInstanceManager` → keep internally, but consumers use adapter
    - `PostgresRestartManager` → keep internally, but consumers use adapter
    - `PostgresInstance` → keep internally, but consumers use adapter
    - `PostgreSQLKnobRetriever` → keep internally, but consumers use adapter

6. **Update `src/knobs/__init__.py`** — export `DatabaseAdapter` and factory instead of PG-branded names.

7. **Update `src/database/__init__.py`** — export `create_adapter`, `DatabaseAdapter`.

### 4.2 Validation

After Phase R1, the existing PostgreSQL workflow MUST work exactly as before. Run the full test suite (when available) and a manual PBT run to confirm no regressions.

---

## 5. Phase R2 — MySQL Connection & Config

### 5.1 Driver Selection

**Recommended:** `mysql-connector-python` (Oracle's official driver, pure Python, no C extension required).

**Alternative:** `PyMySQL` (pure Python, widely used, compatible with `mysql+pymysql://` SQLAlchemy URL).

**Decision factor:** `mysql-connector-python` supports `SET PERSIST` (MySQL 8.0+) natively; `PyMySQL` requires raw SQL. Either works.

**Add to `requirements.txt`:**  
```
mysql-connector-python>=8.0.0
```

### 5.2 MySQL Adapter — Connection Methods

```python
# src/database/mysql_adapter.py

import mysql.connector
from mysql.connector import Error as MySQLError

class MySQLAdapter(DatabaseAdapter):
    
    def connect(self, host, port, user, password, database=None, 
                connect_timeout=10, **kwargs):
        return mysql.connector.connect(
            host=host,
            port=port,
            user=user,
            password=password,
            database=database,
            connection_timeout=connect_timeout,
            autocommit=False,
        )
    
    def get_sqlalchemy_url(self, config):
        return f"mysql+mysqlconnector://{config.user}:{config.password}@{config.host}:{config.port}/{config.dbname}"
    
    def get_admin_connection(self, config):
        """Connect to the 'mysql' system database."""
        return self.connect(
            host=config.host,
            port=config.port,
            user=config.user,
            password=config.password,
            database='mysql',
        )
    
    def default_port(self):
        return 3306
    
    def default_admin_user(self):
        return "root"
    
    def identifier_quote(self):
        return '`'
```

### 5.3 DatabaseConfig Changes

`src/config/database.py` needs to be DBMS-aware:

**Current:** Hardcoded `port=5432`, `user="postgres"`, URL prefix `postgresql://`.

**Change:** Accept a `dbms` parameter, delegate defaults and URL construction to the adapter:

```python
@dataclass
class DatabaseConfig:
    user: str
    password: str
    host: str
    port: str
    dbname: str
    dbms: str = "postgresql"  # NEW: 'postgresql' or 'mysql'
    
    @classmethod
    def from_env(cls, dbms: str = None):
        dbms = dbms or os.getenv("PBT_DBMS", "postgresql")
        defaults = {
            "postgresql": {"port": "5432", "user": "postgres"},
            "mysql": {"port": "3306", "user": "root"},
        }
        d = defaults.get(dbms, defaults["postgresql"])
        return cls(
            user=os.getenv("DB_USER", d["user"]),
            password=os.getenv("DB_PASSWORD", ""),
            host=os.getenv("DB_HOST", "localhost"),
            port=os.getenv("DB_PORT", d["port"]),
            dbname=os.getenv("DB_NAME", "test_dataset"),
            dbms=dbms,
        )
    
    def to_dict(self):
        if self.dbms == "mysql":
            return {"host": self.host, "port": int(self.port), 
                    "user": self.user, "password": self.password, 
                    "database": self.dbname}
        else:
            return {"host": self.host, "port": self.port, 
                    "user": self.user, "password": self.password, 
                    "dbname": self.dbname}
```

### 5.4 Database Management (MySQL)

```python
class MySQLAdapter(DatabaseAdapter):
    
    def database_exists(self, conn, dbname):
        cursor = conn.cursor()
        cursor.execute(
            "SELECT SCHEMA_NAME FROM INFORMATION_SCHEMA.SCHEMATA WHERE SCHEMA_NAME = %s",
            (dbname,)
        )
        return cursor.fetchone() is not None
    
    def create_database(self, conn, dbname):
        cursor = conn.cursor()
        cursor.execute(f"CREATE DATABASE `{dbname}`")
        conn.commit()
    
    def drop_database(self, conn, dbname):
        cursor = conn.cursor()
        # Kill all connections to this database
        cursor.execute(
            "SELECT ID FROM information_schema.PROCESSLIST WHERE DB = %s",
            (dbname,)
        )
        for (conn_id,) in cursor.fetchall():
            try:
                cursor.execute(f"KILL {int(conn_id)}")
            except Exception:
                pass
        cursor.execute(f"DROP DATABASE IF EXISTS `{dbname}`")
        conn.commit()
```

---

## 6. Phase R3 — MySQL Knob System

### 6.1 Knob Retrieval

MySQL exposes system variables very differently from PostgreSQL:

| Aspect | PostgreSQL | MySQL |
|--------|-----------|-------|
| **Primary catalog** | `pg_settings` (13 columns) | `SHOW GLOBAL VARIABLES` (name + value only) |
| **Metadata richness** | Rich: min_val, max_val, unit, context, vartype, boot_val, reset_val, enumvals, description | Minimal: name + current value. Extra metadata requires MySQL 8.0+ `performance_schema.variables_info` |
| **Variable type info** | `vartype` column: integer, real, bool, enum, string | Not exposed in `SHOW VARIABLES`. Must be determined from `INFORMATION_SCHEMA.SYSTEM_VARIABLES` (MySQL 8.0.30+) or hardcoded |
| **Min/max values** | Directly from `pg_settings.min_val`, `pg_settings.max_val` | NOT available from system tables. Must be hardcoded or parsed from MySQL documentation |
| **Unit handling** | `pg_settings.unit` column (kB, 8kB, MB, ms, s, min) | Values are in raw units (bytes, seconds). No unit column |
| **Restart requirement** | `context` column: `postmaster` = restart required | `performance_schema.variables_info.SET_TIME IS NULL` for static vars (MySQL 8.0+) |
| **Enum values** | `pg_settings.enumvals` column | Not available. Must be hardcoded or parsed from docs |
| **Categories** | `pg_settings.category` column (e.g., "Write-Ahead Log") | No category concept. Must be manually assigned by prefix (`innodb_*`, `max_*`, etc.) |

**Critical implication:** MySQL does NOT expose min_val, max_val, variable type, or enum values through system tables. This metadata must be **manually researched and hardcoded** in a MySQL-specific `knob_metadata.py` file. This is why Phase R8 (research) is so important.

### 6.2 MySQL Knob Retrieval Implementation

```python
class MySQLAdapter(DatabaseAdapter):
    
    def get_all_knob_metadata(self, conn):
        cursor = conn.cursor(dictionary=True)
        
        # Get current values
        cursor.execute("SHOW GLOBAL VARIABLES")
        variables = {row['Variable_name']: row['Value'] for row in cursor.fetchall()}
        
        # Get dynamic/static classification (MySQL 8.0+)
        try:
            cursor.execute("""
                SELECT VARIABLE_NAME, VARIABLE_SOURCE, SET_TIME 
                FROM performance_schema.variables_info
            """)
            var_info = {row['VARIABLE_NAME']: row for row in cursor.fetchall()}
        except Exception:
            var_info = {}  # MySQL < 8.0 or performance_schema disabled
        
        records = []
        for name, value in variables.items():
            info = var_info.get(name, {})
            # Dynamic variables have SET_TIME populated or can be SET GLOBAL
            requires_restart = info.get('SET_TIME') is None and info.get('VARIABLE_SOURCE') == 'COMPILED'
            
            records.append(KnobMetadataRecord(
                name=name,
                value=value,
                var_type=self._infer_type(name, value),  # Must guess or use hardcoded metadata
                min_val=None,  # MySQL doesn't expose this — use hardcoded metadata
                max_val=None,  # MySQL doesn't expose this — use hardcoded metadata
                enum_vals=None,  # MySQL doesn't expose this
                requires_restart=requires_restart,
                unit=None,  # MySQL doesn't have a unit column
                category=self._categorize_knob(name),  # Derive from naming convention
                description='',  # MySQL doesn't expose description in SHOW VARIABLES
            ))
        return records
    
    def _infer_type(self, name: str, value: str) -> str:
        """Infer variable type from value string.
        
        Not reliable — should be overridden by hardcoded metadata.
        """
        if value.lower() in ('on', 'off', 'yes', 'no', 'true', 'false'):
            return 'boolean'
        try:
            int(value)
            return 'integer'
        except ValueError:
            pass
        try:
            float(value)
            return 'real'
        except ValueError:
            pass
        return 'string'
    
    def _categorize_knob(self, name: str) -> str:
        """Assign category from naming convention."""
        if name.startswith('innodb_'):
            return 'InnoDB'
        elif name.startswith('max_'):
            return 'Connections'
        elif name.startswith('sort_') or name.startswith('join_'):
            return 'Memory'
        elif name.startswith('optimizer_'):
            return 'Query Optimizer'
        elif name.startswith('log_') or name.startswith('binlog_'):
            return 'Logging'
        elif name.startswith('tmp_') or name.startswith('temp_'):
            return 'Temporary Storage'
        else:
            return 'Other'
```

### 6.3 MySQL Unit Normalization

```python
class MySQLAdapter(DatabaseAdapter):
    
    def normalize_knob_value(self, raw_value, unit=None):
        """MySQL values are already in raw units (bytes, seconds).
        
        No unit conversion needed — unlike PostgreSQL which uses kB, 8kB, ms, etc.
        """
        try:
            return float(raw_value)
        except (ValueError, TypeError):
            return 0.0
```

### 6.4 MySQL Knob Metadata File

Create `src/knobs/mysql_knob_metadata.py` — equivalent of `knob_metadata.py` but with MySQL variable names. This requires the research from Phase R8. Structure mirrors the PG version:

```python
MYSQL_KNOB_TUNING_METADATA: Dict[str, TuningMetadata] = {
    "innodb_buffer_pool_size": TuningMetadata(
        tuning_min=...,       # Research needed
        tuning_max=...,       # Research needed
        scale="log",
        impact_tier="minimal",
        tuning_priority=1,
        notes="Equivalent to PG shared_buffers. Size in bytes.",
    ),
    "innodb_log_file_size": TuningMetadata(...),
    "sort_buffer_size": TuningMetadata(...),
    # ... ~100+ entries
}

MYSQL_IMPACT_TIERS: Dict[str, Optional[List[str]]] = {
    "minimal": [...],   # Top ~5 knobs — determined after research
    "core": [...],      # Top ~13
    "standard": [...],  # All with metadata
    "extensive": None,  # All tunable
}
```

### 6.5 MySQL Preprocessing Pipeline

Create `src/knobs/mysql_preprocess_knobs.py` or extend `preprocess_knobs.py` to accept a DBMS parameter:

**Changes needed:**
- `load_raw_knobs()` — use `MySQLAdapter.get_all_knob_metadata()` instead of `pg_settings`
- `add_tuning_metadata()` — use `MYSQL_KNOB_TUNING_METADATA` instead of `KNOB_TUNING_METADATA`
- `filter_tunable_knobs()` — MySQL-specific: filter by InnoDB + session vars; exclude read-only
- `create_tier_dataframes()` — use `MYSQL_IMPACT_TIERS`
- Output: `data/tuner_knobs/mysql/` subdirectory with tier CSVs

### 6.6 MySQL Knob Loader Updates

`src/knobs/knob_loader.py` changes:

**Current type mapping (PG `vartype` values):**
```python
{"integer": KnobType.INTEGER, "real": KnobType.REAL, "bool": KnobType.BOOLEAN, ...}
```

**MySQL equivalent:** Same mapping works, but the type must come from hardcoded metadata (MySQL doesn't expose `vartype`).

**Current restart logic:** `requires_restart = context == 'postmaster'`

**MySQL equivalent:** `requires_restart` comes from `performance_schema.variables_info` or hardcoded metadata.

**CSV path:** Currently loads from `data/tuner_knobs/`. Need to load from `data/tuner_knobs/postgresql/` or `data/tuner_knobs/mysql/` based on DBMS.

---

## 7. Phase R4 — MySQL Instance Management

### 7.1 MySQL Data Directory Initialization

```python
class MySQLAdapter(DatabaseAdapter):
    
    def initialize_data_directory(self, data_dir, **kwargs):
        """Initialize a MySQL data directory.
        
        MySQL 8.0: `mysqld --initialize-insecure --datadir=... --user=mysql`
        The --initialize-insecure flag creates root with empty password.
        The --initialize flag generates a random temp password (harder to automate).
        """
        mysqld_path = self._find_binary('mysqld')
        cmd = [
            mysqld_path,
            '--initialize-insecure',
            f'--datadir={data_dir}',
            '--user=mysql',  # or current system user
        ]
        result = subprocess.run(cmd, capture_output=True, text=True, timeout=120)
        if result.returncode != 0:
            raise RuntimeError(f"mysqld --initialize failed: {result.stderr}")
```

### 7.2 MySQL Config File

```python
class MySQLAdapter(DatabaseAdapter):
    
    def write_config_file(self, data_dir, port, extra_settings=None):
        """Write my.cnf into data_dir."""
        config_path = data_dir / 'my.cnf'
        lines = [
            '[mysqld]',
            f'port={port}',
            f'datadir={data_dir}',
            f'socket={data_dir}/mysql.sock',
            f'pid-file={data_dir}/mysql.pid',
            f'log-error={data_dir}/mysql_error.log',
            'bind-address=127.0.0.1',
            # Disable performance_schema overhead for tuning workers
            # (enable on primary instance for metadata queries)
            'performance_schema=ON',
            # Allow SET PERSIST
            'persisted-globals-load=ON',
        ]
        if extra_settings:
            for k, v in extra_settings.items():
                lines.append(f'{k}={v}')
        
        config_path.write_text('\n'.join(lines) + '\n')
```

### 7.3 MySQL Instance Start/Stop

```python
class MySQLAdapter(DatabaseAdapter):
    
    def start_instance(self, data_dir, log_file=None):
        """Start a MySQL instance using mysqld_safe or mysqld directly."""
        mysqld = self._find_binary('mysqld')
        my_cnf = data_dir / 'my.cnf'
        log = log_file or data_dir / 'mysql_error.log'
        
        cmd = [
            mysqld,
            f'--defaults-file={my_cnf}',
            f'--datadir={data_dir}',
            '&',  # Background
        ]
        # Use subprocess.Popen for background process
        proc = subprocess.Popen(
            cmd, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL
        )
        
        # Wait for MySQL to be ready (poll connection)
        self._wait_for_ready(data_dir, timeout=30)
        return proc.pid
    
    def stop_instance(self, data_dir, mode='fast'):
        """Stop a MySQL instance."""
        # Option 1: mysqladmin shutdown
        socket = data_dir / 'mysql.sock'
        mysqladmin = self._find_binary('mysqladmin')
        if mysqladmin:
            cmd = [mysqladmin, f'--socket={socket}', '-u', 'root', 'shutdown']
            subprocess.run(cmd, capture_output=True, timeout=30)
            return
        
        # Option 2: Kill via PID file
        pid_file = data_dir / 'mysql.pid'
        if pid_file.exists():
            pid = int(pid_file.read_text().strip())
            os.kill(pid, signal.SIGTERM)
            self._wait_for_stop(pid, timeout=30)
    
    def is_instance_running(self, data_dir):
        """Check if MySQL instance is running from this data directory."""
        pid_file = data_dir / 'mysql.pid'
        if not pid_file.exists():
            return False
        pid = int(pid_file.read_text().strip())
        try:
            os.kill(pid, 0)  # Signal 0 = check if process exists
            return True
        except ProcessLookupError:
            return False
    
    def validate_data_directory(self, data_dir):
        """Check if data_dir contains a valid MySQL data directory."""
        required = ['ibdata1', 'mysql']  # InnoDB system tablespace + mysql system DB
        return all((data_dir / f).exists() for f in required)
    
    def get_excluded_snapshot_files(self):
        return [
            'my.cnf',
            'mysqld-auto.cnf',  # MySQL 8.0 persisted variables
            'mysql.pid',
            'mysql.sock',
            'mysql.sock.lock',
            'mysql_error.log',
            'auto.cnf',  # Server UUID
        ]
    
    def get_process_name(self):
        return 'mysqld'
    
    def detect_data_directory(self, conn):
        cursor = conn.cursor()
        cursor.execute("SHOW VARIABLES LIKE 'datadir'")
        row = cursor.fetchone()
        return row[1] if row else None
    
    def find_binaries(self):
        """Locate MySQL binaries."""
        binaries = {}
        for name in ['mysqld', 'mysqladmin', 'mysqldump', 'mysql']:
            binaries[name] = self._find_binary(name)
        return binaries
    
    def _find_binary(self, name):
        """Search for a MySQL binary in common locations."""
        import shutil
        path = shutil.which(name)
        if path:
            return path
        common_paths = [
            f'/usr/sbin/{name}',
            f'/usr/bin/{name}',
            f'/usr/local/mysql/bin/{name}',
            f'/usr/local/bin/{name}',
        ]
        for p in common_paths:
            if os.path.isfile(p) and os.access(p, os.X_OK):
                return p
        return None
```

### 7.4 Data Directory Structure Differences

| Aspect | PostgreSQL | MySQL |
|--------|-----------|-------|
| **Init command** | `initdb -D {dir} --encoding=UTF8 --locale=C` | `mysqld --initialize-insecure --datadir={dir}` |
| **Config file** | `postgresql.conf` (inside data dir) | `my.cnf` (can be anywhere; we put it in data dir) |
| **Auto-config** | `postgresql.auto.conf` (ALTER SYSTEM writes here) | `mysqld-auto.cnf` (SET PERSIST writes here, MySQL 8.0+) |
| **PID file** | `postmaster.pid` | `mysql.pid` (configurable via `pid-file`) |
| **Socket** | Unix socket in `unix_socket_directories` | `mysql.sock` (configurable via `socket`) |
| **System tablespace** | `base/` directory | `ibdata1` file |
| **Redo log** | `pg_wal/` directory | `ib_logfile0`, `ib_logfile1` (or `#innodb_redo/` in 8.0.30+) |
| **System DB** | `pg_catalog` (inside each DB) | `mysql/` directory |
| **Auth config** | `pg_hba.conf`, `pg_ident.conf` | Via `mysql.user` table (no separate config file) |
| **Version indicator** | `PG_VERSION` file | No explicit file; `mysql_upgrade_info` sometimes present |

### 7.5 Infrastructure Directories

| Current | MySQL Equivalent | Change |
|---------|-----------------|--------|
| `pg_instances/` | `db_instances/postgresql/` and `db_instances/mysql/` | Rename and restructure |
| `pg_snapshots/` | `db_snapshots/postgresql/` and `db_snapshots/mysql/` | Rename and restructure |

**Note:** This directory rename is a breaking change — existing paths in configs and scripts must be updated.

---

## 8. Phase R5 — MySQL Knob Application

### 8.1 Applying Knobs

```python
class MySQLAdapter(DatabaseAdapter):
    
    def apply_knob_persistent(self, conn, name, value):
        """SET PERSIST (MySQL 8.0+) — survives restart.
        
        Equivalent to PostgreSQL's ALTER SYSTEM SET.
        Writes to mysqld-auto.cnf AND takes effect immediately.
        """
        cursor = conn.cursor()
        # Must use prepared statement carefully — SET PERSIST doesn't support
        # parameterized queries in the same way. Value must be properly formatted.
        formatted_value = self._format_value(name, value)
        cursor.execute(f"SET PERSIST `{name}` = {formatted_value}")
        conn.commit()
    
    def apply_knob_runtime(self, conn, name, value):
        """SET GLOBAL — immediate effect, lost on restart.
        
        Key difference from PG: this takes effect IMMEDIATELY,
        no need for pg_reload_conf() equivalent.
        """
        cursor = conn.cursor()
        formatted_value = self._format_value(name, value)
        cursor.execute(f"SET GLOBAL `{name}` = {formatted_value}")
        conn.commit()
    
    def reset_knob(self, conn, name):
        """RESET PERSIST — remove from mysqld-auto.cnf."""
        cursor = conn.cursor()
        cursor.execute(f"RESET PERSIST `{name}`")
        conn.commit()
    
    def reload_config(self, conn):
        """No-op for MySQL: SET GLOBAL takes effect immediately.
        
        PostgreSQL needs SELECT pg_reload_conf() after ALTER SYSTEM.
        MySQL does not need this step.
        """
        pass
    
    def get_current_knob_value(self, conn, name):
        cursor = conn.cursor()
        cursor.execute("SHOW GLOBAL VARIABLES LIKE %s", (name,))
        row = cursor.fetchone()
        return row[1] if row else None
    
    def _format_value(self, name, value):
        """Format a value for SET GLOBAL/PERSIST.
        
        MySQL requires ON/OFF for boolean, numeric for numbers,
        quoted strings for string values.
        """
        if isinstance(value, bool):
            return 'ON' if value else 'OFF'
        elif isinstance(value, (int, float)):
            return str(value)
        else:
            return f"'{value}'"
```

### 8.2 Key Differences in Knob Application

| Aspect | PostgreSQL | MySQL | Impact |
|--------|-----------|-------|--------|
| **Persistent apply** | `ALTER SYSTEM SET` → writes to `postgresql.auto.conf` → requires `pg_reload_conf()` or restart to take effect | `SET PERSIST` → writes to `mysqld-auto.cnf` AND takes immediate effect | MySQL is simpler — no reload step needed |
| **Runtime apply** | `SET {name} = value` → session only | `SET GLOBAL {name} = value` → affects all NEW connections | Different scope semantics |
| **Restart-required** | Knobs with `context='postmaster'` need full restart | Static variables need full restart | Same concept, different detection |
| **Reset** | `ALTER SYSTEM RESET {name}` | `RESET PERSIST {name}` or `RESET PERSIST IF EXISTS {name}` | Similar |
| **Boolean values** | `'on'/'off'` as strings | `ON/OFF` or `1/0` | Minor formatting difference |
| **`optimizer_switch`** | N/A (PG uses individual `enable_*` booleans) | Single comma-delimited variable: `"index_merge=on,index_merge_union=on,..."` | **Fundamentally different model** — must be handled specially (see Section 8.3) |

### 8.3 Special Case: MySQL `optimizer_switch`

PostgreSQL has ~15 separate boolean knobs for optimizer behavior:
- `enable_seqscan`, `enable_indexscan`, `enable_bitmapscan`, `enable_hashjoin`, `enable_mergejoin`, `enable_nestloop`, etc.

MySQL packs equivalent flags into a **single string variable** `optimizer_switch`:
```
index_merge=on,index_merge_union=on,index_merge_sort_union=on,
index_merge_intersection=on,engine_condition_pushdown=on,
index_condition_pushdown=on,mrr=on,mrr_cost_based=on,
block_nested_loop=on,batched_key_access=off,
materialization=on,semijoin=on,loosescan=on,...
```

**This requires special handling in the knob system:**

1. **Option A: Treat each sub-flag as a separate knob.** In `MYSQL_KNOB_TUNING_METADATA`, define `optimizer_switch.index_merge`, `optimizer_switch.block_nested_loop`, etc. The apply logic must:
   - Read current `optimizer_switch` value
   - Parse the comma-delimited string
   - Modify the specific flag
   - Write back the entire string via `SET GLOBAL optimizer_switch = "..."`

2. **Option B: Treat `optimizer_switch` as a single knob.** Have the knob space model it as an enum with possible values being different combinations. This is impractical due to combinatorial explosion.

**Recommendation: Option A.** Define each optimizer flag as a virtual knob. The adapter handles the parse/modify/write cycle transparently.

---

## 9. Phase R6 — MySQL Benchmark Integration

### 9.1 Sysbench

Sysbench natively supports MySQL (it was originally designed for MySQL). Changes are minimal:

```python
class MySQLAdapter(DatabaseAdapter):
    
    def get_sysbench_driver_flags(self, config):
        """Return Sysbench CLI flags for this DBMS."""
        return [
            "--db-driver=mysql",
            f"--mysql-host={config.host}",
            f"--mysql-port={config.port}",
            f"--mysql-user={config.user}",
            f"--mysql-password={config.password}",
            f"--mysql-db={config.dbname}",
        ]
```

**Current PG flags in `SysbenchExecutor._build_base_cmd()` (L133-145):**
```python
"--db-driver=pgsql",
f"--pgsql-host={db_config.host}",
...
```

**Change:** `SysbenchExecutor` gets the driver flags from the adapter.

**VACUUM → ANALYZE:** After Sysbench prepare, PG runs `VACUUM ANALYZE sbtest{i}`. MySQL equivalent: `ANALYZE TABLE sbtest{i}`. This is handled by `adapter.vacuum_equivalent()`.

**Validation query:** Current uses `table_schema = 'public'` (PG-specific). MySQL: `table_schema = '{dbname}'`.

### 9.2 TPC-H

**Schema (`schema.sql`):** Mostly ANSI SQL compatible. One change needed:
- `VARCHAR`, `CHAR`, `INTEGER`, `DECIMAL`, `DATE` — all MySQL-compatible ✓
- If `SERIAL` is used anywhere → `INT AUTO_INCREMENT` for MySQL
- `DROP TABLE IF EXISTS ... CASCADE` → MySQL doesn't support `CASCADE` on `DROP TABLE` (just `DROP TABLE IF EXISTS`)

**Data loading:** This is the biggest difference.

| PostgreSQL | MySQL |
|-----------|-------|
| `psycopg2.copy_expert("COPY {table} FROM STDIN WITH (FORMAT CSV, DELIMITER '\|')", file)` | `LOAD DATA LOCAL INFILE '{file}' INTO TABLE {table} FIELDS TERMINATED BY '\|' LINES TERMINATED BY '\n'` |

```python
class MySQLAdapter(DatabaseAdapter):
    
    def bulk_load(self, conn, table_name, file_path, delimiter='|'):
        """MySQL bulk load using LOAD DATA LOCAL INFILE.
        
        Requires: mysql.connector with allow_local_infile=True in connection,
        and MySQL server with local_infile=ON.
        """
        cursor = conn.cursor()
        cursor.execute(f"""
            LOAD DATA LOCAL INFILE '{file_path}'
            INTO TABLE `{table_name}`
            FIELDS TERMINATED BY '{delimiter}'
            LINES TERMINATED BY '\\n'
        """)
        conn.commit()
```

**Security note:** `LOAD DATA LOCAL INFILE` requires:
- `local_infile=1` on MySQL server
- `allow_local_infile=True` in `mysql.connector.connect()` call

**TPC-H queries — dialect differences:**

| Query | PostgreSQL Syntax | MySQL Syntax |
|-------|-------------------|-------------|
| 22 | `SUBSTRING(c_phone FROM 1 FOR 2)` | `SUBSTRING(c_phone, 1, 2)` |
| Various | `::float` / `::numeric` cast | `CAST(... AS DECIMAL(15,2))` |
| Various | `INTERVAL '1 year'` | `INTERVAL 1 YEAR` (same in MySQL ✓) |
| Various | `EXTRACT(YEAR FROM ...)` | `EXTRACT(YEAR FROM ...)` (same ✓) |

**Implementation:** Either maintain two sets of query files (`tpch/queries/postgresql/` and `tpch/queries/mysql/`) or use Jinja2-style templating. **Recommendation: two sets of files** — simpler, more maintainable, and only ~2-3 queries actually differ.

**VACUUM ANALYZE → ANALYZE TABLE:** After loading each TPC-H table, PG runs `VACUUM ANALYZE {table}`. MySQL: `ANALYZE TABLE {table}`.

---

## 10. Phase R7 — MySQL Metrics & Evaluator

### 10.1 Statistics Reset

```python
class MySQLAdapter(DatabaseAdapter):
    
    def reset_stats(self, conn):
        """Equivalent of SELECT pg_stat_reset() in PostgreSQL."""
        cursor = conn.cursor()
        cursor.execute("FLUSH STATUS")
        conn.commit()
```

### 10.2 Cache Hit Ratio

```python
class MySQLAdapter(DatabaseAdapter):
    
    def get_cache_hit_ratio(self, conn):
        """Read InnoDB buffer pool hit ratio.
        
        PostgreSQL: blks_hit / (blks_hit + blks_read) from pg_stat_database
        MySQL: Innodb_buffer_pool_read_requests / 
               (Innodb_buffer_pool_read_requests + Innodb_buffer_pool_reads)
        """
        cursor = conn.cursor()
        cursor.execute("""
            SHOW GLOBAL STATUS 
            WHERE Variable_name IN (
                'Innodb_buffer_pool_read_requests',
                'Innodb_buffer_pool_reads'
            )
        """)
        stats = {row[0]: int(row[1]) for row in cursor.fetchall()}
        
        requests = stats.get('Innodb_buffer_pool_read_requests', 0)
        reads = stats.get('Innodb_buffer_pool_reads', 0)
        
        total = requests + reads
        if total == 0:
            return 0.0
        return requests / total
```

### 10.3 I/O Statistics

```python
class MySQLAdapter(DatabaseAdapter):
    
    def get_io_stats(self, conn):
        """Read I/O statistics from MySQL global status.
        
        PostgreSQL: blks_read, blks_hit, tup_returned, tup_fetched, etc. from pg_stat_database
        MySQL: Innodb_data_reads, Innodb_data_writes, Innodb_rows_read, etc.
        """
        cursor = conn.cursor()
        cursor.execute("""
            SHOW GLOBAL STATUS 
            WHERE Variable_name IN (
                'Innodb_data_reads',
                'Innodb_data_writes',
                'Innodb_data_read',
                'Innodb_data_written',
                'Innodb_rows_read',
                'Innodb_rows_inserted',
                'Innodb_rows_updated',
                'Innodb_rows_deleted',
                'Innodb_buffer_pool_read_requests',
                'Innodb_buffer_pool_reads'
            )
        """)
        stats = {row[0]: int(row[1]) for row in cursor.fetchall()}
        
        return {
            'data_reads': stats.get('Innodb_data_reads', 0),
            'data_writes': stats.get('Innodb_data_writes', 0),
            'bytes_read': stats.get('Innodb_data_read', 0),
            'bytes_written': stats.get('Innodb_data_written', 0),
            'rows_read': stats.get('Innodb_rows_read', 0),
            'rows_inserted': stats.get('Innodb_rows_inserted', 0),
            'rows_updated': stats.get('Innodb_rows_updated', 0),
            'rows_deleted': stats.get('Innodb_rows_deleted', 0),
        }
```

### 10.4 Other Adapter Methods

```python
class MySQLAdapter(DatabaseAdapter):
    
    def vacuum_equivalent(self, conn, table_name):
        """MySQL doesn't need VACUUM (InnoDB purges automatically).
        ANALYZE TABLE updates index statistics."""
        cursor = conn.cursor()
        cursor.execute(f"ANALYZE TABLE `{table_name}`")
    
    def get_backend_pid(self, conn):
        cursor = conn.cursor()
        cursor.execute("SELECT CONNECTION_ID()")
        return cursor.fetchone()[0]
    
    def user_exists(self, conn, username):
        cursor = conn.cursor()
        cursor.execute("SELECT User FROM mysql.user WHERE User = %s", (username,))
        return cursor.fetchone() is not None
    
    def create_user(self, conn, username, password, superuser=False):
        cursor = conn.cursor()
        cursor.execute(
            f"CREATE USER IF NOT EXISTS '{username}'@'%%' IDENTIFIED BY %s",
            (password,)
        )
        if superuser:
            cursor.execute(f"GRANT ALL PRIVILEGES ON *.* TO '{username}'@'%%' WITH GRANT OPTION")
        cursor.execute("FLUSH PRIVILEGES")
        conn.commit()
    
    def is_in_recovery(self, conn):
        """MySQL standalone does not have a recovery mode concept like PG.
        MySQL replication has a 'slave' mode but it's not equivalent."""
        return False
```

### 10.5 Process Detection

Current code in `evaluator.py` (L1075-1090) checks for `'postgres' in proc_name.lower()` to find the postmaster PID. MySQL equivalent: `'mysqld' in proc_name.lower()`.

The adapter's `get_process_name()` returns `'mysqld'`, so the evaluator uses `adapter.get_process_name()` for process detection.

### 10.6 Block Size Conversion

PostgreSQL uses 8kB blocks internally. When computing I/O in MB from `blks_read`:
```python
io_mb = blocks_read_delta * 8 / 1024.0  # 8kB blocks → MB
```

MySQL's `Innodb_data_read` is already in **bytes**:
```python
io_mb = bytes_read_delta / (1024.0 * 1024.0)  # bytes → MB
```

This conversion logic must be in the adapter, not in the evaluator.

---

## 11. Phase R8 — MySQL Knob Metadata Research

This is the most research-intensive phase. MySQL does NOT expose min/max values for its system variables through any system table. All tuning ranges must be manually researched.

### 11.1 Research Requirements Per Knob

For each MySQL knob to include in `MYSQL_KNOB_TUNING_METADATA`, research and document:

1. **Variable name** — exact MySQL system variable name
2. **Variable type** — integer, real, boolean, enum, string
3. **MySQL native min/max** — from official documentation (not exposed via SQL)
4. **Safe tuning min/max** — curated ranges for production tuning
5. **Default value** — MySQL's compiled-in default
6. **Unit** — bytes, seconds, count, etc.
7. **Requires restart?** — dynamic or static variable
8. **Scale** — linear or log
9. **Impact tier** — minimal, core, standard, extensive
10. **Tuning priority** — 1-5
11. **PostgreSQL equivalent** — for cross-DBMS documentation
12. **Notes** — interactions, gotchas, version-specific behavior

### 11.2 MySQL Knobs to Research (by category)

#### InnoDB Buffer Pool & Memory (CRITICAL — highest impact)

| MySQL Variable | PG Equivalent | Priority | Notes |
|---|---|---|---|
| `innodb_buffer_pool_size` | `shared_buffers` | 1 | Default 128MB. Typically 50-80% of RAM. Dynamic in MySQL 8.0 (can resize without restart!). Size in bytes. |
| `innodb_buffer_pool_instances` | N/A | 2-3 | Number of buffer pool regions. Reduces contention. Default 8 if pool > 1GB. |
| `innodb_log_file_size` | `max_wal_size` (related) | 2 | Redo log file size. Larger = better write performance, longer recovery. |
| `innodb_log_buffer_size` | `wal_buffers` | 2 | Buffer for redo log writes before flush. Default 16MB. |
| `innodb_change_buffer_max_size` | N/A | 3 | % of buffer pool for change buffering. 0-50, default 25. |
| `sort_buffer_size` | `work_mem` (partial) | 1-2 | Per-session buffer for sorts. Default 256KB. |
| `join_buffer_size` | `work_mem` (partial) | 1-2 | Per-join buffer. Default 256KB. |
| `read_buffer_size` | N/A | 3 | Sequential scan buffer. Default 128KB. |
| `read_rnd_buffer_size` | N/A | 3 | Random read buffer (after sort). Default 256KB. |
| `tmp_table_size` | `temp_buffers` (related) | 2-3 | Max in-memory temp table size. |
| `max_heap_table_size` | N/A | 3 | Max size for MEMORY tables. Related to `tmp_table_size`. |
| `key_buffer_size` | N/A | 3-4 | MyISAM index cache. Irrelevant if only using InnoDB. |
| `table_open_cache` | N/A | 3 | Number of open table descriptors. Default 4000. |
| `table_definition_cache` | N/A | 3-4 | Number of cached table definitions. Default 2000 (auto). |

#### InnoDB I/O & Flushing

| MySQL Variable | PG Equivalent | Priority | Notes |
|---|---|---|---|
| `innodb_io_capacity` | `effective_io_concurrency` (related) | 2 | I/O ops/sec for background tasks. Default 200. Set to IOPS of storage. |
| `innodb_io_capacity_max` | N/A | 3 | Max I/O ops/sec. Default 2000. |
| `innodb_read_io_threads` | `effective_io_concurrency` (partial) | 3 | Number of read I/O threads. Default 4. |
| `innodb_write_io_threads` | N/A | 3 | Number of write I/O threads. Default 4. |
| `innodb_flush_method` | N/A | 2-3 | `O_DIRECT`, `O_DSYNC`, `fsync`. Default `fsync`. `O_DIRECT` recommended. |
| `innodb_flush_log_at_trx_commit` | `synchronous_commit` (related) | 1-2 | 0, 1, 2. Default 1 (ACID). 2 = flush every second (better perf, tiny data loss risk). |
| `innodb_doublewrite` | `full_page_writes` (related) | 3 | ON/OFF. Default ON. Disabling improves writes on SSD. |
| `innodb_flush_neighbors` | N/A | 3 | 0, 1, 2. Default 0 in MySQL 8.0. Irrelevant for SSD. |
| `sync_binlog` | N/A | 2-3 | Sync binary log every N commits. Default 1. 0 = fastest. |

#### InnoDB Concurrency & Threading

| MySQL Variable | PG Equivalent | Priority | Notes |
|---|---|---|---|
| `innodb_thread_concurrency` | N/A | 2-3 | 0 = unlimited. Default 0. |
| `innodb_concurrency_tickets` | N/A | 4 | Tickets before re-entering concurrency queue. Default 5000. |
| `innodb_spin_wait_delay` | N/A | 4 | Spin wait delay for mutex. Default 6. |
| `innodb_lock_wait_timeout` | `lock_timeout` | 3 | Seconds to wait for row lock. Default 50. |

#### Connections

| MySQL Variable | PG Equivalent | Priority | Notes |
|---|---|---|---|
| `max_connections` | `max_connections` | 2 | Default 151. Each connection uses ~1MB of memory. |
| `thread_cache_size` | N/A | 3 | Cached threads for reuse. Default 9 (auto in MySQL 8.0). |
| `max_connect_errors` | N/A | 4 | Host blocked after this many connection errors. Default 100. |
| `back_log` | N/A | 4 | Connection queue size. Default 151. |

#### Query Optimizer

| MySQL Variable | PG Equivalent | Priority | Notes |
|---|---|---|---|
| `optimizer_switch` | `enable_seqscan`, `enable_hashjoin`, etc. (split) | 2-3 | **SPECIAL CASE** — single comma-delimited string. See Section 8.3. |
| `optimizer_search_depth` | N/A | 4 | Join optimization search depth. 0 = auto. Default 62. |
| `optimizer_prune_level` | N/A | 4 | Heuristic pruning. Default 1. |
| `eq_range_index_dive_limit` | N/A | 4 | Switch from index dives to index statistics. Default 200. |
| `range_optimizer_max_mem_size` | N/A | 4 | Memory limit for range optimizer. Default 8MB. |

#### Query Execution

| MySQL Variable | PG Equivalent | Priority | Notes |
|---|---|---|---|
| `max_length_for_sort_data` | N/A | 4 | Deprecated in MySQL 8.0.20. |
| `max_sort_length` | N/A | 4 | Bytes compared in ORDER BY. Default 1024. |
| `group_concat_max_len` | N/A | 4-5 | Max length for GROUP_CONCAT. Default 1024. |
| `sql_mode` | N/A | 5 | SQL mode flags. Not a tuning parameter per se. |

#### Logging & Binary Log

| MySQL Variable | PG Equivalent | Priority | Notes |
|---|---|---|---|
| `slow_query_log` | `log_min_duration_statement` (related) | 4 | ON/OFF. Default OFF. |
| `long_query_time` | `log_min_duration_statement` | 4 | Threshold in seconds. Default 10. |
| `binlog_cache_size` | N/A | 3-4 | Binary log cache per transaction. Default 32KB. |
| `binlog_format` | N/A | 3-4 | ROW, STATEMENT, MIXED. Default ROW. |
| `expire_logs_days` | N/A | 5 | Auto-purge binary logs. Default 0 (never). |

#### Replication (if applicable)

| MySQL Variable | PG Equivalent | Priority | Notes |
|---|---|---|---|
| `innodb_parallel_read_threads` | `max_parallel_workers_per_gather` (related) | 3 | Parallel reads for CHECK TABLE and SELECT COUNT(*). Default 4. MySQL 8.0.14+. |

### 11.3 Research Sources

1. **MySQL 8.0 Reference Manual — Server System Variables:**  
   https://dev.mysql.com/doc/refman/8.0/en/server-system-variables.html

2. **MySQL 8.0 Reference Manual — InnoDB Startup Configuration:**  
   https://dev.mysql.com/doc/refman/8.0/en/innodb-init-startup-configuration.html

3. **Percona — MySQL Performance Tuning:**  
   Search for Percona's tuning guides for production-tested safe ranges.

4. **MySQL Tuning Primer Script** (open source) — provides heuristic ranges.

5. **mysqltuner.pl** — Perl script that analyzes a running MySQL instance and recommends settings.

6. **Academic papers:** CDBTune (SIGMOD 2019) includes MySQL knob ranges for their experiments. OtterTune also has MySQL configurations in their open-source repository.

### 11.4 Expected Output

A file `src/knobs/mysql_knob_metadata.py` with:
- `MYSQL_KNOB_TUNING_METADATA: Dict[str, TuningMetadata]` — ~60-100 entries
- `MYSQL_IMPACT_TIERS: Dict[str, Optional[List[str]]]` — 4 tiers

**Data-driven tier redefinition (Issue P) applies here too.** Initial tiers are expert-defined (from research). After extensive-tier PBT runs on MySQL, fANOVA analysis would redefine them data-driven, just as for PostgreSQL.

---

## 12. Phase R9 — CLI, Config & Orchestrator

### 12.1 CLI Changes

Add `--dbms` flag to the main CLI entry point:

```python
# In argument parser (main.py or CLI module)
parser.add_argument(
    '--dbms', 
    choices=['postgresql', 'mysql'],
    default='postgresql',
    help='Target DBMS (default: postgresql)'
)
```

### 12.2 Orchestrator Changes (`src/tuners/pbt/tuner.py`)

**Current** (L203-210):
```python
self.instance_manager = PostgresInstanceManager(
    base_dir=Path(f'./pg_instances/{self.benchmark_name}'),
    base_port=5440,
    ...
)
```

**After refactoring:**
```python
from src.database.adapter_factory import create_adapter

# In __init__:
self.adapter = create_adapter(self.config.dbms)
self.instance_manager = GenericInstanceManager(
    adapter=self.adapter,
    base_dir=Path(f'./db_instances/{self.config.dbms}/{self.benchmark_name}'),
    base_port=self.adapter.default_port() + 8,  # Offset to avoid conflict with system instance
    ...
)
```

### 12.3 Results JSON — DBMS Field

Add `"dbms": "postgresql"` or `"dbms": "mysql"` to the results JSON output. This pairs with the existing `"knob_tier"` field (Task 2.6) and `"system_info"` (Task 2.1):

```json
{
    "tuning_session": {
        "dbms": "mysql",
        "knob_tier": "extensive",
        "workload": "sysbench",
        ...
    }
}
```

### 12.4 Environment Variables

| Variable | Default (PG) | Default (MySQL) |
|----------|-------------|----------------|
| `PBT_DBMS` | `postgresql` | `mysql` |
| `DB_PORT` | `5432` | `3306` |
| `DB_USER` | `postgres` | `root` |
| `DB_PASSWORD` | (from `.env`) | (from `.env`) |

### 12.5 Knob Loader — DBMS-Aware CSV Path

```python
# In knob_loader.py
def load_knobs_for_tier(tier: str, dbms: str = "postgresql"):
    csv_dir = Path(f"data/tuner_knobs/{dbms}")
    csv_path = csv_dir / f"{tier}_knobs.csv"
    ...
```

**Data directory restructure:**
```
data/tuner_knobs/
  postgresql/
    minimal_knobs.csv
    core_knobs.csv
    standard_knobs.csv
    extensive_knobs.csv
  mysql/
    minimal_knobs.csv
    core_knobs.csv
    standard_knobs.csv
    extensive_knobs.csv
```

---

## 13. Phase R10 — Testing

### 13.1 Adapter Interface Tests

```
tests/unit/database/test_adapter_interface.py
```
- Test that `PostgreSQLAdapter` and `MySQLAdapter` both implement all abstract methods
- Test factory function returns correct adapter type
- Test default port/user values

### 13.2 MySQL-Specific Unit Tests

```
tests/unit/database/test_mysql_adapter.py          — connection, management
tests/unit/knobs/test_mysql_knob_metadata.py        — all MySQL metadata entries
tests/unit/knobs/test_mysql_preprocess_knobs.py     — MySQL pipeline
tests/unit/utils/test_mysql_instance_manager.py     — init, start, stop
tests/unit/utils/test_mysql_applicator.py           — SET GLOBAL/PERSIST
tests/unit/benchmarks/test_mysql_sysbench.py        — MySQL Sysbench flags
tests/unit/benchmarks/test_mysql_tpch.py            — LOAD DATA, ANALYZE TABLE
tests/unit/evaluator/test_mysql_metrics.py          — InnoDB stats, cache hit ratio
```

### 13.3 Integration Tests

```
tests/integration/test_mysql_pbt_pipeline.py  — full PBT run against MySQL
```

Requires a running MySQL instance (can use Docker: `docker run mysql:8.0`).

### 13.4 Cross-DBMS Tests

```
tests/integration/test_cross_dbms.py
```
- Verify same scoring formula produces comparable scores across PG and MySQL
- Verify results JSON format is consistent

---

## 14. Phase R11 — Documentation

### 14.1 Files to Create/Update

| File | Content |
|---|---|
| `docs/MYSQL_SETUP.md` (new) | MySQL installation, configuration, environment setup for PBT |
| `docs/ENVIRONMENT_SETUP.md` (update) | Add MySQL setup instructions alongside PostgreSQL |
| `docs/CONFIGURATION_MANAGEMENT.md` (update) | Document `--dbms` flag, MySQL-specific config |
| `README.md` (update) | List MySQL as supported DBMS |
| `requirements.txt` (update) | Add `mysql-connector-python` |

### 14.2 MySQL Setup Guide Contents

1. Install MySQL 8.0+ (apt, yum, brew, Docker)
2. Configure `local_infile=ON` for TPC-H data loading
3. Create PBT user and database
4. Set environment variables (`PBT_DBMS=mysql`, `DB_PORT=3306`, etc.)
5. Run knob preprocessing pipeline: `python -m src.knobs --dbms mysql`
6. Run PBT: `python -m src.tuner --dbms mysql --workload sysbench`

---

## 15. Conceptual Differences: PostgreSQL vs MySQL

### 15.1 The Full Reference Table

| Concept | PostgreSQL | MySQL | Adapter Handles? |
|---------|-----------|-------|-----------------|
| **Driver library** | `psycopg2` | `mysql-connector-python` | Yes — `connect()` |
| **Admin database** | `postgres` | `mysql` | Yes — `get_admin_connection()` |
| **Connection URL** | `postgresql://...` | `mysql+mysqlconnector://...` | Yes — `get_sqlalchemy_url()` |
| **Identifier quoting** | `"double_quotes"` | `` `backticks` `` | Yes — `identifier_quote()` |
| **Default port** | 5432 | 3306 | Yes — `default_port()` |
| **Default admin user** | `postgres` | `root` | Yes — `default_admin_user()` |
| **User/role check** | `pg_roles` | `mysql.user` | Yes — `user_exists()` |
| **User creation** | `CREATE USER ... WITH SUPERUSER PASSWORD` | `CREATE USER ... IDENTIFIED BY` + `GRANT ALL` | Yes — `create_user()` |
| **Database check** | `pg_database` | `INFORMATION_SCHEMA.SCHEMATA` | Yes — `database_exists()` |
| **Kill connections** | `pg_terminate_backend()` | `KILL {CONNECTION_ID}` per processlist | Yes — `drop_database()` |
| **Config application (persistent)** | `ALTER SYSTEM SET` + `pg_reload_conf()` | `SET PERSIST` (immediate, MySQL 8.0+) | Yes — `apply_knob_persistent()` + `reload_config()` |
| **Config application (runtime)** | `SET {name} = value` (session) | `SET GLOBAL {name} = value` (all new sessions) | Yes — `apply_knob_runtime()` |
| **Config reset** | `ALTER SYSTEM RESET` | `RESET PERSIST` | Yes — `reset_knob()` |
| **Config file** | `postgresql.conf`, `postgresql.auto.conf` | `my.cnf`, `mysqld-auto.cnf` | Yes — `write_config_file()` / `get_excluded_snapshot_files()` |
| **Restart detection** | `context = 'postmaster'` | Static variable (not dynamically settable) | Yes — via `requires_restart` in metadata |
| **Stats reset** | `pg_stat_reset()` | `FLUSH STATUS` | Yes — `reset_stats()` |
| **Cache hit ratio** | `pg_stat_database.blks_hit / (blks_hit + blks_read)` | `Innodb_buffer_pool_read_requests / (requests + reads)` | Yes — `get_cache_hit_ratio()` |
| **I/O stats** | `pg_stat_database` (block-based, 8kB blocks) | `SHOW GLOBAL STATUS` (byte-based) | Yes — `get_io_stats()` |
| **Backend PID** | `pg_backend_pid()` | `CONNECTION_ID()` | Yes — `get_backend_pid()` |
| **VACUUM** | `VACUUM ANALYZE` (required for MVCC cleanup) | Not needed (InnoDB auto-purge). `ANALYZE TABLE` updates stats. | Yes — `vacuum_equivalent()` |
| **Bulk load** | `COPY FROM STDIN` (server-side, psycopg2 `copy_expert`) | `LOAD DATA LOCAL INFILE` (client-side, requires `local_infile`) | Yes — `bulk_load()` |
| **Data directory init** | `initdb -D ... --encoding=UTF8 --locale=C` | `mysqld --initialize-insecure --datadir=...` | Yes — `initialize_data_directory()` |
| **Instance start** | `pg_ctl start -D ... -l logfile` | `mysqld --defaults-file=my.cnf &` | Yes — `start_instance()` |
| **Instance stop** | `pg_ctl stop -D ... -m fast` | `mysqladmin shutdown` or `kill -SIGTERM` | Yes — `stop_instance()` |
| **Data dir validation** | Check `PG_VERSION`, `base/`, `postgresql.conf` | Check `ibdata1`, `mysql/` | Yes — `validate_data_directory()` |
| **Process name** | `postgres` | `mysqld` | Yes — `get_process_name()` |
| **Data dir query** | `SHOW data_directory` | `SHOW VARIABLES LIKE 'datadir'` | Yes — `detect_data_directory()` |
| **Recovery check** | `pg_is_in_recovery()` | N/A (standalone) | Yes — `is_in_recovery()` |
| **Binaries** | `pg_ctl`, `initdb`, `psql`, `pg_dump` | `mysqld`, `mysqladmin`, `mysql`, `mysqldump` | Yes — `find_binaries()` |
| **Block size** | 8kB blocks (`blks_read * 8 / 1024` → MB) | Raw bytes (`data_read / 1048576` → MB) | Yes — in `get_io_stats()` conversion |
| **Optimizer flags** | ~15 separate `enable_*` booleans | Single `optimizer_switch` string | Special handling needed (Section 8.3) |
| **Memory model** | `work_mem` = per-sort node | `sort_buffer_size` + `join_buffer_size` = split | Different knob names in metadata |
| **Autovacuum** | `autovacuum_*` (~9 knobs) | No equivalent (InnoDB auto-purge) | MySQL simply doesn't have these knobs |
| **Knob metadata richness** | `pg_settings`: 13 columns (min, max, unit, type, context, boot_val, reset_val, enumvals, description) | `SHOW VARIABLES`: 2 columns (name, value). `performance_schema.variables_info`: limited extra info | MySQL metadata must be hardcoded |
| **Sysbench flags** | `--db-driver=pgsql`, `--pgsql-*` | `--db-driver=mysql`, `--mysql-*` | Adapter provides flag list |
| **TPC-H COPY** | `COPY table FROM STDIN WITH (FORMAT CSV, DELIMITER '\|')` | `LOAD DATA LOCAL INFILE ... FIELDS TERMINATED BY '\|'` | Adapter's `bulk_load()` |
| **TPC-H SUBSTRING** | `SUBSTRING(col FROM 1 FOR 2)` | `SUBSTRING(col, 1, 2)` | Separate query files |
| **DROP CASCADE** | `DROP TABLE IF EXISTS ... CASCADE` | `DROP TABLE IF EXISTS ...` (no CASCADE) | Schema file variant |

---

## 16. File-by-File Change Map

### 16.1 New Files to Create

| # | File Path | Description | Phase |
|---|-----------|-------------|-------|
| 1 | `src/database/adapter.py` | `DatabaseAdapter` ABC | R1 |
| 2 | `src/database/postgresql_adapter.py` | `PostgreSQLAdapter(DatabaseAdapter)` | R1 |
| 3 | `src/database/mysql_adapter.py` | `MySQLAdapter(DatabaseAdapter)` | R2-R7 |
| 4 | `src/database/adapter_factory.py` | Factory function `create_adapter()` | R1 |
| 5 | `src/knobs/mysql_knob_metadata.py` | MySQL knob tuning metadata (~100 entries) | R3, R8 |
| 6 | `src/benchmarks/tpch/queries/mysql/` (dir) | MySQL-dialect TPC-H queries (22 files) | R6 |
| 7 | `docs/MYSQL_SETUP.md` | MySQL setup documentation | R11 |
| 8 | `data/tuner_knobs/mysql/*.csv` | MySQL tier CSV files (4 files) | R3 |
| 9 | `tests/unit/database/test_mysql_adapter.py` | MySQL adapter unit tests | R10 |
| 10 | `tests/unit/knobs/test_mysql_knob_metadata.py` | MySQL metadata tests | R10 |

### 16.2 Existing Files to Modify

| # | File Path | Change Type | Phase |
|---|-----------|-------------|-------|
| 1 | `src/database/connection.py` | Wrap with adapter; keep for PG backward compat | R1 |
| 2 | `src/database/management.py` | Wrap with adapter; keep for PG backward compat | R1 |
| 3 | `src/database/data_loader.py` | Use adapter's SQLAlchemy URL | R1 |
| 4 | `src/database/__init__.py` | Export adapter + factory | R1 |
| 5 | `src/config/database.py` | Add `dbms` field, DBMS-aware defaults/URLs | R2 |
| 6 | `src/knobs/retrieval.py` | Use adapter for SQL queries instead of `pg_settings` | R1, R3 |
| 7 | `src/knobs/knob_metadata.py` | No change (stays PG-specific, adapter selects correct module) | — |
| 8 | `src/knobs/preprocess_knobs.py` | Accept `--dbms` flag, use correct metadata module | R3 |
| 9 | `src/knobs/__init__.py` | Export DBMS-agnostic interface | R1 |
| 10 | `src/tuner/utils/applicator.py` | Use adapter for `apply_knob_*`, `get_knob_metadata` | R1, R5 |
| 11 | `src/tuner/utils/restart_manager.py` | Use adapter for `stop/start_instance`, `detect_data_directory` | R1, R4 |
| 12 | `src/tuner/utils/instance_manager.py` | Use adapter for `initialize_data_directory`, `write_config_file`, binaries | R1, R4 |
| 13 | `src/tuner/utils/postgres_instance.py` | Use adapter; possibly rename to `db_instance.py` | R1 |
| 14 | `src/tuner/utils/snapshot_manager.py` | Use `adapter.get_excluded_snapshot_files()` | R1 |
| 15 | `src/tuner/evaluator/evaluator.py` | Use adapter for stats, VACUUM, cache hit, process detection | R1, R7 |
| 16 | `src/benchmarks/sysbench/executor.py` | Get driver flags from adapter; use `adapter.vacuum_equivalent()` | R1, R6 |
| 17 | `src/benchmarks/tpch/executor.py` | Use `adapter.bulk_load()`, `adapter.vacuum_equivalent()`; DBMS-specific query dir | R1, R6 |
| 18 | `src/knobs/knob_loader.py` | DBMS-aware CSV path; MySQL type mapping | R3 |
| 19 | `src/tuners/pbt/population.py` | Use generic interface instead of `PostgresInstanceManager` | R1 |
| 20 | `src/tuners/pbt/cli.py` | Accept `--dbms`, use adapter factory, DBMS-aware paths | R9 |
| 21 | `src/scripts/setup_database.py` | Use adapter for DDL | R9 |
| 22 | `src/scripts/cleanup_instances.py` | Use adapter | R9 |
| 23 | `src/scripts/analyze_knob_importance.py` | Use adapter for retrieval | R9 |
| 24 | `requirements.txt` | Add `mysql-connector-python` | R2 |
| 25 | `README.md` | Document MySQL support | R11 |
| 26 | `docs/ENVIRONMENT_SETUP.md` | Add MySQL setup | R11 |

### 16.3 Data Files to Restructure

| Current | Change | Phase |
|---------|--------|-------|
| `data/tuner_knobs/*.csv` | Move to `data/tuner_knobs/postgresql/*.csv` | R3 |
| `pg_instances/` | Rename to `db_instances/postgresql/` (or make DBMS-parameterized) | R4 |
| `pg_snapshots/` | Rename to `db_snapshots/postgresql/` (or make DBMS-parameterized) | R4 |
| `src/benchmarks/tpch/queries/*.sql` | Keep as-is (PG-compatible); add `mysql/` subdirectory | R6 |

---

## 17. MySQL Knob Quick Reference

### 17.1 Proposed MySQL Minimal Tier (Top ~5)

Based on DBA consensus and academic literature (CDBTune, OtterTune):

1. **`innodb_buffer_pool_size`** — Most impactful MySQL knob. Equivalent to PG's `shared_buffers`.
2. **`innodb_log_file_size`** — Redo log size. Critical for write workloads.
3. **`innodb_flush_log_at_trx_commit`** — Durability vs performance trade-off (0/1/2).
4. **`sort_buffer_size`** — Per-session sort memory.
5. **`innodb_io_capacity`** — Background I/O rate. Critical for SSD vs HDD.

### 17.2 Proposed MySQL Core Tier (additional ~8)

6. `join_buffer_size` — Per-join buffer
7. `innodb_buffer_pool_instances` — Reduce buffer pool contention
8. `innodb_log_buffer_size` — Redo log buffering
9. `max_connections` — Connection limit
10. `innodb_flush_method` — I/O method (O_DIRECT recommended)
11. `tmp_table_size` — In-memory temp table limit
12. `innodb_read_io_threads` — Read parallelism
13. `innodb_write_io_threads` — Write parallelism

### 17.3 Knobs With No PostgreSQL Equivalent

| MySQL | Why No PG Equivalent |
|---|---|
| `innodb_buffer_pool_instances` | PG doesn't partition shared_buffers |
| `innodb_flush_log_at_trx_commit` | PG has `synchronous_commit` but semantics differ |
| `innodb_doublewrite` | PG has `full_page_writes` (similar concept, different mechanism) |
| `innodb_change_buffer_max_size` | PG doesn't have change buffering |
| `key_buffer_size` | MyISAM-specific, no PG equivalent |
| `innodb_flush_neighbors` | PG doesn't have this concept |
| `thread_cache_size` | PG uses process-per-connection, no thread caching |

### 17.4 PostgreSQL Knobs With No MySQL Equivalent

| PostgreSQL | Why No MySQL Equivalent |
|---|---|
| `effective_cache_size` | PG planner hint; MySQL planner doesn't use this |
| `random_page_cost` / `seq_page_cost` | MySQL uses `optimizer_switch` flags + cost model differently |
| `cpu_tuple_cost` / `cpu_index_tuple_cost` | MySQL doesn't expose cost model constants |
| All `autovacuum_*` knobs (~9) | InnoDB purge is automatic, no user-facing knobs |
| `maintenance_work_mem` | MySQL doesn't separate maintenance vs query memory |
| All `enable_*` booleans (~15) | Packed into single `optimizer_switch` string |
| `default_statistics_target` | MySQL uses `innodb_stats_sample_pages` (different mechanism) |

---

## 18. Dependencies & Requirements

### 18.1 Software Requirements

| Requirement | Version | Notes |
|-------------|---------|-------|
| MySQL Server | 8.0+ | Required for `SET PERSIST`, `performance_schema.variables_info`, `LOAD DATA LOCAL INFILE` |
| `mysql-connector-python` | 8.0+ | Python MySQL driver |
| Sysbench | 1.0+ | Same binary supports both PG and MySQL |
| dbgen | Any | TPC-H data generator — database-agnostic |

### 18.2 Python Package Additions

```
# requirements.txt additions
mysql-connector-python>=8.0.0
```

### 18.3 MySQL Server Configuration Prerequisites

For the PBT system to work, the MySQL server must have:

```ini
[mysqld]
local_infile=ON                    # For LOAD DATA LOCAL INFILE (TPC-H loading)
performance_schema=ON              # For variables_info metadata
persisted-globals-load=ON          # For SET PERSIST support
```

---

## 19. Paper Framing (If Deferred as Future Work)

If MySQL support is NOT implemented, frame it in the paper's Future Work section:

> **Multi-DBMS Generalization.** While our current implementation targets PostgreSQL, the PBT optimization framework is inherently DBMS-agnostic — the evolutionary algorithm, scoring formula, and analysis tools (fANOVA, SHAP) operate on abstract (configuration, score) pairs with no PostgreSQL-specific assumptions. Supporting additional DBMS platforms such as MySQL requires implementing a database adapter layer that encapsulates DBMS-specific operations (connection management, knob application via `SET PERSIST`, instance lifecycle, and metrics collection via `SHOW GLOBAL STATUS`). The primary challenge lies not in the framework itself but in curating MySQL-specific knob metadata — unlike PostgreSQL's rich `pg_settings` catalog, MySQL does not expose min/max ranges or variable types through system tables, requiring manual research of ~100+ system variables. This architectural separation is documented and ready for implementation, making multi-DBMS support a natural extension of this work. Additionally, cross-DBMS knob importance analysis — comparing whether the same knobs dominate performance across different storage engines — presents an interesting research direction.

This framing:
1. Emphasizes the **framework is already DBMS-agnostic** (algorithm, scoring, fANOVA/SHAP)
2. Identifies the **concrete technical barrier** (MySQL metadata research, not architecture)
3. Positions it as a **natural extension**, not a limitation
4. Includes a **research angle** (cross-DBMS importance comparison) that could attract follow-up work

---

## Appendix A: MySQL 8.0 vs Earlier Versions

**MySQL 8.0 is the minimum required version** due to:

| Feature | MySQL 8.0 | MySQL 5.7 |
|---------|----------|----------|
| `SET PERSIST` | ✓ | ✗ (must edit my.cnf manually) |
| `RESET PERSIST` | ✓ | ✗ |
| `performance_schema.variables_info` | ✓ | ✗ (cannot determine static vs dynamic programmatically) |
| Dynamic `innodb_buffer_pool_size` resize | ✓ | Partial |
| `mysqld --initialize` | ✓ | ✓ |
| JSON data type in system tables | ✓ | ✗ |

Using MySQL < 8.0 would require:
- Editing `my.cnf` directly instead of `SET PERSIST` (fragile, file-locking issues)
- Hardcoding ALL dynamic/static classifications (no `variables_info` table)
- Significantly more manual configuration work

**Recommendation: Mandate MySQL 8.0+ and document this requirement.**

---

## Appendix B: Docker Quick-Start for MySQL Testing

```bash
# Start MySQL 8.0 container for development/testing
docker run -d \
    --name pbt-mysql \
    -e MYSQL_ROOT_PASSWORD=pbt_password \
    -e MYSQL_DATABASE=test_dataset \
    -p 3306:3306 \
    mysql:8.0 \
    --local-infile=ON \
    --performance-schema=ON \
    --persisted-globals-load=ON

# Verify connection
mysql -h 127.0.0.1 -P 3306 -u root -ppbt_password -e "SELECT VERSION()"

# Environment variables for PBT
export PBT_DBMS=mysql
export DB_HOST=127.0.0.1
export DB_PORT=3306
export DB_USER=root
export DB_PASSWORD=pbt_password
export DB_NAME=test_dataset
```
