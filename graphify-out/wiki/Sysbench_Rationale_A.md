# Sysbench Rationale A

> 2 nodes · cohesion 1.00

## Key Concepts

- **distribution struct (weighted text sets for column generation)** (1 connections) — `src/benchmarks/tpch/tpch-dbgen/dss.h`
- **tdef struct (table definition: name, base cardinality, loader, seed)** (1 connections) — `src/benchmarks/tpch/tpch-dbgen/dss.h`

## Relationships

- [[DBGEN Data Types]] (2 shared connections)

## Source Files

- `src/benchmarks/tpch/tpch-dbgen/dss.h`

## Audit Trail

- EXTRACTED: 0 (0%)
- INFERRED: 2 (100%)
- AMBIGUOUS: 0 (0%)

---

*Part of the graphify knowledge wiki. See [[index]] to navigate.*