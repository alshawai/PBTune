"""
Pre/Post-Workload Maintenance
=============================

Housekeeping around a worker's measurement window:

- :func:`ensure_benchmark_ready` — validate benchmark state before execution and
  repair it (re-``prepare()``) if validation keeps failing
- :func:`vacuum_after_dml` — bounded post-workload ``VACUUM ANALYZE`` on the user
  tables a DML-heavy workload actually modified

Both are free functions taking explicit handles (the workload executor, or the
relevant ``config`` values) rather than an orchestrator instance.
``WorkloadOrchestrator`` keeps thin delegating methods over them.
"""

from __future__ import annotations

import logging
import time
from typing import Optional

from psycopg2 import sql

from src.config.database import DatabaseConfig
from src.database.connection import get_connection
from src.benchmarks.executor import BenchmarkExecutor
from src.utils.metrics import WorkloadType
from src.utils.logger import get_logger, get_color_context

LOGGER = get_logger("WorkloadOrchestrator")
COLORS = get_color_context()


def vacuum_after_dml(
    workload_type: WorkloadType,
    vacuum_analyze_timeout_seconds: float,
    db_config: DatabaseConfig,
    worker_logger: Optional[logging.Logger] = None,
    next_eval_will_restore: bool = False,
) -> None:
    """
    Run bounded post-workload maintenance after DML-heavy workloads.

    Full-database VACUUM ANALYZE is too expensive for short sysbench-style
    generations and frequently times out while scanning toast/system tables.
    Instead, analyze only user tables that were actually modified.

    When ``next_eval_will_restore`` is True, the caller has guaranteed the
    next evaluation begins with a baseline snapshot restore (PGDATA copied
    over from the post-prepare baseline, which already contains a clean
    VACUUM ANALYZE). Any per-eval VACUUM we run now is:
      1. Too late to influence the just-collected metrics — those are
         captured at B12 before this method is reached.
      2. About to be discarded by the next restore.
    Skipping eliminates 20–60s of dead wall-clock per generation on
    sysbench RW/WO with high table/row counts.
    """
    # Skip for read-only workloads (OLAP, TPC-H)
    if workload_type.value in ("olap", "tpch"):
        return
    worker_logger = worker_logger or LOGGER

    if next_eval_will_restore:
        worker_logger.debug(
            " ➤ Skipping post-workload VACUUM ANALYZE %s(next eval restores"
            " baseline snapshot)%s",
            COLORS.italic,
            COLORS.reset,
        )
        return

    timeout_seconds = max(0.0, float(vacuum_analyze_timeout_seconds))
    if timeout_seconds <= 0:
        worker_logger.debug(
            " ➤ Skipping post-workload maintenance %s(timeout disabled)%s",
            COLORS.italic,
            COLORS.reset,
        )
        return

    try:
        conn = get_connection(config=db_config)
        conn.autocommit = True  # VACUUM cannot run inside a transaction
        cursor = conn.cursor()

        statement_timeout_ms = int(timeout_seconds * 1000)
        lock_timeout_ms = max(1000, statement_timeout_ms // 4)
        cursor.execute("SET statement_timeout = %s", (statement_timeout_ms,))
        cursor.execute("SET lock_timeout = %s", (lock_timeout_ms,))

        cursor.execute(
            """
            SELECT schemaname, relname
            FROM pg_stat_user_tables
            WHERE n_mod_since_analyze > 0 OR n_dead_tup > 0
            ORDER BY n_mod_since_analyze DESC, n_dead_tup DESC
            """
        )
        tables = cursor.fetchall() or []

        if not tables:
            worker_logger.debug(
                " ➤ Skipping post-workload maintenance %s(no modified user tables)%s",
                COLORS.italic,
                COLORS.reset,
            )
            cursor.close()
            conn.close()
            return

        worker_logger.debug(
            "  Running post-workload VACUUM ANALYZE on %d modified tables...",
            len(tables),
        )

        start = time.time()
        for schema_name, table_name in tables:
            try:
                cursor.execute(
                    sql.SQL("VACUUM ANALYZE {}.{}").format(
                        sql.Identifier(schema_name),
                        sql.Identifier(table_name),
                    )
                )
            except Exception as table_error:
                worker_logger.warning(
                    " ➤ Post-workload maintenance failed for %s.%s: %s",
                    schema_name,
                    table_name,
                    table_error,
                )

        elapsed = time.time() - start

        worker_logger.debug(
            " ➤ Post-workload VACUUM ANALYZE completed in %.2fs", elapsed
        )
        cursor.close()
        conn.close()

    except Exception as e:
        worker_logger.warning(" ➤ Post-workload VACUUM ANALYZE failed: %s", e)


def ensure_benchmark_ready(
    workload_executor: BenchmarkExecutor,
    db_config: DatabaseConfig,
    worker_logger: Optional[logging.Logger] = None,
) -> None:
    """Validate benchmark state before execution and repair it if needed.

    Retries validation up to 3 times with a short delay before falling
    back to ``prepare()``, which recreates the full benchmark schema
    (~4.5 GB for large sysbench configs) and generates significant WAL.
    Transient connection errors under co-tenant load would otherwise
    trigger needless ``prepare()`` calls on every iteration.
    """
    if not workload_executor.manages_own_connection:
        return

    worker_logger = worker_logger or LOGGER

    max_retries = 3
    for attempt in range(1, max_retries + 1):
        try:
            if workload_executor.validate(db_config):
                worker_logger.debug(
                    " ➤ Benchmark validation successful, ready to execute"
                )
                return
        except Exception as e:
            worker_logger.warning(
                "Benchmark validation attempt %d/%d raised %s",
                attempt,
                max_retries,
                e,
            )
        if attempt < max_retries:
            worker_logger.debug(
                " ➤ Validation failed (attempt %d/%d); retrying in 2s...",
                attempt,
                max_retries,
            )
            time.sleep(2)

    worker_logger.warning(
        "Benchmark validation failed after %d attempts; running prepare()",
        max_retries,
    )
    workload_executor.prepare(db_config)

    if not workload_executor.validate(db_config):
        raise RuntimeError("Benchmark validation still failing after prepare()")

    worker_logger.debug(" ➤ Benchmark state re-prepared successfully")
