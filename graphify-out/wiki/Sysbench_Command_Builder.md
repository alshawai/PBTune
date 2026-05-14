# Sysbench Command Builder

> 4 nodes · cohesion 0.50

## Key Concepts

- **._build_base_cmd()** (4 connections) — `src/benchmarks/sysbench/executor.py`
- **._run_sysbench()** (4 connections) — `src/benchmarks/sysbench/executor.py`
- **Build the common sysbench CLI prefix (shared by prepare/run).** (1 connections) — `src/benchmarks/sysbench/executor.py`
- **Spawn the sysbench process and wait for completion.** (1 connections) — `src/benchmarks/sysbench/executor.py`

## Relationships

- [[Sysbench Executor Tests]] (2 shared connections)
- [[Benchmark Executor Base]] (1 shared connections)
- [[Visualization Plotting]] (1 shared connections)

## Source Files

- `src/benchmarks/sysbench/executor.py`

## Audit Trail

- EXTRACTED: 10 (100%)
- INFERRED: 0 (0%)
- AMBIGUOUS: 0 (0%)

---

*Part of the graphify knowledge wiki. See [[index]] to navigate.*