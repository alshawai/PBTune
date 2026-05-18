# WorkloadFeatureExtractor

> God node · 41 connections · `src/utils/scoring/workload_features.py`

**Community:** [[Scoring Scorer Core]]

## Connections by Relation

### calls
- [[.__init__()]] `INFERRED`
- [[.test_extract_template_with_joins()]] `INFERRED`
- [[.test_extract_sysbench_oltp_read_only()]] `INFERRED`
- [[.test_extract_template_simple_select()]] `INFERRED`
- [[.test_extract_template_insert_update_delete()]] `INFERRED`
- [[.test_extract_template_with_aggregation()]] `INFERRED`
- [[.test_extract_template_mixed_workload()]] `INFERRED`
- [[.test_extract_template_concurrency_pressure()]] `INFERRED`
- [[.test_extract_template_entropy_with_varied_queries()]] `INFERRED`
- [[.test_extract_template_entropy_with_single_query()]] `INFERRED`
- [[.test_template_features_normalized()]] `INFERRED`
- [[.test_runtime_feature_vector_stability()]] `INFERRED`
- [[.test_runtime_feature_vector_refinement_with_template_queries()]] `INFERRED`
- [[.test_extract_sysbench_oltp_write_only()]] `INFERRED`
- [[.test_extract_sysbench_high_concurrency()]] `INFERRED`
- [[.test_extract_sysbench_working_set_impact()]] `INFERRED`
- [[.test_extract_tpch_small_scale_factor()]] `INFERRED`
- [[.test_extract_tpch_medium_scale_factor()]] `INFERRED`
- [[.test_extract_tpch_large_scale_factor()]] `INFERRED`
- [[.test_extract_tpch_all_22_queries()]] `INFERRED`

### contains
- [[WorkloadFeatures]] `EXTRACTED`

### method
- [[.extract_sysbench_features()]] `EXTRACTED`
- [[.extract_tpch_features()]] `EXTRACTED`
- [[.extract_template_features()]] `EXTRACTED`

### rationale_for
- [[Extract static feature vectors for benchmark and template workloads.]] `EXTRACTED`

### uses
- [[PBTTuner]] `INFERRED`
- [[TestTemplateFeatureExtraction]] `INFERRED`
- [[Test runtime feature vector refinement in evaluator.]] `INFERRED`
- [[TestSysbenchFeatureExtraction]] `INFERRED`
- [[Test TPC-H feature extraction.]] `INFERRED`
- [[TestFeatureNormalization]] `INFERRED`
- [[TestFeatureConsistency]] `INFERRED`

---

*Part of the graphify knowledge wiki. See [[index]] to navigate.*