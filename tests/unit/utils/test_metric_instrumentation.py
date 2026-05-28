"""
Unit tests for metric instrumentation and derived metrics.

Tests cover:
- Tail latency amplification computation
- Scan efficiency calculation
- Derived metrics enrichment
- Edge cases (zero values, extreme ratios)
"""

import pytest
from src.utils.metrics import PerformanceMetrics, WorkloadType
from src.utils.metric_instrumentation import (
    MetricInstrumentationEngine,
    DerivedMetrics,
)


class TestTailLatencyAmplification:
    """Test tail latency amplification computation."""

    def test_consistent_latency(self):
        """Test latency with minimal tail amplification."""
        metrics = PerformanceMetrics(
            latency_p50=10.0,
            latency_p95=11.0,
            latency_p99=12.0,
            latency_variance=0.5,
        )

        amplification = MetricInstrumentationEngine.calculate_tail_amplification(
            metrics.latency_p50, metrics.latency_p99
        )

        # p99 (12) / p50 (10) = 1.2x
        assert amplification == pytest.approx(1.2)
        assert amplification < 1.5  # Minimal tail amplification

    def test_high_tail_amplification(self):
        """Test latency with significant tail amplification."""
        metrics = PerformanceMetrics(
            latency_p50=10.0,
            latency_p95=30.0,
            latency_p99=100.0,
            latency_variance=15.0,
        )

        amplification = MetricInstrumentationEngine.calculate_tail_amplification(
            metrics.latency_p50, metrics.latency_p99
        )

        # p99 (100) / p50 (10) = 10.0x
        assert amplification == pytest.approx(10.0)
        assert amplification > 3.0  # Significant issue

    def test_zero_p50_latency(self):
        """Test edge case with zero p50 latency."""
        metrics = PerformanceMetrics(
            latency_p50=0.0,
            latency_p95=5.0,
            latency_p99=10.0,
        )

        amplification = MetricInstrumentationEngine.calculate_tail_amplification(
            metrics.latency_p50, metrics.latency_p99
        )

        # Should return 0 when p50 is 0
        assert amplification == 0.0

    def test_negative_p50_latency(self):
        """Test edge case with negative p50 latency."""
        metrics = PerformanceMetrics(
            latency_p50=-5.0,
            latency_p95=5.0,
            latency_p99=10.0,
        )

        amplification = MetricInstrumentationEngine.calculate_tail_amplification(
            metrics.latency_p50, metrics.latency_p99
        )

        # Should return 0 for negative values
        assert amplification == 0.0

    def test_equal_percentiles(self):
        """Test when all latency percentiles are equal."""
        metrics = PerformanceMetrics(
            latency_p50=10.0,
            latency_p95=10.0,
            latency_p99=10.0,
        )

        amplification = MetricInstrumentationEngine.calculate_tail_amplification(
            metrics.latency_p50, metrics.latency_p99
        )

        # No tail amplification
        assert amplification == pytest.approx(1.0)


class TestScanEfficiency:
    """Test scan efficiency metric computation."""

    def test_row_counters_override_cache_ratio(self):
        """Row counters should drive scan efficiency when available."""
        efficiency = MetricInstrumentationEngine.calculate_scan_efficiency(
            cache_hit_ratio=0.99,
            rows_examined=1_152_327,
            rows_returned=1_168_291,
        )

        assert efficiency == pytest.approx(1_152_327 / 1_168_291)
        assert efficiency < 1.0

    def test_imbalanced_rows_reduce_scan_efficiency(self):
        """Large row imbalances should no longer collapse to a perfect score."""
        efficiency = MetricInstrumentationEngine.calculate_scan_efficiency(
            cache_hit_ratio=0.99,
            rows_examined=2_873,
            rows_returned=48_303_948,
        )

        assert efficiency == pytest.approx(2_873 / 48_303_948)
        assert efficiency < 0.01

    def test_perfect_cache_hit_ratio(self):
        """Test efficiency with perfect cache hit ratio."""
        metrics = PerformanceMetrics(
            cache_hit_ratio=1.0,
        )

        efficiency = MetricInstrumentationEngine.calculate_scan_efficiency(
            metrics.cache_hit_ratio
        )

        assert efficiency == pytest.approx(1.0)

    def test_high_cache_efficiency(self):
        """Test efficiency with high cache hit ratio."""
        metrics = PerformanceMetrics(
            cache_hit_ratio=0.95,
        )

        efficiency = MetricInstrumentationEngine.calculate_scan_efficiency(
            metrics.cache_hit_ratio
        )

        assert efficiency == pytest.approx(0.95)
        assert 0.9 <= efficiency <= 1.0

    def test_moderate_cache_efficiency(self):
        """Test efficiency with moderate cache hit ratio."""
        metrics = PerformanceMetrics(
            cache_hit_ratio=0.65,
        )

        efficiency = MetricInstrumentationEngine.calculate_scan_efficiency(
            metrics.cache_hit_ratio
        )

        assert efficiency == pytest.approx(0.65)

    def test_low_cache_efficiency(self):
        """Test efficiency with low cache hit ratio."""
        metrics = PerformanceMetrics(
            cache_hit_ratio=0.25,
        )

        efficiency = MetricInstrumentationEngine.calculate_scan_efficiency(
            metrics.cache_hit_ratio
        )

        assert efficiency == pytest.approx(0.25)

    def test_zero_cache_efficiency(self):
        """Test efficiency with no cache hits."""
        metrics = PerformanceMetrics(
            cache_hit_ratio=0.0,
        )

        efficiency = MetricInstrumentationEngine.calculate_scan_efficiency(
            metrics.cache_hit_ratio
        )

        assert efficiency == pytest.approx(0.0)

    def test_negative_cache_ratio_clamped(self):
        """Test that negative cache ratios are clamped to 0."""
        metrics = PerformanceMetrics(
            cache_hit_ratio=-0.1,
        )

        efficiency = MetricInstrumentationEngine.calculate_scan_efficiency(
            metrics.cache_hit_ratio
        )

        assert efficiency == pytest.approx(0.0)


