"""
External Benchmark Executors
=============================

Provides interfaces for executing industry-standard database benchmarks
(Sysbench, TPC-H) via their native C-binaries rather than Python-level
query execution. This eliminates interpreter overhead and produces
results directly comparable to published academic baselines.

References
----------
- OtterTune (Van Aken et al., SIGMOD 2017): Uses Sysbench for OLTP evaluation
- CDBTune (Zhang et al., SIGMOD 2019): Uses Sysbench OLTP + TPCH for evaluation
- QTune (Li et al., VLDB 2019): Uses Sysbench with 10 tables × 100K rows
"""

import subprocess
import re
from abc import ABC, abstractmethod
from typing import Optional

from src.config.database import DatabaseConfig
from src.database.connection import get_connection
from src.tuner.evaluator.metrics import PerformanceMetrics
from src.tuner.utils.logger_config import get_logger


class BenchmarkExecutor(ABC):
    """
    Abstract interface for external C-binary benchmarking tools.

    Subclasses wrap standard benchmark drivers (sysbench, dbgen, etc.)
    and parse their stdout output into PerformanceMetrics.

    Used to provide rigorous academic-standard evaluations for automated tuning
    frameworks, as well as circumvent Python execution overhead.
    """

    @abstractmethod
    def prepare(self, db_config: DatabaseConfig) -> None:
        """Create required schema and data on the target database."""

    @abstractmethod
    def validate(self, db_config: DatabaseConfig) -> bool:
        """Return True if the required schema already exists."""

    @abstractmethod
    def execute(
        self,
        db_config: DatabaseConfig,
        duration: float,
        warmup: float = 30.0,
        worker_id: Optional[int] = None,
        workload_seed: Optional[int] = None,
    ) -> PerformanceMetrics:
        """
        Execute the external benchmark and return parsed metrics.

        Parameters
        ----------
        db_config : DatabaseConfig
            Target PostgreSQL instance connection details.
        duration : float
            Measurement phase duration in seconds.
        warmup : float
            Warmup phase duration in seconds.
        worker_id : Optional[int]
            Worker identifier for structured logging.
        workload_seed : Optional[int]
            RNG seed for reproducible query sequences across workers.
        """


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
        script: str = "oltp_read_write",
    ):
        """
        Parameters
        ----------
        threads : int
            Sysbench client threads. Default: 2.
        tables : int
            Number of sbtest tables. Academic standard: 10.
        table_size : int
            Rows per table. Academic standard: 100,000 (scale factor 1).
        script : str
            Sysbench Lua test script name. Default: oltp_read_write.
        """
        self.threads = threads
        self.tables = tables
        self.table_size = table_size
        self.script = script


    def prepare(self, db_config: DatabaseConfig) -> None:
        """Run native `sysbench prepare` to create all sbtest tables."""
        logger = get_logger(__name__)
        logger.info(
            "Preparing %d sysbench tables (%d rows each) on %s:%s...",
            self.tables, self.table_size, db_config.host, db_config.port,
        )
        cmd = self._build_base_cmd(db_config)
        subprocess.run(
            cmd + ["cleanup"],
            check=False, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
        )
        subprocess.run(cmd + ["prepare"], check=True, stdout=subprocess.DEVNULL)
        logger.info("Sysbench prepare complete.")

    def validate(self, db_config: DatabaseConfig) -> bool:
        """Return True if all required sbtest tables exist."""
        conn = get_connection(config=db_config)
        cursor = conn.cursor()
        cursor.execute(
            "SELECT count(*) FROM information_schema.tables "
            "WHERE table_schema = 'public' AND table_name LIKE 'sbtest%'"
        )
        count = cursor.fetchone()[0]
        cursor.close()
        conn.close()
        return count >= self.tables

    def execute(
        self,
        db_config: DatabaseConfig,
        duration: float,
        warmup: float = 30.0,
        worker_id: Optional[int] = None,
        workload_seed: Optional[int] = None,
    ) -> PerformanceMetrics:

        logger = get_logger(__name__, worker_id=worker_id)

        if warmup > 0:
            logger.debug("Sysbench warmup: %.0fs", warmup)
            self._run_sysbench(
                db_config,
                duration=int(warmup),
                seed=workload_seed,
            )

        logger.debug(
            "Sysbench measurement: %ss with %d threads (seed=%s)",
            duration, self.threads, workload_seed,
        )
        stdout, stderr, returncode = self._run_sysbench(
            db_config,
            duration=int(duration),
            seed=workload_seed,
        )

        if returncode != 0:
            logger.error(
                "Sysbench failed (exit %d): %s",
                returncode, stderr,
            )
            metrics = PerformanceMetrics()
            metrics.error_rate = 1.0
            return metrics

        metrics = self._parse_output(stdout, logger)
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
        seed: Optional[int] = None,
    ) -> tuple[str, str, int]:
        """Spawn the sysbench process and wait for completion."""
        cmd = self._build_base_cmd(db_config) + [
            f"--time={duration}",
            f"--threads={self.threads}",
            "--report-interval=0",
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
        stdout, stderr = process.communicate()
        return stdout, stderr, process.returncode

    @staticmethod
    def _parse_output(stdout: str, logger) -> PerformanceMetrics:
        """Extract TPS, p95 latency, and error rate from sysbench stdout."""
        metrics = PerformanceMetrics()

        tps_match = re.search(
            r'transactions:\s+\d+\s+\(([\d.]+)\s+per sec\.\)', stdout
        )
        if tps_match:
            metrics.throughput = float(tps_match.group(1))

        lat_match = re.search(r'95th percentile:\s+([\d.]+)', stdout)
        if lat_match:
            metrics.latency_p95 = float(lat_match.group(1))

        # Error rate = ignored_errors / total_transactions
        err_match = re.search(r'ignored errors:\s+(\d+)', stdout)
        if err_match:
            error_count = int(err_match.group(1))
            txn_match = re.search(r'transactions:\s+(\d+)', stdout)
            total_txns = int(txn_match.group(1)) if txn_match else 0
            if total_txns > 0:
                metrics.error_rate = error_count / total_txns

        return metrics
