"""Tests for :mod:`src.utils.session_clock`."""

from __future__ import annotations

import time
from datetime import datetime

import pytest

from src.utils.session_clock import (
    SessionClock,
    format_session_id,
    reset_session_timestamp_for_testing,
    session_timestamp,
)


@pytest.fixture(autouse=True)
def _reset_session_timestamp():
    """Each test gets a fresh canonical timestamp."""
    reset_session_timestamp_for_testing()
    yield
    reset_session_timestamp_for_testing()


def test_session_clock_elapsed_is_monotonic_and_nonnegative() -> None:
    clock = SessionClock()
    first = clock.elapsed()
    time.sleep(0.005)
    second = clock.elapsed()
    assert first >= 0.0
    assert second >= first


def test_session_clock_now_is_monotonic() -> None:
    a = SessionClock.now()
    b = SessionClock.now()
    assert b >= a


def test_session_timestamp_is_stable_across_calls() -> None:
    first = session_timestamp()
    time.sleep(0.005)
    second = session_timestamp()
    assert first is second
    assert isinstance(first, datetime)


def test_reset_session_timestamp_allows_fresh_capture() -> None:
    first = session_timestamp()
    reset_session_timestamp_for_testing()
    time.sleep(0.005)
    second = session_timestamp()
    assert first is not second


def test_format_session_id_uses_canonical_timestamp_by_default() -> None:
    ts = session_timestamp()
    expected = ts.strftime("%Y%m%d_%H%M")
    assert format_session_id() == expected


def test_format_session_id_accepts_explicit_timestamp() -> None:
    explicit = datetime(2024, 7, 4, 13, 42, 33)
    assert format_session_id(explicit) == "20240704_1342"


def test_format_session_id_format_shape() -> None:
    sid = format_session_id()
    # YYYYMMDD_HHMM => 13 chars total, underscore at position 8
    assert len(sid) == 13
    assert sid[8] == "_"
    assert sid[:8].isdigit()
    assert sid[9:].isdigit()
