import time
from typing import Optional, List
from pathlib import Path
import io
import logging

import numpy as np

from src.config.database import DatabaseConfig
from src.database.connection import get_connection
from src.utils.logger import get_logger, get_color_context, log_section_header
from src.utils.metrics import PerformanceMetrics
from src.benchmarks.executor import BenchmarkExecutor
from src.benchmarks.tpch import QUERIES_DIR, SCHEMA_SQL, INDEXES_SQL
from src.benchmarks.tpch.setup_dbgen import find_or_build_dbgen, generate_data

LOGGER = get_logger("TPCHExecutor")
COLORS = get_color_context()


def _log_pg_error(logger: logging.Logger, query_label: str, exc: Exception) -> None:
    """Extract and log PostgreSQL diagnostic info from a psycopg2 exception."""
    try:
        import psycopg2

        if isinstance(exc, psycopg2.Error):
            diag = exc.diag
            parts = [f"{query_label} PostgreSQL error"]
            if exc.pgcode:
                parts.append(f" [{exc.pgcode}]")
            if diag.message_primary:
                parts.append(f": {diag.message_primary}")
            if diag.message_detail:
                parts.append(f" — {diag.message_detail}")
            if diag.message_hint:
                parts.append(f" (hint: {diag.message_hint})")
            logger.warning("".join(parts))
            return
    except ImportError:
        pass
    logger.warning("%s failed: %s", query_label, exc)


