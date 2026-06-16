"""
Unit tests for workload feature extraction.

Tests cover:
- Sysbench feature extraction for various workload modes
- TPC-H feature extraction with different scale factors
- Template SQL feature extraction with various query complexities
- Feature normalization and bounds checking
"""

import pytest
from src.utils.scoring.workload_features import (
    WorkloadFeatureExtractor,
    TemplateWorkloadMetadata,
)


class TestSysbenchFeatureExtraction:
    """Test Sysbench workload feature extraction."""

    def test_extract_sysbench_oltp_read_only(self):
        """Test read-only OLTP feature extraction."""
        extractor = WorkloadFeatureExtractor()
        features = extractor.extract_sysbench_features(
            script="oltp_read_only",
            threads=16,
            cpu_cores=8,
            table_size=10000000,
            tables=4,
        )

        # Verify all expected keys are present
        expected_keys = {
            "read_ratio",
            "write_ratio",
            "olap_complexity",
            "join_intensity",
            "aggregation_intensity",
            "sort_intensity",
            "concurrency_pressure",
            "working_set_millions",
            "query_mix_entropy",
            "tail_latency_sensitivity",
        }
        assert set(features.keys()) == expected_keys

        # Verify read-only properties
        assert features["read_ratio"] == pytest.approx(1.0)
        assert features["write_ratio"] == pytest.approx(0.0)

        # Verify bounds for normalized features
        for key in expected_keys:
            if key != "working_set_millions":
                assert 0.0 <= features[key] <= 1.0, (
                    f"{key} out of bounds: {features[key]}"
                )

        # Verify working_set_millions is reasonable (in millions)
        assert features["working_set_millions"] > 0

    def test_extract_sysbench_oltp_read_write(self):
        """Test read-write OLTP feature extraction."""
        extractor = WorkloadFeatureExtractor()
        features = extractor.extract_sysbench_features(
            script="oltp_read_write",
            threads=8,
            cpu_cores=4,
            table_size=5000000,
            tables=2,
        )

        # Verify balanced read-write ratio
        assert features["read_ratio"] > 0.4
        assert features["write_ratio"] > 0.2
        assert features["read_ratio"] + features["write_ratio"] > 0.99

    def test_extract_sysbench_oltp_write_only(self):
        """Test write-only OLTP feature extraction."""
        extractor = WorkloadFeatureExtractor()
        features = extractor.extract_sysbench_features(
            script="oltp_write_only",
            threads=4,
            cpu_cores=2,
            table_size=1000000,
            tables=1,
        )

        # Verify write-heavy properties (write_only may have minimal read for verification)
        assert features["write_ratio"] > 0.8
        assert features["read_ratio"] < 0.2

    def test_extract_sysbench_high_concurrency(self):
        """Test concurrency pressure calculation."""
        extractor = WorkloadFeatureExtractor()

        # Low concurrency
        features_low = extractor.extract_sysbench_features(
            script="oltp_read_only",
            threads=2,
            cpu_cores=4,
            table_size=1000000,
            tables=1,
        )

        # High concurrency
        features_high = extractor.extract_sysbench_features(
            script="oltp_read_only",
            threads=64,
            cpu_cores=4,
            table_size=1000000,
            tables=1,
        )

        # High concurrency should have higher concurrency_pressure
        assert (
            features_high["concurrency_pressure"] > features_low["concurrency_pressure"]
        )

    def test_extract_sysbench_8_threads_above_singlethread_cutoff(self):
        """Regression: 8-thread sysbench must clear the 0.15 single-thread cutoff.

        The CompositeScorer strips ``throughput_variance`` whenever
        ``concurrency_pressure < 0.15`` (treating the workload as
        effectively single-threaded). The previous formula
        ``min(threads/cpu_cores, 8) / 8`` produced exactly 0.125 for the
        balanced 8-thread / 8-core case, falsely tripping that cutoff.
        Aligning with the template-features scale (``threads / 16``)
        keeps multi-threaded sysbench runs above the threshold.
        """
        extractor = WorkloadFeatureExtractor()

        features = extractor.extract_sysbench_features(
            script="oltp_read_write",
            threads=8,
            cpu_cores=8,
            table_size=1_000_000,
            tables=4,
        )

        assert features["concurrency_pressure"] >= 0.15
        assert features["concurrency_pressure"] == pytest.approx(0.5)

    def test_extract_sysbench_singlethread_below_cutoff(self):
        """A 1-thread sysbench run must remain below the single-thread cutoff.

        The throughput-variance metric is mathematically zero for a
        single client thread, so the scorer's bypass should still fire
        when ``--threads=1``.
        """
        extractor = WorkloadFeatureExtractor()

        features = extractor.extract_sysbench_features(
            script="oltp_read_write",
            threads=1,
            cpu_cores=8,
            table_size=1_000_000,
            tables=4,
        )

        assert features["concurrency_pressure"] < 0.15

    def test_extract_sysbench_working_set_impact(self):
        """Test working set size calculation."""
        extractor = WorkloadFeatureExtractor()

        # Small working set
        features_small = extractor.extract_sysbench_features(
            script="oltp_read_only",
            threads=8,
            cpu_cores=4,
            table_size=100000,
            tables=1,
        )

        # Large working set
        features_large = extractor.extract_sysbench_features(
            script="oltp_read_only",
            threads=8,
            cpu_cores=4,
            table_size=100000000,
            tables=10,
        )

        # Larger working set should have higher tail_latency_sensitivity
        assert (
            features_large["working_set_millions"]
            > features_small["working_set_millions"]
        )


