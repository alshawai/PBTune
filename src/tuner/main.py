"""
PBT PostgreSQL Tuner - End-to-End Application
=============================================

Complete end-to-end application for automatic PostgreSQL configuration tuning
using Population Based Training (PBT).

This application integrates all PBT components to perform automated database
optimization through evolutionary hyperparameter search.

Usage:
------
# Quick test with minimal knobs
python -m src.tuner.main --tier minimal --config rapid

# Standard tuning session
python -m src.tuner.main --tier core --config standard

# Comprehensive tuning
python -m src.tuner.main --tier standard --config thorough

# Custom configuration
python -m src.tuner.main --tier minimal --population 8 --generations 50

Features:
---------
- Automatic knob space loading based on tier
- Pre-configured PBT profiles (RAPID, STANDARD, THOROUGH)
- Real workload execution with performance measurement
- Parallel worker evaluation
- Progress tracking and visualization
- Result export (JSON, CSV)
- Best configuration saving
"""

import argparse
import json
import sys
import math
import time
import logging
import re
from dataclasses import replace
from pathlib import Path
from typing import Optional, Dict, Any, Tuple, List
from datetime import datetime
import numpy as np
import psycopg2

from src.config.data_root import resolve_data_root
from src.config.database import get_db_config
from src.database.connection import get_connection

from src.tuner.config import (
    get_knob_space,
    PBTConfig,
    RAPID_CONFIG,
    STANDARD_CONFIG,
    THOROUGH_CONFIG,
    RESEARCH_CONFIG,
    EXTREME_CONFIG,
)
from src.tuner.core.population import Population, PopulationConfig
from src.tuner.core.worker import Worker
from src.tuner.core.barriers import GenerationBarrier
from src.tuner.benchmark.orchestrator import (
    WorkloadOrchestrator,
    WorkloadOrchestratorConfig,
    WorkloadExecutor,
)
from src.tuner.benchmark.workload import (
    WorkloadFileLoader,
    extract_workload_template_metadata,
)
from src.benchmarks.sysbench.executor import (
    SysbenchExecutor,
    DEFAULT_SYSBENCH_WORKLOAD,
)
from src.benchmarks.tpch.executor import TPCHExecutor
from src.utils.environments import EnvironmentFactory
from src.utils.metrics import (
    PerformanceMetrics,
    WorkloadType,
    create_metric_config,
)
from src.utils.types import clone_benchmark_config
from src.utils.scoring.workload_features import WorkloadFeatureExtractor
from src.utils.scoring.contracts import ScoreBreakdown
from src.utils.logger import (
    setup_logging,
    add_html_file_logging,
    set_colors_enabled,
    get_logger,
    get_color_context,
    print_startup_banner,
    log_section_header,
    log_generation_summary,
    log_final_summary,
)
from src.utils.hardware_info import (
    get_system_info,
    log_system_info,
    detect_worker_resources,
)
from src.utils.types import TuningMode


LOGGER = get_logger(
    "PBTune"
)  # inherits from the root logger (defined in setup_logging)
COLORS = get_color_context()


def convert_numpy_types(obj: Any) -> Any:
    """
    Recursively convert numpy types to Python native types for JSON serialization.

    Parameters
    ----------
    obj : Any
        Object potentially containing numpy types

    Returns
    -------
    Any
        Object with numpy types converted to Python types
    """
    # Check numpy bool first (before other numpy types)
    if isinstance(obj, np.bool_):  # type: ignore
        return bool(obj)
    elif isinstance(obj, (np.integer, np.int64, np.int32, np.int16, np.int8)):  # type: ignore
        return int(obj)
    elif isinstance(obj, (np.floating, np.float64, np.float32, np.float16)):  # type: ignore
        return float(obj)
    elif isinstance(obj, np.ndarray):  # type: ignore
        return obj.tolist()
    elif isinstance(obj, dict):
        return {key: convert_numpy_types(value) for key, value in obj.items()}
    elif isinstance(obj, (list, tuple)):
        return [convert_numpy_types(item) for item in obj]
    else:
        return obj


def resolve_output_file_path(
    base_output_dir: Path,
    benchmark: str,
    tier: str,
    timestamp: str,
    sysbench_workload: Optional[str] = None,
    workload: Optional[str] = None,
) -> Path:
    """
    Resolve the HTML log file path based on benchmark configuration and tier.

    Creates the structured output directory hierarchy and returns the full path
    to the HTML log file that will contain the tuning session results.

    Parameters
    ----------
    base_output_dir : Path
        Base output directory (computed from colocate_output and output_dir)
    benchmark : str
        Benchmark type ("sysbench", "tpch", or custom)
    tier : str
        Knob tier ("minimal", "core", "standard", "extensive")
    timestamp : str
        Timestamp string (format: YYYYMMDD_HHMM)
    sysbench_workload : Optional[str]
        Sysbench workload name (only used if benchmark=="sysbench")
    workload : Optional[str]
        Custom workload name (used if benchmark is neither sysbench nor tpch)

    Returns
    -------
    Path
        Full path to the HTML log file (pbt_tuning_<timestamp>.html)
    """
    if benchmark == "sysbench":
        workload_name = sysbench_workload or DEFAULT_SYSBENCH_WORKLOAD
        log_output_dir = base_output_dir / "oltp" / workload_name / "pbt_runs" / tier
    else:
        workload_name = "olap" if benchmark == "tpch" else workload
        log_output_dir = base_output_dir / workload_name / "pbt_runs" / tier

    log_output_dir.mkdir(parents=True, exist_ok=True)
    return log_output_dir / f"pbt_tuning_{timestamp}.html"


