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
  - _Configuration_: 10 tables × 100,000 rows (scale factor 1), 8 threads per worker
  - _Metrics_: TPS (Transactions Per Second) + p95 Transaction Latency (ms)
  - _Usage_: `python -m src.tuner.main --benchmark sysbench`
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

When configuring the population-based training, the internal PBT algorithm does not cross-compare raw numbers across these two methods. It simply records the relative percentage improvement (e.g., "Configuration X improved throughput by 43% against configuration Y"). Thus, both approaches are completely valid and scientifically sound so long as the developer does not switch from internal tests to external tests mid-run.

## 3. Bayesian Optimization Baseline Comparison

> Added: 2026-04-23

Academic reviewers expect a direct comparison between PBT and the dominant paradigm for database auto-tuning: Bayesian Optimization (BO). The BO baseline runner (`src/scripts/run_bo_comparison.py`) provides a controlled, fair comparison by reusing the exact same evaluation pipeline as the PBT tuner.

### Why BO as a Baseline?

BO is the standard approach used by OtterTune (Aken et al., SIGMOD 2017), LlamaTune (Kanellis et al., VLDB 2022), and GPTuner (Lao et al., VLDB 2024). A direct PBT vs BO comparison on identical hardware, workloads, and scoring rules strengthens any claims about PBT's efficiency or quality.

### Fairness Guarantees

The BO runner shares these components with PBT to ensure an apples-to-apples comparison:

| Component | Shared? | Details |
|-----------|---------|---------|
| Knob Space | ✅ | Same tier system, same `KnobSpace` and `KnobDefinition` objects |
| Hardware Detection | ✅ | Same `detect_worker_resources()` and hardware-aware range resolution |
| Scoring Formula | ✅ | Same `MetricConfig.compute_score()` with identical normalization and weights |
| Workload Executor | ✅ | Same `SysbenchExecutor`, `TPCHExecutor`, or custom `WorkloadExecutor` |
| Environment | ✅ | Same `EnvironmentFactory` (Docker or bare-metal PostgreSQL) |
| Evaluation Pipeline | ✅ | Same `Evaluator.evaluate_worker()` method |

Key difference: BO evaluates configurations **sequentially** using a single PostgreSQL instance (standard BO behavior), while PBT evaluates in **parallel** across multiple instances. This is the intended experimental contrast — BO's sample efficiency vs PBT's parallelism.

### BO Backend: SMAC3

The implementation uses [SMAC3](https://github.com/automl/SMAC3) (Lindauer et al., JMLR 2022) as the BO backend:

- **Surrogate Model:** Random Forest (robust to mixed integer/categorical spaces)
- **Acquisition Function:** Expected Improvement (EI) by default
- **Initial Design:** Sobol sequence (quasi-random, better space coverage than pure random)
- **ConfigSpace Integration:** Native support for integer, float, categorical, and log-scale hyperparameters

### Search Space Mapping

PBT knob types are translated to ConfigSpace hyperparameters:

| PBT KnobType | ConfigSpace Type | Log-Scale? |
|---------------|-----------------|------------|
| `INTEGER` | `Integer` | Yes, if `KnobScale.LOG` and `min > 0` |
| `REAL` | `Float` | Yes, if `KnobScale.LOG` and `min > 0` |
| `BOOLEAN` | `Categorical(["true", "false"])` | N/A |
| `ENUM` | `Categorical(enum_values)` | N/A |

### Quick Start

```bash
# Minimal BO baseline (fastest, for testing)
python -m src.scripts.run_bo_comparison --tier minimal --config rapid

# Standard BO baseline comparable to PBT
python -m src.scripts.run_bo_comparison --tier core --config standard --max-evaluations 30

# BO with Sysbench benchmark
python -m src.scripts.run_bo_comparison --benchmark sysbench --tier core --max-evaluations 50

# BO with TPC-H benchmark
python -m src.scripts.run_bo_comparison --benchmark tpch --tier standard --scale-factor 1.0

# Custom BO parameters
python -m src.scripts.run_bo_comparison \
    --tier core \
    --max-evaluations 100 \
    --initial-design-size 15 \
    --acquisition-function LCB \
    --seed 123
```

### CLI Reference

| Argument | Default | Description |
|----------|---------|-------------|
| `--optimizer-backend` | `smac` | BO library (currently only SMAC3) |
| `--max-evaluations` | `30` | Total BO evaluation budget |
| `--initial-design-size` | auto | Random evaluations before BO model (`max(5, num_knobs)`) |
| `--acquisition-function` | `EI` | Acquisition function: `EI`, `LCB`, or `PI` |
| `--seed` | `42` | Random seed for reproducibility |
| `--tier` | `minimal` | Knob space tier (same as PBT) |
| `--config` | `standard` | PBT config profile for workload settings |
| `--benchmark` | — | External benchmark: `sysbench` or `tpch` |
| `--duration` | config | Per-evaluation measurement duration (seconds) |
| `--warmup` | config | Warmup duration (seconds) |
| `--no-docker` | `false` | Use bare-metal PostgreSQL |
| `--output-dir` | `results` | Base output directory |

### Output Format

Results are saved to `{output_dir}/{workload}/bo_runs/{tier}/tuning_sessions/bo_results_YYYYMMDD_HHMM.json`.

The JSON schema is designed to be compatible with PBT results for direct comparison:

```json
{
  "optimizer": "bayesian_optimization",
  "optimizer_backend": "smac",
  "tuning_session": {
    "knob_tier": "core",
    "num_knobs": 10,
    "workload_type": "oltp",
    "max_evaluations": 30,
    "total_evaluations": 30,
    "total_time_seconds": 1800.0
  },
  "best_configuration": {
    "score": 78.5,
    "knobs": { "shared_buffers": 0.25, "work_mem": 0.015 },
    "metrics": { "throughput": 2100.0, "latency_p95": 8.5 }
  },
  "evaluation_history": [
    { "evaluation": 1, "score": 45.2, "best_score_so_far": 45.2 }
  ],
  "convergence": {
    "history": [45.2, 52.1, 62.8, 78.5],
    "final_best_score": 78.5
  }
}
```

### Designing a Fair PBT vs BO Experiment

For a controlled comparison, match these settings:

1. **Same knob tier:** `--tier core` for both PBT and BO
2. **Same benchmark:** `--benchmark sysbench` (or `tpch`) for both
3. **Same evaluation duration:** `--duration 60` for both
4. **Same random seed:** `--seed 42` for both (where applicable)
5. **Same hardware:** Run both on the same machine, sequentially
6. **Equal evaluation budget:** For wall-clock comparison, set BO's `--max-evaluations` equal to PBT's `population_size × generations`

Example experiment:

```bash
# PBT: 4 workers × 30 generations = 120 total evaluations
python -m src.tuner.main --tier core --config standard \
    --benchmark sysbench --population 4 --generations 30

# BO: 120 sequential evaluations (same total budget)
python -m src.scripts.run_bo_comparison --tier core --config standard \
    --benchmark sysbench --max-evaluations 120
```

Then compare:
- **Sample efficiency:** Best score at each evaluation number
- **Wall-clock efficiency:** Best score over elapsed time
- **Final quality:** Best configuration score and metrics
