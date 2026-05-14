# DB Connection Reuse

> 16 nodes · cohesion 0.23

## Key Concepts

- **gen_tbl()** (12 connections) — `src/benchmarks/tpch/tpch-dbgen/driver.c`
- **build.c** (10 connections) — `src/benchmarks/tpch/tpch-dbgen/build.c`
- **mk_order()** (7 connections) — `src/benchmarks/tpch/tpch-dbgen/build.c`
- **mk_part()** (5 connections) — `src/benchmarks/tpch/tpch-dbgen/build.c`
- **mk_ascdate()** (4 connections) — `src/benchmarks/tpch/tpch-dbgen/bm_utils.c`
- **mk_cust()** (4 connections) — `src/benchmarks/tpch/tpch-dbgen/build.c`
- **julian()** (3 connections) — `src/benchmarks/tpch/tpch-dbgen/bm_utils.c`
- **gen_phone()** (3 connections) — `src/benchmarks/tpch/tpch-dbgen/build.c`
- **mk_sparse()** (3 connections) — `src/benchmarks/tpch/tpch-dbgen/build.c`
- **mk_supp()** (3 connections) — `src/benchmarks/tpch/tpch-dbgen/build.c`
- **mk_time()** (3 connections) — `src/benchmarks/tpch/tpch-dbgen/build.c`
- **rpb_routine()** (3 connections) — `src/benchmarks/tpch/tpch-dbgen/build.c`
- **mk_nation()** (2 connections) — `src/benchmarks/tpch/tpch-dbgen/build.c`
- **mk_region()** (2 connections) — `src/benchmarks/tpch/tpch-dbgen/build.c`
- **dump_seeds()** (2 connections) — `src/benchmarks/tpch/tpch-dbgen/rnd.c`
- **row_start()** (2 connections) — `src/benchmarks/tpch/tpch-dbgen/rnd.c`

## Relationships

- [[Knob Validation]] (51 shared connections)
- [[Evolution Tests]] (10 shared connections)
- [[Knob Validation Tests]] (3 shared connections)
- [[Docker Manifest Tests]] (3 shared connections)
- [[Convergence Tests]] (1 shared connections)

## Source Files

- `src/benchmarks/tpch/tpch-dbgen/bm_utils.c`
- `src/benchmarks/tpch/tpch-dbgen/build.c`
- `src/benchmarks/tpch/tpch-dbgen/driver.c`
- `src/benchmarks/tpch/tpch-dbgen/rnd.c`

## Audit Trail

- EXTRACTED: 37 (54%)
- INFERRED: 31 (46%)
- AMBIGUOUS: 0 (0%)

---

*Part of the graphify knowledge wiki. See [[index]] to navigate.*