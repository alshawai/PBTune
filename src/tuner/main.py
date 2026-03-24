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
from pathlib import Path
from typing import Optional, Dict, Any, Tuple, List
from datetime import datetime
import numpy as np
import psycopg2

from src.config.database import DatabaseConfig
from src.tuner.utils.snapshot_manager import SnapshotManager, SnapshotConfig

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
from src.tuner.evaluator.evaluator import (
    Evaluator,
    EvaluatorConfig,
    WorkloadExecutor,
    WorkloadFileLoader,
)
from src.benchmarks.sysbench.executor import SysbenchExecutor
from src.benchmarks.tpch.executor import TPCHExecutor
from src.tuner.evaluator.metrics import (
    PerformanceMetrics,
    WorkloadType,
    create_metric_config,
)
from src.tuner.utils.logger_config import (
    setup_logging,
    get_logger,
    log_section_header,
    log_generation_summary,
    print_startup_banner,
    ColorCode,
    ColorPalette
)
from src.tuner.utils.instance_manager import PostgresInstanceManager
from src.tuner.utils.hardware_info import get_system_info, log_system_info, detect_worker_resources


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
        **kwargs
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
                    Directory to save results
                - timestamp: str (default: current timestamp)
                    Timestamp for result files (format: YYYYMMDD_HHMM)
                - logger: Optional[logging.Logger] (default: None)
                    Custom logger instance. If None, a default logger is created.
        """
        self.knob_tier = knob_tier
        self.pbt_config = pbt_config or STANDARD_CONFIG

        self.force_recreate_instances = kwargs.get('force_recreate_instances', False)
        self.force_recreate_baseline = kwargs.get('force_recreate_baseline', False)

        self.cleanup_instances = kwargs.get('cleanup_instances', False)

        self.warm_start_path = kwargs.get('warm_start_path', None)
        self.warm_start_provenance = {"enabled": False}

        self.output_dir = Path(kwargs.get('output_dir', "results"))
        self.timestamp = kwargs.get("timestamp", datetime.now().strftime("%Y%m%d_%H%M"))
        self.logger = kwargs.get('logger', get_logger(__name__))

        self.logger.debug("Loading knob space: %s", knob_tier.upper())
        self.knob_space = get_knob_space(knob_tier)

        self.logger.debug("  Detecting hardware resources...")
        worker_resources = detect_worker_resources(self.pbt_config.num_parallel_workers)
        self.knob_space.resolve_hardware_ranges(worker_resources)

        self.logger.debug("✓ Loaded %d knobs\n", len(self.knob_space))

        self.db_config = DatabaseConfig.from_env()

        self.metric_config = create_metric_config(workload_type.value)

        self.evaluator_config = EvaluatorConfig(
            workload_type=workload_type,
            metric_config=self.metric_config,
            db_config=self.db_config,
            warmup_duration=self.pbt_config.warmup_duration,
            measurement_duration=self.pbt_config.evaluation_duration,
            cooldown_duration=3.0,
            enable_restart=True,
            restart_interval=10,
            warmup_passes=self.pbt_config.warmup_passes,
        )

        info_color = ColorPalette.get_level_color('INFO', 'ansi')

        if benchmark == 'sysbench':
            self.benchmark_name = 'sysbench'
            self.workload_type = WorkloadType.OLTP

            self.logger.info(
                "%s%sUsing external Sysbench C-binary for rigorous benchmarking.%s",
                ColorCode.BOLD, info_color, ColorCode.RESET
            )
            workload_executor = SysbenchExecutor(
                tables=self.pbt_config.sysbench_tables,
                table_size=self.pbt_config.sysbench_table_size
            )
            self.snapshot_identifier = (
                f"sysbench_t{self.pbt_config.sysbench_tables}_"
                f"s{self.pbt_config.sysbench_table_size}"
            )

        elif benchmark == 'tpch':
            self.benchmark_name = 'tpch'
            self.workload_type = WorkloadType.OLAP

            self.logger.info(
                "%s%sUsing TPC-H benchmark for analytical workload evaluation.%s",
                ColorCode.BOLD, info_color, ColorCode.RESET
            )
            workload_executor = TPCHExecutor(scale_factor=self.pbt_config.scale_factor)
            self.snapshot_identifier = f"tpch_sf{self.pbt_config.scale_factor}"

            self.logger.debug(
                "💡 TPC-H is a read-only OLAP benchmark; no need for snapshot restorations."
            )
            self.pbt_config.enable_snapshots = False

        else:  # Custom workload (defined by workload_file)
            self.benchmark_name = workload_type.value
            self.workload_type = workload_type

            self.logger.info(
                "%s%sUsing custom workload executor defined in %s.%s",
                ColorCode.BOLD, info_color, workload_file, ColorCode.RESET
            )
            workload_executor = self._create_workload_executor(workload_type, workload_file)
            self.snapshot_identifier = f"{self.benchmark_name}_sf{self.pbt_config.scale_factor}"

        self.evaluator = Evaluator(self.evaluator_config, workload_executor)

        pop_config = PopulationConfig(
            population_size=self.pbt_config.population_size,
            ready_interval=self.pbt_config.ready_interval,
            exploit_quantile=self.pbt_config.exploit_quantile,
            perturbation_factors=self.pbt_config.perturbation_factors,
            convergence_threshold=0.05,
            max_generations=self.pbt_config.num_generations,
            early_stopping_patience=10,
            dead_config_threshold=self.pbt_config.dead_config_threshold,
        )

        self.population = Population(
            self.knob_space,
            pop_config,
            evaluator=self.evaluator
        )

        self.instance_manager = PostgresInstanceManager(
            base_dir=Path(f'./pg_instances/{self.benchmark_name}'),
            base_port=5440,
            template_db_config=None if kwargs.get('skip_schema_init') else self.db_config,
            schema_provider=workload_executor,
        )

        self.system_info = get_system_info()

        self.start_time: Optional[float] = None
        self.generation_history = []

        self.current_generation: int = 0
        self.restart_count: int = 0
        self._restart_logged_this_gen: bool = False
        self._last_logged_best_score: float = -1.0

        self.logger.info(
            "%s%sPBT Database Tuner Initialization Complete!%s",
            ColorCode.BOLD, info_color, ColorCode.RESET
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

    def _create_workload_executor(
        self,
        workload_type: WorkloadType,
        workload_file: Optional[str] = None
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
            self.logger.debug("Loading custom workload from file: %s", workload_file)
            return WorkloadFileLoader.load_from_file(workload_file)

        # Standard workload templates map
        template_map = {
            WorkloadType.OLTP: "workloads/oltp.json",
            WorkloadType.OLAP: "workloads/olap.json",
            WorkloadType.MIXED: "workloads/mixed.json",
        }

        template_file = template_map.get(workload_type)
        if template_file:
            self.logger.debug("Loading standard workload template from %s", template_file)
            return WorkloadFileLoader.load_from_file(template_file)

        # Fallback (should not be reached if Enum is exhaustive)
        raise ValueError(f"Unknown workload type: {workload_type}")

    def evaluate_worker(self, worker: Worker) -> Tuple[PerformanceMetrics, float]:
        """
        Evaluate a single worker.
        
        This is the evaluation function passed to Population.
        
        Parameters
        ----------
        worker : Worker
            Worker to evaluate
            
        Returns
        -------
        Tuple[PerformanceMetrics, float]
            (metrics, score)
        """
        worker_logger = get_logger(__name__, worker_id=worker.worker_id)

        try:
            self.evaluator.worker_id = f"Worker-{worker.worker_id}"

            metrics, score, restart_occurred = self.evaluator.evaluate_worker(
                worker,
                apply_config=True,
                generation=self.current_generation
            )

            # Track restart occurrence (will be logged once per generation, not per worker)
            if restart_occurred and not self._restart_logged_this_gen:
                self._restart_logged_this_gen = True
                self.restart_count += 1

            latency_label = self.metric_config.latency_metric
            latency_value = getattr(metrics, f"latency_{latency_label}", 0.0)

            worker_logger.info(
                "score=%.4f, latency_%s=%.2f%s, throughput=%.1f %s,\n "
                "Memory=%.2f%%, IO Read=%.2f MB, IO Write=%.2f MB, " 
                "Cache Hit=%.1f%%, Error Rate=%.2f%%",
                score,
                latency_label,
                latency_value,
                metrics.latency_unit,
                metrics.throughput,
                metrics.throughput_unit,
                metrics.memory_utilization * 100.0,
                metrics.io_read_mb,
                metrics.io_write_mb,
                metrics.cache_hit_ratio * 100.0,
                metrics.error_rate
            )

            return metrics, score

        except (ConnectionError, psycopg2.Error) as e:
            if self.instance_manager is not None:
                recovered = self.instance_manager.recover_instance(worker.worker_id)
                if recovered:
                    worker_logger.info(
                        "[DEAD_CONFIG] Immediate instance recovery succeeded after connection failure"
                    )
                else:
                    worker_logger.error(
                        "[DEAD_CONFIG] Immediate instance recovery failed after connection failure"
                    )

            return self._build_failure_result(
                worker_logger=worker_logger,
                reason="connection",
                exception=e,
                failure_type="crash_dead",
                score=self.pbt_config.dead_config_score,
            )

        except TimeoutError as e:
            return self._build_failure_result(
                worker_logger=worker_logger,
                reason="timeout",
                exception=e,
                failure_type="crash_timeout",
                score=self.pbt_config.crash_score,
            )

        except RuntimeError as e:
            return self._build_failure_result(
                worker_logger=worker_logger,
                reason="runtime",
                exception=e,
                failure_type="crash_runtime",
                score=self.pbt_config.crash_score,
            )

        except Exception as e:
            worker_logger.error(
                "Unexpected error evaluating worker %s: %s",
                worker.worker_id,
                e,
                exc_info=True
            )
            return self._build_failure_result(
                worker_logger=worker_logger,
                reason="unexpected",
                exception=e,
                failure_type="crash_unexpected",
                score=self.pbt_config.crash_score,
            )

    def _build_failure_result(
        self,
        worker_logger: logging.Logger,
        reason: str,
        exception: Exception,
        failure_type: str,
        score: float,
    ) -> Tuple[PerformanceMetrics, float]:
        """Build standardized fallback metrics and score for failed worker evaluations."""
        worker_logger.warning("[DEAD_CONFIG] Evaluation failed (%s): %s", reason, exception)
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
        log_section_header(self.logger, f"GENERATION {generation}")

        self.current_generation = generation

        self._restart_logged_this_gen = False

        result = self.population.train_generation(
            self.evaluate_worker,
            parallel=True,
            require_ready=True,
            max_workers=self.pbt_config.num_parallel_workers,
        )

        if self._restart_logged_this_gen:
            self.logger.info("🟢 PostgreSQL restarted (total restarts: %d)", self.restart_count)

        # Notify user of new high score conditionally (handles dynamic rescales upwards too)
        _, current_global_best_score = self.population.get_best_configuration()
        if self._last_logged_best_score != current_global_best_score:
            if current_global_best_score > self._last_logged_best_score:
                self.logger.info("🎉 NEW BEST SCORE: %.4f", current_global_best_score)
            self._last_logged_best_score = current_global_best_score

        gen_summary = {
            'generation': generation,
            'best_score': result.best_score,
            'mean_score': result.mean_score,
            'std_score': result.std_score,
            'num_exploited': result.num_exploited,
            'best_worker_id': result.best_worker_id,
            'converged': result.converged,
            'restart_count': self.restart_count,
            'timestamp': datetime.now().isoformat(),
            'worker_scores': [
                {
                    'worker_id': w.worker_id,
                    'score': (
                        float(w.performance_score)
                        if w.performance_score is not None else None
                    ),
                    'metrics': w.metrics.to_dict() if w.metrics else None,
                }
                for w in self.population.workers
            ],
            'worker_configs': [
                {
                    'worker_id': w.worker_id,
                    'config': convert_numpy_types(w.knob_config),
                }
                for w in self.population.workers
            ],
        }

        self.generation_history.append(gen_summary)

        elapsed = time.time() - self.start_time if self.start_time else 0
        log_generation_summary(
            self.logger,
            generation=generation,
            best_score=result.best_score,
            mean_score=result.mean_score,
            std_score=result.std_score,
            exploited=result.num_exploited,
            restarts=self.restart_count,
            elapsed=elapsed,
            converged=result.converged
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
        log_section_header(self.logger, "PBT PostgreSQL Tuner - Starting Optimization")
        log_system_info(self.logger, self.system_info)
        self.logger.info("Knob Tier:       %s (%d knobs)", self.knob_tier, len(self.knob_space))
        self.logger.info("Population Size: %d", self.pbt_config.population_size)
        self.logger.info("Max Generations: %d", self.pbt_config.num_generations)
        self.logger.info("Workload Type:   %s", self.workload_type.value)
        self.logger.info("Output Dir:      %s", self.output_dir)
        self.start_time = time.time()

        log_section_header(self.logger, "Setting Up PostgreSQL Instances")
        self.logger.info(
            "Creating %d PostgreSQL instances for parallel execution...",
            self.pbt_config.population_size
        )

        try:
            baseline_path = Path(
                f'./pg_snapshots/baseline_{self.snapshot_identifier}'
            )
            # Skip snapshot-based init if we're about to recreate the baseline
            if not self.force_recreate_baseline:
                self.instance_manager.baseline_snapshot_path = baseline_path

            instances = self.instance_manager.setup_instances(
                num_workers=self.pbt_config.population_size,
                force_recreate=self.force_recreate_instances
            )
            self.logger.info("✓ Created %d instances", len(instances))

            if self.pbt_config.enable_snapshots:
                self._create_baseline_snapshot(instances)

            verification = self.instance_manager.verify_instances()
            failed = [wid for wid, status in verification.items() if not status]
            if failed:
                self.logger.error("❌ Failed to verify instances: %s", failed)
                raise RuntimeError(f"Instance verification failed for workers: {failed}")

            self.logger.info("✓ All instances verified and accessible\n")
        except Exception as e:
            self.logger.error("❌ Failed to setup instances: %s", e)
            raise

        self.logger.info("Initializing population...")
        if self.warm_start_path:
            self.logger.debug("Warm-starting from %s", self.warm_start_path)
            warm_configs = self._build_warm_start_configs(
                warm_start_path=Path(self.warm_start_path),
                population_size=self.pbt_config.population_size,
                seed=42,
            )
            self.population.initialize(
                initial_configs=warm_configs,
                random_seed=42
            )
        else:
            self.population.initialize(random_seed=42)

        self.logger.debug("Assigning instance configurations to workers...")
        self.population.setup_worker_instances(
            instances=instances,
            dbname=self.db_config.dbname,
            user=self.db_config.user,
            password=self.db_config.password
        )
        self.population.instance_manager = self.instance_manager
        self.logger.info(
            "✓ Initialized %d workers with dedicated instances\n",
            len(self.population.workers)
        )

        # Register snapshot manager with population
        if self.pbt_config.enable_snapshots:
            worker_data_dirs = [
                instances[worker_id].data_dir
                for worker_id in range(self.pbt_config.population_size)
            ]
            self.population.setup_snapshots(
                worker_data_dirs=worker_data_dirs,
                instance_manager=self.instance_manager,
                pbt_config=self.pbt_config,
                baseline_path=Path(
                    f'./pg_snapshots/baseline_{self.snapshot_identifier}'
                )
            )
            self.logger.info("✓ Snapshot restoration configured\n")

        try:
            for generation in range(self.pbt_config.num_generations):
                self.run_generation(generation)

                if self.population.should_stop():
                    reason = self._get_stop_reason()
                    self.logger.info("⚠ Early stopping triggered: %s", reason)
                    break

                if (generation + 1) % 5 == 0:
                    self.save_intermediate_results(generation)

        except KeyboardInterrupt:
            self.logger.info("⚠ Interrupted by user. Saving results...")

        except (RuntimeError, ValueError) as e:
            self.logger.error("\n❌ Error during training: %s", e)
            self.logger.debug("Exception details:", exc_info=True)

        finally:
            self.logger.info("Stopping PostgreSQL instances...")
            self.instance_manager.stop_all()
            self.logger.info("✓ All instances stopped")

            if self.cleanup_instances:
                self.logger.info("Cleaning up instance data...")
                self.instance_manager.cleanup(remove_data=True)
                self.logger.info("✓ Instance data removed")

        total_time = time.time() - self.start_time
        results = self.save_final_results(total_time)
        self.print_final_summary(results)

        return results

    def _create_baseline_snapshot(self, instances: List) -> None:
        """
        Create baseline snapshot from worker_0 before verification.
        
        This is called early in the setup process, right after instances
        are created but before verification and worker assignment. This
        allows us to cleanly stop worker_0 without affecting other workers'
        connections.
        
        Parameters
        ----------
        instances : List[InstanceConfig]
            List of instance configurations from instance_manager
        """
        baseline_path = Path(
            f'./pg_snapshots/baseline_{self.snapshot_identifier}'
        )
        snapshot_config = SnapshotConfig(
            baseline_path=baseline_path,
            restore_interval=getattr(self.pbt_config, 'snapshot_restore_interval', 5)
        )

        snapshot_manager = SnapshotManager(snapshot_config)

        if snapshot_manager.baseline_created and not self.force_recreate_baseline:
            self.logger.info("✓ Using existing baseline snapshot at %s", baseline_path)
            return

        worker_0_data_dir = instances[0].data_dir

        success = snapshot_manager.create_baseline(
            source_path=worker_0_data_dir,
            instance_manager=self.instance_manager,
            worker_id=0,
            force=self.force_recreate_baseline,
            wait_timeout=15.0
        )

        if success:
            self.logger.info("✔️ Baseline snapshot created at %s", baseline_path)
        else:
            self.logger.error("❌ Failed to create baseline snapshot")

    def _get_stop_reason(self) -> str:
        """Get the reason for early stopping"""
        if self.population.current_generation >= self.pbt_config.num_generations:
            return "Maximum generations reached"

        if self.population.generations_without_improvement >= 10:
            return (
                f"No improvement for "
                f" {self.population.generations_without_improvement} generations"
            )
        if self.population.history and self.population.history[-1].converged:
            return "Population converged (low variance)"

        return "Unknown reason"

    def save_intermediate_results(self, generation: int):
        """Save intermediate results during training"""
        filename = self.output_dir / f"intermediate_gen{generation}.json"

        results = {
            'generation': generation,
            'best_score': float(self.best_score) if self.best_score else 0.0,
            'best_config': convert_numpy_types(
                self.knob_space.config_to_fractions(self.best_config)
                if self.best_config else {}
            ),
            'elapsed_time': time.time() - self.start_time if self.start_time else 0,
        }

        with open(filename, 'w', encoding='utf-8') as f:
            json.dump(results, f, indent=2)

        self.logger.info("💾 Saved intermediate results to %s", filename)

    def save_final_results(self, total_time: float) -> Dict[str, Any]:
        """Save final tuning results"""
        best_metrics = self.population.best_overall_metrics
        worker_resources = self.knob_space.worker_resources

        results = {
            'tuning_session': {
                'knob_tier': self.knob_tier,
                'num_knobs': len(self.knob_space),
                'workload_type': self.workload_type.value,
                'benchmark_name': self.benchmark_name,
                'scale_factor': self.pbt_config.scale_factor,
                'sysbench_tables': self.pbt_config.sysbench_tables,
                'sysbench_table_size': self.pbt_config.sysbench_table_size,
                'population_size': self.pbt_config.population_size,
                'total_generations': self.population.current_generation,
                'total_time_seconds': total_time,
                'timestamp': datetime.now().isoformat(),
            },
            'best_configuration': {
                'score': float(self.best_score) if self.best_score else 0.0,
                'knobs': convert_numpy_types(
                    self.knob_space.config_to_fractions(self.best_config)
                    if self.best_config else {}
                ),
                'metrics': convert_numpy_types(
                    best_metrics.to_dict() if best_metrics else {}
                ),
            },
            'worker_resources': {
                'ram_bytes': worker_resources.ram_bytes,  # type: ignore
                'cpu_cores': worker_resources.cpu_cores,  # type: ignore
                'disk_type': worker_resources.disk_type  # type: ignore
            },
            'warm_start': self.warm_start_provenance,
            'generation_history': convert_numpy_types(self.generation_history),
            'convergence': {
                'converged': bool(self.population.history[-1].converged)
                if self.population.history else False,
                'generations_without_improvement': int(
                    self.population.generations_without_improvement
                ),
            },
            'system_info': self.system_info,
        }

        json_file = self.output_dir / f"pbt_results_{datetime.now().strftime('%Y%m%d_%H%M%S')}.json"

        with open(json_file, 'w', encoding='utf-8') as f:
            json.dump(results, f, indent=2)
        self.logger.info("💾 Saved results to %s", json_file)

        best_config_file = self.output_dir / "best_config.json"

        with open(best_config_file, 'w', encoding='utf-8') as f:
            json.dump(
                convert_numpy_types(self.knob_space.config_to_fractions(self.best_config)
                if self.best_config else {}),
                f,
                indent=2
            )
        self.logger.info("💾 Saved best config to %s", best_config_file)

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
        """Build initial configs from a previous best_config.json."""
        with open(Path(self.warm_start_path), 'r', encoding='utf-8') as f:  # type: ignore
            best_config_frac = json.load(f)

        for knob_name, knob_val in best_config_frac.items():
            if knob_name in self.knob_space.knobs:
                knob = self.knob_space.knobs[knob_name]
                if knob.hardware_relative:
                    # `max_worker_processes` is the only fraction with a valid range of [0, 2].
                    # All other hardware-relative knobs must be in [0, 1].
                    frac_max = 2.0 if knob.name == "max_worker_processes" else 1.0
                    if knob_val > frac_max:
                        raise ValueError(
                            "Warm-start config contains absolute value for hardware-"
                            f"relative knob {knob_name}. Expected fraction <= {frac_max}."
                        )

        base_config = self.knob_space.fractions_to_config(best_config_frac)

        missing_knobs = [k for k in self.knob_space.knobs if k not in base_config]
        if missing_knobs:
            self.logger.warning(
                "Warm-start config missing knobs, filling in with random values: %s",
                missing_knobs
            )
            template = self.knob_space.sample_random_config(seed=seed)
            for k in missing_knobs:
                base_config[k] = template[k]

        dropped_knobs = [k for k in base_config if k not in self.knob_space.knobs]
        if dropped_knobs:
            self.logger.warning("Warm-start config dropping extra knobs: %s", dropped_knobs)
            for k in dropped_knobs:
                del base_config[k]

        is_valid, errors = self.knob_space.validate_config(base_config)
        if not is_valid:
            self.logger.warning(
                "Warm-start base config validation issues: %s. "
                "Attempting to repair dependencies.",
                errors
            )
        base_config = self.knob_space.repair_config_dependencies(base_config)

        num_warm_start = math.ceil(population_size / 2)

        warm_configs = [base_config]
        factors = self._compute_warm_start_perturbation_factors(num_warm_start - 1)

        for i, (f_min, f_max) in enumerate(factors):
            perturbed = self.knob_space.perturb_config(
                base_config,
                perturbation_factor=(f_min, f_max),
                seed=seed + i,
            )
            warm_configs.append(perturbed)

        self.warm_start_provenance = {
            "enabled": True,
            "source_path": str(warm_start_path),
            "num_warm_start_workers": num_warm_start,
            "num_lhs_workers": population_size - num_warm_start,
            "perturbation_factors": factors
        }

        return warm_configs

    def print_final_summary(self, results: Dict[str, Any]):
        """Print final summary of tuning session"""
        log_section_header(self.logger, "PBT TUNING COMPLETE")
        session = results['tuning_session']
        best = results['best_configuration']

        self.logger.info("Session Summary:")
        self.logger.info("  Total Generations:  %d", session['total_generations'])
        self.logger.info(
            "  Total Time:         %.1fs (%.1f min)",
            session['total_time_seconds'],
            session['total_time_seconds']/60
        )
        self.logger.info("  Knobs Tuned:        %d", session['num_knobs'])
        self.logger.info("  Workload Type:      %s", session['workload_type'])

        self.logger.info("Best Configuration Found:")
        self.logger.info("  Performance Score:  %.4f", best['score'])

        self.logger.info("Optimized Knobs:")
        for knob_name, value in sorted(best['knobs'].items()):
            self.logger.info("    %-40s = %s", knob_name, value)

        self.logger.info("Results saved to: %s", self.output_dir)


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
        """
    )

    config_group = parser.add_argument_group('PBT Configuration')
    config_group.add_argument(
        '--tier',
        type=str,
        default='minimal',
        choices=['minimal', 'core', 'standard', 'extensive'],
        help='Knob space tier (default: minimal)'
    )

    config_group.add_argument(
        '--warm-start',
        type=str,
        metavar='PATH',
        help='Path to saved configs from a previous run for warm-starting'
    )

    config_group.add_argument(
        '--config',
        type=str,
        default='standard',
        choices=['rapid', 'standard', 'thorough', 'research', 'extreme'],
        help='PBT configuration profile (default: standard)'
    )

    config_group.add_argument(
        '--population',
        type=int,
        help='Population size (overrides config)'
    )

    config_group.add_argument(
        '--generations',
        type=int,
        help='Number of generations (overrides config)'
    )

    config_group.add_argument(
        '--parallel-workers',
        type=int,
        help='Number of parallel workers (overrides config)'
    )

    workload_group = parser.add_argument_group('Workload Settings')
    workload_exclusive = workload_group.add_mutually_exclusive_group()
    workload_exclusive.add_argument(
        '--workload',
        type=str,
        default='oltp',
        choices=['oltp', 'olap', 'mixed'],
        help='Workload type (default: oltp)'
    )

    workload_exclusive.add_argument(
        '--workload-file',
        type=str,
        help='Path to custom workload file (JSON/YAML). Overrides --workload.'
    )

    workload_exclusive.add_argument(
        '--benchmark',
        type=str,
        choices=['sysbench', 'tpch'],
        help='Run standard external benchmark (sysbench=OLTP, tpch=OLAP)'
    )

    workload_group.add_argument(
        '--duration',
        type=float,
        help='Evaluation duration in seconds per worker (overrides config)'
    )

    workload_group.add_argument(
        '--warmup',
        type=float,
        help='Warmup duration in seconds before measurement (overrides config)'
    )

    workload_group.add_argument(
        '--scale-factor',
        type=float,
        default=None,
        help='TPC-H scale factor (default: falls back to active PBT config tier parameter). '
             'Only used with --benchmark tpch'
    )

    workload_group.add_argument(
        '--sysbench-tables',
        type=int,
        default=None,
        help='Number of Sysbench tables (default: falls back to active PBT config tier parameter). '
             'Only used with --benchmark sysbench'
    )

    workload_group.add_argument(
        '--sysbench-table-size',
        type=int,
        default=None,
        help='Sysbench rows per table (default: falls back to active PBT config tier parameter). '
             'Only used with --benchmark sysbench'
    )

    instance_group = parser.add_argument_group('Instance Management')
    instance_group.add_argument(
        '--force-recreate-instances',
        action='store_true',
        help='Force recreation of PostgreSQL instances (default: reuse existing)'
    )

    instance_group.add_argument(
        '--cleanup-instances',
        action='store_true',
        help='Remove PostgreSQL instance data after completion'
    )

    instance_group.add_argument(
        '--skip-schema-init',
        action='store_true',
        help='Skip schema initialization from template database (faster startup)'
    )

    instance_group.add_argument(
        '--force-recreate-baseline',
        action='store_true',
        help='Force recreation of baseline snapshot (default: reuse existing)'
    )

    output_group = parser.add_argument_group('Output & Logging')
    output_group.add_argument(
        '--verbose',
        type=str,
        default='INFO',
        choices=['DEBUG', 'INFO', 'WARNING', 'ERROR', 'TRACE'],
        help=(
"Verbosity level for logging output. Available levels:"
"  DEBUG   - Debug, Info, Warning, and Error messages | "
"  INFO    - Info, Warning, and Error messages | "
"  WARNING - Warning & Errors messages | "
"  ERROR   - Only Error messages | "
"  TRACE   - Very detailed trace information"
        )
    )

    output_group.add_argument(
        '--output-dir',
        type=str,
        default='results',
        help='Output directory for results (default: results)'
    )

    return parser.parse_args()


