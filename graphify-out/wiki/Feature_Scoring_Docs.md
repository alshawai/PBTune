# Feature Scoring Docs

> 24 nodes · cohesion 0.12

## Key Concepts

- **TemplateWorkloadMetadata** (22 connections) — `src/utils/scoring/workload_features.py`
- **TestTemplateFeatureExtraction** (12 connections) — `tests/unit/scoring/test_workload_features.py`
- **TestFeatureConsistency** (6 connections) — `tests/unit/scoring/test_workload_features.py`
- **.test_extract_template_with_joins()** (5 connections) — `tests/unit/scoring/test_workload_features.py`
- **.test_extract_template_concurrency_pressure()** (4 connections) — `tests/unit/scoring/test_workload_features.py`
- **.test_extract_template_entropy_with_single_query()** (4 connections) — `tests/unit/scoring/test_workload_features.py`
- **.test_extract_template_entropy_with_varied_queries()** (4 connections) — `tests/unit/scoring/test_workload_features.py`
- **.test_extract_template_insert_update_delete()** (4 connections) — `tests/unit/scoring/test_workload_features.py`
- **.test_extract_template_mixed_workload()** (4 connections) — `tests/unit/scoring/test_workload_features.py`
- **.test_extract_template_simple_select()** (4 connections) — `tests/unit/scoring/test_workload_features.py`
- **.test_extract_template_with_aggregation()** (4 connections) — `tests/unit/scoring/test_workload_features.py`
- **Test template extraction consistency.** (3 connections) — `tests/unit/scoring/test_workload_features.py`
- **Test template-based SQL feature extraction.** (1 connections) — `tests/unit/scoring/test_workload_features.py`
- **Test simple SELECT query feature extraction.** (1 connections) — `tests/unit/scoring/test_workload_features.py`
- **Test write-heavy template feature extraction.** (1 connections) — `tests/unit/scoring/test_workload_features.py`
- **Test template with JOIN queries.** (1 connections) — `tests/unit/scoring/test_workload_features.py`
- **Test template with aggregation queries.** (1 connections) — `tests/unit/scoring/test_workload_features.py`
- **Test template with ORDER BY queries.** (1 connections) — `tests/unit/scoring/test_workload_features.py`
- **Test mixed OLTP/OLAP template.** (1 connections) — `tests/unit/scoring/test_workload_features.py`
- **Test concurrency pressure in template workloads.** (1 connections) — `tests/unit/scoring/test_workload_features.py`
- **Test query mix entropy with varied query types.** (1 connections) — `tests/unit/scoring/test_workload_features.py`
- **Test query mix entropy with single repeated query.** (1 connections) — `tests/unit/scoring/test_workload_features.py`
- **Test feature extraction consistency.** (1 connections) — `tests/unit/scoring/test_workload_features.py`
- **Normalized metadata for template SQL workload feature extraction.** (1 connections) — `src/utils/scoring/workload_features.py`

## Relationships

- [[Evaluator Core]] (64 shared connections)
- [[Scoring Scorer Core]] (13 shared connections)
- [[Import Analysis]] (4 shared connections)
- [[Visualization Plotting]] (2 shared connections)
- [[Evolution Strategies]] (2 shared connections)
- [[BO Baseline & Workload]] (1 shared connections)
- [[Snapshot & Persistence]] (1 shared connections)
- [[Benchmark Executor Tests]] (1 shared connections)

## Source Files

- `src/utils/scoring/workload_features.py`
- `tests/unit/scoring/test_workload_features.py`

## Audit Trail

- EXTRACTED: 46 (52%)
- INFERRED: 42 (48%)
- AMBIGUOUS: 0 (0%)

---

*Part of the graphify knowledge wiki. See [[index]] to navigate.*