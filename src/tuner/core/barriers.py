"""
Generation Barriers for Lockstep Worker Synchronization
========================================================

Provides ``threading.Barrier``-based synchronization so that all workers in a
PBT generation complete each sub-step before any worker advances to the next.

This guarantees **experimental fairness**: every worker's measurement window
experiences identical contention from other workers, regardless of how long
individual setup steps (e.g. restart, reconnect) take.

Barrier Points (B1–B17)
------------------------
Each barrier corresponds to a discrete sub-step inside
``WorkloadOrchestrator.evaluate_worker()``:

    B1  connected             — TCP connection established
    B2  config_applied        — ALTER SYSTEM + pg_reload_conf completed
    B3  restarted             — PostgreSQL restart finished (or skipped)
    B4  reconnected           — Post-restart reconnection established
    B5  config_verified       — SHOW confirms knobs took effect
    B6  pre_stats_captured    — pg_stat_database baseline snapshot taken
    B7  benchmark_ready       — Schema/state validated for benchmark
    B8  warmup_done           — Warmup queries completed
    B9  measurement_done      — Timed measurement window completed
    B10 post_stats_captured   — pg_stat_database final snapshot taken
    B11 io_computed           — I/O delta and buffer stats calculated
    B12 system_metrics_collected — Memory and cache metrics collected
    B13 memory_pressure_computed — Derived memory-pressure metric computed
    B14 reliability_gated     — Reliability classification applied
    B15 vacuum_done           — Post-DML VACUUM ANALYZE completed
    B16 score_computed        — Composite performance score computed
    B17 disconnected          — Connection closed

Usage
-----
>>> barriers = GenerationBarrier(num_workers=4, timeout=120.0)
>>> # Inside each worker thread:
>>> barriers.wait("connected")  # blocks until all 4 threads arrive
>>> barriers.wait("config_applied")

Graceful Degradation
--------------------
If **any** worker thread crashes, its barrier slots are never filled, which
would deadlock the remaining threads. Two safeguards prevent this:

1. ``timeout`` — every ``barrier.wait()`` call has a deadline. On timeout,
   ``BrokenBarrierError`` is raised, the internal ``_broken`` flag is set,
   and **all subsequent** ``wait()`` calls become instant no-ops.

2. ``drain_remaining(start_from)`` — when a worker catches an exception, it
   calls this method to release all barriers it hasn't reached yet, so the
   other threads can proceed.
"""

from __future__ import annotations

import logging
import threading
import time
from typing import Dict, List, Optional

from src.utils.logger import get_logger

LOGGER = get_logger("GenerationBarrier")

# Canonical ordered list of all barrier names.
BARRIER_NAMES: List[str] = [
    "connected",               # B1
    "config_applied",          # B2
    "restarted",               # B3
    "reconnected",             # B4
    "config_verified",         # B5
    "pre_stats_captured",      # B6
    "benchmark_ready",         # B7
    "warmup_done",             # B8
    "measurement_done",        # B9
    "post_stats_captured",     # B10
    "io_computed",             # B11
    "system_metrics_collected",  # B12
    "memory_pressure_computed",  # B13
    "reliability_gated",       # B14
    "vacuum_done",             # B15
    "score_computed",          # B16
    "disconnected",            # B17
]


