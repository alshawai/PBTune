"""Per-component timing instrumentation primitives.

This module provides two small dataclasses used throughout PBTune to
record per-component wall-clock durations for the SIGMOD/PVLDB-style
cost decomposition:

* :class:`TimingRecord` — an immutable record of a single bracketed
  duration.

* :class:`TimingRecorder` — collects records via a context manager and
  exposes per-component aggregations.

All durations are measured with :func:`time.monotonic` so they are not
affected by wall-clock skew (NTP adjustments, leap seconds, etc.).
"""

from __future__ import annotations

import statistics
import time
from contextlib import contextmanager
from dataclasses import dataclass, field
from typing import Any, Iterator


@dataclass(frozen=True)
class TimingRecord:
    """Single immutable timing record.

    Attributes
    ----------
    component
        Component name (e.g., ``"knob_apply"``, ``"activate_reload"``).
    seconds
        Duration in seconds.
    metadata
        Optional structured metadata associated with the bracket.
    """

    component: str
    seconds: float
    metadata: dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        """Serialize for JSON output."""
        out: dict[str, Any] = {
            "component": self.component,
            "seconds": round(self.seconds, 6),
        }
        if self.metadata:
            out["metadata"] = self.metadata
        return out


class TimingRecorder:
    """Collects per-component timing records using the monotonic clock.

    Use the :meth:`span` context manager to time a code block::

        recorder = TimingRecorder()
        with recorder.span("knob_apply"):
            applicator.apply_only(config)
        with recorder.span("activate_reload", strategy="reload"):
            applicator.activate(...)

    Externally-measured durations can be added via :meth:`add`.
    """

    def __init__(self) -> None:
        self._records: list[TimingRecord] = []

    @contextmanager
    def span(self, component: str, **metadata: Any) -> Iterator[None]:
        """Time the body of a ``with`` block as a single component.

        Parameters
        ----------
        component
            Component name to record.
        **metadata
            Arbitrary key/value pairs stored on the resulting
            :class:`TimingRecord`.
        """
        t0 = time.monotonic()
        try:
            yield
        finally:
            elapsed = time.monotonic() - t0
            self._records.append(TimingRecord(component, elapsed, dict(metadata)))

    def add(self, component: str, seconds: float, **metadata: Any) -> None:
        """Record a duration measured externally (e.g., parsed from logs)."""
        self._records.append(TimingRecord(component, seconds, dict(metadata)))

    @property
    def records(self) -> list[TimingRecord]:
        """Return a defensive copy of the recorded entries."""
        return list(self._records)

    def by_component(self) -> dict[str, list[float]]:
        """Group recorded durations by component name."""
        out: dict[str, list[float]] = {}
        for r in self._records:
            out.setdefault(r.component, []).append(r.seconds)
        return out

    def aggregate(self) -> dict[str, dict[str, float]]:
        """Return per-component summary statistics.

        For each component the returned dict contains
        ``{"n", "mean", "std", "min", "max", "total"}``. Std is the
        population standard deviation (``pstdev``) or ``0.0`` when
        ``n == 1``. Empty recorders return ``{}``.
        """
        out: dict[str, dict[str, float]] = {}
        for component, durations in self.by_component().items():
            n = len(durations)
            out[component] = {
                "n": float(n),
                "mean": statistics.fmean(durations),
                "std": statistics.pstdev(durations) if n > 1 else 0.0,
                "min": min(durations),
                "max": max(durations),
                "total": sum(durations),
            }
        return out

    def to_dict(self, *, include_summary: bool = True) -> dict[str, Any]:
        """Serialize records (and optionally aggregate summary) for JSON output.

        Parameters
        ----------
        include_summary
            When ``True`` (default), the result includes both ``records`` and
            ``summary`` keys. When ``False``, only ``records`` is included
            — appropriate for non-aggregating levels (per-worker, per-gen)
            where each component appears at most once and the summary is
            redundant. Top-level aggregates (``timing_summary``,
            ``bootstrap_breakdown``) keep ``include_summary=True``.
        """
        out: dict[str, Any] = {"records": [r.to_dict() for r in self._records]}
        if include_summary:
            out["summary"] = self.aggregate()
        return out

    def merge(self, other: "TimingRecorder") -> None:
        """Append all records from ``other`` to this recorder."""
        self._records.extend(other._records)
