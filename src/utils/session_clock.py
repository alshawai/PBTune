"""Session-level monotonic clock and canonical timestamp helpers.

This module exposes two related primitives that PBTune uses for any
time-sensitive bookkeeping:

* ``SessionClock`` — a thin wrapper around :func:`time.monotonic` for
  duration measurements. All ``TimingRecord`` durations should use this
  clock instead of :func:`time.time` to avoid wall-clock skew (NTP
  adjustments, leap seconds, manual clock changes, etc.).

* ``session_timestamp`` / ``format_session_id`` — a process-wide
  canonical wall-clock timestamp captured exactly once on first access.
  All user-facing artifacts (JSON filenames, log filenames, HTML report
  filenames, JSON metadata fields) within a single tuning session must
  derive their ``YYYYMMDD_HHMM`` string from this timestamp so that
  parallel writers don't drift.
"""

from __future__ import annotations

import time
from datetime import datetime
from typing import Optional


class SessionClock:
    """Monotonic clock for duration measurements within a session.

    All timing brackets in PBTune use this class instead of
    :func:`time.time` to avoid wall-clock skew (NTP adjustments, leap
    seconds, etc.).
    """

    def __init__(self) -> None:
        self._origin = time.monotonic()

    def elapsed(self) -> float:
        """Return seconds elapsed since this clock was constructed."""
        return time.monotonic() - self._origin

    @staticmethod
    def now() -> float:
        """Return the current monotonic clock value (seconds)."""
        return time.monotonic()


_session_timestamp: Optional[datetime] = None


def session_timestamp() -> datetime:
    """Return the canonical session timestamp.

    The first call captures :func:`datetime.now`; every subsequent call
    in the same Python process returns the same value. Use this for any
    user-facing timestamp (filenames, log headers, JSON metadata) so all
    artifacts written during a single session share a single ID.
    """
    global _session_timestamp
    if _session_timestamp is None:
        _session_timestamp = datetime.now()
    return _session_timestamp


def reset_session_timestamp_for_testing() -> None:
    """Reset the cached session timestamp. Test-only.

    Production code must never call this. Tests use it to isolate
    sessions when exercising :func:`session_timestamp` semantics.
    """
    global _session_timestamp
    _session_timestamp = None


def format_session_id(timestamp: Optional[datetime] = None) -> str:
    """Return the canonical session ID string ``YYYYMMDD_HHMM``.

    Parameters
    ----------
    timestamp
        Optional explicit timestamp. When ``None`` (the usual case), the
        canonical :func:`session_timestamp` is used.
    """
    ts = timestamp or session_timestamp()
    return ts.strftime("%Y%m%d_%H%M")