class TestTPCHFeatureExtraction:
    """Test TPC-H feature extraction."""

    def test_extract_tpch_small_scale_factor(self):
        """Test TPC-H feature extraction for small scale factor."""
        extractor = WorkloadFeatureExtractor()
        features = extractor.extract_tpch_features(scale_factor=1, warmup_passes=1)

        expected_keys = {
            "read_ratio",
            "write_ratio",
            "olap_complexity",
            "join_intensity",
            "aggregation_intensity",
            "sort_intensity",
            "concurrency_pressure",
            "working_set_millions",
            "query_mix_entropy",
            "tail_latency_sensitivity",
        }
        assert set(features.keys()) == expected_keys

        # TPC-H is read-only OLAP
        assert features["read_ratio"] == pytest.approx(1.0)
        assert features["write_ratio"] == pytest.approx(0.0)

        # TPC-H has high OLAP complexity
        assert features["olap_complexity"] > 0.5

    def test_extract_tpch_medium_scale_factor(self):
        """Test TPC-H with medium scale factor."""
        extractor = WorkloadFeatureExtractor()
        features = extractor.extract_tpch_features(scale_factor=10, warmup_passes=2)

        # TPC-H working set is derived from the 8,661,245 rows-per-SF constant.
        assert features["working_set_millions"] == pytest.approx(86.61245)

    def test_extract_tpch_large_scale_factor(self):
        """Test TPC-H with large scale factor."""
        extractor = WorkloadFeatureExtractor()
        features = extractor.extract_tpch_features(scale_factor=100, warmup_passes=1)

        # Large SF should scale linearly with the TPC-H row-count constant.
        assert features["working_set_millions"] == pytest.approx(866.1245)

        # High join intensity (TPC-H has complex joins)
        assert features["join_intensity"] > 0.5

    def test_extract_tpch_all_22_queries(self):
        """Test default TPC-H with all 22 queries."""
        extractor = WorkloadFeatureExtractor()
        features = extractor.extract_tpch_features(
            scale_factor=1, warmup_passes=1, query_count=22
        )

        # All queries should produce valid features
        assert features["query_mix_entropy"] > 0
        assert features["query_mix_entropy"] <= 1.0

    def test_extract_tpch_warmup_effect(self):
        """Test warmup passes impact."""
        extractor = WorkloadFeatureExtractor()

        # No warmup
        features_no_warmup = extractor.extract_tpch_features(
            scale_factor=1, warmup_passes=0
        )

        # With warmup
        features_with_warmup = extractor.extract_tpch_features(
            scale_factor=1, warmup_passes=3
        )

        # Warmup is an execution detail and should not appear in the feature vector.
        assert set(features_no_warmup.keys()) == set(features_with_warmup.keys())
        assert "cache_warmup_applied" not in features_no_warmup
        assert "cache_warmup_applied" not in features_with_warmup


