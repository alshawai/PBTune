"""
Restart Policy
===============

Pure-function restart decision logic based on tuning mode, generation,
and knob application outcome.  Extracted from the evaluator to enable
isolated testing and clear separation between *policy* and *mechanism*.
"""

from __future__ import annotations

from enum import Enum


class TuningMode(str, Enum):
    """Tuning mode controlling restart behavior and knob scope.

    ONLINE
        Runtime knobs only. No restarts during normal flow.
        Equivalent to OtterTune's "dynamic-only" mode.

    OFFLINE
        All knobs including postmaster. Restart every generation when
        restart-required knobs are present. Slower but maximally optimized.

    ADAPTIVE
        All knobs with batched restarts every N generations.
        WARNING: May produce phantom configs where restart-required knob
        values don't reflect what was actually running during measurement.
        Preserved for backward compatibility and research comparison.
    """

    ONLINE = "online"
    OFFLINE = "offline"
    ADAPTIVE = "adaptive"


def should_restart(
    mode: TuningMode,
    restart_required: bool,
    generation: int | None,
    adaptive_restart_interval: int = 10,
    force: bool = False,
) -> bool:
    """Decide whether to restart the database after configuration application.

    Parameters
    ----------
    mode : TuningMode
        Active tuning mode.
    restart_required : bool
        Whether the last `apply()` call flagged restart-requiring knobs.
    generation : int | None
        Current generation number (used for ADAPTIVE interval logic).
    adaptive_restart_interval : int
        Restart every N generations in ADAPTIVE mode (default 10).
    force : bool
        Force restart regardless of mode/interval (e.g. post-recovery).

    Returns
    -------
    bool
        True if the database should be restarted.
    """
    if force:
        return True

    if not restart_required:
        return False

    if mode == TuningMode.ONLINE:
        return False

    if mode == TuningMode.OFFLINE:
        return True

    if mode == TuningMode.ADAPTIVE:
        return generation is not None and generation % adaptive_restart_interval == 0

    return False
