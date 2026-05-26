"""Unit tests for KnobApplicator.read_back_knob_state() — the BO read-back abstraction.

Tests are fully offline: psycopg2 connections and cursors are mocked so no
live PostgreSQL instance is required.
"""

from __future__ import annotations

from typing import Any, Dict, List, Optional
from unittest.mock import MagicMock, patch

import pytest

from src.config.database import DatabaseConfig
from src.tuner.config.knob_space import KnobDefinition, KnobScale, KnobSpace, KnobType
from src.utils.applicator import ApplicatorConfig, KnobApplicator


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _make_db_config() -> DatabaseConfig:
    return DatabaseConfig(
        user="postgres",
        password="test",
        host="localhost",
        port=5432,
        dbname="test_db",
    )


def _make_applicator() -> KnobApplicator:
    return KnobApplicator(
        db_config=_make_db_config(),
        config=ApplicatorConfig(persist=False, validate=False),
    )


def _make_knob_space(knobs: List[KnobDefinition]) -> KnobSpace:
    return KnobSpace(knobs)


def _mock_pg_rows(rows: List[tuple]):
    """Return a mock psycopg2 connection whose cursor yields *rows*."""
    cursor = MagicMock()
    cursor.__enter__ = MagicMock(return_value=cursor)
    cursor.__exit__ = MagicMock(return_value=False)
    cursor.fetchall.return_value = rows

    conn = MagicMock()
    conn.cursor.return_value = cursor
    return conn


# ---------------------------------------------------------------------------
# get_current_values
# ---------------------------------------------------------------------------

class TestGetCurrentValues:
    """Test the raw pg_settings read helper."""

    def test_returns_setting_and_unit_tuple(self):
        pg_rows = [("shared_buffers", "16384", "8kB")]
        applicator = _make_applicator()

        with patch(
            "src.utils.applicator.get_connection", return_value=_mock_pg_rows(pg_rows)
        ):
            result = applicator.get_current_values(["shared_buffers"])

        assert result == {"shared_buffers": ("16384", "8kB")}


# ---------------------------------------------------------------------------
# _apply_pg_unit (static helper)
# ---------------------------------------------------------------------------

class TestApplyPgUnit:
    """Test the static unit-conversion helper in isolation."""

    def test_memory_same_unit(self):
        """8kB × 16384 → 16384 (units cancel)."""
        result = KnobApplicator._apply_pg_unit("16384", "8kB", "8kB")
        assert result == pytest.approx(16384.0)

    def test_memory_cross_unit_8kb_to_kb(self):
        """8kB × 1 → 8 kB."""
        result = KnobApplicator._apply_pg_unit("1", "8kB", "kB")
        # 1 × 8192 bytes / 1024 = 8 kB
        assert result == pytest.approx(8.0)

    def test_memory_cross_unit_mb_to_kb(self):
        """MB → kB: 128 MB = 131072 kB."""
        result = KnobApplicator._apply_pg_unit("128", "MB", "kB")
        assert result == pytest.approx(131072.0)

    def test_time_same_unit_ms(self):
        """ms → ms: no change."""
        result = KnobApplicator._apply_pg_unit("500", "ms", "ms")
        assert result == pytest.approx(500.0)

    def test_time_cross_unit_s_to_ms(self):
        """s → ms: 30 s = 30000 ms."""
        result = KnobApplicator._apply_pg_unit("30", "s", "ms")
        assert result == pytest.approx(30_000.0)

    def test_dimensionless_no_unit(self):
        """No pg unit → return numeric as-is."""
        result = KnobApplicator._apply_pg_unit("4", None, None)
        assert result == pytest.approx(4.0)

    def test_empty_string_unit(self):
        """Empty string unit treated as dimensionless."""
        result = KnobApplicator._apply_pg_unit("8", "", None)
        assert result == pytest.approx(8.0)

    def test_non_numeric_setting_returns_none(self):
        """Boolean strings cannot be converted."""
        result = KnobApplicator._apply_pg_unit("on", None, None)
        assert result is None

    def test_empty_setting_returns_none(self):
        result = KnobApplicator._apply_pg_unit("", "kB", "kB")
        assert result is None

    def test_unknown_unit_falls_back_to_raw(self):
        """Unrecognised unit → return raw numeric value."""
        result = KnobApplicator._apply_pg_unit("42", "UNKNOWN_UNIT", None)
        assert result == pytest.approx(42.0)


# ---------------------------------------------------------------------------
# read_back_knob_state — successful paths
# ---------------------------------------------------------------------------