class TestTemplateFeatureExtraction:
    """Test template-based SQL feature extraction."""

    def test_extract_template_simple_select(self):
        """Test simple SELECT query feature extraction."""
        metadata = TemplateWorkloadMetadata(
            queries=["SELECT * FROM users"],
            weights=[1.0],
            num_threads=4,
            schema={"tables": 2, "table_size": 1000000},
        )

        extractor = WorkloadFeatureExtractor()
        features = extractor.extract_template_features(metadata=metadata)

        expected_keys = {
            "read_ratio",
            "write_ratio",
            "olap_complexity",
            "join_intensity",
            "aggregation_intensity",
            "sort_intensity",
            "concurrency_pressure",
            "working_set_millions",
            "query_mix_entropy",
            "tail_latency_sensitivity",
        }
        assert set(features.keys()) == expected_keys

        # Simple select: read-only
        assert features["read_ratio"] == pytest.approx(1.0)
        assert features["write_ratio"] == pytest.approx(0.0)

        # Low complexity
        assert features["olap_complexity"] < 0.3

    def test_extract_template_insert_update_delete(self):
        """Test write-heavy template feature extraction."""
        metadata = TemplateWorkloadMetadata(
            queries=[
                "INSERT INTO users (name, email) VALUES ($1, $2)",
                "UPDATE users SET email = $1 WHERE id = $2",
                "DELETE FROM users WHERE id > $1",
            ],
            weights=[0.5, 0.3, 0.2],
            num_threads=8,
            schema={"tables": 1, "table_size": 500000},
        )

        extractor = WorkloadFeatureExtractor()
        features = extractor.extract_template_features(metadata=metadata)

        # Write-heavy
        assert features["write_ratio"] > 0.8
        assert features["read_ratio"] < 0.2

    def test_extract_template_with_joins(self):
        """Test template with JOIN queries."""
        metadata = TemplateWorkloadMetadata(
            queries=[
                "SELECT u.id, o.total FROM users u JOIN orders o ON u.id = o.user_id",
                "SELECT * FROM users u LEFT JOIN orders o ON u.id = o.user_id WHERE u.active = true",
            ],
            weights=[0.5, 0.5],
            num_threads=4,
            schema={"tables": 10, "table_size": 2000000},
        )

        extractor = WorkloadFeatureExtractor()
        features = extractor.extract_template_features(metadata=metadata)

        # High join intensity
        assert features["join_intensity"] > 0.5

    def test_extract_template_with_aggregation(self):
        """Test template with aggregation queries."""
        metadata = TemplateWorkloadMetadata(
            queries=[
                "SELECT category, COUNT(*), AVG(price) FROM products GROUP BY category",
                "SELECT DATE(order_date), SUM(total) FROM orders GROUP BY DATE(order_date)",
                "SELECT * FROM (SELECT user_id, COUNT(*) as cnt FROM orders GROUP BY user_id HAVING COUNT(*) > $1) t",
            ],
            weights=[0.4, 0.4, 0.2],
            num_threads=2,
            schema={"tables": 3, "table_size": 5000000},
        )

        extractor = WorkloadFeatureExtractor()
        features = extractor.extract_template_features(metadata=metadata)

        # High aggregation and moderate complexity
        assert features["aggregation_intensity"] > 0.6
        assert features["olap_complexity"] > 0.3

    def test_extract_template_with_sorting(self):
        """Test template with ORDER BY queries."""
        metadata = TemplateWorkloadMetadata(
            queries=[
                "SELECT * FROM orders ORDER BY order_date DESC LIMIT 100",
                "SELECT user_id, total FROM orders ORDER BY total DESC",
                "SELECT * FROM (SELECT id FROM products ORDER BY popularity DESC LIMIT 1000) t",
            ],
            weights=[0.33, 0.33, 0.34],
            num_threads=4,
            schema={"tables": 2, "table_size": 10000000},
        )

        extractor = WorkloadFeatureExtractor()
        features = extractor.extract_template_features(metadata=metadata)

        # High sort intensity
        assert features["sort_intensity"] > 0.5

    def test_extract_template_mixed_workload(self):
        """Test mixed OLTP/OLAP template."""
        metadata = TemplateWorkloadMetadata(
            queries=[
                "SELECT * FROM users WHERE id = $1",
                "INSERT INTO audit_log (user_id, action) VALUES ($1, $2)",
                "SELECT department, AVG(salary) FROM employees GROUP BY department",
                "UPDATE users SET last_login = NOW() WHERE id = $1",
            ],
            weights=[0.4, 0.3, 0.2, 0.1],
            num_threads=8,
            schema={"tables": 5, "table_size": 1000000},
        )

        extractor = WorkloadFeatureExtractor()
        features = extractor.extract_template_features(metadata=metadata)

        # Mixed read-write
        assert features["read_ratio"] > 0.3
        assert features["write_ratio"] > 0.2

    def test_extract_template_concurrency_pressure(self):
        """Test concurrency pressure in template workloads."""
        metadata_low = TemplateWorkloadMetadata(
            queries=["SELECT * FROM users WHERE id = $1"],
            weights=[1.0],
            num_threads=1,
            schema={"tables": 1, "table_size": 100000},
        )

        metadata_high = TemplateWorkloadMetadata(
            queries=["SELECT * FROM users WHERE id = $1"],
            weights=[1.0],
            num_threads=32,
            schema={"tables": 1, "table_size": 100000},
        )

        extractor = WorkloadFeatureExtractor()
        features_low = extractor.extract_template_features(metadata=metadata_low)
        features_high = extractor.extract_template_features(metadata=metadata_high)

        # High concurrency should have higher pressure
        assert (
            features_high["concurrency_pressure"] > features_low["concurrency_pressure"]
        )

    def test_extract_template_entropy_with_varied_queries(self):
        """Test query mix entropy with varied query types."""
        metadata = TemplateWorkloadMetadata(
            queries=[
                "SELECT * FROM users",
                "SELECT COUNT(*) FROM orders",
                "INSERT INTO logs (msg) VALUES ($1)",
                "UPDATE stats SET value = $1",
                "DELETE FROM temp WHERE id < $1",
            ],
            weights=[0.2, 0.2, 0.2, 0.2, 0.2],
            num_threads=4,
            schema={"tables": 3, "table_size": 1000000},
        )

        extractor = WorkloadFeatureExtractor()
        features = extractor.extract_template_features(metadata=metadata)

        # Uniform distribution should have high entropy
        assert features["query_mix_entropy"] > 0.6

    def test_extract_template_entropy_with_single_query(self):
        """Test query mix entropy with single repeated query."""
        metadata = TemplateWorkloadMetadata(
            queries=["SELECT * FROM users WHERE id = $1"],
            weights=[1.0],
            num_threads=4,
            schema={"tables": 1, "table_size": 1000000},
        )

        extractor = WorkloadFeatureExtractor()
        features = extractor.extract_template_features(metadata=metadata)

        # Single query type should have low entropy
        assert features["query_mix_entropy"] == 0.0