class PBTTuner:
    """
    Main PBT Tuner application class.

    Orchestrates the complete tuning workflow:
    1. Initialize population with random configurations
    2. Evaluate workers (apply config → run workload → measure performance)
    3. Exploit-explore evolution
    4. Track best configuration
    5. Export results
    """

    def __init__(
        self,
        knob_tier: str = "minimal",
        pbt_config: Optional[PBTConfig] = None,
        benchmark: Optional[str] = None,
        workload_type: WorkloadType = WorkloadType.OLTP,
        workload_file: Optional[str] = None,
        random_seed: Optional[int] = None,
        **kwargs,
    ):
        """
        Initialize PBT Tuner.

        Parameters
        ----------
        knob_tier : str
            Knob space tier: 'minimal', 'core', 'standard', 'extensive'
        pbt_config : Optional[PBTConfig]
            PBT hyperparameters. If None, uses STANDARD_CONFIG
        benchmark : Optional[str]
            Benchmark name for workload executor ('sysbench', 'tpch', or None for custom)
        workload_type : WorkloadType
            Workload type for optimization
        workload_file : Optional[str]
            Path to custom workload file (JSON/YAML). If provided, overrides workload_type.
        random_seed : Optional[int]
            Seed for random number generation (default: 42)
        **kwargs
            Additional keyword arguments for configuration.
                - force_recreate_instances: bool (default: False)
                    Force recreate PostgreSQL instances
                - force_recreate_baseline: bool (default: False)
                    Force recreate baseline snapshot
                - cleanup_instances: bool (default: False)
                    Whether to clean up instance data after tuning
                - warm_start_path: Optional[str] (default: None)
                    Path to previous best_config.json for warm-starting
                - output_dir: str (default: "results")
                    Base output directory. Results are organized into
                    {output_dir}/{workload_type}/pbt_runs/{knob_tier}/
                - timestamp: str (default: current timestamp)
                    Timestamp for result files (format: YYYYMMDD_HHMM)
                - logger: Optional[logging.Logger] (default: None)
                    Custom logger instance. If None, a default logger is created.
                - disable_early_stopping: bool (default: False)
                    Disable the no-improvement early stop gate.
        """
        log_section_header(
            LOGGER,
            "%sStarting PBT Tuner initialization%s",
            COLORS.bold,
            COLORS.reset,
        )

        self.knob_tier = knob_tier
        self.pbt_config = pbt_config or STANDARD_CONFIG
        self.random_seed = random_seed

        self.force_recreate_instances = kwargs.get("force_recreate_instances", False)
        self.force_recreate_baseline = kwargs.get("force_recreate_baseline", False)

        self.cleanup_instances = kwargs.get("cleanup_instances", False)
        self.no_docker = kwargs.get("no_docker", False)
        self.docker_image = kwargs.get("docker_image", None)
        self.disable_early_stopping = kwargs.get("disable_early_stopping", False)

        self.data_root = kwargs.get("data_root")
        if self.data_root is None:
            self.data_root = resolve_data_root()

        self.warm_start_path = kwargs.get("warm_start_path", None)
        self.warm_start_provenance = {"enabled": False}

        self.ablation_variable = kwargs.get("ablation_variable", None)
        self.ablation_value = kwargs.get("ablation_value", None)

        self.timestamp = kwargs.get("timestamp", datetime.now().strftime("%Y%m%d_%H%M"))

        LOGGER.info("Loading knob space: %s", knob_tier.capitalize())
        self.knob_space = get_knob_space(knob_tier)

        self.full_knob_space = self.knob_space
        if self.pbt_config.benchmark_config.tuning_mode == TuningMode.ONLINE:
            LOGGER.info(
                "Creating ONLINE knob view by filtering out `restart-required` knobs..."
            )
            self.knob_space = self.knob_space.create_online_view()

        LOGGER.info(
            "Detecting hardware resources for %s%s%s parallel workers...",
            COLORS.bold,
            self.pbt_config.num_parallel_workers,
            COLORS.reset,
        )
        self.worker_resources = detect_worker_resources(
            self.pbt_config.num_parallel_workers,
            data_path=self.data_root,
        )

        LOGGER.info(
            "Resolving hardware-relative knob ranges based on detected worker resources..."
        )
        self.full_knob_space.resolve_hardware_ranges(self.worker_resources)
        self.knob_space.worker_resources = self.worker_resources
        self.db_config = get_db_config()

        self.metric_config = create_metric_config(
            workload_type.value,
            scoring_policy=self.pbt_config.scoring_policy,
            scoring_policy_version=self.pbt_config.scoring_policy_version,
            metric_reference_version=self.pbt_config.metric_reference_version,
        )

        self.workload_features: Dict[str, float] = {}
        self.feature_extractor = WorkloadFeatureExtractor()

        if benchmark == "sysbench":
            self.benchmark_name = "sysbench"
            self.workload_type = WorkloadType.OLTP

            tables = self.pbt_config.benchmark_config.sysbench_tables
            table_size = self.pbt_config.benchmark_config.sysbench_table_size
            script = self.pbt_config.benchmark_config.sysbench_workload

            workload_executor = SysbenchExecutor(
                tables=tables,
                table_size=table_size,
                script=script,
            )

            LOGGER.info(
                "Extracting workload features from Sysbench (script='%s', threads=%d, "
                "cpu_cores=%d, tables=%d, table_size=%d)",
                script,
                self.pbt_config.num_parallel_workers,
                int(self.worker_resources.cpu_cores or 1),
                tables,
                table_size,
            )
            self.workload_features = self.feature_extractor.extract_sysbench_features(
                script=script,
                threads=self.pbt_config.num_parallel_workers,
                cpu_cores=int(self.worker_resources.cpu_cores or 1),
                table_size=table_size,
                tables=tables,
            )
            self.snapshot_identifier = f"sysbench_{script}_t{tables}_s{table_size}"

            if script == "oltp_read_only":
                LOGGER.debug(
                    "%sSysbench read-only workload detected; no need for snapshot restorations.%s",
                    COLORS.italic,
                    COLORS.reset,
                )
                self.pbt_config.enable_snapshots = False

        elif benchmark == "tpch":
            self.benchmark_name = "tpch"
            self.workload_type = WorkloadType.OLAP

            scale_factor = self.pbt_config.benchmark_config.scale_factor
            workload_executor = TPCHExecutor(scale_factor=scale_factor)

            LOGGER.info(
                "Extracting workload features from TPC-H (SF=%.2f, warmup_passes=%d)",
                scale_factor,
                self.pbt_config.benchmark_config.warmup_passes,
            )
            self.workload_features = self.feature_extractor.extract_tpch_features(
                scale_factor=scale_factor,
                warmup_passes=self.pbt_config.benchmark_config.warmup_passes,
                queries=workload_executor.queries,
            )
            self.snapshot_identifier = f"tpch_sf{scale_factor}"

            LOGGER.debug(
                "%sTPC-H is a read-only OLAP benchmark; no need for snapshot restorations.%s",
                COLORS.italic,
                COLORS.reset,
            )
            self.pbt_config.enable_snapshots = False

        else:  # Custom workload (defined by workload_file)
            self.benchmark_name = workload_type.value
            self.workload_type = workload_type

            LOGGER.info(
                "%s%sInitializing custom workload executor...%s",
                COLORS.bold,
                workload_file,
                COLORS.reset,
            )
            workload_executor = self._create_workload_executor(
                workload_type, workload_file
            )

            template_metadata = extract_workload_template_metadata(workload_executor)
            LOGGER.info(
                "Extracting workload features from TemplateWorkload (query_count=%d)",
                len(template_metadata.queries),
            )

            self.workload_features = self.feature_extractor.extract_template_features(
                metadata=template_metadata,
            )
            self.snapshot_identifier = f"{self.benchmark_name}_sf{self.pbt_config.benchmark_config.scale_factor}"

        self.snapshot_identifier = self._normalize_snapshot_identifier(
            self.snapshot_identifier
        )
        no_docker = kwargs.get("no_docker", False)

        self.env = EnvironmentFactory.create(
            schema_provider=workload_executor,
            use_docker=not no_docker,
            base_dir=self.data_root,
            base_port=5440,
            db_config=self.db_config,
            worker_resources=self.worker_resources,
            run_id=self.snapshot_identifier,
            image_name=self.docker_image,
            force_recreate_baseline=self.force_recreate_baseline,
        )

        self.metric_config.workload_features = dict(self.workload_features)

        self.evaluator_config = WorkloadOrchestratorConfig(
            workload_type=workload_type,
            metric_config=self.metric_config,
            db_config=self.db_config,
            warmup_duration=self.pbt_config.benchmark_config.warmup_duration,
            measurement_duration=self.pbt_config.benchmark_config.evaluation_duration,
            cooldown_duration=3.0,
            tuning_mode=self.pbt_config.benchmark_config.tuning_mode,
            adaptive_restart_interval=self.pbt_config.benchmark_config.adaptive_restart_interval,
            random_seed=random_seed,
            warmup_passes=self.pbt_config.benchmark_config.warmup_passes,
            worker_memory_budget_bytes=self.worker_resources.ram_bytes,
        )

        self.orchestrator = WorkloadOrchestrator(
            self.evaluator_config, workload_executor, self.env
        )

        pop_config = PopulationConfig(
            population_size=self.pbt_config.population_size,
            ready_interval=self.pbt_config.ready_interval,
            exploit_quantile=self.pbt_config.exploit_quantile,
            perturbation_factors=self.pbt_config.perturbation_factors,
            convergence_threshold=0.05,
            max_generations=self.pbt_config.num_generations,
            early_stopping_patience=10,
            disable_early_stopping=self.disable_early_stopping,
            dead_config_threshold=self.pbt_config.dead_config_threshold,
        )

        self.population = Population(
            self.knob_space, pop_config, orchestrator=self.orchestrator
        )

        LOGGER.info("Collecting system hardware and software information...")
        self.system_info = get_system_info(data_path=self.data_root)

        self.output_dir = self._build_output_dir(
            Path(kwargs.get("output_dir", "results"))
        )
        self.output_dir.mkdir(parents=True, exist_ok=True)

        # initialized at tuning start to accurately track wall clock time
        self.start_time: float = 0
        self.generation_history = []

        self.current_generation: int = 0
        self.restart_count: int = 0

        self._restarted_this_generation = False

        LOGGER.info(
            "%s%sPBT Tuner Initialization Complete!%s",
            COLORS.bold,
            COLORS.green,
            COLORS.reset,
        )

    @property
    def best_config(self) -> Dict[str, Any]:
        """Dynamically fetch the all-time best configuration from Population"""
        config, _ = self.population.get_best_configuration()
        return config

    @property
    def best_score(self) -> float:
        """Dynamically fetch the all-time mathematically rescored best score from Population"""
        _, score = self.population.get_best_configuration()
        return score

    def _normalize_snapshot_identifier(self, snapshot_identifier: str) -> str:
        """Normalize snapshot identifiers for filesystem and Docker compatibility."""
        normalized = re.sub(r"[^a-z0-9_.-]+", "-", snapshot_identifier.lower()).strip(
            "-"
        )
        return normalized or "default"

    def _build_output_dir(self, base_output_dir: Path) -> Path:
        """Build structured output directory, canonically separating ablation runs if specified."""
        ablation_subpath = Path("")
        if (
            getattr(self, "ablation_variable", None)
            and getattr(self, "ablation_value", None) is not None
        ):
            ablation_subpath = (
                Path("ablations")
                / str(self.ablation_variable)
                / str(self.ablation_value)
            )

        if self.benchmark_name == "sysbench":
            return (
                base_output_dir
                / self.workload_type.value
                / self.pbt_config.benchmark_config.sysbench_workload
                / "pbt_runs"
                / self.knob_tier
                / ablation_subpath
            )
        return (
            base_output_dir
            / self.workload_type.value
            / "pbt_runs"
            / self.knob_tier
            / ablation_subpath
        )

    def _get_runtime_supported_knobs(self, worker_id: int = 0) -> Tuple[set[str], str]:
        """Get runtime pg_settings knob names and server version from a worker instance."""
        db_config = self.env.get_db_config(worker_id)

        conn = None
        cursor = None
        try:
            conn = get_connection(config=db_config, connect_timeout=5)
            cursor = conn.cursor()
            cursor.execute("SELECT current_setting('server_version')")
            version_row = cursor.fetchone()
            server_version = str(version_row[0]) if version_row else "unknown"

            cursor.execute("SELECT name FROM pg_settings")
            supported_knobs = {str(row[0]) for row in cursor.fetchall()}
            return supported_knobs, server_version
        except (psycopg2.Error, RuntimeError, OSError, ValueError) as exc:
            LOGGER.warning(
                "Failed to inspect runtime pg_settings for knob compatibility: %s",
                exc,
            )
            return set(self.knob_space.knobs.keys()), "unknown"
        finally:
            if cursor is not None:
                cursor.close()
            if conn is not None:
                conn.close()

    def _prune_unsupported_runtime_knobs(self) -> None:
        """Prune knobs unavailable on runtime PostgreSQL to avoid apply/verify failures."""
        supported_knobs, server_version = self._get_runtime_supported_knobs(worker_id=0)
        configured_knobs = set(self.knob_space.knobs.keys())
        unsupported_knobs = sorted(configured_knobs - supported_knobs)

        if not unsupported_knobs:
            LOGGER.debug(
                "➤ Runtime knob compatibility check passed against PostgreSQL %s (%d knobs)",
                server_version,
                len(configured_knobs),
            )
            return

        for knob_name in unsupported_knobs:
            self.knob_space.knobs.pop(knob_name, None)

        preview = unsupported_knobs[:10]
        suffix = " ..." if len(unsupported_knobs) > len(preview) else ""
        LOGGER.warning(
            " Pruned %d unsupported knobs for PostgreSQL %s: %s%s",
            len(unsupported_knobs),
            server_version,
            ", ".join(preview),
            suffix,
        )

        if len(self.knob_space) == 0:
            raise RuntimeError(
                "No runtime-compatible knobs remain after pg_settings compatibility pruning."
            )

        LOGGER.debug(
            "➤ Continuing with %d runtime-compatible knobs", len(self.knob_space)
        )

    def _create_workload_executor(
        self, workload_type: WorkloadType, workload_file: Optional[str] = None
    ) -> WorkloadExecutor:
        """
        Create appropriate workload executor based on workload type.

        Parameters
        ----------
        workload_type : WorkloadType
            Type of workload (OLTP, OLAP, MIXED)
        workload_file : Optional[str]
            Path to custom workload file. If provided, creates CustomQueryExecutor
            from file and ignores workload_type.

        Returns
        -------
        WorkloadExecutor
            Configured workload executor
        """
        if workload_file:
            LOGGER.debug(" Loading custom workload from file: %s", workload_file)
            return WorkloadFileLoader.load_from_file(workload_file)

        # Standard workload templates map
        template_map = {
            WorkloadType.OLTP: "workloads/oltp.json",
            WorkloadType.OLAP: "workloads/olap.json",
            WorkloadType.MIXED: "workloads/mixed.json",
        }

        template_file = template_map.get(workload_type)
        if template_file:
            LOGGER.debug(" Loading standard workload template from %s", template_file)
            return WorkloadFileLoader.load_from_file(template_file)

        # Fallback (should not be reached if Enum is exhaustive)
        raise ValueError(f"Unknown workload type: {workload_type}")

    def evaluate_worker(
        self,
        worker: Worker,
        *,
        barriers: Optional[GenerationBarrier] = None,
    ) -> Tuple[PerformanceMetrics, float]:
        """
        Evaluate a single worker.

        This is the evaluation function passed to Population.

        Parameters
        ----------
        worker : Worker
            Worker to evaluate
        barriers : GenerationBarrier | None
            Optional lockstep barriers for synchronized evaluation.

        Returns
        -------
        Tuple[PerformanceMetrics, float]
            (metrics, score)
        """

        try:
            worker.logger.info(
                "Evaluating configuration on instance port %d...", worker.port or 0
            )
            self.orchestrator.worker_id = f"Worker-{worker.worker_id}"

            metrics, score, restart_occurred, _actual_db_config = (
                self.orchestrator.evaluate_worker(
                    worker,
                    apply_config=True,
                    generation=self.current_generation,
                    barriers=barriers,
                )
            )

            if restart_occurred and not self._restarted_this_generation:
                self.restart_count += 1
                self._restarted_this_generation = True

            return metrics, score

        except (ConnectionError, psycopg2.Error) as e:
            # Safety: drain any remaining barriers not already drained by orchestrator
            if barriers is not None:
                barriers.drain_remaining("connected", worker_id=worker.worker_id)

            recovered = False
            if self.env is None:
                worker.logger.error(
                    " ➤ No environment available for immediate recovery"
                )
            else:
                try:
                    recovered = self.env.recover_instance(worker.worker_id)
                except (ConnectionError, RuntimeError, OSError) as recovery_error:
                    worker.logger.error(
                        " ➤ Immediate recovery raised an unexpected error: %s",
                        recovery_error,
                        exc_info=True,
                    )

            if recovered:
                worker.logger.debug(
                    " ➤ Immediate instance recovery succeeded after connection failure"
                )
            else:
                worker.logger.error(
                    " ➤ Immediate instance recovery failed after connection failure"
                )

            return self._build_failure_result(
                worker=worker,
                worker_logger=worker.logger,
                reason="connection",
                exception=e,
                failure_type="crash_dead",
                score=self.pbt_config.dead_config_score,
            )

        except TimeoutError as e:
            if barriers is not None:
                barriers.drain_remaining("connected", worker_id=worker.worker_id)
            return self._build_failure_result(
                worker=worker,
                worker_logger=worker.logger,
                reason="timeout",
                exception=e,
                failure_type="crash_timeout",
                score=self.pbt_config.crash_score,
            )

        except RuntimeError as e:
            if barriers is not None:
                barriers.drain_remaining("connected", worker_id=worker.worker_id)
            return self._build_failure_result(
                worker=worker,
                worker_logger=worker.logger,
                reason="runtime",
                exception=e,
                failure_type="crash_runtime",
                score=self.pbt_config.crash_score,
            )

        except Exception as e:
            if barriers is not None:
                barriers.drain_remaining("connected", worker_id=worker.worker_id)
            worker.logger.error(
                "Unexpected error evaluating worker %s: %s",
                worker.worker_id,
                e,
                exc_info=True,
            )
            return self._build_failure_result(
                worker=worker,
                worker_logger=worker.logger,
                reason="unexpected",
                exception=e,
                failure_type="crash_unexpected",
                score=self.pbt_config.crash_score,
            )

    def _build_failure_result(
        self,
        worker: Worker,
        worker_logger: logging.Logger,
        reason: str,
        exception: Exception,
        failure_type: str,
        score: float,
    ) -> Tuple[PerformanceMetrics, float]:
        """Build standardized fallback metrics and score for failed worker evaluations."""
        worker_logger.warning("➤ Evaluation failed (%s): %s", reason, exception)
        fallback_metrics = PerformanceMetrics(
            latency_p50=9999.0,
            latency_p95=9999.0,
            latency_p99=9999.0,
            throughput=0.0,
            memory_utilization=1.0,
            io_read_mb=0.0,
            io_write_mb=0.0,
            cache_hit_ratio=0.0,
            error_rate=1.0,
            total_queries=0,
            total_time=1.0,
            failure_type=failure_type,
        )
        engine = self.orchestrator.scorer
        worker.score_breakdown = engine.compute_breakdown(
            fallback_metrics, worker_logger=worker_logger
        )
        return fallback_metrics, score

    def run_generation(self, generation: int) -> Dict[str, Any]:
        """
        Run a single PBT generation.

        Parameters
        ----------
        generation : int
            Generation number

        Returns
        -------
        Dict[str, Any]
            Generation results
        """
        log_section_header(
            LOGGER, "%sGENERATION %d%s", COLORS.bold, generation, COLORS.reset
        )

        self.orchestrator.scorer.log_generation_weights(generation=generation)

        self.current_generation = generation
        self._restarted_this_generation = False

        gen_start_time = time.time()
        generation_result = self.population.train_generation(
            self.evaluate_worker,
            parallel=True,
            require_ready=True,
            max_workers=self.pbt_config.num_parallel_workers,
            synchronize_workers=self.pbt_config.synchronize_workers,
        )
        gen_elapsed_time = time.time() - gen_start_time

        if self.population.generations_without_improvement == 0:
            LOGGER.info(
                "%s🔺 NEW BEST SCORE: %s%.4f%%%s",
                COLORS.bold,
                COLORS.teal,
                self.best_score,
                COLORS.reset,
            )

        engine = self.orchestrator.scorer
        gen_summary = {
            **generation_result.to_dict(),
            "restart_count": self.restart_count,
            "timestamp": datetime.now().isoformat(),
            "wall_clock_seconds": time.time() - self.start_time,
            "generation_elapsed_seconds": gen_elapsed_time,
            "worker_scores": [
                {
                    "worker_id": w.worker_id,
                    "score": (
                        float(w.performance_score)
                        if w.performance_score is not None
                        else None
                    ),
                    "metrics": w.metrics.to_dict() if w.metrics else None,
                    "score_breakdown": (
                        convert_numpy_types(w.score_breakdown.to_dict())
                        if w.score_breakdown is not None
                        else (
                            convert_numpy_types(
                                engine.compute_breakdown(
                                    w.metrics, worker_logger=w.logger
                                ).to_dict()
                            )
                            if w.metrics
                            else None
                        )
                    ),
                }
                for w in self.population.workers
            ],
            "worker_configs": [
                {
                    "worker_id": w.worker_id,
                    "config": convert_numpy_types(w.knob_config),
                }
                for w in self.population.workers
            ],
        }

        self.generation_history.append(gen_summary)

        elapsed = time.time() - self.start_time
        log_generation_summary(
            LOGGER,
            elapsed,
            self.restart_count,
            generation=generation_result.generation,
            best_score=generation_result.best_score,
            mean_score=generation_result.mean_score,
            std_score=generation_result.std_score,
            exploited=generation_result.num_exploited,
            converged=generation_result.converged,
        )
        return gen_summary

    def run(self) -> Dict[str, Any]:
        """
        Run the complete PBT tuning process.

        Returns
        -------
        Dict[str, Any]
            Final tuning results
        """
        log_section_header(
            LOGGER,
            "%sPBT PostgreSQL Tuner - Starting Optimization%s",
            COLORS.bold,
            COLORS.reset,
        )
        log_system_info(LOGGER, self.system_info)
        LOGGER.info(
            "Knob Tier:       %s%s (%d knobs)%s",
            COLORS.cyan,
            self.knob_tier,
            len(self.knob_space),
            COLORS.reset,
        )
        LOGGER.info(
            "Population Size: %s%d%s",
            COLORS.cyan,
            self.pbt_config.population_size,
            COLORS.reset,
        )
        LOGGER.info(
            "Max Generations: %s%d%s",
            COLORS.cyan,
            self.pbt_config.num_generations,
            COLORS.reset,
        )
        LOGGER.info(
            "Workload Type:   %s%s%s",
            COLORS.cyan,
            self.workload_type.value,
            COLORS.reset,
        )
        LOGGER.info(
            "Output Dir:      %s%s%s", COLORS.cyan, self.output_dir, COLORS.reset
        )

        self.start_time = time.time()
        try:
            log_section_header(
                LOGGER,
                "%sSetting Up PostgreSQL Instances%s",
                COLORS.bold,
                COLORS.reset,
            )

            try:
                LOGGER.info(
                    "Creating %d PostgreSQL containers (force_recreate=%s)",
                    self.pbt_config.population_size,
                    self.force_recreate_instances,
                )
                instances = self.env.setup_instances(
                    num_workers=self.pbt_config.population_size,
                    force_recreate=self.force_recreate_instances,
                    num_parallel_workers=self.pbt_config.num_parallel_workers,
                )

                LOGGER.info("Verifying instance accessibility and configurations...")
                self.env.verify_instances()

                LOGGER.info("Pruning unsupported knobs based on container version...")
                self._prune_unsupported_runtime_knobs()

                LOGGER.info(
                    "%s%sPostgreSQL instances are ready.%s",
                    COLORS.bold,
                    COLORS.green,
                    COLORS.reset,
                )
            except Exception as e:
                LOGGER.error(
                    "%sFailed to setup instances:%s %s", COLORS.bold, COLORS.reset, e
                )
                raise

            log_section_header(
                LOGGER,
                "%sInitializing PBT population%s",
                COLORS.bold,
                COLORS.reset,
            )
            if self.warm_start_path:
                LOGGER.info("Warm-starting from %s", self.warm_start_path)
                warm_configs = self._build_warm_start_configs(
                    warm_start_path=Path(self.warm_start_path),
                    population_size=self.pbt_config.population_size,
                    seed=42,
                )

                num_lhs = self.pbt_config.population_size - len(warm_configs)
                if num_lhs > 0:
                    lhs_configs = self.full_knob_space.sample_diverse_configs(
                        num_samples=num_lhs, seed=self.random_seed
                    )
                    warm_configs.extend(lhs_configs)

                LOGGER.info(
                    "Initializing %d workers configurations",
                    self.pbt_config.population_size,
                )
                self.population.initialize(
                    initial_configs=warm_configs, random_seed=self.random_seed
                )
            else:
                LOGGER.info(
                    "Initializing %d workers configurations",
                    self.pbt_config.population_size,
                )
                initial_configs = self.full_knob_space.sample_diverse_configs(
                    num_samples=self.pbt_config.population_size, seed=self.random_seed
                )
                self.population.initialize(
                    initial_configs=initial_configs, random_seed=self.random_seed
                )

            LOGGER.info("Assigning instance configurations to workers...")
            self.population.setup_worker_instances(
                instances=instances,
                dbname=self.db_config.dbname,
                user=self.db_config.user,
                password=self.db_config.password,
            )

            LOGGER.info("Configuring snapshot restoration...")
            self.population.setup_snapshots(
                env=self.env,
                pbt_config=self.pbt_config,
            )

            LOGGER.info(
                "%s%sInitialized %d workers with dedicated instances.%s",
                COLORS.bold,
                COLORS.green,
                len(self.population.workers),
                COLORS.reset,
            )

            try:
                for generation in range(self.pbt_config.num_generations):
                    self.run_generation(generation)

                    LOGGER.info(
                        "Checking stopping criteria after generation %d...", generation
                    )
                    if self.population.should_stop():
                        break

                    LOGGER.info(
                        "Saving intermediate results after generation %d...", generation
                    )
                    if (generation + 1) % 5 == 0:
                        self.save_intermediate_results(generation)

            except KeyboardInterrupt:
                LOGGER.info(
                    "%s%sInterrupted by user. Saving results...%s",
                    COLORS.bold,
                    COLORS.warning,
                    COLORS.reset,
                )

            except (RuntimeError, ValueError) as e:
                LOGGER.error(
                    "%sError during training: %s%s", COLORS.bold, COLORS.reset, e
                )
                LOGGER.debug(" Exception details:", exc_info=True)

        finally:
            try:
                self.env.stop_all()
            except (RuntimeError, ValueError, ConnectionError, OSError) as e:
                LOGGER.warning(
                    "%sFailed to stop PostgreSQL instances cleanly:%s %s",
                    COLORS.warning,
                    COLORS.reset,
                    e,
                )

            if self.cleanup_instances:
                try:
                    self.env.cleanup(remove_data=True)
                except (RuntimeError, ValueError, ConnectionError, OSError) as e:
                    LOGGER.warning(
                        "%sFailed to clean up instance data:%s %s",
                        COLORS.warning,
                        COLORS.reset,
                        e,
                    )

        total_time = time.time() - self.start_time

        LOGGER.info("Saving final results to output directory...")
        results = self.save_final_results(total_time)
        log_final_summary(LOGGER, results)

        return results

    def _build_scoring_payload(
        self,
        metrics: Optional[PerformanceMetrics],
        score_breakdown: Optional[ScoreBreakdown] = None,
    ) -> Dict[str, Any]:
        """Build score metadata payload persisted into tuning artifacts."""
        score_breakdown_payload: Dict[str, Any] = {}
        if score_breakdown is not None:
            score_breakdown_payload = convert_numpy_types(score_breakdown.to_dict())
        elif metrics is not None:
            LOGGER.debug(
                "  %sComputing detailed score breakdown...%s",
                COLORS.italic,
                COLORS.reset,
            )
            score_breakdown_payload = convert_numpy_types(
                self.orchestrator.scorer.compute_breakdown(metrics).to_dict()
            )

        scoring_metadata = convert_numpy_types(
            self.metric_config.get_scoring_metadata()
        )
        return {
            "scoring_policy": scoring_metadata.get("scoring_policy", "fixed_v1"),
            "scoring_policy_version": scoring_metadata.get(
                "scoring_policy_version", "1.0"
            ),
            "metric_reference_version": scoring_metadata.get(
                "metric_reference_version", "v1"
            ),
            "workload_features": scoring_metadata.get("workload_features", {}),
            "normalization_metadata": scoring_metadata.get(
                "normalization_metadata", {}
            ),
            "score_breakdown": score_breakdown_payload,
        }

    def save_intermediate_results(self, generation: int):
        """Save intermediate results during training"""
        interim_output_dir = (
            self.output_dir / "intermediate_generations" / f"session_{self.timestamp}"
        )
        interim_output_dir.mkdir(parents=True, exist_ok=True)
        filename = interim_output_dir / f"intermediate_gen{generation}.json"

        results = {
            "generation": generation,
            "best_score": float(self.best_score) if self.best_score else 0.0,
            "best_config": convert_numpy_types(
                self.full_knob_space.config_to_fractions(self.best_config)
                if self.best_config
                else {}
            ),
            "elapsed_time": time.time() - self.start_time,
        }

        scoring_payload = self._build_scoring_payload(
            metrics=self.population.best_overall_metrics,
            score_breakdown=self.population.best_overall_score_breakdown,
        )
        results.update(scoring_payload)

        with open(filename, "w", encoding="utf-8") as f:
            json.dump(results, f, indent=2)

        LOGGER.debug("%sSaved intermediate result.%s", COLORS.italic, COLORS.reset)

    def save_final_results(self, total_time: float) -> Dict[str, Any]:
        """Save final tuning results"""
        best_metrics = self.population.best_overall_metrics
        worker_resources = self.worker_resources

        LOGGER.debug(" Building scoring payload for final results...")
        scoring_payload = self._build_scoring_payload(
            metrics=best_metrics,
            score_breakdown=self.population.best_overall_score_breakdown,
        )

        results = {
            "tuning_session": {
                "knob_tier": self.knob_tier,
                "num_knobs": len(self.full_knob_space),
                "workload_type": self.workload_type.value,
                "benchmark_name": self.benchmark_name,
                "tpch_scale_factor": self.pbt_config.benchmark_config.scale_factor,
                "tpch_warmup_passes": self.pbt_config.benchmark_config.warmup_passes,
                "sysbench_tables": self.pbt_config.benchmark_config.sysbench_tables,
                "sysbench_table_size": self.pbt_config.benchmark_config.sysbench_table_size,
                "sysbench_workload": self.pbt_config.benchmark_config.sysbench_workload,
                "sysbench_duration_seconds": self.pbt_config.benchmark_config.evaluation_duration,
                "sysbench_warmup_seconds": self.pbt_config.benchmark_config.warmup_duration,
                "population_size": self.pbt_config.population_size,
                "num_parallel_workers": self.pbt_config.num_parallel_workers,
                "exploit_quantile": self.pbt_config.exploit_quantile,
                "perturbation_factors": self.pbt_config.perturbation_factors,
                "ready_interval": self.pbt_config.ready_interval,
                "dead_config_threshold": self.pbt_config.dead_config_threshold,
                "seed": self.random_seed,
                "total_generations": self.population.current_generation,
                "total_time_seconds": total_time,
                "timestamp": self.timestamp,
                "tuning_mode": self.pbt_config.benchmark_config.tuning_mode.value,
                "adaptive_restart_interval": self.pbt_config.benchmark_config.adaptive_restart_interval,
                "scoring_policy": scoring_payload["scoring_policy"],
                "scoring_policy_version": scoring_payload["scoring_policy_version"],
                "metric_reference_version": scoring_payload["metric_reference_version"],
            },
            "best_configuration": {
                "score": float(self.best_score) if self.best_score else 0.0,
                "knobs": convert_numpy_types(
                    self.full_knob_space.config_to_fractions(self.best_config)
                    if self.best_config
                    else {}
                ),
                "metrics": convert_numpy_types(
                    best_metrics.to_dict() if best_metrics else {}
                ),
            },
            "worker_resources": {
                "ram_bytes": worker_resources.ram_bytes,  # type: ignore
                "cpu_cores": worker_resources.cpu_cores,  # type: ignore
                "disk_type": worker_resources.disk_type,  # type: ignore
            },
            "warm_start": self.warm_start_provenance,
            "generation_history": convert_numpy_types(self.generation_history),
            "convergence": {
                "converged": bool(self.population.history[-1].converged)
                if self.population.history
                else False,
                "generations_without_improvement": int(
                    self.population.generations_without_improvement
                ),
            },
            "system_info": self.system_info,
            "scoring_policy": scoring_payload["scoring_policy"],
            "scoring_policy_version": scoring_payload["scoring_policy_version"],
            "metric_reference_version": scoring_payload["metric_reference_version"],
            "workload_features": scoring_payload["workload_features"],
            "normalization_metadata": scoring_payload["normalization_metadata"],
            "score_breakdown": scoring_payload["score_breakdown"],
        }

        tuning_output_dir = self.output_dir / "tuning_sessions"
        tuning_output_dir.mkdir(parents=True, exist_ok=True)
        json_file = tuning_output_dir / f"pbt_results_{self.timestamp}.json"

        with open(json_file, "w", encoding="utf-8") as f:
            json.dump(results, f, indent=2)
        LOGGER.info("%sSaved results to %s%s", COLORS.bold, json_file, COLORS.reset)

        best_config_output_dir = self.output_dir / "best_configs"
        best_config_output_dir.mkdir(parents=True, exist_ok=True)
        best_config_file = best_config_output_dir / f"best_config_{self.timestamp}.json"

        with open(best_config_file, "w", encoding="utf-8") as f:
            json.dump(
                convert_numpy_types(
                    self.full_knob_space.config_to_fractions(self.best_config)
                    if self.best_config
                    else {}
                ),
                f,
                indent=2,
            )
        LOGGER.info(
            "%sSaved best config to %s%s", COLORS.bold, best_config_file, COLORS.reset
        )

        return results

    def _compute_warm_start_perturbation_factors(
        self,
        num_variants: int,
    ) -> List[Tuple[float, float]]:
        """Compute graduated perturbation factors for warm-start variants."""
        if num_variants == 0:
            return []
        if num_variants == 1:
            return [(0.65, 1.35)]
        factors = []
        for i in range(num_variants):
            t = i / (num_variants - 1)
            spread = 0.20 + t * 0.30
            factors.append((round(1.0 - spread, 4), round(1.0 + spread, 4)))
        return factors

    def _build_warm_start_configs(
        self,
        warm_start_path: Path,
        population_size: int,
        seed: int,
    ) -> List[Dict[str, Any]]:
        """Build initial configs from a previous warm-start artifact.

        Accepts either:
        - ``best_config_*.json`` (flat mapping: knob -> fraction)
        - ``pbt_results_*.json`` (nested at ``best_configuration.knobs``)
        """
        with open(warm_start_path, "r", encoding="utf-8") as f:
            warm_start_data = json.load(f)

        if not isinstance(warm_start_data, dict):
            raise ValueError(
                "Warm-start file must be a JSON object containing knob fractions"
            )

        best_config_frac: Dict[str, Any]
        if "best_configuration" in warm_start_data:
            best_configuration = warm_start_data.get("best_configuration")
            if not isinstance(best_configuration, dict):
                raise ValueError(
                    "Warm-start tuning session file has invalid best_configuration block"
                )

            knobs = best_configuration.get("knobs")
            if not isinstance(knobs, dict):
                raise ValueError(
                    "Warm-start tuning session file is missing best_configuration.knobs"
                )

            LOGGER.debug(
                " Warm-start source detected as tuning session output; using best_configuration.knobs"
            )
            best_config_frac = knobs
        else:
            best_config_frac = warm_start_data

        for knob_name, knob_val in best_config_frac.items():
            if knob_name in self.full_knob_space.knobs:
                knob = self.full_knob_space.knobs[knob_name]
                if knob.hardware_relative and knob.resource_type != "disk_type":
                    # Compute the RAW (unclamped) absolute value to detect
                    # whether the fraction is actually an absolute value.
                    # We cannot use fractions_to_config() because it clamps
                    # via normalize_value(), silently hiding overflows.
                    resources = self.full_knob_space.worker_resources
                    raw_abs = None
                    if resources is not None:
                        if knob.resource_type == "ram":
                            bytes_per_unit = self.full_knob_space._get_bytes_per_unit(
                                knob
                            )
                            raw_abs = (knob_val * resources.ram_bytes) / bytes_per_unit
                        elif knob.resource_type == "cpu":
                            raw_abs = knob_val * resources.cpu_cores

                    if raw_abs is not None and knob.max_value is not None:
                        if raw_abs > knob.max_value * 1.05:  # 5% tolerance for rounding
                            raise ValueError(
                                f"Warm-start config contains absolute value for "
                                f"hardware-relative knob {knob_name}. "
                                f"Fraction {knob_val} resolves to {raw_abs:.0f}, "
                                f"which exceeds max {knob.max_value}."
                            )

        base_config = self.full_knob_space.fractions_to_config(best_config_frac)

        missing_knobs = [k for k in self.full_knob_space.knobs if k not in base_config]
        if missing_knobs:
            LOGGER.warning(
                " Warm-start config missing knobs, filling in with random values: %s",
                missing_knobs,
            )
            template = self.full_knob_space.sample_random_config(seed=seed)
            for k in missing_knobs:
                base_config[k] = template[k]

        dropped_knobs = [k for k in base_config if k not in self.full_knob_space.knobs]
        if dropped_knobs:
            LOGGER.warning(" Warm-start config dropping extra knobs: %s", dropped_knobs)
            for k in dropped_knobs:
                del base_config[k]

        is_valid, errors = self.full_knob_space.validate_config(base_config)
        if not is_valid:
            LOGGER.warning(
                " Warm-start base config validation issues: %s. "
                "Attempting to repair dependencies.",
                errors,
            )
        base_config = self.full_knob_space.repair_config_dependencies(
            base_config, worker_id=0
        )

        num_warm_start = math.ceil(population_size / 2)

        warm_configs = [base_config]
        factors = self._compute_warm_start_perturbation_factors(num_warm_start - 1)

        for i, (f_min, f_max) in enumerate(factors):
            perturbed = self.full_knob_space.perturb_config(
                base_config,
                perturbation_factor=(f_min, f_max),
                seed=seed + i,
            )
            warm_configs.append(perturbed)

        LOGGER.debug("➤ Built warm start configs")
        self.warm_start_provenance = {
            "enabled": True,
            "source_path": str(warm_start_path),
            "num_warm_start_workers": num_warm_start,
            "num_lhs_workers": population_size - num_warm_start,
            "perturbation_factors": factors,
        }

        return warm_configs


