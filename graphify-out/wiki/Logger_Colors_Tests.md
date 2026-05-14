# Logger Colors Tests

> 15 nodes · cohesion 0.20

## Key Concepts

- **Shared field-length constants for all TPC-H table columns** (7 connections) — `src/benchmarks/tpch/tpch-dbgen/shared.h`
- **line_t struct (TPC-H Lineitem table row)** (4 connections) — `src/benchmarks/tpch/tpch-dbgen/dsstypes.h`
- **supplier_t struct (TPC-H Supplier table row)** (4 connections) — `src/benchmarks/tpch/tpch-dbgen/dsstypes.h`
- **DSS schema model: table IDs, distributions, seed indexes, output macros** (3 connections) — `src/benchmarks/tpch/tpch-dbgen/dss.h`
- **code_t struct (Nation/Region reference table row)** (3 connections) — `src/benchmarks/tpch/tpch-dbgen/dsstypes.h`
- **customer_t struct (TPC-H Customer table row)** (3 connections) — `src/benchmarks/tpch/tpch-dbgen/dsstypes.h`
- **order_t struct (TPC-H Orders table row, embeds line_t[])** (3 connections) — `src/benchmarks/tpch/tpch-dbgen/dsstypes.h`
- **partsupp_t struct (TPC-H PartSupp table row)** (3 connections) — `src/benchmarks/tpch/tpch-dbgen/dsstypes.h`
- **Platform portability layer (DSS_HUGE typedef, RNG constants, process control)** (2 connections) — `src/benchmarks/tpch/tpch-dbgen/config.h`
- **part_t struct (TPC-H Part table row, embeds partsupp_t[])** (2 connections) — `src/benchmarks/tpch/tpch-dbgen/dsstypes.h`
- **32-bit PRNG engine (NextRand/UnifInt) with per-row seed table** (2 connections) — `src/benchmarks/tpch/tpch-dbgen/rnd.h`
- **Seed[MAX_STREAM+1] array (48 per-column seed initializers)** (2 connections) — `src/benchmarks/tpch/tpch-dbgen/rnd.h`
- **64-bit RNG extension (AdvanceRand64, NextRand64, dss_random64)** (2 connections) — `src/benchmarks/tpch/tpch-dbgen/rng64.h`
- **BCD2 Arbitrary-Precision Arithmetic API** (1 connections) — `src/benchmarks/tpch/tpch-dbgen/bcd2.h`
- **seed_t struct (per-column RNG state: table, value, usage, boundary)** (1 connections) — `src/benchmarks/tpch/tpch-dbgen/dss.h`

## Relationships

- [[Safety Validation]] (42 shared connections)

## Source Files

- `src/benchmarks/tpch/tpch-dbgen/bcd2.h`
- `src/benchmarks/tpch/tpch-dbgen/config.h`
- `src/benchmarks/tpch/tpch-dbgen/dss.h`
- `src/benchmarks/tpch/tpch-dbgen/dsstypes.h`
- `src/benchmarks/tpch/tpch-dbgen/rnd.h`
- `src/benchmarks/tpch/tpch-dbgen/rng64.h`
- `src/benchmarks/tpch/tpch-dbgen/shared.h`

## Audit Trail

- EXTRACTED: 38 (90%)
- INFERRED: 4 (10%)
- AMBIGUOUS: 0 (0%)

---

*Part of the graphify knowledge wiki. See [[index]] to navigate.*