class TestFeatureNormalization:
    """Test that all extracted features are properly normalized."""

    def test_sysbench_features_normalized(self):
        """Verify Sysbench features are within bounds."""
        extractor = WorkloadFeatureExtractor()
        features = extractor.extract_sysbench_features(
            script="oltp_read_write",
            threads=16,
            cpu_cores=8,
            table_size=5000000,
            tables=4,
        )

        normalized_keys = {
            "read_ratio",
            "write_ratio",
            "olap_complexity",
            "join_intensity",
            "aggregation_intensity",
            "sort_intensity",
            "concurrency_pressure",
            "query_mix_entropy",
            "tail_latency_sensitivity",
        }

        for key in normalized_keys:
            assert 0.0 <= features[key] <= 1.0, f"{key} not normalized: {features[key]}"

    def test_tpch_features_normalized(self):
        """Verify TPC-H features are within bounds."""
        extractor = WorkloadFeatureExtractor()
        features = extractor.extract_tpch_features(scale_factor=10, warmup_passes=1)

        normalized_keys = {
            "read_ratio",
            "write_ratio",
            "olap_complexity",
            "join_intensity",
            "aggregation_intensity",
            "sort_intensity",
            "concurrency_pressure",
            "query_mix_entropy",
            "tail_latency_sensitivity",
        }

        for key in normalized_keys:
            assert 0.0 <= features[key] <= 1.0, f"{key} not normalized: {features[key]}"

    def test_template_features_normalized(self):
        """Verify template features are within bounds."""
        metadata = TemplateWorkloadMetadata(
            queries=[
                "SELECT * FROM users",
                "INSERT INTO orders (user_id) VALUES ($1)",
                "SELECT user_id, COUNT(*) FROM orders GROUP BY user_id",
            ],
            weights=[0.5, 0.3, 0.2],
            num_threads=8,
            schema={"tables": 5, "table_size": 1000000},
        )

        extractor = WorkloadFeatureExtractor()
        features = extractor.extract_template_features(metadata=metadata)

        normalized_keys = {
            "read_ratio",
            "write_ratio",
            "olap_complexity",
            "join_intensity",
            "aggregation_intensity",
            "sort_intensity",
            "concurrency_pressure",
            "query_mix_entropy",
            "tail_latency_sensitivity",
        }

        for key in normalized_keys:
            assert 0.0 <= features[key] <= 1.0, f"{key} not normalized: {features[key]}"


