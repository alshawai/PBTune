# Worker Scoring Tests

> 14 nodes · cohesion 0.25

## Key Concepts

- **TPC-H Q2: Minimum Cost Supplier (correlated subquery across part, supplier, partsupp, nation, region)** (4 connections) — `src/benchmarks/tpch/tpch-dbgen/queries/2.sql`
- **TPC-H Q20: Potential Part Promotion (supplier-nation with nested partsupp/part/lineitem subqueries)** (4 connections) — `src/benchmarks/tpch/tpch-dbgen/queries/20.sql`
- **TPC-H Q21: Suppliers Who Kept Orders Waiting (supplier-lineitem-orders-nation with EXISTS/NOT EXISTS)** (4 connections) — `src/benchmarks/tpch/tpch-dbgen/queries/21.sql`
- **TPC-H Q3: Shipping Priority (customer-orders-lineitem join with revenue aggregation)** (4 connections) — `src/benchmarks/tpch/tpch-dbgen/queries/3.sql`
- **TPC-H Q5: Local Supplier Volume (6-table join: customer, orders, lineitem, supplier, nation, region)** (4 connections) — `src/benchmarks/tpch/tpch-dbgen/queries/5.sql`
- **TPC-H Q7: Volume Shipping (supplier-lineitem-orders-customer-nation bilateral trade analysis)** (4 connections) — `src/benchmarks/tpch/tpch-dbgen/queries/7.sql`
- **TPC-H Q9: Product Type Profit Measure (part, supplier, lineitem, partsupp, orders, nation profit calculation)** (4 connections) — `src/benchmarks/tpch/tpch-dbgen/queries/9.sql`
- **TPC-H Q10: Returned Item Reporting (customer-orders-lineitem-nation join filtering on returnflag='R')** (3 connections) — `src/benchmarks/tpch/tpch-dbgen/queries/10.sql`
- **TPC-H Q11: Important Stock Identification (partsupp-supplier-nation with HAVING threshold subquery)** (3 connections) — `src/benchmarks/tpch/tpch-dbgen/queries/11.sql`
- **TPC-H Q16: Parts/Supplier Relationship (partsupp-part with NOT IN subquery excluding flagged suppliers)** (3 connections) — `src/benchmarks/tpch/tpch-dbgen/queries/16.sql`
- **TPC-H Q8: National Market Share (8-table join: part, supplier, lineitem, orders, customer, nation x2, region)** (3 connections) — `src/benchmarks/tpch/tpch-dbgen/queries/8.sql`
- **TPC-H Q12: Shipping Modes and Order Priority (orders-lineitem join with CASE priority classification)** (2 connections) — `src/benchmarks/tpch/tpch-dbgen/queries/12.sql`
- **TPC-H Q18: Large Volume Customer (customer-orders-lineitem with IN subquery on sum quantity threshold)** (2 connections) — `src/benchmarks/tpch/tpch-dbgen/queries/18.sql`
- **TPC-H Q4: Order Priority Checking (orders with EXISTS subquery on lineitem)** (2 connections) — `src/benchmarks/tpch/tpch-dbgen/queries/4.sql`

## Relationships

- [[TPC-H DBGEN Queries]] (46 shared connections)

## Source Files

- `src/benchmarks/tpch/tpch-dbgen/queries/10.sql`
- `src/benchmarks/tpch/tpch-dbgen/queries/11.sql`
- `src/benchmarks/tpch/tpch-dbgen/queries/12.sql`
- `src/benchmarks/tpch/tpch-dbgen/queries/16.sql`
- `src/benchmarks/tpch/tpch-dbgen/queries/18.sql`
- `src/benchmarks/tpch/tpch-dbgen/queries/2.sql`
- `src/benchmarks/tpch/tpch-dbgen/queries/20.sql`
- `src/benchmarks/tpch/tpch-dbgen/queries/21.sql`
- `src/benchmarks/tpch/tpch-dbgen/queries/3.sql`
- `src/benchmarks/tpch/tpch-dbgen/queries/4.sql`
- `src/benchmarks/tpch/tpch-dbgen/queries/5.sql`
- `src/benchmarks/tpch/tpch-dbgen/queries/7.sql`
- `src/benchmarks/tpch/tpch-dbgen/queries/8.sql`
- `src/benchmarks/tpch/tpch-dbgen/queries/9.sql`

## Audit Trail

- EXTRACTED: 0 (0%)
- INFERRED: 46 (100%)
- AMBIGUOUS: 0 (0%)

---

*Part of the graphify knowledge wiki. See [[index]] to navigate.*