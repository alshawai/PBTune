# Adding a Custom Workload

See also: [workload-orchestrator](../architecture/workload-orchestrator.md), [benchmarking](../reference/benchmarking.md), [feature-driven-scoring](../architecture/feature-driven-scoring.md), [workloads/README](../../workloads/README.md)

This guide walks through authoring a custom JSON or YAML workload template — the kind you'd use to tune PostgreSQL for your own production traffic rather than against a stock Sysbench / TPC-H benchmark.

For the architecture of how the workload is actually executed, see [workload-orchestrator](../architecture/workload-orchestrator.md). This is the user-facing recipe.

---

## When to author a custom workload

| You want… | Use this |
| --- | --- |
| Rigorous OLTP measurement against an industry-standard benchmark | `--benchmark sysbench --sysbench-workload oltp_read_write` |
| Rigorous OLAP measurement against TPC-H | `--benchmark tpch --scale-factor 1.0` |
| **Tune PostgreSQL for your own application's queries** | **Custom JSON/YAML workload** (this guide) |
| Mix custom and built-in queries | A custom workload — there is no first-class blending mechanism with sysbench/tpch |

The custom workload runs through the [`WorkloadExecutor`](../../src/tuner/benchmark/workload.py), which is pure Python over psycopg2. It's slower than the C-binary sysbench/tpch executors because of GIL and round-trip overhead, but for moderately-complex queries the database execution time dominates the Python overhead and the measurement is still meaningful.

---

## File format

Custom workloads are JSON or YAML files placed under `workloads/` (or anywhere — pass the path via `--workload-file`). The minimal shape:

```json
{
  "name": "My Workload",
  "description": "Optional human-readable summary",
  "schema": {
    "tables": 10,
    "table_size": 100000
  },
  "queries": [
    { "sql": "SELECT * FROM {table} WHERE id = {id}", "weight": 0.4 },
    { "sql": "SELECT COUNT(*) FROM {table} WHERE k > {threshold}", "weight": 0.6 }
  ]
}
```

Field semantics:

| Field | Required | Purpose |
| --- | --- | --- |
| `name` | yes | Used in session metadata + log banner. |
| `description` | no | Free-form. |
| `schema.tables` | no, defaults to 10 | Number of `sbtest{N}` tables the orchestrator will create or expect. |
| `schema.table_size` | no, defaults to 100 000 | Rows per table at schema initialisation. |
| `queries[].sql` | yes | A SQL template with placeholder substitutions (see below). Raw unparameterised SQL is also accepted. |
| `queries[].weight` | yes | Relative execution frequency. Weights are normalised to sum to 1 at load time. |
| `queries[].description` | no | Annotation; appears in HTML logs. |

**Without a `schema` block** the executor logs a warning and defaults to 10 tables × 100K rows. Always set `schema` explicitly unless you're tuning against a real database snapshot (see below).

## Placeholders

| Placeholder | Resolved to | Use for |
| --- | --- | --- |
| `{id}` | random integer in `[1, table_size]` | Point lookups by primary key. |
| `{k_val}` | random integer in `[1, table_size]` | Secondary-index lookups. |
| `{threshold}` | random integer in `[1, 10000]` | Range-scan thresholds. |
| `{low}` / `{high}` | a random `(low, high)` pair with `low < high`, both in `[1, table_size]` | Range scans. |
| `{table}` | random table from `sbtest1..sbtest{tables}` | Multi-table workload distribution. |
| `{table2}` | a different random table | Cross-table joins. |

Placeholders are resolved per-query-instance inside `WorkloadExecutor._instantiate_query()` using `numpy.random.default_rng()`. The RNG is seeded from the orchestrator's `random_seed` so a session with a fixed seed produces the same query stream.

If your SQL needs literal curly braces (e.g. JSON path operators in PostgreSQL), escape them by doubling: `{{` and `}}`.

## Weights are relative, not absolute

A workload with weights `[0.3, 0.5, 0.2]` and `[3, 5, 2]` produce identical query streams — the loader normalises to sum 1. So you can think in raw frequencies, percentages, or fractions; whatever is clearest for the workload you're modelling.

The weights are sampled with replacement (i.e. each query draw is independent), so over a 30-second measurement window the realised query mix matches the weights up to sampling noise. For low-weight queries (< 5%) consider increasing the measurement duration to see them sampled enough times for stable percentile estimates.

---

## Step-by-step

### 1. Capture your real query distribution

If you're tuning for a production application, capture the top queries from `pg_stat_statements`:

```sql
WITH total AS (SELECT sum(calls) AS total_calls FROM pg_stat_statements)
SELECT
    query,
    calls,
    ROUND((calls::numeric / total.total_calls::numeric), 4) AS weight,
    mean_exec_time
FROM pg_stat_statements, total
ORDER BY calls DESC
LIMIT 20;
```

The `weight` column is already in the format the workload file expects.

### 2. Author the JSON

Save under `workloads/my_app.json`:

