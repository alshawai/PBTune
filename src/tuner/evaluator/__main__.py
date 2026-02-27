"""
Tuner Evaluator Module Test
============================

Test the performance metrics and metric configuration.

Usage:
------
python -m src.tuner.evaluator
"""

if __name__ == "__main__":
    from unittest.mock import patch
    from unittest.mock import Mock, MagicMock

    from src.tuner.config import get_knob_space
    from src.tuner.core.worker import Worker
    from src.tuner.evaluator.evaluator import (
        Evaluator,
        EvaluatorConfig,
        SysbenchOLTPExecutor,
        CustomQueryExecutor,
    )
    from src.tuner.evaluator.metrics import (
        PerformanceMetrics,
        MetricConfig,
        WorkloadType,
        OLTP_METRIC_CONFIG,
        OLAP_METRIC_CONFIG,
    )

    print("Tuner Evaluator - Performance Metrics Test")
    print("=" * 42)

    print("\n[TEST 1] OLTP Workload Metrics")
    print("-" * 30)
    try:
        oltp_metrics = PerformanceMetrics(
            throughput=5000.0,
            latency_p50=2.5,
            latency_p95=8.0,
            latency_p99=15.0,
            cpu_utilization=0.65,
            memory_utilization=0.70,
            io_read_mb=120.0,
            io_write_mb=80.0,
            cache_hit_ratio=0.95,
            total_queries=150000,
            total_time=30.0,
            error_rate=0.001,
        )

        score = OLTP_METRIC_CONFIG.compute_score(oltp_metrics)
        print("Sample OLTP Metrics:")
        print(f"  Throughput: {oltp_metrics.throughput} TPS")
        print(f"  Latency (p95): {oltp_metrics.latency_p95} ms")
        print(f"  CPU Utilization: {oltp_metrics.cpu_utilization:.1%}")
        print(f"  Cache Hit Ratio: {oltp_metrics.cache_hit_ratio:.2%}")
        print(f"\n🟢 OLTP Score: {score:.4f}")

    except (ValueError, TypeError, AttributeError) as e:
        print(f"\n🔴 ERROR: {e}")
        import traceback
        traceback.print_exc()

    print("-" * 30)
    print("\n[TEST 2] OLAP Workload Metrics")
    print("-" * 30)
    try:
        olap_metrics = PerformanceMetrics(
            throughput=50.0,  # Lower for analytical queries
            latency_p50=500.0,
            latency_p95=2000.0,
            latency_p99=5000.0,
            cpu_utilization=0.80,
            memory_utilization=0.85,
            io_read_mb=5000.0,  # Higher for scans
            io_write_mb=1000.0,
            cache_hit_ratio=0.75,
            total_queries=1000,
            total_time=20.0,
            error_rate=0.0,
        )

        score = OLAP_METRIC_CONFIG.compute_score(olap_metrics)
        print("Sample OLAP Metrics:")
        print(f"  Latency (p95): {olap_metrics.latency_p95} ms")
        print(f"  CPU Utilization: {olap_metrics.cpu_utilization:.1%}")
        print(f"  Memory Utilization: {olap_metrics.memory_utilization:.1%}")
        print(f"  I/O Read: {olap_metrics.io_read_mb} MB")
        print(f"\n🟢 OLAP Score: {score:.4f}")

    except (ValueError, TypeError, AttributeError) as e:
        print(f"\n🔴 ERROR: {e}")
        import traceback
        traceback.print_exc()

    print("-" * 30)
    print("\n[TEST 3] Custom Metric Configuration")
    print("-" * 36)
    try:
        custom_config = MetricConfig(
            workload_type=WorkloadType.MIXED,
            weight_latency=0.35,
            weight_throughput=0.30,
            weight_cpu=0.20,
            weight_memory=0.10,
            weight_error=0.05,
        )

        # Validate weights
        total_weight = (
            custom_config.weight_latency
            + custom_config.weight_throughput
            + custom_config.weight_cpu
            + custom_config.weight_memory
            + custom_config.weight_error
        )
        print("Custom Config:")
        print(f"  Workload Type: {custom_config.workload_type.value}")
        print(f"  Latency Weight: {custom_config.weight_latency}")
        print(f"  Throughput Weight: {custom_config.weight_throughput}")
        print(f"  CPU Weight: {custom_config.weight_cpu}")
        print(f"  Memory Weight: {custom_config.weight_memory}")
        print(f"  Error Weight: {custom_config.weight_error}")
        print(f"  Total Weight: {total_weight:.2f} (should be 1.0)")

        score = custom_config.compute_score(oltp_metrics)
        print(f"\n✓ Mixed Workload Score: {score:.4f}")

    except (ValueError, TypeError, AttributeError) as e:
        print(f"\n🔴 ERROR: {e}")
        import traceback
        traceback.print_exc()

    print("-" * 36)
    print("\n[TEST 4] Edge Cases & Validation")
    print("-" * 32)
    try:
        minimal_metrics = PerformanceMetrics(
            throughput=0.1,  # Very low
            latency_p50=1000.0,
            latency_p95=2000.0,
            latency_p99=3000.0,
            cpu_utilization=0.50,
            memory_utilization=0.50,
            io_read_mb=100.0,
            io_write_mb=50.0,
            cache_hit_ratio=0.90,
            total_queries=10,
            total_time=100.0,
            error_rate=0.0,
        )

        score = OLTP_METRIC_CONFIG.compute_score(minimal_metrics)
        print(f"🟢 Low throughput handled: score = {score:.4f}")

        try:
            invalid_config = MetricConfig(
                workload_type=WorkloadType.OLTP,
                weight_latency=0.5,
                weight_throughput=0.3,
                weight_cpu=0.1,
                weight_memory=0.05,
                weight_error=0.01,  # Sum = 0.96 (invalid)
            )
            print("🔴 Weight validation failed (should have raised error)")
        except ValueError as ve:
            print("🟢 Weight validation working: caught ValueError")

        try:
            invalid_latency = MetricConfig(
                workload_type=WorkloadType.OLTP,
                weight_latency=0.5,
                weight_throughput=0.3,
                weight_cpu=0.1,
                weight_memory=0.05,
                weight_error=0.05,
                latency_metric="p75",  # Invalid
            )
            print("🔴 Latency metric validation failed (should have raised error)")
        except ValueError as ve:
            print("🟢 Latency metric validation working: caught ValueError")

    except (ValueError, TypeError, AttributeError) as e:
        print(f"\n🔴 ERROR: {e}")
        import traceback
        traceback.print_exc()

    print("=" * 55)
    print("All Metrics tests completed!")
    print("=" * 28)

    print("\nPART 2: EVALUATOR CLASS TESTS")
    print("=" * 29)

    knob_space = get_knob_space('minimal')
    print(f"Using knob space: MINIMAL ({len(knob_space.knobs)} knobs)")

    print("\n[TEST 2.1] SYSBENCH OLTP Executor (Mock)")
    print("-" * 40)

    try:
        mock_conn = MagicMock()
        mock_cursor = MagicMock()
        mock_conn.cursor.return_value = mock_cursor

        mock_cursor.fetchall.return_value = [(1, "test", 100)]
        mock_cursor.execute.return_value = None

        executor = SysbenchOLTPExecutor(
            table_size=1000,
            num_threads=2,
            read_write_ratio=0.8
        )

        print("Created executor: table_size=1000, threads=2, read_write=0.8")

        metrics = executor.execute(
            connection=mock_conn,
            duration=0.1,  # 100ms for quick test
            warmup=5.0
        )

        print("\nMetrics collected:")
        print(f"  Latency p95: {metrics.latency_p95:.2f}ms")
        print(f"  Throughput: {metrics.throughput:.2f} TPS")
        print(f"  Total queries: {metrics.total_queries}")
        print(f"  Error rate: {metrics.error_rate:.4f}")

        assert metrics.total_queries > 0, "Should execute queries"
        assert metrics.throughput > 0, "Should have throughput > 0"

        print("\n🟢 SYSBENCH executor working!")
        print("-" * 29)

    except (TypeError, ValueError, AssertionError) as e:
        print(f"🔴 ERROR: {e}")
        import traceback
        traceback.print_exc()

    print("[TEST 2.2] Custom Query Executor (Mock)")
    print("-" * 39)

    try:
        mock_conn = MagicMock()
        mock_cursor = MagicMock()
        mock_conn.cursor.return_value = mock_cursor
        mock_cursor.fetchall.return_value = [(1,)]

        queries = [
            "SELECT COUNT(*) FROM users",
            "SELECT * FROM products LIMIT 10",
            "SELECT AVG(price) FROM orders"
        ]
        weights = [0.5, 0.3, 0.2]

        executor = CustomQueryExecutor(queries=queries, weights=weights)
        print(f"Created custom executor with {len(queries)} queries")

        metrics = executor.execute(
            connection=mock_conn,
            duration=0.1,
            warmup=3.0
        )

        print("\nMetrics collected:")
        print(f"  Latency p95: {metrics.latency_p95:.2f}ms")
        print(f"  Throughput: {metrics.throughput:.2f} QPS")
        print(f"  Total queries: {metrics.total_queries}")

        assert metrics.total_queries > 0, "Should execute queries"

        print("\n🟢 Custom query executor working!")
        print("-" * 33)

    except (TypeError, ValueError, AssertionError) as e:
        print(f"🔴 ERROR: {e}")
        import traceback
        traceback.print_exc()

    print("[TEST 2.3] Evaluator Initialization")
    print("-" * 35)

    try:
        config = EvaluatorConfig(
            workload_type=WorkloadType.OLTP,
            metric_config=MetricConfig.for_oltp(),
            connection_params={
                'host': 'localhost',
                'port': 5432,
                'dbname': 'testdb',
                'user': 'postgres',
                'password': 'password'
            },
            warmup_duration=30.0,
            measurement_duration=30.0,
            cooldown_duration=2.0
        )

        executor = SysbenchOLTPExecutor(table_size=1000)
        evaluator = Evaluator(config, executor)

        print(f"Created: {evaluator}")
        print(f"  Workload: {config.workload_type.value}")
        print(f"  Duration: {config.measurement_duration}s")

        assert evaluator.connection is None, "Should not be connected initially"

        print("\n🟢 Evaluator initialization working!")
        print("-" * 36)

    except (TypeError, ValueError, AssertionError) as e:
        print(f"🔴 ERROR: {e}")
        import traceback
        traceback.print_exc()

    print("[TEST 2.4] Worker Evaluation (Mock)")
    print("-" * 35)

    try:
        config = EvaluatorConfig(
            workload_type=WorkloadType.OLTP,
            metric_config=MetricConfig.for_oltp(),
            connection_params={'host': 'localhost'},
            warmup_duration=5.0,
            measurement_duration=0.1,
            cooldown_duration=0.05
        )

        mock_executor = Mock(spec=SysbenchOLTPExecutor)
        mock_executor.execute.return_value = PerformanceMetrics(
            latency_p50=10.0,
            latency_p95=20.0,
            latency_p99=30.0,
            throughput=500.0,
            total_queries=100,
            total_time=0.2,
            error_rate=0.01
        )

        evaluator = Evaluator(config, mock_executor)

        mock_conn = MagicMock()
        mock_cursor = MagicMock()
        mock_conn.cursor.return_value = mock_cursor
        mock_conn.closed = False

        mock_cursor.fetchone.side_effect = [
            (12345,),  # pg_backend_pid()
            (0.95,),   # Cache hit ratio
        ]

        evaluator.connection = mock_conn

        mock_process = MagicMock()
        mock_process.cpu_percent.return_value = 45.5  # 45.5% CPU
        mock_process.memory_percent.return_value = 12.3  # 12.3% memory

        mock_io = MagicMock()
        mock_io.read_bytes = 1024 * 1024 * 150  # 150 MB
        mock_io.write_bytes = 1024 * 1024 * 80   # 80 MB
        mock_process.io_counters.return_value = mock_io

        worker = Worker(worker_id=0, knob_space=knob_space)
        print(f"Evaluating: {worker}")

        with patch('psutil.Process', return_value=mock_process):
            metrics, score = evaluator.evaluate_worker(worker, apply_config=False)

        print("\nResults:")
        print(f"  Score: {score:.4f}")
        print(f"  Latency p95: {metrics.latency_p95:.2f}ms")
        print(f"  Throughput: {metrics.throughput:.2f} TPS")
        print(f"  CPU utilization: {metrics.cpu_utilization:.1%}")
        print(f"  Memory utilization: {metrics.memory_utilization:.1%}")
        print(f"  I/O Read: {metrics.io_read_mb:.2f} MB")
        print(f"  I/O Write: {metrics.io_write_mb:.2f} MB")
        print(f"  Cache hit ratio: {metrics.cache_hit_ratio:.2%}")

        assert score > 0, "Score should be > 0"
        assert metrics.throughput == 500.0, "Should match mock throughput"
        assert metrics.cpu_utilization > 0, "CPU utilization should be populated"
        assert metrics.memory_utilization > 0, "Memory utilization should be populated"
        assert metrics.io_read_mb > 0, "I/O read should be populated"
        assert metrics.io_write_mb > 0, "I/O write should be populated"

        print("\n🟢 Worker evaluation working!")
        print("-" * 29)

    except (TypeError, ValueError, AssertionError) as e:
        print(f"🔴 ERROR: {e}")
        import traceback
        traceback.print_exc()

    print("[TEST 2.5] MetricConfig Static Methods")
    print("-" * 38)

    try:
        oltp_config = MetricConfig.for_oltp()
        olap_config = MetricConfig.for_olap()
        mixed_config = MetricConfig.for_mixed()

        print(f"OLTP config: {oltp_config.workload_type.value}")
        print(f"OLAP config: {olap_config.workload_type.value}")
        print(f"Mixed config: {mixed_config.workload_type.value}")

        assert oltp_config.workload_type == WorkloadType.OLTP
        assert olap_config.workload_type == WorkloadType.OLAP
        assert mixed_config.workload_type == WorkloadType.MIXED

        print("\n🟢 Static methods working!")
        print("-" * 26)
    except (TypeError, ValueError, AssertionError) as e:
        print(f"🔴 ERROR: {e}")
        import traceback
        traceback.print_exc()

    print("ALL TESTS COMPLETED!")
    print("=" * 65)
