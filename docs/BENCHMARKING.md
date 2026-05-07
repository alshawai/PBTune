# Dual-Evaluation Benchmarking Strategy

> Last reviewed: 2026-03-13

See also: [Documentation Index](./README.md)

The Population-Based Training (PBT) Auto-Tuning framework employs a unique **Dual-Evaluation Benchmarking Strategy** designed to support both rigorous academic peer-review and flexible real-world application tuning.

## Architecture: SchemaProvider Protocol

All executors implement a common **SchemaProvider** interface (`prepare()` + `validate()`), allowing the `PostgresInstanceManager` to initialize worker database schemas without knowing benchmark-specific details.

```
┌─────────────────────────┐  ┌──────────────────────────┐  ┌──────────────────────────┐
│   SysbenchExecutor      │  │   TPCHExecutor           │  │   WorkloadExecutor       │
│   (C-binary benchmark)  │  │   (dbgen + psycopg2)     │  │   (JSON/YAML templates)  │
│                         │  │                          │  │                          │
│  prepare() → sysbench   │  │  prepare() → dbgen+COPY  │  │  prepare() → sysbench    │
│  validate() → SQL count │  │  validate() → SQL count  │  │  validate() → SQL count  │
│  execute() → sysbench   │  │  execute() → psycopg2    │  │  execute() → Python SQL  │
└────────────┬────────────┘  └─────────────┬────────────┘  └─────────────┬────────────┘
             │              SchemaProvider │                             │
             └─────────────────────────────│─────────────────────────────┘
                                           ▼
                            ┌──────────────────────────┐
                            │  PostgresInstanceManager │
                            │                          │
                            │  _initialize_schema():   │
                            │    if !validate() →      │
                            │       prepare()          │
                            └──────────────────────────┘
```

All executors implement the common **SchemaProvider** interface, allowing the `PostgresInstanceManager` to initialize worker database schemas without knowing benchmark-specific details.

## 1. Academic Validation: External C-Binary Benchmarks

When testing auto-tuning algorithms like PBT for academic publication (to compare against tools like OtterTune or CDBTune), using a high-level interpreted language like Python to execute queries can introduce network and GIL-related latency bottlenecks. The database may end up sitting idle waiting for the Python client to send the next query, skewing evaluation metrics.

To ensure **overhead-free, scientifically rigorous evaluations**, the tuner supports delegating workload generation entirely to standard external C-binary drivers via the `--benchmark` flag.

### Supported External Drivers:

- **Sysbench (OLTP)**: The industry standard for transactional database benchmarking.
  - _Modes_: `oltp_read_only`, `oltp_read_write`, `oltp_write_only`
  - _Configuration_: 10 tables × 100,000 rows (scale factor 1), 8 threads per worker
  - _Metrics_: TPS (Transactions Per Second) + p95 Transaction Latency (ms)
  - _Usage_: `python -m src.tuner.main --benchmark sysbench --sysbench-workload oltp_read_write`
