"""
Tolerant accessors for the unified session-JSON schema.

The tuning-session schema was standardized so PBT, LHS, and BO all emit the
same fields (see :mod:`src.tuners.utils.session_assembly`):

* top-level ``history`` (was ``generation_history`` / ``evaluation_history``)
* per-record ``iteration``/``generation``/``batch`` round index (strategy's own
  vocabulary)
* per-record ``<strategy>_elapsed_seconds`` (PBT ``generation_elapsed_seconds``,
  BO ``iteration_elapsed_seconds``, LHS ``batch_elapsed_seconds``)
* per-record ``<strategy>_overhead_seconds`` (was BO's ``bo_overhead_seconds``)
* per-record strategy-specific fields emitted flat (e.g. ``num_exploited``,
  ``phase``); legacy traces nested them under ``strategy_params``

Every loader reads through these helpers so both the new schema and older
on-disk traces load identically — new key first, legacy key as fallback.
"""

from __future__ import annotations

from typing import Any, Dict, List, Optional

# Ordered (new, *legacy) key aliases. First present key wins.
_HISTORY_KEYS = ("history", "generation_history", "evaluation_history")
# Per-record round-index key. Each strategy speaks its own vocabulary
# (PBT ``generation``, BO ``iteration``, LHS ``batch``); ``generation`` is also
# the legacy key. Any present key wins.
_ITERATION_KEYS = ("iteration", "generation", "batch", "round")
_ELAPSED_KEYS = (
    "generation_elapsed_seconds",
    "iteration_elapsed_seconds",
    "batch_elapsed_seconds",
)
# Per-record strategy overhead. The flat key now speaks each strategy's name
# (``pbt_overhead_seconds`` / ``bo_overhead_seconds`` / ``lhs_overhead_seconds``);
# ``strategy_overhead_seconds`` is the prior generic name kept for legacy traces.
_OVERHEAD_KEYS = (
    "pbt_overhead_seconds",
    "bo_overhead_seconds",
    "lhs_overhead_seconds",
    "strategy_overhead_seconds",
)


def get_history(data: Dict[str, Any]) -> List[Dict[str, Any]]:
    """Return the per-round history list, tolerating legacy top-level keys."""
    for key in _HISTORY_KEYS:
        value = data.get(key)
        if value is not None:
            return value
    return []


def has_history(data: Dict[str, Any]) -> bool:
    """True if any recognized history key is present (new or legacy)."""
    return any(key in data for key in _HISTORY_KEYS)


def _first(record: Dict[str, Any], keys: tuple, default: Any) -> Any:
    for key in keys:
        if key in record and record[key] is not None:
            return record[key]
    return default


def get_iteration_index(record: Dict[str, Any], default: Any = None) -> Any:
    """Per-record iteration index (new ``iteration``, legacy ``generation``)."""
    return _first(record, _ITERATION_KEYS, default)


def get_iteration_elapsed(record: Dict[str, Any], default: float = 0.0) -> float:
    """Per-record elapsed wall time for the round (new/legacy elapsed key)."""
    return float(_first(record, _ELAPSED_KEYS, default))


def get_strategy_overhead(record: Dict[str, Any], default: float = 0.0) -> float:
    """Per-record strategy/optimizer overhead seconds (new/legacy key)."""
    return float(_first(record, _OVERHEAD_KEYS, default))


def get_strategy_field(
    record: Dict[str, Any], name: str, default: Optional[Any] = None
) -> Any:
    """Read a strategy-specific per-record field.

    Prefers the nested ``strategy_params`` block; falls back to a flat
    top-level key for legacy traces (e.g. ``num_exploited``, ``phase``).
    """
    params = record.get("strategy_params")
    if isinstance(params, dict) and name in params and params[name] is not None:
        return params[name]
    return record.get(name, default)
