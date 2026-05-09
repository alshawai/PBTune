import subprocess
import re
from typing import Optional

import psycopg2

from src.config.database import DatabaseConfig
from src.database.connection import get_connection
from src.utils.logger import get_logger
from src.utils.metrics import PerformanceMetrics
from src.benchmarks.executor import BenchmarkExecutor

SYSBENCH_WORKLOADS = (
    "oltp_read_only",
    "oltp_read_write",
    "oltp_write_only",
)
DEFAULT_SYSBENCH_WORKLOAD = "oltp_read_write"


def validate_sysbench_workload(mode: str) -> str:
    """Validate and normalize a sysbench workload mode."""
    normalized = str(mode).strip().lower()
    if normalized not in SYSBENCH_WORKLOADS:
        raise ValueError(
            f"Unsupported sysbench workload '{mode}'. "
            f"Expected one of {SYSBENCH_WORKLOADS}."
        )
    return normalized


class SysbenchExecutor(BenchmarkExecutor):
    """
    Executes the standard Sysbench OLTP read-write benchmark via CLI.

    Academic Standard Configuration (OtterTune, CDBTune, QTune):
    - Script: oltp_read_write (18 queries per transaction)
    - Tables: 10 tables × 100,000 rows each (scale factor 1)
    - Threads: Equal to available CPU cores per worker

    Each sysbench "transaction" executes:
        BEGIN → 10 point SELECTs → 4 range SELECTs → 1 UPDATE (index)
        → 1 UPDATE (non-index) → 1 DELETE → 1 INSERT → COMMIT

    Metrics reported as TPS (Transactions Per Second) and p95
    Transaction Latency (ms) per SIGMOD/VLDB convention.
    """

    def __init__(
        self,
        threads: int = 8,
        tables: int = 10,
        table_size: int = 100000,
        script: str = DEFAULT_SYSBENCH_WORKLOAD,
    ):
        """
        Parameters
        ----------
        threads : int
            Sysbench client threads. Default: 8.
        tables : int
            Number of sbtest tables. Academic standard: 10.
        table_size : int
            Rows per table. Academic standard: 100,000 (scale factor 1).
        script : str
            Sysbench Lua test script name. Default: oltp_read_write.
        """
        self.logger = get_logger("SysbenchExecutor")

        self.threads = threads
        self.tables = tables
        self.table_size = table_size
        self.script = validate_sysbench_workload(script)

    def prepare(self, db_config: DatabaseConfig) -> None:
        """Run native `sysbench prepare` to create all sbtest tables."""
        self.logger.info(
            "Preparing %d sysbench tables (%d rows each) on %s:%s...",
            self.tables,
            self.table_size,
            db_config.host,
            db_config.port,
        )

        # Cross-benchmark safety: ensure TPC-H leftovers do not survive into
        # a Sysbench prepare fallback path after snapshot restore failure.
        cleanup_conn = get_connection(config=db_config)
        cleanup_conn.autocommit = True
        cleanup_cursor = cleanup_conn.cursor()
        try:
            self._drop_existing_public_tables(cleanup_cursor)
        finally:
            cleanup_cursor.close()
            cleanup_conn.close()

        cmd = self._build_base_cmd(db_config)
        subprocess.run(
            cmd + ["cleanup"],
            check=False,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
        )
        subprocess.run(cmd + ["prepare"], check=True, stdout=subprocess.DEVNULL)

        # Run VACUUM ANALYZE to stabilize statistics and prevent 'autoanalyze'
        # from randomly triggering during benchmark
        try:
            conn = get_connection(config=db_config)
            conn.autocommit = True
            cursor = conn.cursor()
            for i in range(1, self.tables + 1):
                cursor.execute(f"VACUUM ANALYZE sbtest{i}")
            cursor.close()
            conn.close()
            self.logger.debug(
                "Successfully executed post-prepare VACUUM ANALYZE on all sbtest tables."
            )
        except (RuntimeError, psycopg2.Error, OSError, ValueError) as e:
            self.logger.warning("Failed to post-vacuum sysbench tables: %s", e)

        self.logger.info("Sysbench prepare complete.")

    def validate(self, db_config: DatabaseConfig) -> bool:
        """Return True only when schema shape matches the configured Sysbench profile."""
        conn = None
        cursor = None
        try:
            conn = get_connection(config=db_config)
            cursor = conn.cursor()

            # Schema must match the expected table set exactly.
            cursor.execute(
                "SELECT table_name FROM information_schema.tables "
                "WHERE table_schema = 'public' AND table_name LIKE 'sbtest%'"
            )
            found_tables = {str(row[0]) for row in cursor.fetchall()}
            expected_tables = {f"sbtest{i}" for i in range(1, self.tables + 1)}

            if found_tables != expected_tables:
                missing_tables = sorted(expected_tables - found_tables)
                extra_tables = sorted(found_tables - expected_tables)
                self.logger.debug(
                    "Sysbench table layout mismatch "
                    "(missing=%s, extra=%s, expected_count=%d, found_count=%d)",
                    missing_tables,
                    extra_tables,
                    len(expected_tables),
                    len(found_tables),
                )
                return False

            # Validate table cardinality against the configured profile.
            cursor.execute("SELECT max(id) FROM sbtest1")
            max_id = cursor.fetchone()[0]  # type: ignore

            # max(id) grows over time due delete+insert churn. Estimate table cardinality
            # using row count to detect profile mismatches (e.g., standard -> rapid).
            cursor.execute("SELECT count(*) FROM sbtest1")
            row_count = cursor.fetchone()[0]  # type: ignore

            lower_bound = int(self.table_size * 0.9)
            upper_bound = int(self.table_size * 1.1)
            if row_count is None or row_count < lower_bound or row_count > upper_bound:
                self.logger.debug(
                    "Sysbench row cardinality mismatch "
                    "(row_count=%s, max_id=%s, expected~%d, bounds=[%d,%d])",
                    row_count,
                    max_id,
                    self.table_size,
                    lower_bound,
                    upper_bound,
                )
                return False

            return True

        except (RuntimeError, psycopg2.Error, OSError, ValueError) as e:
            self.logger.debug("Sysbench validation failed: %s", e)
            return False
        finally:
            if cursor is not None:
                cursor.close()
            if conn is not None:
                conn.close()

    def execute(
        self, db_config: DatabaseConfig, worker_id: Optional[int] = None, **kwargs
    ) -> PerformanceMetrics:
        """Execute Sysbench benchmark and return performance metrics."""
        logger = get_logger("SysbenchExecutor", worker_id=worker_id)

        duration = kwargs.get("duration", 60.0)
        warmup = kwargs.get("warmup", 30.0)
        random_seed = kwargs.get("random_seed", None)

        logger.debug(
            "Sysbench measurement: %ss with %d threads (warmup=%ss, seed=%s)",
            duration,
            self.threads,
            warmup,
            random_seed,
        )
        stdout, stderr, returncode = self._run_sysbench(
            db_config,
            duration=int(duration),
            warmup=int(warmup),
            seed=random_seed,
        )

        if returncode != 0:
            failure_detail = (
                stderr or stdout or "sysbench exited without output"
            ).strip()
            logger.error(
                "Sysbench failed (exit %d): %s",
                returncode,
                failure_detail,
            )
            raise RuntimeError(f"Sysbench failed (exit {returncode}): {failure_detail}")

        metrics = self._parse_output(stdout)

        if metrics.throughput == 0.0:
            logger.error("Sysbench extracted 0 throughput! Dumping output:")
            logger.error("STDOUT:\\n%s", stdout)
            logger.error("STDERR:\\n%s", stderr)
            raise RuntimeError("Sysbench executed but parsed 0 throughput. Check logs.")

        logger.debug(
            "Sysbench metrics extracted: latency_p50=%.2f, latency_p95=%.2f, latency_p99=%.2f, throughput=%.2f",
            metrics.latency_p50,
            metrics.latency_p95,
            metrics.latency_p99,
            metrics.throughput,
        )

        if metrics.latency_p95 == 0.0 or metrics.latency_p99 == 0.0:
            logger.warning(
                "Sysbench extracted 0 for p95=%s or p99=%s! This may indicate output format mismatch. "
                "Raw output (first 5000 chars):\n%s",
                metrics.latency_p95,
                metrics.latency_p99,
                stdout[:5000],
            )
            # Also dump the section that contains percentile data
            if "General statistics:" in stdout:
                idx = stdout.find("General statistics:")
                logger.warning(
                    "Percentile section (from 'General statistics:'):\n%s",
                    stdout[idx : idx + 2000],
                )
            # Dump sample interval lines to debug format
            logger.warning("Sample interval lines:")
            for i, line in enumerate(stdout.splitlines()[:30]):
                if "[" in line and "s ]" in line:
                    logger.warning("  Line %d: %s", i, line)

        return metrics

    def _build_base_cmd(self, db_config: DatabaseConfig) -> list[str]:
        """Build the common sysbench CLI prefix (shared by prepare/run)."""
        return [
            "sysbench",
            self.script,
            "--db-driver=pgsql",
            f"--pgsql-host={db_config.host}",
            f"--pgsql-port={db_config.port}",
            f"--pgsql-user={db_config.user}",
            f"--pgsql-password={db_config.password}",
            f"--pgsql-db={db_config.dbname}",
            f"--tables={self.tables}",
            f"--table-size={self.table_size}",
        ]

    def _run_sysbench(
        self,
        db_config: DatabaseConfig,
        duration: int,
        warmup: int = 0,
        seed: Optional[int] = None,
    ) -> tuple[str, str, int]:
        """Spawn the sysbench process and wait for completion."""
        cmd = self._build_base_cmd(db_config) + [
            f"--time={duration + warmup}",
            f"--threads={self.threads}",
            "--report-interval=1",
            "--percentile=99",
            "--histogram=on",
        ]

        if seed is not None:
            cmd.append(f"--rand-seed={seed}")

        cmd.append("run")

        process = subprocess.Popen(
            cmd,
            stdout=subprocess.PIPE,  # sysbench outputs to stdout
            stderr=subprocess.PIPE,
            text=True,
        )
        try:
            stdout, stderr = process.communicate(timeout=duration + warmup + 15)
        except subprocess.TimeoutExpired:
            process.kill()
            stdout, stderr = process.communicate()
            return "", "Sysbench timeout expired", -1
        return stdout, stderr, process.returncode

    @staticmethod
    def _parse_histogram(stdout: str) -> dict[str, float]:
        """Parse Sysbench latency histogram to compute exact percentiles and variance."""
        import numpy as np

        in_histogram = False
        bins = []
        counts = []

        for line in stdout.splitlines():
            if "Latency histogram" in line:
                in_histogram = True
                continue
            if in_histogram:
                if not line.strip() or "SQL statistics:" in line:
                    break
                # Match line like: "       1.219 |***     10"
                m = re.match(r"^\s*([\d.]+)\s*\|.*?\s+(\d+)$", line)
                if m:
                    bins.append(float(m.group(1)))
                    counts.append(int(m.group(2)))

        if not bins or not counts:
            return {}

        bins_arr = np.array(bins)
        counts_arr = np.array(counts)
        total_count = counts_arr.sum()

        if total_count == 0:
            return {}

        cumulative = np.cumsum(counts_arr)
        percentiles = cumulative / total_count

        # Find exact percentiles
        p50_idx = np.searchsorted(percentiles, 0.50)
        p95_idx = np.searchsorted(percentiles, 0.95)
        p99_idx = np.searchsorted(percentiles, 0.99)

        # Standard deviation from histogram
        mean_val = np.sum(bins_arr * counts_arr) / total_count
        variance = np.sum(counts_arr * ((bins_arr - mean_val) ** 2)) / total_count

        return {
            "p50": bins_arr[p50_idx] if p50_idx < len(bins_arr) else bins_arr[-1],
            "p95": bins_arr[p95_idx] if p95_idx < len(bins_arr) else bins_arr[-1],
            "p99": bins_arr[p99_idx] if p99_idx < len(bins_arr) else bins_arr[-1],
            "variance": float(np.sqrt(variance)),  # technically stddev
        }

    @staticmethod
    def _parse_output(stdout: str) -> PerformanceMetrics:
        """Extract TPS, p95/p99 latency, and error rate from sysbench stdout."""
        import numpy as np

        metrics = PerformanceMetrics()

        tps_match = re.search(r"transactions:\s+\d+\s+\(([\d.]+)\s+per sec\.\)", stdout)
        if tps_match:
            metrics.throughput = float(tps_match.group(1))

        # Attempt precise extraction via histogram first
        hist_data = SysbenchExecutor._parse_histogram(stdout)
        if hist_data:
            metrics.latency_p50 = hist_data["p50"]
            metrics.latency_p95 = hist_data["p95"]
            metrics.latency_p99 = hist_data["p99"]
            metrics.latency_variance = hist_data["variance"]
        else:
            # Fallback to summary/interval estimation
            lat_p95_match = re.search(r"95th percentile:\s+([\d.]+)", stdout)
            if lat_p95_match:
                metrics.latency_p95 = float(lat_p95_match.group(1))

            lat_p99_match = re.search(r"99th percentile:\s+([\d.]+)", stdout)
            if lat_p99_match:
                metrics.latency_p99 = float(lat_p99_match.group(1))

            if metrics.latency_p95 == 0.0 or metrics.latency_p99 == 0.0:
                interval_p95 = []
                interval_p99 = []
                for line in stdout.splitlines():
                    m95 = re.search(r"lat \(ms,95%\):\s+([\d.]+)", line)
                    if m95:
                        interval_p95.append(float(m95.group(1)))

                    m99 = re.search(r"lat \(ms,99%\):\s+([\d.]+)", line)
                    if m99:
                        interval_p99.append(float(m99.group(1)))

                if interval_p99:
                    if metrics.latency_p99 == 0.0:
                        metrics.latency_p99 = float(np.mean(interval_p99))
                    warmup_skip = max(0, len(interval_p99) // 4)
                    steady_state_lat = (
                        interval_p99[warmup_skip:]
                        if len(interval_p99) > 4
                        else interval_p99
                    )
                    metrics.latency_variance = float(np.std(steady_state_lat))

                if interval_p95 and metrics.latency_p95 == 0.0:
                    metrics.latency_p95 = float(np.mean(interval_p95))
                elif metrics.latency_p95 == 0.0 and interval_p99:
                    metrics.latency_p95 = metrics.latency_p99 / 1.2

            avg_match = re.search(r"avg:\s+([\d.]+)", stdout)
            if avg_match:
                metrics.latency_p50 = float(avg_match.group(1))

        # Parse interval lines for throughput variance
        interval_tps = []
        for line in stdout.splitlines():
            # Example: [ 1s ] thds: 8 tps: 100.00 qps: ...
            m = re.search(r"\[\s*\d+s\s*\]\s*thds:\s*\d+\s*tps:\s*([\d.]+)", line)
            if m:
                interval_tps.append(float(m.group(1)))

        if interval_tps:
            # Drop the first few seconds if possible to avoid warmup noise
            warmup_skip = max(0, len(interval_tps) // 4)
            steady_state = (
                interval_tps[warmup_skip:] if len(interval_tps) > 4 else interval_tps
            )
            metrics.throughput_variance = float(np.std(steady_state))

        # Calculate tail amplification
        if metrics.latency_p50 > 0:
            metrics.tail_amplification = metrics.latency_p99 / metrics.latency_p50

        # Error rate = ignored_errors / total_transactions
        err_match = re.search(r"ignored errors:\s+(\d+)", stdout)
        if err_match:
            error_count = int(err_match.group(1))
            txn_match = re.search(r"transactions:\s+(\d+)", stdout)
            total_txns = int(txn_match.group(1)) if txn_match else 0
            if total_txns > 0:
                metrics.error_rate = error_count / total_txns

        # Extract total queries
        queries_match = re.search(r"queries:\s+(\d+)\s+\(", stdout)
        if queries_match:
            metrics.total_queries = int(queries_match.group(1))

        # Extract total time
        time_match = re.search(r"total time:\s+([\d.]+)s", stdout)
        if time_match:
            metrics.total_time = float(time_match.group(1))

        return metrics