class GenerationBarrier:
    """
    Thread-safe barrier collection for lockstep worker synchronization.

    Parameters
    ----------
    num_workers : int
        Number of worker threads that must arrive at each barrier.
    timeout : float
        Maximum seconds to wait at any single barrier before raising
        ``BrokenBarrierError``.  Default 120 s accommodates Docker
        restart worst-case (~70 s) with headroom.
    enabled : bool
        When ``False``, all ``wait()`` calls are instant no-ops.
        Used for sequential evaluation or ``--no-sync`` mode.
    """

    def __init__(
        self,
        num_workers: int,
        timeout: float = 120.0,
        enabled: bool = True,
    ) -> None:
        self._num_workers = num_workers
        self._timeout = timeout
        self._enabled = enabled
        self._broken = False

        # Build one threading.Barrier per sub-step.
        self._barriers: Dict[str, threading.Barrier] = {}
        if self._enabled:
            for name in BARRIER_NAMES:
                self._barriers[name] = threading.Barrier(
                    parties=num_workers, timeout=timeout
                )

        # Index lookup for drain_remaining().
        self._name_to_index: Dict[str, int] = {
            name: idx for idx, name in enumerate(BARRIER_NAMES)
        }

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    @property
    def enabled(self) -> bool:
        """Whether barriers are active."""
        return self._enabled and not self._broken

    @property
    def broken(self) -> bool:
        """Whether the barrier set has been broken (timeout or abort)."""
        return self._broken

    def wait(
        self,
        name: str,
        *,
        worker_id: Optional[int] = None,
    ) -> None:
        """
        Block until all workers reach barrier *name*.

        Parameters
        ----------
        name : str
            One of the names in ``BARRIER_NAMES``.
        worker_id : int | None
            Optional worker ID for log messages.

        Raises
        ------
        ValueError
            If *name* is not a recognized barrier.
        """
        if not self._enabled or self._broken:
            return

        if name not in self._barriers:
            raise ValueError(
                f"Unknown barrier name '{name}'. "
                f"Valid names: {BARRIER_NAMES}"
            )

        barrier = self._barriers[name]
        tag = f"Worker-{worker_id}" if worker_id is not None else "worker"

        LOGGER.debug("[%s] waiting at barrier '%s'", tag, name)
        t0 = time.monotonic()

        try:
            barrier.wait()
        except threading.BrokenBarrierError:
            self._broken = True
            LOGGER.warning(
                "[%s] barrier '%s' broken — disabling remaining barriers "
                "for this generation",
                tag,
                name,
            )
            return

        elapsed_ms = (time.monotonic() - t0) * 1000
        LOGGER.debug(
            "[%s] passed barrier '%s' (waited %.1f ms)", tag, name, elapsed_ms
        )

    def drain_remaining(
        self,
        start_from: str,
        *,
        worker_id: Optional[int] = None,
    ) -> None:
        """
        Release all barriers from *start_from* onward (inclusive).

        Call this when a worker fails mid-evaluation so that other threads
        waiting at later barriers are not deadlocked.  Each barrier is
        ``wait()``-ed to contribute this thread's "arrival".

        If the barrier set is already broken or disabled, this is a no-op.

        Parameters
        ----------
        start_from : str
            First barrier name to drain (inclusive).
        worker_id : int | None
            Optional worker ID for log messages.
        """
        if not self._enabled or self._broken:
            return

        start_idx = self._name_to_index.get(start_from)
        if start_idx is None:
            LOGGER.warning(
                "drain_remaining called with unknown barrier '%s'", start_from
            )
            return

        tag = f"Worker-{worker_id}" if worker_id is not None else "worker"
        remaining = BARRIER_NAMES[start_idx:]
        LOGGER.debug(
            "[%s] draining %d remaining barriers starting from '%s'",
            tag,
            len(remaining),
            start_from,
        )

        for name in remaining:
            if self._broken:
                return
            try:
                self._barriers[name].wait()
            except threading.BrokenBarrierError:
                self._broken = True
                LOGGER.debug(
                    "[%s] barrier '%s' broke during drain", tag, name
                )
                return

    def abort(self) -> None:
        """
        Immediately break all barriers, unblocking any waiting threads.

        Call this when a worker is known to be dead and will never arrive
        at its barriers. Unlike ``drain_remaining`` (which acts as a
        participant), ``abort`` forces a ``BrokenBarrierError`` on all
        waiters instantly without requiring the dead thread to cooperate.

        After calling ``abort()``, the barrier set is marked broken and
        all subsequent ``wait()`` calls are no-ops.
        """
        if not self._enabled or self._broken:
            return

        self._broken = True
        LOGGER.warning("Barrier set aborted — all barriers broken instantly")
        for barrier in self._barriers.values():
            try:
                barrier.abort()
            except threading.BrokenBarrierError:
                pass

    def reset(self) -> None:
        """
        Reset all barriers for reuse in the next generation.

        Must be called from the **main thread** after all worker threads
        have joined (i.e. after the ``ThreadPoolExecutor`` context exits).
        """
        self._broken = False
        for barrier in self._barriers.values():
            # Barrier.reset() aborts any still-waiting threads (safety net).
            try:
                barrier.reset()
            except threading.BrokenBarrierError:
                pass
            # Recreate a fresh barrier to avoid leftover state.
        if self._enabled:
            for name in BARRIER_NAMES:
                self._barriers[name] = threading.Barrier(
                    parties=self._num_workers, timeout=self._timeout
                )

    def next_barrier_name(self, current: str) -> Optional[str]:
        """Return the barrier name after *current*, or ``None`` if last."""
        idx = self._name_to_index.get(current)
        if idx is None or idx + 1 >= len(BARRIER_NAMES):
            return None
        return BARRIER_NAMES[idx + 1]

    def __repr__(self) -> str:
        status = "enabled" if self.enabled else ("broken" if self._broken else "disabled")
        return (
            f"GenerationBarrier(workers={self._num_workers}, "
            f"timeout={self._timeout}s, status={status})"
        )
