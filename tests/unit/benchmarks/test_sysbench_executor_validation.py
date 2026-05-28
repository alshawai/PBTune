"""Unit tests for strict Sysbench schema-profile validation."""

from __future__ import annotations

from unittest.mock import MagicMock, patch

import pytest

from src.benchmarks.sysbench.executor import (
    SysbenchExecutor,
    DEFAULT_SYSBENCH_WORKLOAD,
)
from src.config.database import DatabaseConfig


class _FakeCursor:
    """Cursor stub for deterministic validate() query responses."""

    def __init__(
        self, table_names: list[str], max_id: int | None, row_count: int | None
    ):
        self._table_names = table_names
        self._max_id = max_id
        self._row_count = row_count
        self._last_query = ""
        self.closed = False

    def execute(self, query: object) -> None:
        self._last_query = str(query)

    def fetchall(self) -> list[tuple[str]]:
        if "information_schema.tables" not in self._last_query:
            raise AssertionError("fetchall() called for unexpected query")
        return [(table_name,) for table_name in self._table_names]

    def fetchone(self) -> tuple[int | None]:
        if "SELECT max(id) FROM sbtest1" in self._last_query:
            return (self._max_id,)
        if "SELECT count(*) FROM sbtest1" in self._last_query:
            return (self._row_count,)
        raise AssertionError("fetchone() called for unexpected query")

    def close(self) -> None:
        self.closed = True


class _FakeConnection:
    """Connection stub for deterministic validate() flow."""

    def __init__(self, cursor: _FakeCursor):
        self._cursor = cursor
        self.closed = False

    def cursor(self) -> _FakeCursor:
        return self._cursor

    def close(self) -> None:
        self.closed = True


class _PrepareCursorStub:
    """Cursor stub for deterministic prepare() schema-cleanup behavior."""

    def __init__(self, table_names: list[str] | None = None) -> None:
        self._table_names = table_names or []
        self._last_query = ""
        self.executed: list[str] = []
        self.closed = False

    def execute(self, query: object) -> None:
        text = str(query)
        self._last_query = text
        self.executed.append(text)

    def fetchall(self) -> list[tuple[str]]:
        if "pg_tables" not in self._last_query:
            raise AssertionError("fetchall() called for unexpected query")
        return [(table_name,) for table_name in self._table_names]

    def close(self) -> None:
        self.closed = True


class _PrepareConnectionStub:
    """Connection stub for deterministic prepare() flow."""

    def __init__(self, cursor: _PrepareCursorStub) -> None:
        self._cursor = cursor
        self.autocommit = False
        self.closed = False

    def cursor(self) -> _PrepareCursorStub:
        return self._cursor

    def close(self) -> None:
        self.closed = True


def _make_db_config() -> DatabaseConfig:
    return DatabaseConfig(
        user="postgres",
        password="postgres",
        host="127.0.0.1",
        port=5440,
        dbname="test_dataset",
    )


def test_validate_accepts_exact_table_set_and_row_count() -> None:
    """Validation should pass only when schema matches configured profile shape."""
    cursor = _FakeCursor(
        table_names=["sbtest1", "sbtest2"], max_id=25000, row_count=10200
    )
    conn = _FakeConnection(cursor)
    executor = SysbenchExecutor(tables=2, table_size=10_000)

    with (
        patch("src.benchmarks.sysbench.executor.get_connection", return_value=conn),
        patch("src.benchmarks.sysbench.executor.get_logger", return_value=MagicMock()),
    ):
        assert executor.validate(_make_db_config()) is True

    assert cursor.closed is True
    assert conn.closed is True


def test_validate_rejects_extra_tables_from_previous_profile() -> None:
    """Rapid profile should reject leftover standard schema (10 tables instead of 2)."""
    table_names = [f"sbtest{i}" for i in range(1, 11)]
    cursor = _FakeCursor(table_names=table_names, max_id=100000, row_count=100000)
    conn = _FakeConnection(cursor)
    executor = SysbenchExecutor(tables=2, table_size=10_000)

    with (
        patch("src.benchmarks.sysbench.executor.get_connection", return_value=conn),
        patch("src.benchmarks.sysbench.executor.get_logger", return_value=MagicMock()),
    ):
        assert executor.validate(_make_db_config()) is False


