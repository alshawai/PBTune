# Custom Workload Files

> Last reviewed: 2026-06-15

See also: [Documentation Index](../docs/README.md)

This directory contains workload definitions for PBT PostgreSQL Tuner.

## File Format

Workload files can be in **JSON** or **YAML** format.

### JSON Format

```json
{
  "name": "My Workload",
  "description": "Description of the workload",
  "schema": {
    "tables": 10,
    "table_size": 100000
  },
  "queries": [
    {
      "sql": "SELECT * FROM {table} WHERE id = {id}",
      "weight": 0.5,
      "description": "Optional description"
    },
    {
      "sql": "SELECT COUNT(*) FROM {table}",
      "weight": 0.3
    }
  ]
}
```

### YAML Format

```yaml
name: My Workload
description: Description of the workload
schema:
  tables: 10
  table_size: 100000
queries:
  - sql: "SELECT * FROM {table} WHERE id = {id}"
    weight: 0.5
    description: "Optional description"
  - sql: "SELECT COUNT(*) FROM {table}"
    weight: 0.3
```

### Simplified Format

You can also use a simple list of SQL queries (equal weights):

```json
{
  "queries": [
    "SELECT * FROM sbtest1 WHERE id = 1",
    "SELECT COUNT(*) FROM sbtest1"
  ]
}
```

## Fields

| Field                   | Required | Default  | Description                                                      |
| ----------------------- | -------- | -------- | ---------------------------------------------------------------- |
| `name`                  | No       | filename | Workload name                                                    |
| `description`           | No       | —        | Workload description                                             |
| `schema`                | No       | —        | Schema configuration (see below)                                 |
| `schema.tables`         | No       | 1        | Number of sbtest tables to create. **Academic standard: 10.**    |
| `schema.table_size`     | No       | 100000   | Rows per table. **Academic standard: 100,000 (scale factor 1).** |
| `queries`               | **Yes**  | —        | List of SQL queries                                              |
| `queries[].sql`         | **Yes**  | —        | SQL query string (supports placeholders — see below)             |
| `queries[].weight`      | No       | 1.0      | Execution frequency weight                                       |
| `queries[].description` | No       | —        | Query description                                                |

> **Warning:** Workloads without a `schema` section default to 1 table. A warning is logged recommending you add a `schema` section for realistic multi-table evaluation.

## Query Placeholders

| Placeholder            | Description                                | Example Resolution   |
| ---------------------- | ------------------------------------------ | -------------------- |
| `{table}`              | Random sbtest table (sbtest1..sbtestN)     | `sbtest7`            |
| `{table1}`-`{table10}` | Sequenced unique sbtest tables (for JOINs) | `sbtest3`, `sbtest9` |
| `{id}`                 | Random row ID (1..table_size)              | `42931`              |
| `{k_val}`              | Random k column value                      | `78412`              |
| `{threshold}`          | Random value in upper quartile range       | `62500`              |
| `{low}`                | Random value in lower half                 | `25000`              |
| `{high}`               | Random value in upper half                 | `75000`              |
| `{low_k}`              | Random k value in lower half               | `30000`              |
| `{high_k}`             | Random k value in upper half               | `80000`              |
| `{offset}`             | Random offset (0..table_size-1)            | `50000`              |

## Weights

- Weights determine how frequently each query is executed
- They are normalized to sum to 1.0
- Higher weight = more frequent execution
- Example: weights `[0.5, 0.3, 0.2]` means 50%, 30%, 20% execution frequency

## Usage

### With Command Line

```bash
# Use a built-in template workload
python -m src.tuners pbt --tier minimal --workload oltp

# Use custom workload file
python -m src.tuners pbt --tier core --workload-file workloads/my_workload.json

# YAML format (requires pyyaml)
python -m src.tuners pbt --tier minimal --workload-file workloads/my_workload.yaml
```

### With Python API

```python
from src.tuners.pbt.tuner import PBTTuner
from src.utils.metrics import WorkloadType

tuner = PBTTuner(
    knob_tier="core",
    workload_type=WorkloadType.OLTP,  # Ignored when workload_file is provided
    workload_file="workloads/my_workload.json"
)

tuner.run()
```

## Provided Templates

### oltp.json

Standard OLTP workload (10 tables × 100K rows) modeling Sysbench read/writes:

- Point selects (52%)
- Range scans (28%)
- Updates (17%)
- Deletes (1.5%)
- Inserts (1.5%)

**Use for**: Transaction processing optimization

### olap.json

Standard OLAP workload (10 tables × 100K rows) modeling TPC-H analytical patterns:

- Aggregations (37%)
- GROUP BY queries (34%)
- Range & Sorting queries (18%)
- Statistical functions (8%)
- Cross-table JOINs & complex analytics (3%)

**Use for**: Analytical query optimization

### mixed.json

Combined OLTP + OLAP workload (10 tables × 100K rows) with realistic mixed traffic.

## Creating Custom Workloads

### Step 1: Identify Representative Queries

Extract your most common queries from application logs or `pg_stat_statements`:

```sql
WITH total AS (SELECT sum(calls) as total_calls FROM pg_stat_statements)
SELECT
    query,
    calls,
    ROUND((calls::numeric / total.total_calls::numeric), 4) as weight,
    mean_exec_time
FROM pg_stat_statements, total
ORDER BY calls DESC
LIMIT 20;
```

### Step 2: Create Workload File

```json
{
  "name": "Production App Workload",
  "description": "Top 20 queries from production",
  "schema": {
    "tables": 10,
    "table_size": 100000
  },
  "queries": [
    {
      "sql": "SELECT * FROM {table} WHERE id = {id}",
      "weight": 0.4,
      "description": "User lookup by ID"
    },
    {
      "sql": "SELECT id, k FROM {table} WHERE k BETWEEN {low_k} AND {high_k} ORDER BY k DESC LIMIT 10",
      "weight": 0.3,
      "description": "Recent items range scan"
    }
  ]
}
```

### Step 3: Run Tuning

```bash
python -m src.tuners pbt --tier core --workload-file workloads/my_workload.json
```

## Best Practices

### Query Selection

✅ **Do**: Include your most frequent queries  
✅ **Do**: Include queries that represent different patterns  
✅ **Do**: Use `{table}` placeholder for multi-table distribution  
❌ **Don't**: Include admin queries (VACUUM, ANALYZE)  
❌ **Don't**: Include DDL statements (CREATE, ALTER)

### Schema Configuration

✅ **Do**: Set `tables` to match your production table count (or use 10 for academic standard)  
✅ **Do**: Set `table_size` based on your expected data volume  
❌ **Don't**: Use 1 table for serious benchmarking — it doesn't exercise buffer eviction or JOIN planning

### Weights

✅ **Do**: Base weights on actual query frequency  
✅ **Do**: Use higher weights for performance-critical queries  
❌ **Don't**: Make weights too extreme (e.g., 0.99, 0.01)  
❌ **Don't**: Ignore low-frequency but important queries

## Troubleshooting

### "Workload file not found"

- Check file path is correct
- Use absolute paths or paths relative to project root

### "PyYAML is required for YAML workload files"

```bash
pip install pyyaml
```

### "Query validation failed"

- Ensure tables and columns exist in database
- Check for typos in SQL statements
- Verify database connection

> Check `src/tuners/engine/orchestrator.py` for implementation details and [`../docs/reference/benchmarking.md`](../docs/reference/benchmarking.md) for the full architecture overview.
