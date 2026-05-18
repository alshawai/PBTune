# Paper Design & Structure

> 11 nodes · cohesion 0.18

## Key Concepts

- **TestSysbenchFeatureExtraction** (8 connections) — `tests/unit/scoring/test_workload_features.py`
- **.test_extract_sysbench_oltp_read_only()** (4 connections) — `tests/unit/scoring/test_workload_features.py`
- **.test_extract_sysbench_high_concurrency()** (3 connections) — `tests/unit/scoring/test_workload_features.py`
- **.test_extract_sysbench_oltp_write_only()** (3 connections) — `tests/unit/scoring/test_workload_features.py`
- **.test_extract_sysbench_working_set_impact()** (3 connections) — `tests/unit/scoring/test_workload_features.py`
- **Test working set size calculation.** (1 connections) — `tests/unit/scoring/test_workload_features.py`
- **Test Sysbench workload feature extraction.** (1 connections) — `tests/unit/scoring/test_workload_features.py`
- **Test read-only OLTP feature extraction.** (1 connections) — `tests/unit/scoring/test_workload_features.py`
- **Test read-write OLTP feature extraction.** (1 connections) — `tests/unit/scoring/test_workload_features.py`
- **Test write-only OLTP feature extraction.** (1 connections) — `tests/unit/scoring/test_workload_features.py`
- **Test concurrency pressure calculation.** (1 connections) — `tests/unit/scoring/test_workload_features.py`

## Relationships

- [[Benchmark Executor Tests]] (20 shared connections)
- [[Scoring Scorer Core]] (5 shared connections)
- [[Evaluator Core]] (1 shared connections)
- [[Import Analysis]] (1 shared connections)

## Source Files

- `tests/unit/scoring/test_workload_features.py`

## Audit Trail

- EXTRACTED: 21 (78%)
- INFERRED: 6 (22%)
- AMBIGUOUS: 0 (0%)

---

*Part of the graphify knowledge wiki. See [[index]] to navigate.*