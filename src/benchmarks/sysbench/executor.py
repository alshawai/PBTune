import subprocess
import re
from typing import Optional

from src.config.database import DatabaseConfig
from src.database.connection import get_connection
from src.tuner.evaluator.metrics import PerformanceMetrics
from src.tuner.utils.logger_config import get_logger
from src.tuner.evaluator.executor import BenchmarkExecutor

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
            Sysbench client threads. Default: 8.
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
            logger.debug("Successfully executed post-prepare VACUUM ANALYZE on all sbtest tables.")
        except Exception as e:
            logger.warning("Failed to post-vacuum sysbench tables: %s", e)

        logger.info("Sysbench prepare complete.")

    def validate(self, db_config: DatabaseConfig) -> bool:
        """Return True if all required sbtest tables exist AND have expected rows."""
        logger = get_logger(__name__)
        try:
            conn = get_connection(config=db_config)
            cursor = conn.cursor()

            # First check if all tables exist
            cursor.execute(
                "SELECT count(*) FROM information_schema.tables "
                "WHERE table_schema = 'public' AND table_name LIKE 'sbtest%'"
            )
            count = cursor.fetchone()[0]  # type: ignore

            if count < self.tables:
                logger.debug("Sysbench tables missing (found %d, expected %d)", count, self.tables)
                cursor.close()
                conn.close()
                return False

            # Then check if they're actually populated (just sample table 1).
            cursor.execute("SELECT max(id) FROM sbtest1")
            max_id = cursor.fetchone()[0]  # type: ignore

            # Sysbench insert might not be exactly equal to table_size if there were errors
            # during prepare, but it should be close or equal
            if max_id is None or max_id < (self.table_size * 0.9):
                logger.debug(
                    "Sysbench tables exist but are empty/underpopulated "
                    "(max id %s, expected ~%d)", max_id, self.table_size
                )
                cursor.close()
                conn.close()
                return False

            cursor.close()
            conn.close()
            return True

        except Exception as e:
            logger.debug("Sysbench validation failed: %s", e)
            return False

    def execute(
        self,
        db_config: DatabaseConfig,
        worker_id: Optional[int] = None,
        **kwargs
    ) -> PerformanceMetrics:
        logger = get_logger(__name__, worker_id=worker_id)

        duration = kwargs.get("duration", 60.0)
        warmup = kwargs.get("warmup", 30.0)
        random_seed = kwargs.get("random_seed", None)

        logger.debug(
            "Sysbench measurement: %ss with %d threads (warmup=%ss, seed=%s)",
            duration, self.threads, warmup, random_seed,
        )
        stdout, stderr, returncode = self._run_sysbench(
            db_config,
            duration=int(duration),
            warmup=int(warmup),
            seed=random_seed,
        )

        if returncode != 0:
            failure_detail = (stderr or stdout or "sysbench exited without output").strip()
            logger.error(
                "Sysbench failed (exit %d): %s",
                returncode,
                failure_detail,
            )
            raise RuntimeError(f"Sysbench failed (exit {returncode}): {failure_detail}")

        metrics = self._parse_output(stdout)
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
            f"--time={duration}",
            f"--threads={self.threads}",
            "--report-interval=0",
        ]

        # if warmup > 0:
        #     cmd.append(f"--warmup-time={warmup}")

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
    def _parse_output(stdout: str) -> PerformanceMetrics:
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