class TestDerivedMetricsComputation:
    """Test derived metrics computation."""

    def test_compute_all_derived_metrics(self):
        """Test computation of all derived metrics together."""
        metrics = PerformanceMetrics(
            latency_p50=15.0,
            latency_p95=25.0,
            latency_p99=50.0,
            latency_variance=8.0,
            cache_hit_ratio=0.85,
        )

        engine = MetricInstrumentationEngine()
        derived = engine.compute_derived_metrics(metrics)

        assert isinstance(derived, DerivedMetrics)
        assert derived.tail_latency_amplification == pytest.approx(50.0 / 15.0)
        assert derived.scan_efficiency == pytest.approx(0.85)
        assert derived.latency_variance == pytest.approx(8.0)

    def test_derived_metrics_enrich_dict(self):
        """Test enriching a metrics dictionary with derived metrics."""
        metrics = PerformanceMetrics(
            latency_p50=10.0,
            latency_p95=15.0,
            latency_p99=30.0,
            latency_variance=5.0,
            cache_hit_ratio=0.90,
            throughput=1000.0,
        )

        metrics_dict = {
            "latency_p50": 10.0,
            "throughput": 1000.0,
        }

        engine = MetricInstrumentationEngine()
        enriched = engine.enrich_metrics_dict(metrics_dict, metrics)

        # Original keys should be preserved
        assert enriched["latency_p50"] == 10.0
        assert enriched["throughput"] == 1000.0

        # Derived keys should be added
        assert "tail_latency_amplification" in enriched
        assert "scan_efficiency" in enriched
        assert "latency_variance" in enriched

        assert enriched["tail_latency_amplification"] == pytest.approx(3.0)
        assert enriched["scan_efficiency"] == pytest.approx(0.90)
        assert enriched["latency_variance"] == pytest.approx(5.0)


class TestOLTPMetrics:
    """Test derived metrics for OLTP workloads."""

    def test_typical_oltp_metrics(self):
        """Test typical OLTP performance profile."""
        # Typical Sysbench OLTP metrics
        metrics = PerformanceMetrics(
            latency_p50=2.5,
            latency_p95=4.5,
            latency_p99=8.0,
            latency_variance=1.2,
            throughput=2500.0,
            cache_hit_ratio=0.95,
            memory_utilization=0.65,
        )

        derived = MetricInstrumentationEngine.compute_derived_metrics(metrics)

        # OLTP should have low tail amplification
        assert derived.tail_latency_amplification < 5.0
        # OLTP with good tuning should have high cache hit
        assert derived.scan_efficiency > 0.9

    def test_oltp_under_stress(self):
        """Test OLTP metrics when experiencing resource stress."""
        metrics = PerformanceMetrics(
            latency_p50=10.0,
            latency_p95=40.0,
            latency_p99=150.0,
            latency_variance=25.0,
            throughput=500.0,
            cache_hit_ratio=0.60,
            memory_utilization=0.95,
        )

        derived = MetricInstrumentationEngine.compute_derived_metrics(metrics)

        # Stressed OLTP should show high tail amplification
        assert derived.tail_latency_amplification > 5.0
        # Cache efficiency degraded under stress
        assert derived.scan_efficiency < 0.75


