# Benchmark Validation Tests

> 17 nodes · cohesion 0.17

## Key Concepts

- **speed_seed.c** (9 connections) — `src/benchmarks/tpch/tpch-dbgen/speed_seed.c`
- **rnd.c** (6 connections) — `src/benchmarks/tpch/tpch-dbgen/rnd.c`
- **NextRand()** (5 connections) — `src/benchmarks/tpch/tpch-dbgen/rnd.c`
- **dss_random()** (4 connections) — `src/benchmarks/tpch/tpch-dbgen/rnd.c`
- **UnifInt()** (4 connections) — `src/benchmarks/tpch/tpch-dbgen/rnd.c`
- **NthElement()** (4 connections) — `src/benchmarks/tpch/tpch-dbgen/speed_seed.c`
- **rng64.c** (3 connections) — `src/benchmarks/tpch/tpch-dbgen/rng64.c`
- **row_stop()** (3 connections) — `src/benchmarks/tpch/tpch-dbgen/rnd.c`
- **advanceStream()** (3 connections) — `src/benchmarks/tpch/tpch-dbgen/speed_seed.c`
- **AdvanceRand64()** (2 connections) — `src/benchmarks/tpch/tpch-dbgen/rng64.c`
- **fake_a_rnd()** (2 connections) — `src/benchmarks/tpch/tpch-dbgen/speed_seed.c`
- **sd_line()** (2 connections) — `src/benchmarks/tpch/tpch-dbgen/speed_seed.c`
- **sd_order()** (2 connections) — `src/benchmarks/tpch/tpch-dbgen/speed_seed.c`
- **sd_cust()** (1 connections) — `src/benchmarks/tpch/tpch-dbgen/speed_seed.c`
- **sd_part()** (1 connections) — `src/benchmarks/tpch/tpch-dbgen/speed_seed.c`
- **sd_psupp()** (1 connections) — `src/benchmarks/tpch/tpch-dbgen/speed_seed.c`
- **sd_supp()** (1 connections) — `src/benchmarks/tpch/tpch-dbgen/speed_seed.c`

## Relationships

- [[Knob Validation Tests]] (46 shared connections)
- [[Knob Validation]] (4 shared connections)
- [[Docker Manifest Tests]] (3 shared connections)

## Source Files

- `src/benchmarks/tpch/tpch-dbgen/rnd.c`
- `src/benchmarks/tpch/tpch-dbgen/rng64.c`
- `src/benchmarks/tpch/tpch-dbgen/speed_seed.c`

## Audit Trail

- EXTRACTED: 44 (83%)
- INFERRED: 9 (17%)
- AMBIGUOUS: 0 (0%)

---

*Part of the graphify knowledge wiki. See [[index]] to navigate.*