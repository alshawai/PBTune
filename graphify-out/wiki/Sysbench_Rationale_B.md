# Sysbench Rationale B

> 2 nodes · cohesion 1.00

## Key Concepts

- **TPC-H Q13: Customer Distribution (customer LEFT OUTER JOIN orders with nested count aggregation)** (1 connections) — `src/benchmarks/tpch/tpch-dbgen/queries/13.sql`
- **TPC-H Q22: Global Sales Opportunity (customer with avg subquery and NOT EXISTS on orders)** (1 connections) — `src/benchmarks/tpch/tpch-dbgen/queries/22.sql`

## Relationships

- [[DBGEN Customer Queries]] (2 shared connections)

## Source Files

- `src/benchmarks/tpch/tpch-dbgen/queries/13.sql`
- `src/benchmarks/tpch/tpch-dbgen/queries/22.sql`

## Audit Trail

- EXTRACTED: 0 (0%)
- INFERRED: 2 (100%)
- AMBIGUOUS: 0 (0%)

---

*Part of the graphify knowledge wiki. See [[index]] to navigate.*