class TPCHExecutor(BenchmarkExecutor):
    """
    Executes the standard TPC-H analytical benchmark.

    Academic Standard Configuration (CDBTune, OtterTune, GPTuner):
    - Schema: 8 tables (nation, region, part, supplier, partsupp,
              customer, orders, lineitem)
    - Queries: 22 standard decision-support queries
    - Scale Factor: SF=1 (~1GB raw data, ~6M lineitem rows)

    Data Generation:
        Uses the `dbgen` C-binary from the `electrum/tpch-dbgen` mirror.
        Auto-compiled from source if not found in PATH.

    Data Loading:
        Uses psycopg2's `copy_expert()` for fast bulk COPY from .tbl files.

    Metrics:
        Unlike OLTP (TPS), OLAP performance is measured by query latency.
        Returns throughput as queries/second and individual query latencies.

    Python overhead is negligible: each TPC-H query takes seconds to minutes;
    the millisecond `psycopg2` cost is mathematically insignificant.
    This is standard practice in CDBTune, OtterTune, and GPTuner.
    """

    # TPC-H table names in load order (respecting FK dependencies)
    TABLES_LOAD_ORDER = [
        "region",
        "nation",
        "part",
        "supplier",
        "partsupp",
        "customer",
        "orders",
        "lineitem",
    ]

    # Tables in reverse order for safe dropping (materialized to avoid iterator exhaustion)
    TABLES_DROP_ORDER = list(reversed(TABLES_LOAD_ORDER))

    def __init__(self, scale_factor: float = 1.0):
        """
        Parameters
        ----------
        scale_factor : float
            TPC-H scale factor. SF=1 produces ~1GB of raw data.
            Common values: 0.01 (tiny), 0.1 (dev), 1.0 (standard), 10.0 (large).
        """
        LOGGER.info(
            "%sUsing TPC-H benchmark for analytical workload evaluation.%s",
            COLORS.bold,
            COLORS.reset,
        )

        self.scale_factor = scale_factor
        self._queries: Optional[List[str]] = None
        self._dbgen_path: Optional[Path] = None
        self._data_dir: Optional[Path] = None

    @property
    def queries(self) -> List[str]:
        """Lazy-load the 22 TPC-H SQL queries from disk."""
        if self._queries is None:
            self._queries = []
            for i in range(1, 23):
                qfile = QUERIES_DIR / f"{i}.sql"
                if not qfile.exists():
                    raise FileNotFoundError(f"TPC-H query file missing: {qfile}")
                self._queries.append(qfile.read_text())
        return self._queries

    def prepare(self, db_config: DatabaseConfig) -> None:
        """
        Generate TPC-H data and load into PostgreSQL.

        Steps:
        1. Compile/locate dbgen binary
        2. Generate .tbl flat files for the configured scale factor
        3. Drop existing schema safely
        4. Create TPC-H schema (8 tables)
        5. Bulk load data using COPY
        6. Build indexes and foreign keys
        7. Run VACUUM ANALYZE
        """
        # Step 1-2: Get dbgen binary and generate data
        LOGGER.info("   Preparing TPCH-H data (SF=%.1f)...", self.scale_factor)
        self._dbgen_path = find_or_build_dbgen()
        self._data_dir = generate_data(self._dbgen_path, self.scale_factor)

        # Step 3-4: Safely Drop & Create schema
        conn = get_connection(db_config)
        conn.autocommit = True
        try:
            cursor = conn.cursor()

            LOGGER.debug("    Dropping old schema if exists...")
            self._drop_existing_public_tables(cursor)

            LOGGER.debug("    Creating schema (8 tables)...")
            schema_sql = SCHEMA_SQL.read_text()
            cursor.execute(schema_sql)

            # Step 5: Bulk load using COPY
            for table_name in self.TABLES_LOAD_ORDER:
                tbl_file = self._data_dir / f"{table_name}.tbl"
                if not tbl_file.exists():
                    raise FileNotFoundError(f"Data file missing: {tbl_file}")

                LOGGER.debug(
                    "    %sLoading %s...%s", COLORS.italic, table_name, COLORS.reset
                )
                with open(tbl_file, "r", encoding="utf-8") as f:
                    conn.cursor().copy_expert(
                        f"COPY {table_name} FROM STDIN WITH (FORMAT CSV, DELIMITER '|')",
                        self._strip_trailing_delimiter(f),
                    )

            # Step 6: Build indexes and FKs
            LOGGER.debug("    Building indexes and foreign keys...")
            indexes_sql = INDEXES_SQL.read_text()
            cursor.execute(indexes_sql)

            # Step 7: VACUUM ANALYZE
            LOGGER.debug("    Running VACUUM ANALYZE...")
            for table_name in self.TABLES_LOAD_ORDER:
                cursor.execute(f"VACUUM ANALYZE {table_name}")

            # Step 8: Stamp metadata for SF validation
            cursor.execute("DROP TABLE IF EXISTS _tpch_metadata")
            cursor.execute(
                "CREATE TABLE _tpch_metadata (key TEXT PRIMARY KEY, value TEXT)"
            )
            cursor.execute(
                "INSERT INTO _tpch_metadata (key, value) VALUES ('scale_factor', %s)",
                (str(self.scale_factor),),
            )

            cursor.close()
            LOGGER.debug(
                "   %s➤ TPC-H preparation complete (SF=%.2f)%s",
                COLORS.italic,
                self.scale_factor,
                COLORS.reset,
            )

        finally:
            conn.close()

    def _drop_existing_public_tables(
        self,
        cursor,
        log_prefix: str = "[TPC-H]",
        logger: logging.Logger | None = None,
    ) -> None:
        """Drop all public tables before loading TPC-H data."""
        super()._drop_existing_public_tables(cursor, log_prefix=log_prefix)

    @staticmethod
    def _strip_trailing_delimiter(file_obj):
        """
        Generator that strips the trailing '|' from each line of a .tbl file.
        """
        buffer = io.StringIO()
        for line in file_obj:
            stripped = line.rstrip("\n").rstrip("|")
            buffer.write(stripped + "\n")
        buffer.seek(0)
        return buffer

    def validate(self, db_config: DatabaseConfig) -> bool:
        """Check if TPC-H schema exists and matches the current scale factor."""
        try:
            conn = get_connection(db_config)
            cursor = conn.cursor()

            # Check metadata table exists and SF matches
            cursor.execute(
                "SELECT EXISTS (SELECT 1 FROM information_schema.tables "
                "WHERE table_name = '_tpch_metadata')"
            )
            if not cursor.fetchone()[0]:  # type: ignore
                LOGGER.debug("TPC-H validation failed: metadata table missing")
                cursor.close()
                conn.close()
                return False

            cursor.execute(
                "SELECT value FROM _tpch_metadata WHERE key = 'scale_factor'"
            )
            row = cursor.fetchone()
            if row is None or float(row[0]) != self.scale_factor:
                loaded_sf = row[0] if row else "unknown"
                LOGGER.debug(
                    " ➤ TPC-H validation failed: loaded SF=%s, expected SF=%.2f",
                    loaded_sf,
                    self.scale_factor,
                )
                cursor.close()
                conn.close()
                return False

            # Verify all 8 core tables exist
            for table_name in self.TABLES_LOAD_ORDER:
                cursor.execute(
                    "SELECT EXISTS (SELECT 1 FROM information_schema.tables "
                    "WHERE table_name = %s)",
                    (table_name,),
                )
                if not cursor.fetchone()[0]:  # type: ignore
                    LOGGER.debug("TPC-H table missing: %s", table_name)
                    cursor.close()
                    conn.close()
                    return False

            return True

        except Exception as e:
            LOGGER.debug("TPC-H validation failed: %s", e)
            return False

    def execute(
        self, db_config: DatabaseConfig, worker_id: Optional[int] = None, **kwargs
    ) -> PerformanceMetrics:
        """Execute TPC-H benchmark and return performance metrics."""
        logger = get_logger("TPCHExecutor", worker_id=worker_id)
        warmup_passes = kwargs.get("warmup_passes", 0)

        logger.info(
            " %sStarting TPC-H execution (SF=%.2f, warmup_passes=%d)...%s",
            COLORS.bold,
            self.scale_factor,
            warmup_passes,
            COLORS.reset,
        )

        logger.debug("  Establishing connection to database for TPC-H execution...")
        max_conn_retries = 3
        conn = None
        for attempt in range(max_conn_retries):
            try:
                conn = get_connection(db_config)
                conn.autocommit = True
                cursor = conn.cursor()
                cursor.execute("SELECT 1")
                cursor.fetchone()
                break
            except Exception as e:
                logger.warning(
                    "  Connection health check failed (attempt %d/%d): %s",
                    attempt + 1,
                    max_conn_retries,
                    e,
                )
                if conn and not conn.closed:
                    conn.close()
                if attempt < max_conn_retries - 1:
                    time.sleep(2.0)
                else:
                    raise RuntimeError(
                        f"Failed to establish healthy connection after {max_conn_retries} attempts"
                    ) from e
        logger.debug("  ➤ Connection established successfully for TPC-H execution")

        # Enforce safety timeout to prevent bad configs from hanging indefinitely.
        base_timeout_ms = 300000  # 5 minutes
        statement_timeout_ms = int(base_timeout_ms * self.scale_factor)

        cursor = conn.cursor()  # type: ignore
        cursor.execute(f"SET statement_timeout = {statement_timeout_ms}")
        cursor.close()
        logger.debug(
            "  ➤ Enforced failsafe statement_timeout=%ds for TPC-H execution (SF=%.2f)",
            statement_timeout_ms // 1000,
            self.scale_factor,
        )

        query_indices = list(range(len(self.queries)))

        if warmup_passes > 0:
            logger.info("  Executing %d cache warming pass(es)", warmup_passes)
            cursor = conn.cursor()  # type: ignore
            for _ in range(warmup_passes):
                for idx in query_indices:
                    try:
                        cursor.execute(self.queries[idx])
                        cursor.fetchall()
                    except Exception as e:
                        _log_pg_error(logger, f"Warmup Q{idx + 1}", e)
                        logger.warning("  Aborting warmup — Assigning fatal penalty.")
                        if not conn.closed:  # type: ignore
                            conn.close()  # type: ignore

                        return PerformanceMetrics(
                            latency_p50=99999.9,
                            latency_p95=99999.9,
                            latency_p99=99999.9,
                            throughput=0.0,
                            memory_utilization=100.0,
                            error_rate=100.0,
                            total_queries=len(self.queries),
                            total_time=0.0,
                            failure_type="warmup_failed",
                        )
            cursor.close()

            if conn.closed:  # type: ignore
                logger.warning(
                    "  Connection lost during warmup — reconnecting for measurement"
                )
                conn = get_connection(db_config)
                conn.autocommit = True
        logger.debug("  ➤ Warmup complete, starting timed measurement of TPC-H queries")

        logger.info(
            " Executing Power Test sequence of %d TPC-H queries...", len(self.queries)
        )

        latencies: List[float] = []
        errors = 0
        total_queries = 0
        cursor = conn.cursor()  # type: ignore
        measurement_start = time.time()

        for idx in query_indices:
            query_start = time.time()
            try:
                cursor.execute(self.queries[idx])
                cursor.fetchall()
                elapsed_ms = (time.time() - query_start) * 1000.0
                latencies.append(elapsed_ms)
                total_queries += 1
            except Exception as e:
                errors += 1
                _log_pg_error(logger, f"Q{idx + 1}", e)
                logger.warning(
                    "  Fast-failing remaining queries to avoid reward hacking."
                )
                break

        cursor.close()

        if not conn.closed:  # type: ignore
            conn.close()  # type: ignore

        total_time = time.time() - measurement_start

        # If any query failed, bomb the metrics to prevent reward hacking
        expected_queries = len(self.queries)
        if errors > 0 or total_queries < expected_queries:
            if errors == 0:
                logger.warning(
                    "  ➤ TPC-H incomplete: only %d/%d queries executed "
                    "(no exception caught). Assigning fatal penalty.",
                    total_queries,
                    expected_queries,
                )
            else:
                logger.warning(
                    "  ➤ TPC-H evaluation failed %d queries. Assigning fatal penalty.",
                    errors,
                )
            return PerformanceMetrics(
                latency_p50=99999.9,
                latency_p95=99999.9,
                latency_p99=99999.9,
                throughput=0.0,
                memory_utilization=100.0,
                error_rate=100.0,
                total_queries=expected_queries,
                total_time=total_time,
                failure_type="query_failed_or_timeout",
            )
        logger.debug(
            "  ➤ TPC-H results: %d queries in %.1fs", total_queries, total_time
        )

        logger.info(" Computing performance metrics from execution results...")
        metrics = PerformanceMetrics()
        metrics.total_queries = total_queries
        metrics.total_time = total_time
        metrics.throughput_unit = "QphH"

        if latencies:
            sorted_lat = sorted(latencies)
            metrics.latency_p50 = float(np.percentile(sorted_lat, 50))
            metrics.latency_p95 = float(np.percentile(sorted_lat, 95))
            metrics.latency_p99 = float(np.percentile(sorted_lat, 99))
            metrics.latency_variance = float(np.std(latencies))

            if metrics.latency_p50 > 0:
                metrics.tail_amplification = metrics.latency_p99 / metrics.latency_p50

            # Throughput as Queries Per Hour (QphH metric analogous)
            metrics.throughput = (
                (total_queries / total_time) * 3600.0 if total_time > 0 else 0.0
            )
        else:
            logger.warning("  ➤ No successful queries during measurement")

        if total_queries > 0:
            metrics.error_rate = 0.0  # Since errors > 0 are already caught above

        log_section_header(
            logger,
            "  %sTPC-H metrics extracted:%s",
            COLORS.bold,
            COLORS.reset,
            level="debug",
            top_separator=False,
        )
        for metric_name, value in metrics.__dict__.items():
            if metric_name in ["latency_p50", "latency_p95", "latency_p99"]:
                logger.debug(
                    "    %s%-12s: %.3f s%s",
                    COLORS.bold,
                    metric_name,
                    value / 1000.0,
                    COLORS.reset,
                )
            elif metric_name == "throughput":
                logger.debug(
                    "    %s%-12s: %.3f QphH%s",
                    COLORS.bold,
                    metric_name,
                    value,
                    COLORS.reset,
                )

        return metrics
