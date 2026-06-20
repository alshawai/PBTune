"""
Runtime knob-compatibility pruning shared across tuning strategies.

Both PBT and BO query a live instance's ``pg_settings`` to discover which
configured knobs the running PostgreSQL build actually supports, then drop any
that are absent to avoid apply/verify failures. This module lifts that logic
into reusable, side-effect-free helpers (copy-not-refactor): the caller owns
the ``KnobSpace`` mutation so this stays testable without a database.
"""

from __future__ import annotations

from typing import Any, Iterable, Set, Tuple

import psycopg2

from src.database.connection import get_connection
from src.utils.logger import get_logger

LOGGER = get_logger("TunerKnobFilter")


def query_runtime_supported_knobs(
    db_config: Any,
    *,
    fallback_knobs: Iterable[str],
    connect_timeout: int = 5,
) -> Tuple[Set[str], str]:
    """
    Return ``(supported_knob_names, server_version)`` from a live instance.

    On any connection/query failure this degrades gracefully to the supplied
    ``fallback_knobs`` and a ``"unknown"`` version, matching PBT's tolerant
    behavior (BO re-raises; new strategies prefer graceful degradation).
    """
    conn = None
    cursor = None
    try:
        conn = get_connection(config=db_config, connect_timeout=connect_timeout)
        cursor = conn.cursor()
        cursor.execute("SELECT current_setting('server_version')")
        version_row = cursor.fetchone()
        server_version = str(version_row[0]) if version_row else "unknown"

        cursor.execute("SELECT name FROM pg_settings")
        supported = {str(row[0]) for row in cursor.fetchall()}
        return supported, server_version
    except (psycopg2.Error, RuntimeError, OSError, ValueError) as exc:
        LOGGER.warning(
            "Failed to inspect runtime pg_settings for knob compatibility: %s",
            exc,
        )
        return set(fallback_knobs), "unknown"
    finally:
        if cursor is not None:
            cursor.close()
        if conn is not None:
            conn.close()


def compute_unsupported_knobs(
    configured_knobs: Iterable[str],
    supported_knobs: Set[str],
) -> list[str]:
    """Return the sorted set of configured knobs absent from the runtime."""
    return sorted(set(configured_knobs) - set(supported_knobs))


def log_pruning_summary(
    unsupported_knobs: list[str],
    server_version: str,
    *,
    remaining: int,
) -> None:
    """Emit a human-readable summary of the pruning decision."""
    if not unsupported_knobs:
        LOGGER.debug(
            "Runtime knob compatibility check passed against PostgreSQL %s",
            server_version,
        )
        return

    preview = unsupported_knobs[:10]
    suffix = " ..." if len(unsupported_knobs) > len(preview) else ""
    LOGGER.warning(
        "Pruned %d unsupported knobs for PostgreSQL %s: %s%s (continuing with %d)",
        len(unsupported_knobs),
        server_version,
        ", ".join(preview),
        suffix,
        remaining,
    )