class TestOLAPMetrics:
    """Test derived metrics for OLAP workloads."""

    def test_typical_olap_metrics(self):
        """Test typical OLAP performance profile."""
        # Typical TPC-H metrics
        metrics = PerformanceMetrics(
            latency_p50=2500.0,
            latency_p95=4000.0,
            latency_p99=6000.0,
            latency_variance=800.0,
            throughput=20.0,
            cache_hit_ratio=0.80,
            memory_utilization=0.70,
        )

        derived = MetricInstrumentationEngine.compute_derived_metrics(metrics)

        # OLAP queries typically have consistent behavior (lower tail amplification)
        assert derived.tail_latency_amplification == pytest.approx(2.4)

    def test_olap_analytical_query_large_result_set(self):
        """Test OLAP query returning large result sets."""
        metrics = PerformanceMetrics(
            latency_p50=5000.0,
            latency_p95=8000.0,
            latency_p99=12000.0,
            latency_variance=2000.0,
            throughput=10.0,
            cache_hit_ratio=0.75,
            memory_utilization=0.85,
            io_read_mb=5000.0,
        )

        derived = MetricInstrumentationEngine.compute_derived_metrics(metrics)

        # Large analytical queries may show higher variance
        assert derived.latency_variance == pytest.approx(2000.0)
        # Moderate cache efficiency due to large working set
        assert derived.scan_efficiency == pytest.approx(0.75)


class TestMixedWorkloadMetrics:
    """Test derived metrics for mixed OLTP/OLAP workloads."""

    def test_mixed_workload_metrics(self):
        """Test metrics for workload with mixed query types."""
        metrics = PerformanceMetrics(
            latency_p50=50.0,
            latency_p95=150.0,
            latency_p99=500.0,
            latency_variance=120.0,
            throughput=500.0,
            cache_hit_ratio=0.75,
            memory_utilization=0.72,
        )

        derived = MetricInstrumentationEngine.compute_derived_metrics(metrics)

        # Mixed workloads show moderate tail amplification
        assert derived.tail_latency_amplification == pytest.approx(10.0)
        assert derived.scan_efficiency == pytest.approx(0.75)


class TestMetricsFormatting:
    """Test formatting and logging of derived metrics."""

    def test_format_derived_metrics(self):
        """Test string formatting of derived metrics."""
        derived = DerivedMetrics(
            tail_latency_amplification=2.5,
            scan_efficiency=0.85,
            latency_variance=5.0,
        )

        engine = MetricInstrumentationEngine()
        formatted = engine.format_derived_metrics(derived)

        assert "Tail Latency Amplification: 2.50x" in formatted
        assert "Scan Efficiency: 85.0%" in formatted
        assert "Latency Variance (stddev): 5.00ms" in formatted

    def test_log_metrics_summary(self, caplog):
        """Test that metrics summary logging includes derived metrics."""
        metrics = PerformanceMetrics(
            latency_p50=10.0,
            latency_p95=20.0,
            latency_p99=40.0,
            latency_variance=8.0,
            throughput=1000.0,
            cache_hit_ratio=0.90,
            memory_utilization=0.60,
        )

        engine = MetricInstrumentationEngine()

        with caplog.at_level("INFO"):
            engine.log_metrics_summary(metrics, WorkloadType.OLTP)

        log_text = caplog.text
        assert "Tail Amplification" in log_text
        assert "Scan Efficiency" in log_text
        assert "Latency Variance" in log_text


class TestEdgeCasesAndBoundaries:
    """Test edge cases and boundary conditions."""

    def test_all_zero_metrics(self):
        """Test with all metrics at zero."""
        metrics = PerformanceMetrics(
            latency_p50=0.0,
            latency_p95=0.0,
            latency_p99=0.0,
            latency_variance=0.0,
            cache_hit_ratio=0.0,
        )

        derived = MetricInstrumentationEngine.compute_derived_metrics(metrics)

        assert derived.tail_latency_amplification == 0.0
        assert derived.scan_efficiency == 0.0
        assert derived.latency_variance == 0.0

    def test_extreme_tail_amplification(self):
        """Test with extreme tail amplification."""
        metrics = PerformanceMetrics(
            latency_p50=1.0,
            latency_p95=50.0,
            latency_p99=1000.0,
            latency_variance=300.0,
        )

        derived = MetricInstrumentationEngine.compute_derived_metrics(metrics)

        assert derived.tail_latency_amplification == pytest.approx(1000.0)
        assert derived.latency_variance == 300.0

    def test_metrics_consistency(self):
        """Test that derived metrics remain consistent across multiple calls."""
        metrics = PerformanceMetrics(
            latency_p50=15.0,
            latency_p95=25.0,
            latency_p99=50.0,
            latency_variance=10.0,
            cache_hit_ratio=0.80,
        )

        engine = MetricInstrumentationEngine()
        derived1 = engine.compute_derived_metrics(metrics)
        derived2 = engine.compute_derived_metrics(metrics)

        assert (
            derived1.tail_latency_amplification == derived2.tail_latency_amplification
        )
        assert derived1.scan_efficiency == derived2.scan_efficiency
        assert derived1.latency_variance == derived2.latency_variance
