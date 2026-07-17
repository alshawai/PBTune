"""
Worker Metric Collection
========================

The "measure what happened" cluster for a single worker evaluation. These are
the read-side helpers the orchestrator uses to turn raw PostgreSQL state and the
environment into ``PerformanceMetrics`` fields:

- :func:`collect_system_metrics` — memory + cache-hit ratio via the environment
- :func:`fetch_pg_stat_database_snapshot` — a point-in-time read of
  ``pg_stat_database`` counters (block/tuple I/O)
- :func:`compute_io_metrics` — derive I/O and row-count metrics from the delta
  between two snapshots

They take explicit handles (``env``, bound ``connect``/``disconnect`` callables)
rather than an orchestrator instance, so they carry no orchestration state.
``WorkloadOrchestrator`` keeps thin delegating methods over them.
"""

from __future__ import annotations

import logging
import time
from typing import Any, Callable, Dict, Optional

from src.config.database import DatabaseConfig
from src.utils.environments.base import DatabaseEnvironment
from src.utils.metrics import PerformanceMetrics
from src.utils.logger import get_logger

LOGGER = get_logger("WorkloadOrchestrator")


def collect_system_metrics(
    env: DatabaseEnvironment,
    worker_id: Optional[int] = None,
) -> Dict[str, float]:
    """Collect system-level metrics by delegating to the environment.

    Memory utilization and cache hit ratio are collected via the
    DatabaseEnvironment abstraction, eliminating the need for
    process-scanning via psutil.

    Parameters
    ----------
    env : DatabaseEnvironment
        Environment backing the worker instance.
    worker_id : Optional[int]
        Worker ID for environment delegation

    Returns
    -------
    Dict[str, float]
        System metrics including memory and cache hit ratio
    """
    metrics: Dict[str, float] = {
        "memory_utilization": 0.0,
        "cache_hit_ratio": 0.0,
    }

    wid = worker_id if worker_id is not None else 0

    # Retry once because worker restarts and transient reconnect windows can
    # briefly make environment-backed metrics unavailable.
    for attempt in range(2):
        try:
            metrics["memory_utilization"] = env.collect_memory_utilization(wid)
            metrics["cache_hit_ratio"] = env.collect_cache_hit_ratio(wid)
            break
        except (
            RuntimeError,
            OSError,
            ValueError,
            TypeError,
            AttributeError,
        ) as exc:
            LOGGER.debug(
                "System metric collection attempt %d failed for worker %d: %s",
                attempt + 1,
                wid,
                exc,
            )
            if attempt == 0:
                time.sleep(0.1)
    return metrics


def fetch_pg_stat_database_snapshot(
    db_config: DatabaseConfig,
    *,
    connect: Callable[..., Any],
    disconnect: Callable[[Any], Any],
    connection: Any | None = None,
    worker_logger: Optional[logging.Logger] = None,
) -> tuple[int, int, int, int, int, int, int] | None:
    """Read pg_stat_database counters with a retry for transient failures."""
    query = """
        SELECT
            blks_read,
            blks_hit,
            tup_returned,
            tup_fetched,
            tup_inserted,
            tup_updated,
            tup_deleted
        FROM pg_stat_database
        WHERE datname = current_database()
    """

    logger = worker_logger or LOGGER
    last_error: Exception | None = None

    for attempt in range(2):
        active_connection = connection
        owns_connection = False
        try:
            if active_connection is None or getattr(active_connection, "closed", True):
                active_connection = connect(
                    db_config, max_retries=2, retry_delay=1.0
                )
                owns_connection = True

            if not active_connection:
                continue

            cursor = active_connection.cursor()
            cursor.execute(query)
            row = cursor.fetchone()
            cursor.close()

            if owns_connection:
                disconnect(active_connection)

            if row is None:
                return None

            return (
                int(row[0] or 0),
                int(row[1] or 0),
                int(row[2] or 0),
                int(row[3] or 0),
                int(row[4] or 0),
                int(row[5] or 0),
                int(row[6] or 0),
            )
        except Exception as exc:
            last_error = exc
            logger.debug(
                " ➤ Failed to capture pg_stat_database snapshot (attempt %d): %s",
                attempt + 1,
                exc,
            )
            if owns_connection and active_connection is not None:
                disconnect(active_connection)
            if attempt == 0:
                time.sleep(0.2)

    if last_error is not None:
        logger.debug(" ➤ Last pg_stat_database snapshot error: %s", last_error)
    return None


