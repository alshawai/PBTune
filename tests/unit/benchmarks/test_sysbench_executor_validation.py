"""Unit tests for strict Sysbench schema-profile validation."""

from __future__ import annotations

from unittest.mock import MagicMock, patch

from src.benchmarks.sysbench.executor import SysbenchExecutor
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