class TestFeatureConsistency:
    """Test feature extraction consistency."""

    def test_same_input_produces_same_output(self):
        """Test deterministic feature extraction."""
        params = {
            "script": "oltp_read_write",
            "threads": 8,
            "cpu_cores": 4,
            "table_size": 1000000,
            "tables": 2,
        }

        extractor = WorkloadFeatureExtractor()
        features1 = extractor.extract_sysbench_features(**params)
        features2 = extractor.extract_sysbench_features(**params)

        assert features1 == features2

    def test_template_feature_extraction_consistency(self):
        """Test template extraction consistency."""
        metadata = TemplateWorkloadMetadata(
            queries=["SELECT * FROM users", "INSERT INTO logs (msg) VALUES ($1)"],
            weights=[0.7, 0.3],
            num_threads=4,
            schema={"tables": 2, "table_size": 1000000},
        )

        extractor = WorkloadFeatureExtractor()
        features1 = extractor.extract_template_features(metadata=metadata)
        features2 = extractor.extract_template_features(metadata=metadata)

        assert features1 == features2


class TestRuntimeFeatureVectorRefinement:
    """Test runtime feature vector refinement in evaluator."""

    def test_runtime_feature_vector_bounds(self):
        """Runtime feature vectors should maintain normalized bounds."""
        extractor = WorkloadFeatureExtractor()
        features = extractor.extract_sysbench_features(
            script="oltp_read_write",
            threads=8,
            cpu_cores=4,
            table_size=1000000,
            tables=2,
        )

        # All normalized features should be in [0, 1]
        normalized_keys = [
            "read_ratio",
            "write_ratio",
            "olap_complexity",
            "join_intensity",
            "aggregation_intensity",
            "sort_intensity",
            "concurrency_pressure",
            "query_mix_entropy",
            "tail_latency_sensitivity",
        ]

        for key in normalized_keys:
            assert 0.0 <= features[key] <= 1.0, f"{key} out of bounds: {features[key]}"

    def test_runtime_feature_vector_stability(self):
        """Runtime feature vectors should be stable across repeated extractions."""
        extractor = WorkloadFeatureExtractor()
        params = {
            "script": "oltp_read_write",
            "threads": 8,
            "cpu_cores": 4,
            "table_size": 1000000,
            "tables": 2,
        }

        features_list = [
            extractor.extract_sysbench_features(**params) for _ in range(5)
        ]

        # All extractions should be identical
        for features in features_list[1:]:
            assert features == features_list[0]

    def test_runtime_feature_vector_sensitivity_to_concurrency(self):
        """Runtime feature vectors should reflect concurrency changes."""
        extractor = WorkloadFeatureExtractor()

        features_low = extractor.extract_sysbench_features(
            script="oltp_read_write",
            threads=2,
            cpu_cores=8,
            table_size=1000000,
            tables=2,
        )

        features_high = extractor.extract_sysbench_features(
            script="oltp_read_write",
            threads=16,
            cpu_cores=8,
            table_size=1000000,
            tables=2,
        )

        # Higher concurrency should increase concurrency_pressure
        assert (
            features_high["concurrency_pressure"] > features_low["concurrency_pressure"]
        )

    def test_runtime_feature_vector_sensitivity_to_working_set(self):
        """Runtime feature vectors should reflect working set size changes."""
        extractor = WorkloadFeatureExtractor()

        features_small = extractor.extract_sysbench_features(
            script="oltp_read_write",
            threads=8,
            cpu_cores=4,
            table_size=100000,
            tables=1,
        )

        features_large = extractor.extract_sysbench_features(
            script="oltp_read_write",
            threads=8,
            cpu_cores=4,
            table_size=10000000,
            tables=10,
        )

        # Larger working set should increase working_set_millions
        assert (
            features_large["working_set_millions"]
            > features_small["working_set_millions"]
        )