def parse_args():
    """Parse command-line arguments"""
    parser = argparse.ArgumentParser(
        description="PBT PostgreSQL Configuration Tuner",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
Examples:
  # Quick test with minimal knobs (2-3 minutes)
  python -m src.tuner.main --tier minimal --config rapid
  
  # Standard tuning session (20-30 minutes)
  python -m src.tuner.main --tier core --config standard
  
  # Comprehensive tuning (45-60 minutes)
  python -m src.tuner.main --tier standard --config thorough
  
  # Custom configuration
  python -m src.tuner.main --tier minimal --population 8 --generations 50

Keep in mind that actual execution time varies significantly based
on your hardware, configuration, and workload/benchmark.
        """,
    )

    config_group = parser.add_argument_group("PBT Configuration")
    config_group.add_argument(
        "--tier",
        type=str,
        default="minimal",
        choices=["minimal", "core", "standard", "extensive"],
        help="Knob space tier (default: minimal)",
    )

    config_group.add_argument(
        "--warm-start",
        type=str,
        metavar="PATH",
        help="Path to saved configs from a previous run for warm-starting",
    )

    config_group.add_argument(
        "--config",
        type=str,
        default="standard",
        choices=["rapid", "standard", "thorough", "research", "extreme"],
        help="PBT configuration profile (default: standard)",
    )

    config_group.add_argument(
        "--random-seed",
        type=int,
        default=42,
        help="Global random seed for reproducible tuning",
    )

    config_group.add_argument(
        "--population", type=int, help="Population size (overrides config)"
    )

    config_group.add_argument(
        "--generations", type=int, help="Number of generations (overrides config)"
    )

    config_group.add_argument(
        "--parallel-workers",
        type=int,
        help="Number of parallel workers (overrides config)",
    )

    config_group.add_argument(
        "--tuning-mode",
        type=str,
        default=None,
        choices=["online", "offline", "adaptive"],
        help=(
            "Tuning mode controlling restart behavior "
            "(default: online). "
            "online = runtime knobs only, no restarts; "
            "offline = all knobs, restart every generation; "
            "adaptive = all knobs, restart every N generations"
        ),
    )

    config_group.add_argument(
        "--disable-early-stopping",
        action="store_true",
        help=(
            "Disable the no-improvement early stop gate "
            "(low-variance convergence and max generations still apply)"
        ),
    )

    config_group.add_argument(
        "--no-sync",
        action="store_true",
        help=(
            "Disable lockstep barrier synchronization between workers. "
            "By default, workers wait at each sub-step so they advance "
            "in lockstep for fair resource sharing."
        ),
    )

    scoring_group = parser.add_argument_group("Scoring & Normalization")
    scoring_group.add_argument(
        "--scoring-policy",
        type=str,
        default=None,
        choices=["fixed_v1", "feature_driven_v2"],
        help="Policy for performance score aggregation (default: falls back to PBT config)",
    )
    scoring_group.add_argument(
        "--scoring-policy-version",
        type=str,
        default=None,
        help="Frozen policy version string for reproducibility (e.g., 'v2.1')",
    )
    scoring_group.add_argument(
        "--metric-reference-version",
        type=str,
        default=None,
        help="Frozen normalizer metadata reference version (e.g., 'v1.0')",
    )
    scoring_group.add_argument(
        "--scoring-calibration-evals",
        type=int,
        default=None,
        help="Number of initial evaluations for normalizer calibration (default: 5)",
    )

    workload_group = parser.add_argument_group("Workload Settings")
    workload_exclusive = workload_group.add_mutually_exclusive_group()
    workload_exclusive.add_argument(
        "--workload",
        type=str,
        default="oltp",
        choices=["oltp", "olap", "mixed"],
        help="Workload type (default: oltp)",
    )

    workload_exclusive.add_argument(
        "--workload-file",
        type=str,
        help="Path to custom workload file (JSON/YAML). Overrides --workload.",
    )

    workload_exclusive.add_argument(
        "--benchmark",
        type=str,
        default="sysbench",
        choices=["sysbench", "tpch"],
        help="Run standard external benchmark (sysbench=OLTP, tpch=OLAP)",
    )

    workload_group.add_argument(
        "--duration",
        type=float,
        help="Evaluation duration in seconds per worker (overrides config)",
    )

    workload_group.add_argument(
        "--warmup",
        type=float,
        help="Warmup duration in seconds before measurement (overrides config)",
    )

    workload_group.add_argument(
        "--scale-factor",
        type=float,
        default=None,
        help="TPC-H scale factor (default: falls back to active PBT config tier parameter). "
        "Only used with --benchmark tpch",
    )

    workload_group.add_argument(
        "--sysbench-tables",
        type=int,
        default=None,
        help="Number of Sysbench tables (default: falls back to active PBT config tier parameter). "
        "Only used with --benchmark sysbench",
    )

    workload_group.add_argument(
        "--sysbench-table-size",
        type=int,
        default=None,
        help="Sysbench rows per table (default: falls back to active PBT config tier parameter). "
        "Only used with --benchmark sysbench",
    )

    workload_group.add_argument(
        "--sysbench-workload",
        type=str,
        default=None,
        choices=["oltp_read_only", "oltp_read_write", "oltp_write_only"],
        help=(
            "Sysbench workload profile (default: oltp_read_write). "
            "Only used with --benchmark sysbench"
        ),
    )

    instance_group = parser.add_argument_group("Instance Management")
    instance_group.add_argument(
        "--data-dir",
        type=str,
        default=None,
        help=(
            "Base directory for PostgreSQL instances and snapshots. "
            "Overrides PBT_DATA_ROOT env var. (default: ./.instances)"
        ),
    )

    instance_group.add_argument(
        "--no-docker",
        action="store_true",
        help="Run natively on bare-metal PostgreSQL instead of using Docker",
    )

    instance_group.add_argument(
        "--docker-image",
        type=str,
        default=None,
        help=(
            "Docker image override for PostgreSQL workers "
            "(e.g., postgres:18). If omitted, auto-resolved from host version."
        ),
    )

    instance_group.add_argument(
        "--force-recreate-instances",
        action="store_true",
        help="Force recreation of PostgreSQL instances (default: reuse existing)",
    )

    instance_group.add_argument(
        "--cleanup-instances",
        action="store_true",
        help="Remove PostgreSQL instance data after completion",
    )

    instance_group.add_argument(
        "--skip-schema-init",
        action="store_true",
        help="Skip schema initialization from template database (faster startup)",
    )

    instance_group.add_argument(
        "--force-recreate-baseline",
        action="store_true",
        help="Force recreation of baseline snapshot (default: reuse existing)",
    )

    output_group = parser.add_argument_group("Output & Logging")
    output_group.add_argument(
        "--verbose",
        type=str,
        default="INFO",
        choices=["DEBUG", "INFO", "WARNING", "ERROR", "TRACE"],
        help=(
            "Verbosity level for logging output. Available levels:"
            "  DEBUG   - Debug, Info, Warning, and Error messages | "
            "  INFO    - Info, Warning, and Error messages | "
            "  WARNING - Warning & Errors messages | "
            "  ERROR   - Only Error messages | "
            "  TRACE   - Very detailed trace information"
        ),
    )

    output_group.add_argument(
        "--output-dir",
        type=str,
        default="results",
        help=(
            "Base output directory (default: results). Results are organized into "
            "{output_dir}/oltp/{sysbench_workload}/pbt_runs/{tier}/ for Sysbench "
            "and {output_dir}/{workload}/pbt_runs/{tier}/ for other workloads"
        ),
    )

    output_group.add_argument(
        "--colocate-output",
        action="store_true",
        help="Place results/logs under the data directory instead of the default ./results/ directory",
    )

    output_group.add_argument(
        "--ablation-variable",
        type=str,
        default=None,
        help="Ablation study variable name (e.g., 'population_size')",
    )

    output_group.add_argument(
        "--ablation-value",
        type=str,
        default=None,
        help="Ablation study variable value (e.g., '4')",
    )

    output_group.add_argument(
        "--no-color",
        action="store_true",
        help="Disable ANSI colors in terminal logger output",
    )

    return parser.parse_args()


def main():
    """Main entry point"""
    args = parse_args()
    timestamp = datetime.now().strftime("%Y%m%d_%H%M")

    enable_colors = not args.no_color
    set_colors_enabled(enable_colors)
    print_startup_banner()

    setup_logging(verbosity=args.verbose, show_module=True)

    config_map = {
        "rapid": RAPID_CONFIG,
        "standard": STANDARD_CONFIG,
        "thorough": THOROUGH_CONFIG,
        "research": RESEARCH_CONFIG,
        "extreme": EXTREME_CONFIG,
    }
    base_config = config_map[args.config]

    benchmark_config = replace(
        clone_benchmark_config(base_config.benchmark_config),
        benchmark=args.benchmark,
        workload_type=args.workload,
        workload_file=args.workload_file,
        evaluation_duration=(
            args.duration
            if args.duration is not None
            else base_config.benchmark_config.evaluation_duration
        ),
        warmup_duration=(
            args.warmup
            if args.warmup is not None
            else base_config.benchmark_config.warmup_duration
        ),
        scale_factor=(
            args.scale_factor
            if args.scale_factor is not None
            else base_config.benchmark_config.scale_factor
        ),
        sysbench_tables=(
            args.sysbench_tables
            if args.sysbench_tables is not None
            else base_config.benchmark_config.sysbench_tables
        ),
        sysbench_table_size=(
            args.sysbench_table_size
            if args.sysbench_table_size is not None
            else base_config.benchmark_config.sysbench_table_size
        ),
        sysbench_workload=(
            args.sysbench_workload
            if args.sysbench_workload is not None
            else base_config.benchmark_config.sysbench_workload
        ),
        tuning_mode=(
            args.tuning_mode
            if args.tuning_mode is not None
            else base_config.benchmark_config.tuning_mode
        ),
    )

    pbt_config = replace(
        base_config,
        population_size=(
            args.population
            if args.population is not None
            else base_config.population_size
        ),
        num_generations=(
            args.generations
            if args.generations is not None
            else base_config.num_generations
        ),
        num_parallel_workers=(
            args.parallel_workers
            if args.parallel_workers is not None
            else base_config.num_parallel_workers
        ),
        scoring_policy=(
            args.scoring_policy
            if args.scoring_policy is not None
            else base_config.scoring_policy
        ),
        scoring_policy_version=(
            args.scoring_policy_version
            if args.scoring_policy_version is not None
            else base_config.scoring_policy_version
        ),
        metric_reference_version=(
            args.metric_reference_version
            if args.metric_reference_version is not None
            else base_config.metric_reference_version
        ),
        scoring_calibration_evals=(
            args.scoring_calibration_evals
            if args.scoring_calibration_evals is not None
            else base_config.scoring_calibration_evals
        ),
        synchronize_workers=not args.no_sync,
        benchmark_config=benchmark_config,
    )

    workload_type = {
        "oltp": WorkloadType.OLTP,
        "olap": WorkloadType.OLAP,
        "mixed": WorkloadType.MIXED,
    }[args.workload]

    workload_type = {
        "tpch": WorkloadType.OLAP,
        "sysbench": WorkloadType.OLTP,
    }.get(args.benchmark, workload_type)

    # Resolve data root and potentially adjust output dir
    data_root = resolve_data_root(cli_override=args.data_dir)
    base_output_dir = (
        data_root / "results" if args.colocate_output else Path(args.output_dir)
    )

    output_file = resolve_output_file_path(
        base_output_dir=base_output_dir,
        benchmark=benchmark_config.benchmark or "custom",
        tier=args.tier,
        timestamp=timestamp,
        sysbench_workload=benchmark_config.sysbench_workload,
        workload=getattr(benchmark_config, "workload", None),
    )

    add_html_file_logging(output_file=output_file, show_module=True)

    LOGGER.debug(
        "%sLogging to HTML file: %s%s",
        COLORS.italic,
        output_file,
        COLORS.reset,
    )

    try:
        tuner = PBTTuner(
            knob_tier=args.tier,
            pbt_config=pbt_config,
            benchmark=args.benchmark,
            workload_type=workload_type,
            workload_file=args.workload_file,
            random_seed=args.random_seed,
            force_recreate_instances=args.force_recreate_instances,
            force_recreate_baseline=args.force_recreate_baseline,
            cleanup_instances=args.cleanup_instances,
            warm_start_path=args.warm_start,
            skip_schema_init=args.skip_schema_init,
            output_dir=str(base_output_dir),
            logger=LOGGER,
            timestamp=timestamp,
            no_docker=args.no_docker,
            docker_image=args.docker_image,
            data_root=data_root,
            disable_early_stopping=args.disable_early_stopping,
            ablation_variable=args.ablation_variable,
            ablation_value=args.ablation_value,
        )

        tuner.run()

        LOGGER.info(
            "%sOutput logs are available at: %s%s",
            COLORS.italic,
            output_file.resolve(),
            COLORS.reset,
        )
        LOGGER.info(
            "%s%sTuning completed successfully!%s",
            COLORS.bold,
            COLORS.green,
            COLORS.reset,
        )

        return 0

    except (RuntimeError, ValueError, ConnectionError) as e:
        LOGGER.error("🔴 Fatal error: %s", e)
        LOGGER.debug("Exception details:", exc_info=True)
        return 1


if __name__ == "__main__":
    sys.exit(main())
