"""
Tolerant accessors for the unified session-JSON schema.

The tuning-session schema was standardized so PBT, LHS, and BO all emit the
same fields (see :mod:`src.tuners.utils.session_assembly`):

* top-level ``history`` (was ``generation_history`` / ``evaluation_history``)
* per-record ``iteration``/``generation``/``batch`` round index (strategy's own
  vocabulary)
* per-record ``<strategy>_elapsed_seconds`` (PBT ``generation_elapsed_seconds``,
  BO ``iteration_elapsed_seconds``, LHS ``batch_elapsed_seconds``)
* per-record ``<strategy>_overhead_seconds`` (PBT ``pbt_overhead_seconds``,
  BO ``bo_overhead_seconds``, LHS ``lhs_overhead_seconds``; legacy
  ``strategy_overhead_seconds``)
* per-record strategy-specific fields emitted flat (e.g. ``num_exploited``,
  ``phase``); legacy traces nested them under ``strategy_params``

Every loader reads through these helpers so both the new schema and older
on-disk traces load identically — new key first, legacy key as fallback.
"""

from __future__ import annotations

from typing import Any, Dict, List, NamedTuple, Optional

from src.tuners.utils.types import TuningStrategy


class HistoryKeys(NamedTuple):
    """Persisted per-record ``history`` keys one strategy writes."""

    round_index: str
    elapsed: str
    overhead: str


# Single source of truth for the per-record keys each strategy persists. Each
# strategy speaks its own vocabulary; the keys are part of the on-disk schema
# and are independent of the strategy's display ``round_label``. The reader key
# tuples below are derived from this registry, so a writer key is always one
# the reader recognises.
HISTORY_KEYS: Dict[TuningStrategy, HistoryKeys] = {
    TuningStrategy.PBT: HistoryKeys(
        round_index="generation",
        elapsed="generation_elapsed_seconds",
        overhead="pbt_overhead_seconds",
    ),
    TuningStrategy.BO: HistoryKeys(
        round_index="iteration",
        elapsed="iteration_elapsed_seconds",
        overhead="bo_overhead_seconds",
    ),
    TuningStrategy.LHS: HistoryKeys(
        round_index="batch",
        elapsed="batch_elapsed_seconds",
        overhead="lhs_overhead_seconds",
    ),
}


def history_keys_for(strategy: TuningStrategy) -> HistoryKeys:
    """Return the persisted ``history`` keys for ``strategy``.

    Raises ``KeyError`` for a strategy with no registered keys, so a new
    strategy fails loudly instead of writing keys the reader cannot find.
    """
    try:
        return HISTORY_KEYS[strategy]
    except KeyError as exc:
        raise KeyError(
            f"No persisted history keys registered for strategy {strategy!r}; "
            "add an entry to session_schema.HISTORY_KEYS."
        ) from exc


# Ordered (new, *legacy) top-level history-block aliases. First present wins.
_HISTORY_BLOCK_KEYS = ("history", "generation_history", "evaluation_history")
# Per-record key aliases: every registered strategy's key, then legacy generic
# names. A record carries exactly one key per group (each writer emits only its
# own strategy's key), so the order among registry entries never matters.
_ITERATION_KEYS = tuple(keys.round_index for keys in HISTORY_KEYS.values()) + ("round",)
_ELAPSED_KEYS = tuple(keys.elapsed for keys in HISTORY_KEYS.values())
# ``strategy_overhead_seconds`` is the prior generic overhead name.
_OVERHEAD_KEYS = tuple(keys.overhead for keys in HISTORY_KEYS.values()) + (
    "strategy_overhead_seconds",
)


def get_history(data: Dict[str, Any]) -> List[Dict[str, Any]]:
    """Return the per-round history list, tolerating legacy top-level keys."""
    for key in _HISTORY_BLOCK_KEYS:
        value = data.get(key)
        if value is not None:
            return value
    return []


def has_history(data: Dict[str, Any]) -> bool:
    """True if any recognized history key is present (new or legacy)."""
    return any(key in data for key in _HISTORY_BLOCK_KEYS)


def _first(record: Dict[str, Any], keys: tuple, default: Any) -> Any:
    for key in keys:
        if key in record and record[key] is not None:
            return record[key]
    return default


def get_iteration_index(record: Dict[str, Any], default: Any = None) -> Any:
    """Per-record round index under any strategy's key, or legacy ``round``."""
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
