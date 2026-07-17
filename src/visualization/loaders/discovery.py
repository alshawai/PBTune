"""Session-trace file discovery for the visualization loaders.

The unified tuners write a strategy-agnostic ``trace_*.json`` — the strategy is
encoded in the ``sessions/<workload>/<strategy>/`` path, not the filename — so
arms are separated by *directory*, never by stem. The legacy per-strategy stems
(``pbt_results_*.json`` / ``lhs_results_*.json`` / ``bo_results_*.json``) are
still matched so pre-rename traces on disk keep loading. Both discovery helpers
include ``trace_*.json``; the caller scopes the arm by passing the ``/pbt/`` or
``/bo/`` ``traces`` directory.
"""

from __future__ import annotations

from pathlib import Path
from typing import List, Tuple

# PBT/LHS-shaped session traces (``generation_history`` payload).
SESSION_TRACE_GLOBS: Tuple[str, ...] = (
    "trace_*.json",
    "pbt_results_*.json",
    "lhs_results_*.json",
)

# BO baseline traces (per-iteration ``optimization_history`` payload).
BO_TRACE_GLOBS: Tuple[str, ...] = (
    "trace_*.json",
    "bo_results_*.json",
)


def _discover(directory: Path, globs: Tuple[str, ...]) -> List[Path]:
    found: List[Path] = []
    for pattern in globs:
        try:
            found.extend(directory.glob(pattern))
        except OSError:
            # Directory is missing or inaccessible; treat as no traces.
            # (Python 3.11 raises FileNotFoundError here, 3.13 returns empty.)
            continue
    # De-dup (``trace_*`` never overlaps the legacy stems, but be defensive)
    # and sort by name for deterministic, timestamp-ordered discovery.
    return sorted(set(found), key=lambda p: p.name)


def discover_session_traces(directory: Path) -> List[Path]:
    """Return name-sorted PBT/LHS session traces in ``directory``."""
    return _discover(directory, SESSION_TRACE_GLOBS)


def discover_bo_traces(directory: Path) -> List[Path]:
    """Return name-sorted BO baseline traces in ``directory``."""
    return _discover(directory, BO_TRACE_GLOBS)
