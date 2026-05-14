"""Unit tests for TPC-H schema cleanup safeguards."""

from __future__ import annotations

from unittest.mock import MagicMock

from src.benchmarks.tpch.executor import TPCHExecutor


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
    executor.logger = MagicMock()
    cursor = _CursorStub(["sbtest1", "lineitem", "_tpch_metadata"])

    executor._drop_existing_public_tables(cursor)

    assert len(cursor.executed) == 4
    assert "SELECT tablename FROM pg_tables" in str(cursor.executed[0][0])

    drop_statements = [str(query) for query, _ in cursor.executed[1:]]
    assert any("sbtest1" in statement for statement in drop_statements)
    assert any("lineitem" in statement for statement in drop_statements)
    assert any("_tpch_metadata" in statement for statement in drop_statements)
    executor.logger.debug.assert_called_once_with(
        "[TPC-H] Dropping existing public tables (%d)...",
        3,
    )


def test_drop_existing_public_tables_noop_when_schema_is_empty() -> None:
    """Cleanup should be a no-op when public schema has no tables."""
    executor = TPCHExecutor(scale_factor=0.1)
    executor.logger = MagicMock()
    cursor = _CursorStub([])

    executor._drop_existing_public_tables(cursor)

    assert len(cursor.executed) == 1
    executor.logger.debug.assert_not_called()
