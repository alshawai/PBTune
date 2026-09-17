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

# 2. Run (sysbench has no warmup flag — warmup is folded into --time
#         and trimmed by the parser)
sysbench oltp_read_write \
    --db-driver=pgsql \
    --pgsql-host=127.0.0.1 --pgsql-port={port} \
    --pgsql-db={dbname} --pgsql-user={user} --pgsql-password={password} \
    --tables=10 --table-size=100000 \
    --threads={threads} --time={duration + warmup} \
    --report-interval=1 --percentile=99 --histogram=on \
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
- Non-zero exit code → executor raises `RuntimeError`; the orchestrator's workload-execution
  handler tags `failure_type = "EXECUTION_CRASH"` and returns zeroed metrics
- Parsed throughput of 0 → executor raises `RuntimeError` (same path; there is no
  `benchmark_crash` / `output_parse_error` failure type)

### Warmup Handling
sysbench is invoked with `--time={duration + warmup}` — no warmup flag is passed.
`_parse_output` then discards the first `max(0, len(samples) // 4)` `--report-interval`
samples before computing steady-state throughput/latency variance.

---

## TPC-H OLAP Execution

### Power Test
Sequential execution of all 22 TPC-H queries. No parallelism.

```python
latencies_ms = []
for i in range(1, 23):
    sql = load_query(f"{i}.sql")      # query files are 1.sql .. 22.sql (no "q" prefix)
    start = time.time()
    cursor.execute(sql)
    cursor.fetchall()
    latencies_ms.append((time.time() - start) * 1000.0)

# Metrics: latency percentiles + QphH throughput (no Power@Size / geometric mean)
metrics.latency_p50 = float(np.percentile(sorted(latencies_ms), 50))
metrics.latency_p95 = float(np.percentile(sorted(latencies_ms), 95))
metrics.latency_p99 = float(np.percentile(sorted(latencies_ms), 99))
metrics.throughput = (total_queries / total_time) * 3600.0
metrics.throughput_unit = "QphH"
```

Any query error or statement timeout fast-fails the run: `execute()` returns a fatal
penalty with `failure_type = "query_failed_or_timeout"` (there is no partial scoring).

### Statement Timeout
Scales with `scale_factor`, floored at 60 s:
```python
timeout_ms = max(60000, int(300000 * scale_factor))   # base 5 min, 60 s floor
cursor.execute(f"SET statement_timeout = {timeout_ms}")   # unquoted
```

### Data Generation
```bash
# dbgen generates TPC-H data files
./dbgen -s {scale_factor} -f
# Then loaded into PostgreSQL tables via COPY
```

---

## WorkloadOrchestrator Pipeline

The `WorkloadOrchestrator` class (`src/tuners/engine/orchestrator.py`) orchestrates
the full evaluation of a single worker:

```
evaluate_worker(worker):
    ├── apply_configuration(worker.knob_config)
    │   ├── Validate knobs against pg_settings (type/bounds/context)
    │   ├── Apply via ALTER SYSTEM SET (→ postgresql.auto.conf)
    │   ├── _perform_restart() if postmaster knobs changed
    │   └── _verify_and_capture_config() → KnobApplicator.verify()
    ├── _ensure_benchmark_ready()
    │   └── Check tables exist, restore snapshot if needed
    ├── _vacuum_after_dml() — VACUUM ANALYZE after DML warmup
    ├── executor.execute(ctx)
    │   └── SysbenchExecutor.execute(ctx) or TPCHExecutor.execute(ctx)
    ├── collect_system_metrics()
    │   └── psutil: CPU%, memory%, I/O counters
    └── Return (PerformanceMetrics, score)
```

### Configuration Verification
After applying config, the orchestrator reads back the *applied* (quantised) values:
```python
def _verify_and_capture_config(self, worker):
    # KnobApplicator.verify() queries pg_settings for each knob:
    #   SELECT setting, unit, vartype FROM pg_settings WHERE name = %s
    # and returns the typed, PostgreSQL-quantised values actually in effect.
    return self.applicator.verify(worker.knob_config)
```