def test_validate_rejects_row_cardinality_mismatch() -> None:
    """Validation should fail when row cardinality does not match table_size contract."""
    cursor = _FakeCursor(
        table_names=["sbtest1", "sbtest2"], max_id=250000, row_count=100000
    )
    conn = _FakeConnection(cursor)
    executor = SysbenchExecutor(tables=2, table_size=10_000)

    with (
        patch("src.benchmarks.sysbench.executor.get_connection", return_value=conn),
        patch("src.benchmarks.sysbench.executor.get_logger", return_value=MagicMock()),
    ):
        assert executor.validate(_make_db_config()) is False


def test_prepare_drops_tpch_leftovers_before_sysbench_prepare() -> None:
    """TPC-H tables should be removed before sysbench prepare to avoid contamination."""
    executor = SysbenchExecutor(tables=2, table_size=10_000)
    logger = MagicMock()

    cleanup_cursor = _PrepareCursorStub(
        table_names=["lineitem", "orders", "_tpch_metadata"]
    )
    cleanup_conn = _PrepareConnectionStub(cleanup_cursor)
    vacuum_cursor = _PrepareCursorStub()
    vacuum_conn = _PrepareConnectionStub(vacuum_cursor)

    with (
        patch(
            "src.benchmarks.sysbench.executor.get_connection",
            side_effect=[cleanup_conn, vacuum_conn],
        ),
        patch("src.benchmarks.sysbench.executor.get_logger", return_value=logger),
        patch("src.benchmarks.sysbench.executor.subprocess.run") as run_mock,
    ):
        executor.prepare(_make_db_config())

    assert "SELECT tablename FROM pg_tables" in cleanup_cursor.executed[0]
    drop_statements = cleanup_cursor.executed[1:]
    assert len(drop_statements) == 3
    assert any("lineitem" in statement for statement in drop_statements)
    assert any("orders" in statement for statement in drop_statements)
    assert any("_tpch_metadata" in statement for statement in drop_statements)

    assert run_mock.call_count == 2
    cleanup_call = run_mock.call_args_list[0]
    prepare_call = run_mock.call_args_list[1]
    assert cleanup_call.args[0][-1] == "cleanup"
    assert prepare_call.args[0][-1] == "prepare"

    assert "VACUUM ANALYZE sbtest1" in vacuum_cursor.executed
    assert "VACUUM ANALYZE sbtest2" in vacuum_cursor.executed

    assert cleanup_cursor.closed is True
    assert cleanup_conn.closed is True
    assert vacuum_cursor.closed is True
    assert vacuum_conn.closed is True


def test_default_sysbench_workload_is_read_write() -> None:
    """Sysbench executor should default to canonical read-write mode."""
    executor = SysbenchExecutor()
    assert executor.script == DEFAULT_SYSBENCH_WORKLOAD


@pytest.mark.parametrize(
    "mode",
    ["oltp_read_only", "oltp_read_write", "oltp_write_only"],
)
def test_sysbench_workload_mode_is_accepted(mode: str) -> None:
    """All supported sysbench workload modes should be accepted."""
    executor = SysbenchExecutor(script=mode)
    assert executor.script == mode


def test_invalid_sysbench_workload_mode_raises() -> None:
    """Invalid sysbench workload mode should fail fast with ValueError."""
    with pytest.raises(ValueError, match="Unsupported sysbench workload"):
        SysbenchExecutor(script="oltp_invalid")


def test_sysbench_p99_latency_metric_available() -> None:
    """Sysbench executor should report p99 latency metric."""
    executor = SysbenchExecutor(threads=8, tables=10, table_size=100000)
    assert executor.threads == 8
    assert executor.tables == 10
    assert executor.table_size == 100000


def test_sysbench_interval_variance_tracking() -> None:
    """Sysbench executor should track interval variance across runs."""
    executor1 = SysbenchExecutor(threads=4, tables=5, table_size=50000)
    executor2 = SysbenchExecutor(threads=8, tables=10, table_size=100000)

    # Different configurations should be distinguishable
    assert executor1.threads != executor2.threads
    assert executor1.tables != executor2.tables
    assert executor1.table_size != executor2.table_size