class TestReadBackKnobStateSuccess:
    """Test successful read-back scenarios with mocked DB."""

    def test_integer_knob_with_unit_conversion(self):
        """shared_buffers: pg reports setting=16384 unit=8kB → 16384 pages."""
        knob_space = _make_knob_space([
            KnobDefinition(
                name="shared_buffers",
                knob_type=KnobType.INTEGER,
                unit="8kB",
                min_value=128,
                max_value=1_000_000,
                scale=KnobScale.LOG,
            )
        ])
        pg_rows = [("shared_buffers", "16384", "8kB")]

        applicator = _make_applicator()
        with patch(
            "src.utils.applicator.get_connection", return_value=_mock_pg_rows(pg_rows)
        ):
            result = applicator.read_back_knob_state(
                knob_names=["shared_buffers"],
                knob_space=knob_space,
            )

        assert result == {"shared_buffers": 16384}
        assert isinstance(result["shared_buffers"], int)

    def test_real_knob_dimensionless(self):
        """random_page_cost: dimensionless float."""
        knob_space = _make_knob_space([
            KnobDefinition(
                name="random_page_cost",
                knob_type=KnobType.REAL,
                unit=None,
                min_value=0.1,
                max_value=10.0,
                scale=KnobScale.LINEAR,
            )
        ])
        pg_rows = [("random_page_cost", "1.1", None)]

        applicator = _make_applicator()
        with patch(
            "src.utils.applicator.get_connection", return_value=_mock_pg_rows(pg_rows)
        ):
            result = applicator.read_back_knob_state(
                knob_names=["random_page_cost"],
                knob_space=knob_space,
            )

        assert result == {"random_page_cost": pytest.approx(1.1)}
        assert isinstance(result["random_page_cost"], float)

    def test_boolean_knob_on(self):
        """fsync: pg stores 'on' → True."""
        knob_space = _make_knob_space([
            KnobDefinition(
                name="fsync",
                knob_type=KnobType.BOOLEAN,
                unit=None,
            )
        ])
        pg_rows = [("fsync", "on", None)]

        applicator = _make_applicator()
        with patch(
            "src.utils.applicator.get_connection", return_value=_mock_pg_rows(pg_rows)
        ):
            result = applicator.read_back_knob_state(
                knob_names=["fsync"],
                knob_space=knob_space,
            )

        assert result == {"fsync": True}
        assert isinstance(result["fsync"], bool)

    def test_boolean_knob_off(self):
        """fsync: pg stores 'off' → False."""
        knob_space = _make_knob_space([
            KnobDefinition(name="fsync", knob_type=KnobType.BOOLEAN, unit=None)
        ])
        pg_rows = [("fsync", "off", None)]

        applicator = _make_applicator()
        with patch(
            "src.utils.applicator.get_connection", return_value=_mock_pg_rows(pg_rows)
        ):
            result = applicator.read_back_knob_state(
                knob_names=["fsync"],
                knob_space=knob_space,
            )

        assert result == {"fsync": False}

    def test_enum_knob(self):
        """wal_level: pg stores 'replica' → str."""
        knob_space = _make_knob_space([
            KnobDefinition(
                name="wal_level",
                knob_type=KnobType.ENUM,
                unit=None,
                enum_values=["minimal", "replica", "logical"],
            )
        ])
        pg_rows = [("wal_level", "replica", None)]

        applicator = _make_applicator()
        with patch(
            "src.utils.applicator.get_connection", return_value=_mock_pg_rows(pg_rows)
        ):
            result = applicator.read_back_knob_state(
                knob_names=["wal_level"],
                knob_space=knob_space,
            )

        assert result == {"wal_level": "replica"}
        assert isinstance(result["wal_level"], str)

    def test_multiple_knobs_mixed_types(self):
        """Multiple knobs returned in a single query."""
        knob_space = _make_knob_space([
            KnobDefinition(
                name="shared_buffers",
                knob_type=KnobType.INTEGER,
                unit="8kB",
                min_value=128,
                max_value=1_000_000,
                scale=KnobScale.LOG,
            ),
            KnobDefinition(
                name="work_mem",
                knob_type=KnobType.INTEGER,
                unit="kB",
                min_value=64,
                max_value=1_000_000,
                scale=KnobScale.LOG,
            ),
            KnobDefinition(
                name="random_page_cost",
                knob_type=KnobType.REAL,
                unit=None,
                min_value=0.1,
                max_value=10.0,
                scale=KnobScale.LINEAR,
            ),
        ])
        # shared_buffers: 16384 × 8kB = 134 217 728 bytes / 8192 = 16384 pages
        # work_mem: 8192 × kB = 8 388 608 bytes / 1024 = 8192 kB
        pg_rows = [
            ("shared_buffers", "16384", "8kB"),
            ("work_mem", "8192", "kB"),
            ("random_page_cost", "1.5", None),
        ]

        applicator = _make_applicator()
        with patch(
            "src.utils.applicator.get_connection", return_value=_mock_pg_rows(pg_rows)
        ):
            result = applicator.read_back_knob_state(
                knob_names=["shared_buffers", "work_mem", "random_page_cost"],
                knob_space=knob_space,
            )

        assert result["shared_buffers"] == 16384
        assert result["work_mem"] == 8192
        assert result["random_page_cost"] == pytest.approx(1.5)

    def test_quantization_reflected(self):
        """
        Core correctness test: BO suggested 200 000 kB but PG rounded to
        16384 × 8kB = 16384 pages.  The returned value should be 16384, not
        the originally suggested value.
        """
        knob_space = _make_knob_space([
            KnobDefinition(
                name="shared_buffers",
                knob_type=KnobType.INTEGER,
                unit="8kB",
                min_value=128,
                max_value=2_000_000,
                scale=KnobScale.LOG,
            )
        ])
        # pg actually applied 16384 pages (not whatever BO asked for)
        pg_rows = [("shared_buffers", "16384", "8kB")]

        applicator = _make_applicator()
        with patch(
            "src.utils.applicator.get_connection", return_value=_mock_pg_rows(pg_rows)
        ):
            result = applicator.read_back_knob_state(
                knob_names=["shared_buffers"],
                knob_space=knob_space,
            )

        # Must reflect the quantized PG value, not the BO suggestion
        assert result["shared_buffers"] == 16384

    def test_knob_not_in_knob_space_treated_as_float(self):
        """A knob absent from KnobSpace is stored as raw float."""
        knob_space = _make_knob_space([])  # empty space
        pg_rows = [("some_pg_internal_param", "42", None)]

        applicator = _make_applicator()
        with patch(
            "src.utils.applicator.get_connection", return_value=_mock_pg_rows(pg_rows)
        ):
            result = applicator.read_back_knob_state(
                knob_names=["some_pg_internal_param"],
                knob_space=knob_space,
            )

        assert result["some_pg_internal_param"] == pytest.approx(42.0)

    def test_empty_knob_names_returns_empty_dict(self):
        """Passing an empty list skips the DB query entirely."""
        applicator = _make_applicator()
        knob_space = _make_knob_space([])
        with patch("src.utils.applicator.get_connection") as mock_conn:
            result = applicator.read_back_knob_state(
                knob_names=[],
                knob_space=knob_space,
            )
        mock_conn.assert_not_called()
        assert result == {}


