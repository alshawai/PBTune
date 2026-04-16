# Benchmark Execution Patterns — Detailed Reference

## Sysbench OLTP Execution

### CLI Pattern
```bash
# 1. Prepare (create tables + load data)
sysbench oltp_read_write \
    --db-driver=pgsql \
    --pgsql-host=127.0.0.1 --pgsql-port={port} \
    --pgsql-db={dbname} --pgsql-user={user} --pgsql-password={password} \
    --tables=10 --table-size=100000 \
    prepare

# 2. Run (with warmup)
sysbench oltp_read_write \
    --db-driver=pgsql \
    --pgsql-host=127.0.0.1 --pgsql-port={port} \
    --pgsql-db={dbname} --pgsql-user={user} --pgsql-password={password} \
    --tables=10 --table-size=100000 \
    --threads={threads} --time={duration} --warmup-time={warmup} \
    --report-interval=1 \
    run

# 3. Cleanup (drop tables)
sysbench oltp_read_write ... cleanup
```

### Output Parsing
```python
# Regex patterns for sysbench output
TPS_PATTERN = r"transactions:\s+\d+\s+\((\d+\.\d+) per sec\.\)"
LATENCY_P95_PATTERN = r"95th percentile:\s+(\d+\.\d+)"
ERROR_PATTERN = r"errors:\s+(\d+)"
```

### Error Handling
- `subprocess.run(timeout=...)` prevents hangs
- Non-zero exit code → `failure_type = "benchmark_crash"`
- Parse errors → `failure_type = "output_parse_error"`

---

## TPC-H OLAP Execution

### Power Test
Sequential execution of all 22 TPC-H queries. No parallelism.

```python
query_times = []
for i in range(1, 23):
    sql = load_query(f"queries/q{i}.sql")
    start = time.time()
    cursor.execute(sql)
    cursor.fetchall()
    elapsed = time.time() - start
    query_times.append(elapsed)

# Metric: geometric mean of all query times
power_at_size = geometric_mean(query_times)
```

### Statement Timeout
Scales with `scale_factor` to accommodate larger datasets:
```python
timeout_ms = base_timeout * scale_factor  # e.g., 60000ms * SF
SET statement_timeout = '{timeout_ms}'
```

### Data Generation
```bash
# dbgen generates TPC-H data files
./dbgen -s {scale_factor} -f
# Then loaded into PostgreSQL tables via COPY
```

---

## WorkloadEvaluator Pipeline

The `WorkloadEvaluator` class (`src/tuner/evaluator/evaluator.py`) orchestrates
the full evaluation of a single worker:

```
evaluate_worker(worker):
    ├── apply_configuration(worker.knob_config)
    │   ├── Separate knobs by context (postmaster/sighup)
    │   ├── Write to postgresql.conf
    │   ├── _perform_restart() if postmaster knobs changed
    │   └── _verify_configuration() — SELECT current_setting()
    ├── _ensure_benchmark_ready()
    │   └── Check tables exist, restore snapshot if needed
    ├── _vacuum_after_dml() — VACUUM ANALYZE after DML warmup
    ├── executor.run_benchmark()
    │   └── SysbenchExecutor.run() or TPCHExecutor.run()
    ├── collect_system_metrics()
    │   └── psutil: CPU%, memory%, I/O counters
    └── Return (PerformanceMetrics, score)
```

### Configuration Verification
After applying config, the evaluator verifies each knob:
```python
def _verify_configuration(self):
    for knob_name, expected_value in config.items():
        actual = connection.execute(
            f"SELECT current_setting('{knob_name}')"
        )
        # Compare with type-aware tolerance
```