def compute_io_metrics(
    metrics: PerformanceMetrics,
    *,
    stats_before: tuple[int, int, int, int, int, int, int],
    stats_after: tuple[int, int, int, int, int, int, int],
    worker_logger: logging.Logger,
) -> None:
    """Populate I/O and row-count metrics from pg_stat_database deltas (B11).

    Derives read MB from block deltas, estimates write MB from row
    modification counts (no filesystem-level write counters available),
    and computes the buffer miss rate. All failures are swallowed at debug
    level so a stats hiccup never fails an otherwise-healthy evaluation.
    """
    try:
        (
            blks_read_after,
            blks_hit_after,
            tup_returned_after,
            tup_fetched_after,
            tup_inserted_after,
            tup_updated_after,
            tup_deleted_after,
        ) = stats_after

        (
            blks_read_before,
            blks_hit_before,
            tup_returned_before,
            tup_fetched_before,
            tup_inserted_before,
            tup_updated_before,
            tup_deleted_before,
        ) = stats_before

        blocks_read_delta = blks_read_after - blks_read_before
        blocks_hit_delta = blks_hit_after - blks_hit_before
        tup_returned_delta = tup_returned_after - tup_returned_before
        tup_fetched_delta = tup_fetched_after - tup_fetched_before

        # Convert to MB (8KB blocks)
        io_read_mb = (blocks_read_delta * 8) / 1024.0
        metrics.io_read_mb = max(0, io_read_mb)

        # Estimate write MB from row modification counts when
        # filesystem-level write counters aren't directly
        # available. This is a conservative heuristic: assume
        # an average row size (bytes) and multiply by the number
        # of inserted/updated/deleted rows observed.
        try:
            tup_inserted_delta = tup_inserted_after - tup_inserted_before
            tup_updated_delta = tup_updated_after - tup_updated_before
            tup_deleted_delta = tup_deleted_after - tup_deleted_before
        except Exception:
            tup_inserted_delta = tup_updated_delta = tup_deleted_delta = 0

        total_rows_written = max(
            0,
            tup_inserted_delta + tup_updated_delta + tup_deleted_delta,
        )
        # Conservative average row size in bytes; adjustable later
        avg_row_bytes = 128
        io_write_bytes = total_rows_written * avg_row_bytes
        metrics.io_write_mb = max(0.0, io_write_bytes / (1024.0 * 1024.0))

        # Diagnostic: if we observed no written rows but the workload
        # appears to have read/fetched rows or the DB changed, emit a
        # debug message to help trace why writes are zero.
        if total_rows_written == 0 and (
            tup_fetched_delta > 0 or blocks_read_delta > 0
        ):
            worker_logger.debug(
                "IO write estimation produced 0 bytes (inserted=%s updated=%s deleted=%s). "
                "Blocks read delta=%s, tup_returned_delta=%s, tup_fetched_delta=%s",
                tup_inserted_delta,
                tup_updated_delta,
                tup_deleted_delta,
                blocks_read_delta,
                tup_returned_delta,
                tup_fetched_delta,
            )

        # Populate DB counters
        metrics.rows_returned = max(0, tup_returned_delta)
        metrics.rows_examined = max(0, tup_fetched_delta)

        # Compute buffer miss rate
        total_blocks = blocks_read_delta + blocks_hit_delta
        if total_blocks > 0:
            metrics.buffer_miss_rate = max(
                0.0, min(1.0, blocks_read_delta / total_blocks)
            )
        else:
            metrics.buffer_miss_rate = 0.0

    except Exception as e:
        worker_logger.debug("Failed to calculate IO stats: %s", e)
