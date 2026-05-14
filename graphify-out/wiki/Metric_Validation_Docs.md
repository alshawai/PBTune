# Metric Validation Docs

> 10 nodes · cohesion 0.31

## Key Concepts

- **pick_str()** (9 connections) — `src/benchmarks/tpch/tpch-dbgen/bm_utils.c`
- **txt_sentence()** (5 connections) — `src/benchmarks/tpch/tpch-dbgen/text.c`
- **text.c** (4 connections) — `src/benchmarks/tpch/tpch-dbgen/text.c`
- **varsub()** (4 connections) — `src/benchmarks/tpch/tpch-dbgen/varsub.c`
- **agg_str()** (3 connections) — `src/benchmarks/tpch/tpch-dbgen/bm_utils.c`
- **txt_np()** (3 connections) — `src/benchmarks/tpch/tpch-dbgen/text.c`
- **txt_vp()** (3 connections) — `src/benchmarks/tpch/tpch-dbgen/text.c`
- **permute()** (2 connections) — `src/benchmarks/tpch/tpch-dbgen/permute.c`
- **permute_dist()** (2 connections) — `src/benchmarks/tpch/tpch-dbgen/permute.c`
- **dbg_text()** (2 connections) — `src/benchmarks/tpch/tpch-dbgen/text.c`

## Relationships

- [[Knob Validation]] (32 shared connections)
- [[Evolution Tests]] (3 shared connections)
- [[Knob Validation Tests]] (1 shared connections)
- [[Docker Manifest Tests]] (1 shared connections)

## Source Files

- `src/benchmarks/tpch/tpch-dbgen/bm_utils.c`
- `src/benchmarks/tpch/tpch-dbgen/permute.c`
- `src/benchmarks/tpch/tpch-dbgen/text.c`
- `src/benchmarks/tpch/tpch-dbgen/varsub.c`

## Audit Trail

- EXTRACTED: 19 (51%)
- INFERRED: 18 (49%)
- AMBIGUOUS: 0 (0%)

---

*Part of the graphify knowledge wiki. See [[index]] to navigate.*