```json
{
  "name": "My App Production Trace",
  "description": "Top 10 queries from pg_stat_statements (week of 2026-06-01)",
  "schema": {
    "tables": 8,
    "table_size": 250000
  },
  "queries": [
    { "sql": "SELECT * FROM users WHERE id = {id}", "weight": 0.35, "description": "user lookup by primary key" },
    { "sql": "SELECT * FROM orders WHERE user_id = {id} ORDER BY created_at DESC LIMIT 20", "weight": 0.20, "description": "recent orders" },
    { "sql": "INSERT INTO events (user_id, kind, payload) VALUES ({id}, 'click', '{}'::jsonb)", "weight": 0.15, "description": "event ingestion" },
    { "sql": "UPDATE users SET last_seen_at = NOW() WHERE id = {id}", "weight": 0.10, "description": "session heartbeat" },
    { "sql": "SELECT user_id, COUNT(*) FROM events WHERE kind = 'click' AND created_at > NOW() - INTERVAL '1 day' GROUP BY user_id LIMIT 100", "weight": 0.05, "description": "daily aggregation" }
  ]
}
```

### 3. Run the tuner against your workload

```bash
python -m src.tuners.pbt \
    --workload-file workloads/my_app.json \
    --tier core \
    --config standard \
    --population 4 \
    --generations 30
```

The orchestrator will:

1. Initialise schema (`tables` × `table_size` `sbtest`-shaped tables) on each worker's PostgreSQL instance.
2. Extract a [workload feature vector](../architecture/feature-driven-scoring.md#workload-features) from the query templates and schema metadata. This drives the feature-driven scoring policy's metric weights — read-heavy workloads weight latency p95 higher, write-heavy workloads weight throughput and variance, OLAP-shaped workloads weight p99 and tail amplification.
3. Run PBT for the requested generations.

### 4. Inspect the workload features in the session JSON

Check that the extracted features match your intuition:

```bash
python -c "
import json
data = json.load(open('results/.../tuning_sessions/pbt_results_<timestamp>.json'))
print(json.dumps(data['workload_features'], indent=2))
"
```

Expected output for the example above (read-heavy with some writes and one aggregation):

```json
{
  "read_ratio": 0.60,
  "write_ratio": 0.25,
  "olap_complexity": 0.18,
  "join_intensity": 0.0,
  "aggregation_intensity": 0.05,
  "concurrency_pressure": 0.42,
  ...
}
```

If the features look off (e.g. `olap_complexity` near 1 for a clearly OLTP workload), the SQL templates may be parsing in an unexpected way — file an issue with the offending SQL.

---

## Variant: tuning against a real database snapshot

If you have a production replica with the actual schema and data, you don't need the `schema` block or the placeholders at all. The orchestrator uses `pg_basebackup` to clone the source database into each worker's PostgreSQL instance.

```bash
export DB_HOST=my-production-replica.internal
export DB_PORT=5432
export DB_USER=admin
export DB_PASSWORD=...
export DB_NAME=myapp

python -m src.tuners.pbt \
    --workload-file workloads/my_real_queries.json \
    --tier core \
    --config standard
```

Where `my_real_queries.json` is **without** placeholders or `schema`:

```json
{
  "name": "Production Trace (real schema)",
  "queries": [
    { "sql": "SELECT SUM(salary) FROM employees WHERE department = 'Sales'", "weight": 0.6 },
    { "sql": "SELECT * FROM orders o JOIN customers c ON o.customer_id = c.id LIMIT 10", "weight": 0.4 }
  ]
}
```

Each worker gets its own isolated clone of the source database; the source replica is read-only from `pg_basebackup`'s perspective. See [benchmarking §Tuning Against a Real Database Snapshot](../reference/benchmarking.md) for the full workflow including a `pg_stat_statements` capture script.

---

## Validating before launch

Before committing to a long PBT run:

```bash
# Smoke-test the workload file parses
python -c "
from src.tuner.benchmark.workload import WorkloadFileLoader
exe = WorkloadFileLoader.load_from_file('workloads/my_app.json')
print(f'queries: {len(exe.queries)}')
print(f'weights: {exe.weights}')
print(f'tables: {exe.num_tables}, size: {exe.table_size}')
"

# Confirm the feature extractor parses the SQL templates correctly
python -c "
from src.utils.scoring.workload_features import WorkloadFeatureExtractor
from src.tuner.benchmark.workload import extract_workload_template_metadata
meta = extract_workload_template_metadata('workloads/my_app.json')
features = WorkloadFeatureExtractor.from_template_metadata(meta).extract()
print(features)
"
```

If both run cleanly, the workload is ready for a full session.

---

## Common pitfalls

| Pitfall | Symptom | Fix |
| --- | --- | --- |
| Curly braces in literal SQL | `KeyError` during query instantiation | Escape with `{{` and `}}`. |
| Weights don't sum to 1 | None — they're auto-normalised | Optional cleanup, not a bug. |
| Missing `schema` block | Warning in log; uses 10×100K default | Add `schema` explicitly to make the test bed reproducible. |
| `{table}` in a workload that's tuning a real database | "Relation sbtestN does not exist" | Drop the placeholder; use the real table names. |
| One query at weight 0.99, others at 0.0025 | Low-weight queries never sampled in 30s window | Increase `--duration` or rebalance weights. |
| Custom workload featured 100% writes | Feature extractor flags as `write_ratio: 1.0` and the scoring policy heavily weights `latency_variance` | Expected behaviour — write-heavy workloads should be scored on tail-latency stability. |
| `WorkloadExecutor` blocked on a slow query | Generation appears stuck | Confirm the query actually completes against PostgreSQL directly; the orchestrator's `vacuum_analyze_timeout_seconds` only bounds maintenance, not the workload itself. |

For the more elaborate format reference (validation rules, all placeholder semantics, YAML support), see [workloads/README](../../workloads/README.md).
