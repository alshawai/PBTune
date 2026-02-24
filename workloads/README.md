# Custom Workload Files

This directory contains example workload definitions for PBT PostgreSQL Tuner.

## File Format

Workload files can be in **JSON** or **YAML** format.

### JSON Format

```json
{
  "name": "My Workload",
  "description": "Description of the workload",
  "queries": [
    {
      "sql": "SELECT * FROM table WHERE id = 1",
      "weight": 0.5,
      "description": "Optional description"
    },
    {
      "sql": "SELECT COUNT(*) FROM table",
      "weight": 0.3
    }
  ]
}
```

### YAML Format

```yaml
name: My Workload
description: Description of the workload
queries:
  - sql: "SELECT * FROM table WHERE id = 1"
    weight: 0.5
    description: "Optional description"
  - sql: "SELECT COUNT(*) FROM table"
    weight: 0.3
```

### Simplified Format

You can also use a simple list of SQL queries (equal weights):

```json
{
  "queries": ["SELECT * FROM table WHERE id = 1", "SELECT COUNT(*) FROM table"]
}
```

## Fields

| Field                   | Required | Description                               |
| ----------------------- | -------- | ----------------------------------------- |
| `name`                  | No       | Workload name (defaults to filename)      |
| `description`           | No       | Workload description                      |
| `queries`               | **Yes**  | List of SQL queries                       |
| `queries[].sql`         | **Yes**  | SQL query string                          |
| `queries[].weight`      | No       | Execution frequency weight (default: 1.0) |
| `queries[].description` | No       | Query description                         |

## Weights

- Weights determine how frequently each query is executed
- They are normalized to sum to 1.0
- Higher weight = more frequent execution
- Example: weights `[0.5, 0.3, 0.2]` means 50%, 30%, 20% execution frequency

## Usage

### With Command Line

```bash
# Use custom workload file
python -m src.tuner.main --tier core --workload-file workloads/my_workload.json

# JSON format standard workload parsing
python -m src.tuner.main --tier minimal --workload-file workloads/oltp.json

# YAML format (requires pyyaml)
python -m src.tuner.main --tier minimal --workload-file workloads/my_workload.yaml
```

### With Python API

```python
from src.tuner.main import PBTTuner
from src.tuner.evaluator.metrics import WorkloadType

tuner = PBTTuner(
    knob_tier="core",
    workload_type=WorkloadType.OLTP,  # Ignored when workload_file is provided
    workload_file="workloads/my_workload.json"
)

tuner.run()
```

## Provided Examples

### oltp.json

Definitive OLTP standard workload with:

- Point selects (52%)
- Range scans (16%)
- Updates (17%)
- Deletes (1.5%)
- Inserts (1.5%)

**Use for**: Transaction processing optimization

### olap.json

Definitive OLAP standard workload with:

- Aggregations (42%)
- GROUP BY queries (34%)
- Range scans (10%)
- Statistical functions (8%)
- Window functions & complex joins (6%)

**Use for**: Analytical query optimization

### mixed.json

Definitive Mixed workload containing probabilities from both `oltp.json` and `olap.json`.

## Creating Custom Workloads

### Step 1: Identify Representative Queries

Extract your most common queries from application logs or `pg_stat_statements`:

```sql
SELECT query, calls, mean_exec_time
FROM pg_stat_statements
ORDER BY calls DESC
LIMIT 20;
```

### Step 2: Create Workload File

```json
{
  "name": "Production App Workload",
  "description": "Top 20 queries from production",
  "queries": [
    {
      "sql": "SELECT * FROM users WHERE email = 'user@example.com'",
      "weight": 0.4,
      "description": "User lookup by email"
    },
    {
      "sql": "SELECT * FROM orders WHERE user_id = 123 ORDER BY created_at DESC LIMIT 10",
      "weight": 0.3,
      "description": "Recent orders for user"
    }
  ]
}
```

### Step 3: Run Tuning

```bash
python -m src.tuner.main --tier core --workload-file workloads/my_workload.json
```

## Best Practices

### Query Selection

✅ **Do**: Include your most frequent queries  
✅ **Do**: Include queries that represent different patterns  
✅ **Do**: Use parameterized queries when possible  
❌ **Don't**: Include admin queries (VACUUM, ANALYZE)  
❌ **Don't**: Include DDL statements (CREATE, ALTER)

### Weights

✅ **Do**: Base weights on actual query frequency  
✅ **Do**: Use higher weights for performance-critical queries  
❌ **Don't**: Make weights too extreme (e.g., 0.99, 0.01)  
❌ **Don't**: Ignore low-frequency but important queries

### Query Complexity

✅ **Do**: Include a mix of simple and complex queries  
✅ **Do**: Test range scans, joins, and aggregations  
✅ **Do**: Include representative WHERE clauses  
❌ **Don't**: Use only trivial queries (e.g., `SELECT 1`)  
❌ **Don't**: Include queries that always fail

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

### Queries Running Slowly

- Reduce `measurement_duration` in evaluator config
- Use fewer complex queries
- Check database has appropriate indexes

## Advanced Features

### Parameterized Queries

Use Python string formatting for dynamic queries:

```json
{
  "sql": "SELECT * FROM users WHERE id = {user_id}",
  "weight": 0.5
}
```

**Note**: Parameter bindings are supported natively by `WorkloadExecutor`. Supported parameters include: `{id}`, `{k_val}`, `{threshold}`, `{low}`, `{high}`, `{low_k}`, `{high_k}`, and `{offset}`. To use custom variables, you will need to add them to `WorkloadExecutor._instantiate_query()`.

### Multiple Workload Files

Test different scenarios:

```bash
# Morning workload (read-heavy)
python -m src.tuner.main --workload-file workloads/morning.json

# Evening workload (write-heavy)
python -m src.tuner.main --workload-file workloads/evening.json
```

> Check `src/tuner/evaluator/evaluator.py` for implementation details