- **TPC-H (OLAP)**: The gold standard for analytical queries.
  - _Configuration_: 8 tables, 22 standard decision-support queries, configurable scale factor (default SF=1 → ~1GB data, ~6M `lineitem` rows per worker)
  - _Metrics_: Query Throughput (QPS) + p50/p95 Query Latency (ms)
  - _Usage_: `python -m src.tuner.main --benchmark tpch [--scale-factor 1.0]`
  - _Data Generation_: Uses the standard `dbgen` C-binary. On first run, the tuner automatically clones the trusted [`electrum/tpch-dbgen`](https://github.com/electrum/tpch-dbgen) mirror, compiles it, and generates `.tbl` data files. Requires `build-essential` (`sudo apt install build-essential`).
  - _Data Loading_: Uses `psycopg2.copy_expert()` with PostgreSQL `COPY` for fast bulk loading directly from `.tbl` files (no intermediate CSVs).

_Why Python is acceptable for TPC-H:_ Unlike Sysbench where throughput (queries per microsecond) is tested, TPC-H consists of 22 complex queries each taking several seconds or minutes. Because the database execution time completely dwarfs the millisecond overhead of a Python client submitting the query string over the network, using Python wrappers for TPC-H is mathematically sound and widely accepted in academic tuning literature.

## 2. Real-World Prototyping: Internal JSON Templates

For developers and companies wanting to tune PostgreSQL for their _actual_ production applications, standard benchmarks are irrelevant. Production apps run unique, messy query mixes.

To solve this, the PBT tuner fundamentally supports an internal Python-based `WorkloadExecutor` that parses generic `JSON` or `YAML` templates.

### Multi-Table Schema Support

Built-in templates use a configurable `schema` section to declare the number of tables and rows:

```json
{
  "schema": {
    "tables": 10,
    "table_size": 100000
  },
  "queries": [{ "sql": "SELECT * FROM {table} WHERE id = {id}", "weight": 0.4 }]
}
```

The `{table}` placeholder is randomly resolved to any of the declared tables at execution time, simulating realistic multi-table production load distribution. Templates also support `{table2}` for cross-table JOINs.

### Custom Workloads

Developers can copy logs from `pg_stat_statements`, define them in a custom `.json` file with appropriate probability weights, and instantly run a tuning session tailored to their proprietary application without writing custom C++ testing harnesses.

- _Usage_: `python -m src.tuner.main --workload-file workloads/my_custom_app.json`

> **Note:** Custom workloads without a `schema` section will trigger a warning and default to 1 table with 100K rows.

### Tuning Against a Real Database Snapshot

If you have a production replica and want the tuner to optimize against your _actual_ schema and data (not just `sbtest` tables), you do not need the `schema` block or placeholders at all.

The PBT tuner uses standard PostgreSQL tools (`pg_basebackup`) to clone whichever database you point it to.

**Workflow for tuning a real database:**

1. **Provide connection details to your real database** via environment variables:

   ```bash
   export DB_HOST=my-production-replica.domain.com
   export DB_PORT=5432
   export DB_USER=admin
   export DB_PASSWORD=secret
   export DB_NAME=myapp
   ```

2. **Extract your top queries and their weights** from your production database. Run this query to get your most frequent statements along with automatically calculated JSON weights:

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

3. **Prepare a custom workload without placeholders** containing the extracted queries (`my_real_queries.json`):

   ```json
   {
     "name": "Production Trace",
     "queries": [
       {
         "sql": "SELECT SUM(salary) FROM employees WHERE department = 'Sales';",
         "weight": 0.6
       },
       {
         "sql": "SELECT * FROM orders o JOIN customers c ON o.customer_id = c.id LIMIT 10;",
         "weight": 0.4
       }
     ]
   }
   ```

   _(Notice there are no `{table}` or `{id}` placeholders — the `WorkloadExecutor` natively supports raw unparameterized SQL)._

4. **Run the tuner:**
   ```bash
   python -m src.tuner.main --workload-file workloads/my_real_queries.json
   ```

**What happens under the hood:**
The tuner connects to your `DB_HOST`, uses `pg_basebackup` to pull a binary snapshot of your entire database locally, spins up 4 isolated parallel worker instances from that exact snapshot, and executes your raw queries against them, scoring the performance of different knob configurations.

## Note on PBT Relative Scoring

When configuring the population-based training, the internal PBT algorithm does not cross-compare raw benchmark numbers across these two methods. It records benchmark metrics, then converts them through the shared scoring-v2 pipeline documented in [Feature-Driven Scoring](./FEATURE_DRIVEN_SCORING.md).

The practical implication is that the benchmark driver can differ, but the scorer remains the same. That keeps Sysbench, TPC-H, and template workloads comparable within a single policy version while preserving compatibility with historical `fixed_v1` sessions.

## Benchmark Executor Interface

All benchmark executors implement a common interface for schema management and execution:

### SchemaProvider Protocol

- `prepare(db_config)`: Initialize benchmark schema (create tables, load data)
- `validate(db_config)`: Verify schema matches expected configuration
- `execute(db_config, duration)`: Run benchmark and return metrics

### Executor Implementations

#### SysbenchExecutor

- **Workloads**: oltp_read_only, oltp_read_write, oltp_write_only
- **Configuration**: Threads, tables, table size
- **Metrics**: TPS, p95/p99 latency
- **Implementation**: Delegates to external `sysbench` binary

#### TPCHExecutor

- **Workloads**: 22 standard TPC-H queries
- **Configuration**: Scale factor, warmup passes
- **Metrics**: QphH, p50/p95 query latency
- **Implementation**: Uses `dbgen` for data generation, `psycopg2` for execution

#### WorkloadExecutor

- **Workloads**: Custom JSON/YAML templates
- **Configuration**: Query list with weights, schema metadata
- **Metrics**: Custom metrics from query execution
- **Implementation**: Pure Python SQL execution

## Benchmark Validation and Reproducibility

Each executor validates schema state before execution:

1. **Table Count**: Verify expected number of tables exist
2. **Row Count**: Check table cardinality matches configuration
3. **Schema Consistency**: Ensure no leftover tables from previous benchmarks
4. **Data Integrity**: Validate data hasn't been corrupted

Validation failures trigger automatic schema preparation to ensure consistent starting state.

## Performance Metrics Collection

Benchmark executors collect standardized metrics:

- **Throughput**: Transactions per second (OLTP) or queries per hour (OLAP)
- **Latency**: P50, P95, P99 percentiles in milliseconds
- **Resource Utilization**: CPU, memory, I/O statistics
- **Error Rates**: Failed transactions or queries
- **Stability**: Variance across measurement intervals

These metrics are normalized through the scoring-v2 pipeline for cross-benchmark comparison.

