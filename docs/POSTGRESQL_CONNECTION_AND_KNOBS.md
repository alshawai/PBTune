# PostgreSQL Connection and Configuration Parameters System

## Overview

This document describes the comprehensive system built for connecting to PostgreSQL databases and retrieving all tunable configuration parameters (knobs) for machine learning-based database optimization. The system is organized into four main modules: `config`, `database`, `knobs`, and `scripts`.

---

## Table of Contents

1. [Architecture Overview](#architecture-overview)
2. [Module: config](#module-config)
3. [Module: database](#module-database)
4. [Module: knobs](#module-knobs)
5. [Module: scripts](#module-scripts)
6. [Usage Examples](#usage-examples)
7. [Data Files Generated](#data-files-generated)
8. [Future Enhancements](#future-enhancements)

---

## Architecture Overview

```
┌─────────────────────────────────────────────────────────┐
│                    Application Layer                    │
│              (scripts, ML models, analysis)             │
└───────────────────────────┬─────────────────────────────┘
                            │
            ┌───────────────┼───────────────┐
            │               │               │
            ▼               ▼               ▼
        ┌─────────┐   ┌──────────┐   ┌─────────────┐
        │ Config  │   │ Database │   │    Knobs    │
        │ Module  │   │  Module  │   │   Module    │
        └────┬────┘   └─────┬────┘   └──────┬──────┘
             │              │               │
             │              │               │
             └──────────────┼───────────────┘
                            │
                            ▼
                   ┌─────────────────┐
                   │   PostgreSQL    │
                   │    Database     │
                   └─────────────────┘
```

### Design Principles

1. **Centralized Configuration**: Single source of truth for database credentials
2. **Separation of Concerns**: Each module has a specific responsibility
3. **Type Safety**: Using dataclasses and type hints throughout
4. **Error Handling**: Comprehensive error messages with helpful guidance
5. **Reusability**: Functions designed for both interactive and programmatic use

---

## Module: config

**Location:** `src/config/`

### Purpose

Provides centralized database configuration management using environment variables. This is the **SINGLE SOURCE OF TRUTH** for database credentials across the entire project.

### Components

#### 1. `database.py`

**Main Class:** `DatabaseConfig`

A dataclass that holds database connection parameters:
- `user`: Database username
- `password`: Database password
- `host`: Database host address
- `port`: Database port
- `name`: Database name

**Key Functions:**

```python
get_db_config() -> DatabaseConfig
```
- Returns singleton database configuration instance
- Loads from environment variables only once
- Raises `ValueError` if `DB_PASSWORD` is not set

**DatabaseConfig Methods:**

```python
@classmethod
def from_env(cls) -> DatabaseConfig
    """Create configuration from environment variables"""

def to_dict(self) -> Dict[str, str]
    """Get configuration as dictionary for psycopg2"""

def get_connection_string(self, hide_password: bool = True) -> str
    """Get PostgreSQL connection string (for display)"""

def get_sqlalchemy_url(self) -> str
    """Get SQLAlchemy database URL"""
```

**Environment Variables:**
- `DB_USER` (default: "postgres")
- `DB_PASSWORD` (required)
- `DB_HOST` (default: "localhost")
- `DB_PORT` (default: "5432")
- `DB_NAME` (default: "test_dataset")

**Security Features:**
- Password masking in string representations
- Validation of required credentials
- Singleton pattern prevents multiple loads

#### 2. `__main__.py`

Provides a configuration test utility.

**Run with:**
```bash
python -m src.config
```

**Output:**
- ✅ Configuration validation
- Connection details (with masked password)
- Helpful error messages if setup is incomplete

---

## Module: database

**Location:** `src/database/`

### Purpose

Provides utilities for PostgreSQL database operations including connection management, database lifecycle management, and data loading.

### Components

#### 1. `connection.py`

**Functions:**

```python
def get_connection(
    config: Optional[DatabaseConfig] = None,
    dbname: Optional[str] = None
) -> PgConnection
```
- Creates psycopg2 database connection
- Optionally override database name for admin operations
- Uses centralized config by default

```python
def get_engine(config: Optional[DatabaseConfig] = None) -> Engine
```
- Creates SQLAlchemy engine
- Used for pandas operations and ORM
- Connection pooling included

**Use Cases:**
- Direct SQL queries with cursors
- Pandas DataFrame operations
- Administrative operations on 'postgres' database

#### 2. `management.py`

**Functions:**

```python
def create_database(config: Optional[DatabaseConfig] = None) -> None
```
- Creates database if it doesn't exist
- Connects to 'postgres' database for creation
- Uses `ISOLATION_LEVEL_AUTOCOMMIT`

```python
def drop_database(config: Optional[DatabaseConfig] = None) -> None
```
- Drops database if it exists
- Terminates all connections first
- **⚠️ DESTRUCTIVE - Cannot be undone**

```python
def reset_database(config: Optional[DatabaseConfig] = None) -> None
```
- Drops and recreates database
- Provides clean slate
- **⚠️ DESTRUCTIVE - All data lost**

**Safety Features:**
- Automatic connection termination before drop
- Clear warning messages
- Existence checks before operations

#### 3. `data_loader.py`

**Functions:**

```python
def load_csv_to_table(
    csv_path: str,
    table_name: str,
    if_exists: str = "fail",
    config: Optional[DatabaseConfig] = None,
    engine: Optional[Engine] = None
) -> None
```
- Loads CSV data into PostgreSQL table
- Three modes: `"fail"`, `"replace"`, `"append"`
- Uses pandas for CSV parsing
- Automatic schema inference

```python
def load_products_dataset(config: Optional[DatabaseConfig] = None) -> None
```
- Convenience function for products dataset
- Loads `data/products-1000000.csv`
- 1 million product records

```python
def load_leads_dataset(config: Optional[DatabaseConfig] = None) -> None
```
- Convenience function for leads dataset
- Loads `data/leads-100000.csv`
- 100,000 lead records

**Features:**
- Automatic path resolution
- Progress reporting
- Graceful error handling
- Informative success messages

#### 4. `__init__.py`

Exposes clean API for the database module:

```python
from src.database import (
    get_connection,
    get_engine,
    create_database,
    drop_database,
    reset_database,
    load_csv_to_table,
    load_products_dataset,
    load_leads_dataset,
)
```

#### 5. `__main__.py`

Comprehensive test suite for all database functionality.

**Run with:**
```bash
python -m src.database
```

**Tests:**
1. Connection module (psycopg2 & SQLAlchemy)
2. Management module (create, reset, drop)
3. Data loader module (CSV loading with all modes)

**Features:**
- Uses temporary test database
- No impact on production data
- Verifies all operations
- Automatic cleanup

---

## Module: knobs

**Location:** `src/knobs/`

### Purpose

Retrieves and categorizes PostgreSQL configuration parameters (knobs) for machine learning-based database optimization. This module provides comprehensive access to ALL PostgreSQL settings from `pg_settings`.

### Components

#### 1. `retrieval.py`

**Main Class:** `PostgreSQLKnobRetriever`

A comprehensive utility for retrieving and analyzing PostgreSQL configuration parameters.

**Supporting Classes:**

```python
class KnobCategory(Enum):
    """Categories of PostgreSQL configuration parameters"""
    MEMORY = "memory"
    QUERY_PLANNER = "query_planner"
    WAL = "wal"
    CHECKPOINT = "checkpoint"
    AUTOVACUUM = "autovacuum"
    CONNECTIONS = "connections"
    PARALLELISM = "parallelism"
    STATISTICS = "statistics"
    LOCKS = "locks"
    OTHER = "other"
```

```python
@dataclass
class ConfigParameter:
    """Represents a PostgreSQL configuration parameter"""
    name: str
    value: str
    unit: Optional[str]
    category: str
    context: str
    vartype: str
    source: str
    min_val: Optional[str]
    max_val: Optional[str]
    enumvals: Optional[List[str]]
    boot_val: Optional[str]
    reset_val: Optional[str]
    description: Optional[str]
```

**Predefined Tunable Knobs:**

The class defines 80+ commonly tuned parameters organized by category:

- **Memory (6 knobs)**: `shared_buffers`, `work_mem`, `maintenance_work_mem`, etc.
- **Query Planner (13 knobs)**: `random_page_cost`, `cpu_tuple_cost`, `enable_*` flags
- **WAL (5 knobs)**: `wal_level`, `fsync`, `synchronous_commit`, etc.
- **Checkpoint (4 knobs)**: `checkpoint_timeout`, `max_wal_size`, etc.
- **Autovacuum (9 knobs)**: `autovacuum_max_workers`, thresholds, scale factors
- **Connections (4 knobs)**: `max_connections`, `max_worker_processes`, etc.
- **Parallelism (6 knobs)**: Parallel query configuration
- **Statistics (5 knobs)**: Query statistics and tracking
- **Locks (3 knobs)**: Lock management parameters

**Core Retrieval Methods:**

```python
def get_all_parameters(self) -> pd.DataFrame
    """Retrieve ALL PostgreSQL configuration parameters"""
```
- Queries `pg_settings` system view
- Returns complete parameter metadata
- ~300+ parameters in typical PostgreSQL installation

```python
def get_tunable_knobs(
    self, 
    categories: Optional[List[KnobCategory]] = None
) -> pd.DataFrame
    """Retrieve predefined tunable parameters for ML optimization"""
```
- Filters to 80+ commonly tuned knobs
- Optionally filter by category
- Adds custom category labels

```python
def get_numeric_knobs(self) -> pd.DataFrame
    """Get only numeric knobs (integer and real) suitable for ML"""
```
- Filters to numeric parameters only
- Ready for ML model input
- Includes min/max bounds

```python
def get_all_knobs_with_metadata(self) -> pd.DataFrame
    """Retrieve ALL parameters with additional metadata"""
```
- All PostgreSQL settings
- Flags: `is_predefined_tunable`, `is_runtime_modifiable`
- Custom category assignments
- Complete metadata for analysis

**Advanced Retrieval Methods:**

```python
def get_knobs_by_context(self, context: str) -> pd.DataFrame
    """Get knobs by modification context"""
```
Contexts:
- `internal`: Cannot be changed
- `postmaster`: Requires PostgreSQL restart
- `sighup`: Reload configuration (no restart)
- `superuser`: Superuser can change per session
- `user`: Any user can change per session

```python
def get_knobs_by_category(self, category: str) -> pd.DataFrame
    """Get knobs by PostgreSQL category"""
```
PostgreSQL categories include:
- "Resource Usage / Memory"
- "Query Tuning / Planner Cost Constants"
- "Write-Ahead Log"
- And many more...

```python
def get_modifiable_knobs(self) -> pd.DataFrame
    """Get knobs that don't require restart"""
```
- Excludes `internal` and `postmaster` contexts
- Safe for runtime tuning
- Ideal for online optimization

**Data Export Methods:**

```python
def save_all_knobs(
    self, 
    filepath: str, 
    include_metadata: bool = True
) -> None
    """Save ALL PostgreSQL knobs to CSV"""
```
- Exports complete parameter set
- Optional metadata columns
- Detailed statistics in output

```python
def export_to_csv(
    self, 
    filepath: str, 
    include_all: bool = False
) -> None
    """Export knobs to CSV for analysis or ML training"""
```
- Choose between all parameters or tunable only
- Suitable for ML pipelines

**Utility Methods:**

```python
def get_current_values_dict(
    self, 
    knob_names: Optional[List[str]] = None
) -> Dict[str, str]
    """Get current values as dictionary"""
```

```python
def get_knob_details(self, knob_name: str) -> Optional[ConfigParameter]
    """Get detailed information about a specific knob"""
```

```python
def normalize_value(self, value: str, unit: Optional[str]) -> float
    """Normalize knob value to standard units"""
```
- Converts memory to MB
- Converts time to seconds
- Handles PostgreSQL-specific units (8kB blocks)

```python
def get_normalized_features(self) -> Dict[str, float]
    """Get normalized numeric features for ML models"""
```

```python
def get_knobs_summary(self) -> Dict[str, int]
    """Get summary statistics of all knobs"""
```

#### 2. `__init__.py`

Exposes clean API:

```python
from src.knobs import (
    PostgreSQLKnobRetriever,
    KnobCategory,
    ConfigParameter
)
```

#### 3. `__main__.py`

Quick demonstration of knob retrieval.

**Run with:**
```bash
python -m src.knobs
```

**Output:**
- Summary statistics
- Sample tunable knobs
- Numeric knob count
- Success/error messages

---

## Module: scripts

**Location:** `src/scripts/`

### Purpose

Provides executable scripts for database setup and comprehensive knobs analysis.

### Components

#### 1. `setup_database.py`

**Purpose:** Initial database setup and data loading

**Functions:**

```python
def setup_fresh_database() -> None
    """Create and populate database from scratch"""
```
1. Creates database
2. Loads products dataset (1M rows)
3. Loads leads dataset (100K rows)
4. Verifies setup

```python
def reset_existing_database() -> None
    """Reset database with confirmation (DESTRUCTIVE)"""
```
1. Prompts for confirmation
2. Drops and recreates database
3. Reloads all datasets
4. **⚠️ Destroys all data**

**Usage:**

Interactive mode:
```bash
python -m src.scripts.setup_database
```

Command-line mode:
```bash
python -m src.scripts.setup_database setup
python -m src.scripts.setup_database reset
```

**Features:**
- Interactive menu
- Safety confirmations
- Progress reporting
- Error recovery

#### 2. `analyze_knobs.py`

**Purpose:** Comprehensive analysis of ALL PostgreSQL configuration parameters

**Analysis Sections:**

1. **Summary of ALL PostgreSQL Knobs**
   - Total knob count
   - Breakdown by type (integer, real, bool, string, enum)
   - Modifiability statistics

2. **Comparison - Predefined vs All**
   - Predefined tunable count
   - Non-predefined count
   - Percentage distribution

3. **Discovering New Tunable Candidates**
   - Non-predefined knobs
   - Runtime-modifiable
   - Numeric types (integer/real)
   - Top 10 candidates with details

4. **Knobs by Context**
   - Breakdown by modification context
   - Count of numeric knobs per context
   - Restart requirements

5. **Top 10 Categories by Knob Count**
   - PostgreSQL category distribution
   - Most parameter-rich categories

6. **Interesting Non-Predefined Knobs**
   - JIT compilation settings
   - Additional parallel query settings
   - Extra WAL/replication settings

7. **Exporting All Knobs**
   - Saves to CSV with metadata
   - Location: `data/postgresql_all_knobs_demo.csv`

8. **ML-Ready Knob Filtering**
   - Numeric + runtime-modifiable count
   - All numeric knobs count
   - Predefined numeric tunable count
   - Recommendations for ML optimization

9. **Creating Custom Tuning Profile**
   - OLAP-relevant knobs
   - OLTP-relevant knobs
   - Workload-specific parameter sets

**Usage:**

```bash
python -m src.scripts.analyze_knobs
```

**Output:**
- Comprehensive console report
- CSV export with all parameters
- Insights and recommendations

---

## Usage Examples

### Basic Configuration

```python
from src.config.database import get_db_config

config = get_db_config()
print(config.user)  # 'postgres'
print(config.get_connection_string())  # Safe display string
```

### Database Connection

```python
from src.database import get_connection, get_engine
import pandas as pd

# Using psycopg2
conn = get_connection()
cursor = conn.cursor()
cursor.execute("SELECT version()")
print(cursor.fetchone())
conn.close()

# Using SQLAlchemy
engine = get_engine()
df = pd.read_sql("SELECT * FROM products LIMIT 10", engine)
```

### Database Management

```python
from src.database import create_database, reset_database

create_database()
reset_database()
```

### Data Loading

```python
from src.database import load_products_dataset, load_csv_to_table

load_products_dataset()
load_csv_to_table("data/my_data.csv", "my_table", if_exists="replace")
```

### Knobs Retrieval

```python
from src.knobs import PostgreSQLKnobRetriever, KnobCategory

retriever = PostgreSQLKnobRetriever()

all_params = retriever.get_all_parameters()
print(f"Total parameters: {len(all_params)}")

tunable = retriever.get_tunable_knobs()
print(f"Predefined tunable: {len(tunable)}")

numeric = retriever.get_numeric_knobs()
print(f"Numeric knobs: {len(numeric)}")

memory_knobs = retriever.get_tunable_knobs(
    categories=[KnobCategory.MEMORY]
)

all_knobs = retriever.get_all_knobs_with_metadata()
print(f"Runtime modifiable: {all_knobs['is_runtime_modifiable'].sum()}")
```

### Advanced Knobs Analysis

```python
from src.knobs import PostgreSQLKnobRetriever

retriever = PostgreSQLKnobRetriever()

sighup_knobs = retriever.get_knobs_by_context("sighup")
print(f"SIGHUP knobs: {len(sighup_knobs)}")

summary = retriever.get_knobs_summary()
for key, value in summary.items():
    print(f"{key}: {value}")

all_knobs = retriever.get_all_knobs_with_metadata()
candidates = all_knobs[
    (~all_knobs["is_predefined_tunable"]) &
    (all_knobs["is_runtime_modifiable"]) &
    (all_knobs["vartype"].isin(["integer", "real"]))
]
print(f"New candidates: {len(candidates)}")

retriever.save_all_knobs("data/my_analysis.csv", include_metadata=True)
```

### Normalized Values for ML

```python
from src.knobs import PostgreSQLKnobRetriever

retriever = PostgreSQLKnobRetriever()

features = retriever.get_normalized_features()
print(features["shared_buffers"])  # Value in MB

current_values = retriever.get_current_values_dict()
print(current_values["max_connections"])
```

---

## Data Files Generated

### 1. CSV Exports from Knobs Module

**`data/postgresql_all_knobs.csv`**
- Complete set of PostgreSQL parameters
- Generated by: `retriever.save_all_knobs()`

**`data/postgresql_all_knobs_demo.csv`**
- Complete set with metadata columns
- Generated by: `analyze_knobs.py` script
- Extra columns:
  - `is_predefined_tunable`: Boolean flag
  - `custom_category`: Custom category assignment
  - `is_runtime_modifiable`: Boolean flag

**`data/postgresql_impactful_knobs.csv`**
- Subset of most impactful tunable parameters
- Generated by: Custom filtering

**`data/postgresql_knobs.csv`**
- Predefined tunable knobs only
- Generated by: `retriever.export_to_csv(include_all=False)`

### 2. Typical Parameter Counts

Based on PostgreSQL 13+:
- **Total parameters:** ~340
- **Predefined tunable:** 80+
- **Runtime modifiable:** ~200
- **Numeric (integer/real):** ~180
- **New candidates (numeric, runtime-modifiable, non-predefined):** ~100

---

## Technical Details

### Database Connection Architecture

1. **Configuration Layer** (`config.database`)
   - Single source of truth
   - Environment variable loading
   - Validation and error handling

2. **Connection Layer** (`database.connection`)
   - psycopg2 for direct SQL
   - SQLAlchemy for pandas/ORM
   - Connection parameter management

3. **Application Layer** (scripts, models)
   - High-level operations
   - User-friendly interfaces
   - Error recovery

### PostgreSQL pg_settings View

The system queries `pg_settings`, which provides:
- Current parameter values
- Value units (kB, ms, etc.)
- Valid ranges (min/max)
- Type information
- Modification context
- Source of current value
- Boot/reset values
- Enum values for enum types
- Descriptions

### Knob Categories

**Predefined Custom Categories:**
- Focus on performance-critical parameters
- Organized by functional area
- Based on industry best practices

**PostgreSQL Native Categories:**
- More fine-grained
- ~25+ categories
- Covers all PostgreSQL features

### Value Normalization

For ML compatibility:
- **Memory**: Normalized to MB
  - kB → MB (÷ 1024)
  - GB → MB (× 1024)
  - 8kB blocks → MB (× 8 ÷ 1024)

- **Time**: Normalized to seconds
  - ms → s (÷ 1000)
  - min → s (× 60)

---

## Error Handling

### Configuration Errors

**Missing DB_PASSWORD:**
```
ValueError: DB_PASSWORD environment variable is required.
Please set it in .env file. See docs/ENVIRONMENT_SETUP.md for help.
```

**Solution:** Create `.env` file with required variables

### Connection Errors

**PostgreSQL not running:**
```
psycopg2.OperationalError: could not connect to server
```

**Solution:** Start PostgreSQL service

**Wrong credentials:**
```
psycopg2.OperationalError: password authentication failed
```

**Solution:** Check DB_PASSWORD in `.env`

### Database Operation Errors

**Database already exists:**
```
Database 'test_dataset' already exists.
```

**Solution:** Use `reset_database()` or connect to existing

**Table already exists:**
```
ValueError: Table 'products' already exists
```

**Solution:** Use `if_exists="replace"` or `"append"`

---

## Performance Considerations

### Connection Management

- SQLAlchemy engines use connection pooling
- Close connections explicitly when done
- Reuse engines for multiple operations

### Data Loading

- Pandas bulk insert via SQLAlchemy
- Automatic batching for large datasets
- Memory-efficient CSV reading

### Knobs Retrieval

- Single query fetches all parameters
- Results cached in DataFrame
- Filter/analyze in memory

---

## Future Enhancements

### Planned Features

1. **Dynamic Knob Discovery**
   - Automatic identification of tunable candidates
   - Machine learning for knob importance ranking
   - Workload-specific knob selection

2. **Configuration Validation**
   - Check for conflicting settings
   - Validate against system resources
   - Suggest safe value ranges

3. **Knob Dependency Graph**
   - Map parameter dependencies
   - Identify correlated knobs
   - Optimize tuning order

4. **Historical Value Tracking**
   - Log knob value changes
   - Track performance impact
   - A/B testing framework

5. **Multi-Database Support**
   - Extend to MySQL, MongoDB, etc.
   - Unified knob retrieval interface
   - Cross-database tuning strategies

6. **Real-time Configuration**
   - Apply knob changes without restart
   - Hot reload for applicable parameters
   - Rollback mechanism

7. **Knob Recommendations**
   - Workload analysis
   - Resource-based suggestions
   - Best practice enforcement

---

## Testing

### Module Tests

**Config Module:**
```bash
python -m src.config
```

**Database Module:**
```bash
python -m src.database
```

**Knobs Module:**
```bash
python -m src.knobs
```

### Integration Tests

**Full Setup:**
```bash
python -m src.scripts.setup_database setup
```

**Comprehensive Analysis:**
```bash
python -m src.scripts.analyze_knobs
```

---

## References

### PostgreSQL Documentation

- [pg_settings View](https://www.postgresql.org/docs/current/view-pg-settings.html)
- [Configuration Parameters](https://www.postgresql.org/docs/current/runtime-config.html)
- [Performance Tuning](https://wiki.postgresql.org/wiki/Performance_Optimization)

---

## Summary

This system provides a **complete foundation** for PostgreSQL database operations and machine learning-based optimization:

✅ **Centralized configuration management** with security best practices  
✅ **Comprehensive database utilities** for all common operations  
✅ **Complete access to PostgreSQL parameters** (~390 knobs)  
✅ **Predefined tunable sets** for quick optimization (80+ knobs)  
✅ **Discovery tools** for finding new optimization candidates  
✅ **ML-ready data formats** with normalization and metadata  
✅ **Executable scripts** for setup and analysis  
✅ **Type-safe implementation** with dataclasses and type hints  
✅ **Thorough error handling** with helpful guidance  

The system is designed to support advanced database optimization research, particularly for developing AI-powered tuning systems and adaptive indexing strategies.

---

**For questions or issues, refer to:**
- [ENVIRONMENT_SETUP.md](./ENVIRONMENT_SETUP.md) for initial setup
- Module docstrings for detailed API documentation
- `__main__.py` files for usage examples

---

## Related Documentation

### PBT System Integration

The database connection and knob retrieval system serves as the **foundation** for the Population Based Training (PBT) system:

- **[Configuration Management](./CONFIGURATION_MANAGEMENT.md)**: How retrieved knobs are used to define KnobSpace for PBT optimization
- **[PBT Core Components](./PBT_CORE_COMPONENTS.md)**: How Workers use knob configurations during evolutionary optimization
- **[Performance Evaluation](./PERFORMANCE_EVALUATION.md)**: How KnobApplicator uses this connection system to apply configurations