# ---------------------------------------------------------------------------
# read_back_knob_state — error / edge-case paths
# ---------------------------------------------------------------------------

class TestReadBackKnobStateEdgeCases:
    """Test that failures are handled gracefully and never raise."""

    def test_connection_failure_returns_empty_dict(self):
        """OperationalError (DB down / timeout) → empty dict, no exception."""
        import psycopg2

        knob_space = _make_knob_space([
            KnobDefinition(name="shared_buffers", knob_type=KnobType.INTEGER, unit="8kB")
        ])
        applicator = _make_applicator()

        with patch(
            "src.utils.applicator.get_connection",
            side_effect=psycopg2.OperationalError("connection refused"),
        ):
            result = applicator.read_back_knob_state(
                knob_names=["shared_buffers"],
                knob_space=knob_space,
            )

        assert result == {}

    def test_generic_psycopg2_error_returns_empty_dict(self):
        """Any psycopg2 query error → empty dict."""
        import psycopg2

        knob_space = _make_knob_space([])
        applicator = _make_applicator()

        conn = MagicMock()
        cursor = MagicMock()
        cursor.__enter__ = MagicMock(return_value=cursor)
        cursor.__exit__ = MagicMock(return_value=False)
        cursor.execute.side_effect = psycopg2.ProgrammingError("syntax error")
        conn.cursor.return_value = cursor

        with patch("src.utils.applicator.get_connection", return_value=conn):
            result = applicator.read_back_knob_state(
                knob_names=["bad_knob"],
                knob_space=knob_space,
            )

        assert result == {}

    def test_knob_missing_from_pg_settings_is_silently_skipped(self):
        """A requested knob absent from pg_settings is simply not in the result."""
        knob_space = _make_knob_space([
            KnobDefinition(name="known_knob", knob_type=KnobType.REAL, unit=None),
        ])
        # pg_settings only returns the one known knob; the requested unknown is absent
        pg_rows = [("known_knob", "2.0", None)]

        applicator = _make_applicator()
        with patch(
            "src.utils.applicator.get_connection", return_value=_mock_pg_rows(pg_rows)
        ):
            result = applicator.read_back_knob_state(
                knob_names=["known_knob", "nonexistent_knob"],
                knob_space=knob_space,
            )

        assert "known_knob" in result
        assert "nonexistent_knob" not in result
