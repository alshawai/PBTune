# Database Operations

> 8 nodes · cohesion 0.29

## Key Concepts

- **setup_dbgen.py** (4 connections) — `src/benchmarks/tpch/setup_dbgen.py`
- **_compile_dbgen()** (4 connections) — `src/benchmarks/tpch/setup_dbgen.py`
- **find_or_build_dbgen()** (4 connections) — `src/benchmarks/tpch/setup_dbgen.py`
- **generate_data()** (4 connections) — `src/benchmarks/tpch/setup_dbgen.py`
- **TPC-H dbgen Setup =================  Automatically locates or compiles the TPC-H** (1 connections) — `src/benchmarks/tpch/setup_dbgen.py`
- **Generate TPC-H .tbl data files using dbgen.      Parameters     ----------     d** (1 connections) — `src/benchmarks/tpch/setup_dbgen.py`
- **Locate an existing dbgen binary or compile from source.      Search order:     1** (1 connections) — `src/benchmarks/tpch/setup_dbgen.py`
- **Clone electrum/tpch-dbgen and compile with make.** (1 connections) — `src/benchmarks/tpch/setup_dbgen.py`

## Relationships

- [[DBGEN Compilation]] (16 shared connections)
- [[BO Config & Worker]] (2 shared connections)
- [[Benchmark Executor Base]] (2 shared connections)

## Source Files

- `src/benchmarks/tpch/setup_dbgen.py`

## Audit Trail

- EXTRACTED: 16 (80%)
- INFERRED: 4 (20%)
- AMBIGUOUS: 0 (0%)

---

*Part of the graphify knowledge wiki. See [[index]] to navigate.*