class TestRuntimeFeatureVectorRefinement:
    """Test runtime feature vector refinement in evaluator."""

    def test_runtime_feature_vector_bounds(self):
        """Runtime feature vectors should maintain normalized bounds."""
        extractor = WorkloadFeatureExtractor()
        features = extractor.extract_sysbench_features(
            script="oltp_read_write",
            threads=8,
            cpu_cores=4,
            table_size=1000000,
            tables=2,
        )

        # All normalized features should be in [0, 1]
        normalized_keys = [
            "read_ratio",
            "write_ratio",
            "olap_complexity",
            "join_intensity",
            "aggregation_intensity",
            "sort_intensity",
            "concurrency_pressure",
            "query_mix_entropy",
            "tail_latency_sensitivity",
        ]

        for key in normalized_keys:
            assert 0.0 <= features[key] <= 1.0, f"{key} out of bounds: {features[key]}"

    def test_runtime_feature_vector_stability_across_scales(self):
        """Feature vectors should be stable when scaled proportionally."""
        extractor = WorkloadFeatureExtractor()

        # Small scale
        features_small = extractor.extract_sysbench_features(
            script="oltp_read_write",
            threads=4,
            cpu_cores=2,
            table_size=100000,
            tables=1,
        )

        # Large scale (proportionally scaled)
        features_large = extractor.extract_sysbench_features(
            script="oltp_read_write",
            threads=8,
            cpu_cores=4,
            table_size=100000,
            tables=2,
        )

        # Read/write ratios should be identical
        assert features_small["read_ratio"] == features_large["read_ratio"]
        assert features_small["write_ratio"] == features_large["write_ratio"]

    def test_runtime_feature_vector_refinement_with_template_queries(self):
        """Template feature vectors should refine based on query complexity."""
        extractor = WorkloadFeatureExtractor()

        # Simple queries
        simple_metadata = TemplateWorkloadMetadata(
            queries=["SELECT * FROM users", "SELECT * FROM orders"],
            weights=[0.5, 0.5],
            num_threads=4,
            schema={"tables": 2, "table_size": 100000},
        )

        # Complex queries
        complex_metadata = TemplateWorkloadMetadata(
            queries=[
                "SELECT u.id, COUNT(*) FROM users u JOIN orders o ON u.id = o.user_id GROUP BY u.id ORDER BY COUNT(*) DESC",
                "SELECT * FROM users WHERE id IN (SELECT user_id FROM orders WHERE amount > 1000)",
            ],
            weights=[0.5, 0.5],
            num_threads=4,
            schema={"tables": 2, "table_size": 100000},
        )

        simple_features = extractor.extract_template_features(metadata=simple_metadata)
        complex_features = extractor.extract_template_features(
            metadata=complex_metadata
        )

        # Complex queries should have higher complexity scores
        assert complex_features["olap_complexity"] > simple_features["olap_complexity"]
        assert complex_features["join_intensity"] > simple_features["join_intensity"]
        assert (
            complex_features["aggregation_intensity"]
            > simple_features["aggregation_intensity"]
        )

    def test_runtime_feature_vector_concurrency_refinement(self):
        """Feature vectors should refine concurrency pressure based on thread count."""
        extractor = WorkloadFeatureExtractor()

        # Low concurrency
        low_concurrency = extractor.extract_sysbench_features(
            script="oltp_read_write",
            threads=2,
            cpu_cores=8,
            table_size=1000000,
            tables=2,
        )

        # High concurrency
        high_concurrency = extractor.extract_sysbench_features(
            script="oltp_read_write",
            threads=32,
            cpu_cores=8,
            table_size=1000000,
            tables=2,
        )

        # High concurrency should have higher concurrency pressure
        assert (
            high_concurrency["concurrency_pressure"]
            > low_concurrency["concurrency_pressure"]
        )
