"""Unit tests for TPC-H schema cleanup safeguards."""

from __future__ import annotations

from unittest.mock import patch

from src.benchmarks.tpch.executor import TPCHExecutor
from src.benchmarks.tpch.setup_dbgen import remove_generated_data


class _CursorStub:
    """Cursor stub for validating DROP behavior in schema cleanup."""

    def __init__(self, table_names: list[str]) -> None:
        self._table_names = table_names
        self.executed: list[tuple[object, tuple[object, ...]]] = []

    def execute(self, query: object, *params: object) -> None:
        self.executed.append((query, params))

    def fetchall(self) -> list[tuple[str]]:
        return [(table_name,) for table_name in self._table_names]


def test_drop_existing_public_tables_removes_foreign_workload_tables() -> None:
    """TPC-H cleanup should remove leftover Sysbench/public tables before load."""
    executor = TPCHExecutor(scale_factor=0.1)
    cursor = _CursorStub(["sbtest1", "lineitem", "_tpch_metadata"])

    with patch("src.benchmarks.executor.LOGGER") as mock_logger:
        executor._drop_existing_public_tables(cursor)

        assert len(cursor.executed) == 4
        assert "SELECT tablename FROM pg_tables" in str(cursor.executed[0][0])

        drop_statements = [str(query) for query, _ in cursor.executed[1:]]
        assert any("sbtest1" in statement for statement in drop_statements)
        assert any("lineitem" in statement for statement in drop_statements)
        assert any("_tpch_metadata" in statement for statement in drop_statements)
        assert mock_logger.debug.call_count == 2


def test_drop_existing_public_tables_noop_when_schema_is_empty() -> None:
    """Cleanup should be a no-op when public schema has no tables."""
    executor = TPCHExecutor(scale_factor=0.1)
    cursor = _CursorStub([])

    with patch("src.benchmarks.executor.LOGGER") as mock_logger:
        executor._drop_existing_public_tables(cursor)

        assert len(cursor.executed) == 1
        mock_logger.debug.assert_not_called()


def test_remove_generated_data_reclaims_tables_and_cache_markers(tmp_path) -> None:
    """Transient dbgen data should not compete with PostgreSQL index builds."""
    table_file = tmp_path / "lineitem.tbl"
    marker = tmp_path / ".generated_sf1.0"
    binary = tmp_path / "dbgen"
    table_file.write_bytes(b"test data")
    marker.write_text("SF=1.0\n", encoding="utf-8")
    binary.write_text("binary", encoding="utf-8")

    remove_generated_data(tmp_path)

    assert not table_file.exists()
    assert not marker.exists()
    assert binary.exists()