def main():
    """Main entry point"""
    args = parse_args()

    output_dir = Path(args.output_dir)
    output_dir.mkdir(parents=True, exist_ok=True)

    output_file = output_dir / 'pbt_tuning.html'

    print_startup_banner()

    setup_logging(
        verbosity=args.verbose,
        enable_colors=True,
        show_module=True,
        output_file=output_file
    )
    logger = get_logger(__name__)  # inherits from the root logger (defined in setup_logging)

    info_color = ColorPalette.get_level_color('INFO', 'ansi')
    logger.info(
        "%s%sStarting PBT Database Tuner Initialization...%s",
        ColorCode.BOLD, info_color, ColorCode.RESET
    )

    logger.debug("📝 Logging to HTML file: %s", output_file)

    config_map = {
        'rapid': RAPID_CONFIG,
        'standard': STANDARD_CONFIG,
        'thorough': THOROUGH_CONFIG,
        'research': RESEARCH_CONFIG,
        'extreme': EXTREME_CONFIG,
    }
    pbt_config = config_map[args.config]
    config_dict = pbt_config.to_dict()

    if (
        args.population
        or args.generations
        or args.parallel_workers
        or args.duration
        or args.warmup
        or args.scale_factor
        or args.sysbench_tables
        or args.sysbench_table_size
    ):
        if args.population:
            config_dict['population_size'] = args.population
        if args.generations:
            config_dict['num_generations'] = args.generations
        if args.parallel_workers:
            config_dict['num_parallel_workers'] = args.parallel_workers
        if args.duration:
            config_dict['evaluation_duration'] = args.duration
        if args.warmup:
            config_dict['warmup_duration'] = args.warmup
        if args.scale_factor:
            config_dict['scale_factor'] = args.scale_factor
        if args.sysbench_tables:
            config_dict['sysbench_tables'] = args.sysbench_tables
        if args.sysbench_table_size:
            config_dict['sysbench_table_size'] = args.sysbench_table_size

        pbt_config = PBTConfig(**config_dict)

    workload_map = {
        'oltp': WorkloadType.OLTP,
        'olap': WorkloadType.OLAP,
        'mixed': WorkloadType.MIXED,
    }
    workload_type = workload_map[args.workload]

    if args.benchmark == 'tpch':
        workload_type = WorkloadType.OLAP
    elif args.benchmark == 'sysbench':
        workload_type = WorkloadType.OLTP

    try:
        tuner = PBTTuner(
            knob_tier=args.tier,
            pbt_config=pbt_config,
            benchmark=args.benchmark,
            workload_type=workload_type,
            workload_file=args.workload_file,
            force_recreate_instances=args.force_recreate_instances,
            force_recreate_baseline=args.force_recreate_baseline,
            cleanup_instances=args.cleanup_instances,
            warm_start_path=args.warm_start,
            skip_schema_init=args.skip_schema_init,
            output_dir=args.output_dir,
            logger=logger,
            timestamp=timestamp
        )

        tuner.run()

        logger.info("🟢 Tuning completed successfully!")
        return 0

    except (RuntimeError, ValueError, ConnectionError) as e:
        logger.error("🔴 Fatal error: %s", e)
        logger.debug("Exception details:", exc_info=True)
        return 1


if __name__ == "__main__":
    sys.exit(main())
