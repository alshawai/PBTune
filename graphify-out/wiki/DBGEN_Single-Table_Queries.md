# DBGEN Single-Table Queries

> 6 nodes · cohesion 0.53

## Key Concepts

- **TPC-H Q1: Pricing Summary Report (lineitem scan with aggregation on returnflag/linestatus)** (3 connections) — `src/benchmarks/tpch/tpch-dbgen/queries/1.sql`
- **TPC-H Q14: Promotion Effect (lineitem-part join computing promotional revenue ratio)** (3 connections) — `src/benchmarks/tpch/tpch-dbgen/queries/14.sql`
- **TPC-H Q17: Small-Quantity-Order Revenue (lineitem-part with correlated subquery on avg quantity)** (3 connections) — `src/benchmarks/tpch/tpch-dbgen/queries/17.sql`
- **TPC-H Q6: Forecasting Revenue Change (lineitem-only scan with date/discount/quantity filters)** (3 connections) — `src/benchmarks/tpch/tpch-dbgen/queries/6.sql`
- **TPC-H Q15: Top Supplier (CREATE VIEW on lineitem revenue, then supplier join to find max revenue)** (2 connections) — `src/benchmarks/tpch/tpch-dbgen/queries/15.sql`
- **TPC-H Q19: Discounted Revenue (lineitem-part with 3-way OR disjunction on brand/container/size)** (2 connections) — `src/benchmarks/tpch/tpch-dbgen/queries/19.sql`

## Relationships

- No strong cross-community connections detected

## Source Files

- `src/benchmarks/tpch/tpch-dbgen/queries/1.sql`
- `src/benchmarks/tpch/tpch-dbgen/queries/14.sql`
- `src/benchmarks/tpch/tpch-dbgen/queries/15.sql`
- `src/benchmarks/tpch/tpch-dbgen/queries/17.sql`
- `src/benchmarks/tpch/tpch-dbgen/queries/19.sql`
- `src/benchmarks/tpch/tpch-dbgen/queries/6.sql`

## Audit Trail

- EXTRACTED: 0 (0%)
- INFERRED: 16 (100%)
- AMBIGUOUS: 0 (0%)

---

*Part of the graphify knowledge wiki. See [[index]] to navigate.*