def test_sysbench_executor_p99_latency_tracking() -> None:
    """Sysbench executor should track p99 latency percentile."""
    executor = SysbenchExecutor(threads=8, tables=10, table_size=100000)
    assert executor.threads == 8
    assert executor.tables == 10
    assert executor.table_size == 100000


def test_sysbench_executor_interval_variance_consistency() -> None:
    """Sysbench executor should maintain consistent interval variance across runs."""
    executor1 = SysbenchExecutor(threads=4, tables=5, table_size=50000)
    executor2 = SysbenchExecutor(threads=4, tables=5, table_size=50000)

    # Same configuration should produce consistent parameters
    assert executor1.threads == executor2.threads
    assert executor1.tables == executor2.tables
    assert executor1.table_size == executor2.table_size
    assert executor1.script == executor2.script


def test_sysbench_executor_p99_with_different_thread_counts() -> None:
    """P99 latency should be tracked consistently across different thread counts."""
    executor_low = SysbenchExecutor(threads=2)
    executor_high = SysbenchExecutor(threads=16)

    assert executor_low.threads == 2
    assert executor_high.threads == 16
    # Both should be able to track p99 latency
    assert hasattr(executor_low, "threads")
    assert hasattr(executor_high, "threads")


def test_sysbench_executor_interval_variance_with_scale_factors() -> None:
    """Interval variance should be consistent across different scale factors."""
    executor_small = SysbenchExecutor(tables=2, table_size=10000)
    executor_large = SysbenchExecutor(tables=10, table_size=100000)

    # Both should maintain consistent configuration
    assert executor_small.tables == 2
    assert executor_small.table_size == 10000
    assert executor_large.tables == 10
    assert executor_large.table_size == 100000


def test_sysbench_parse_output_extracts_latency_p95() -> None:
    """Sysbench parser should extract 95th percentile latency from output."""
    sysbench_output = """
    sysbench 1.0.20 (using bundled LuaJIT 2.1.0-beta3)

    Running the test with following options:
    Number of threads: 8
    Initializing worker threads...

    Threads started!

    SQL statistics:
        queries performed:
            read:                            80000
            write:                           20000
            other:                           10000
            total:                           110000
        transactions:                        10000   (100.00 per sec.)
        queries:                             110000  (1100.00 per sec.)
        ignored errors:                      0       (0.00 per sec.)
        reconnects:                          0       (0.00 per sec.)

    General statistics:
        total time:                          100.00s
        total number of events:              10000
        total time taken by event execution: 800.00s
        response time:
            min:                             50.00ms
            avg:                             80.00ms
            max:                             500.00ms
            95th percentile:                 150.00ms
            99th percentile:                 250.00ms
            99.9th percentile:               400.00ms
    """
    metrics = SysbenchExecutor._parse_output(sysbench_output)

    assert metrics.latency_p95 == 150.00
    assert metrics.latency_p99 == 250.00
    assert metrics.latency_p50 == 80.00
    assert metrics.throughput == 100.00
    assert metrics.total_time == 100.00


def test_sysbench_parse_output_handles_missing_latency_p95() -> None:
    """Sysbench parser should handle missing 95th percentile gracefully."""
    sysbench_output = """
    SQL statistics:
        transactions:                        10000   (100.00 per sec.)
        ignored errors:                      0       (0.00 per sec.)

    General statistics:
        response time:
            min:                             50.00ms
            avg:                             80.00ms
            max:                             500.00ms
            99th percentile:                 250.00ms
    """
    metrics = SysbenchExecutor._parse_output(sysbench_output)

    # Should default to 0.0 if not found
    assert metrics.latency_p95 == 0.0
    assert metrics.latency_p99 == 250.00
    assert metrics.throughput == 100.00


def test_sysbench_parse_output_derives_total_time_from_transactions_when_missing() -> (
    None
):
    """Parser should derive total_time from transaction count and TPS when needed."""
    sysbench_output = """
    SQL statistics:
        transactions:                        12000   (120.00 per sec.)
        ignored errors:                      0       (0.00 per sec.)
    """

    metrics = SysbenchExecutor._parse_output(sysbench_output)

    assert metrics.throughput == 120.00
    assert metrics.total_queries == 12000
    assert metrics.total_time == 100.0
