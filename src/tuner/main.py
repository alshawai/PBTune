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
import time
import logging
from pathlib import Path
from typing import Optional, Dict, Any, Tuple
from datetime import datetime
import numpy as np

from src.database.connection import get_connection
from src.config.database import DatabaseConfig
from src.scripts.setup_database import setup_sysbench_table
from src.tuner.config import (
    get_knob_space,
    PBTConfig,
    RAPID_CONFIG,
    STANDARD_CONFIG,
    THOROUGH_CONFIG,
)
from src.tuner.core.population import Population, PopulationConfig
from src.tuner.core.evolution import get_best_worker
from src.tuner.core.worker import Worker
from src.tuner.evaluator.evaluator import (
    Evaluator,
    EvaluatorConfig,
    SysbenchOLTPExecutor,
    TPCHOLAPExecutor,
    CustomQueryExecutor,
    WorkloadExecutor,
    WorkloadFileLoader,
)
from src.tuner.evaluator.metrics import (
    PerformanceMetrics,
    WorkloadType,
    MetricConfig,
)
from src.tuner.utils.logger_config import (
    setup_logging,
    get_logger,
    log_section_header,
    log_generation_summary
)
from src.tuner.utils.instance_manager import PostgresInstanceManager


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
        workload_type: WorkloadType = WorkloadType.OLTP,
        output_dir: str = "results",
        workload_file: Optional[str] = None,
        force_recreate_instances: bool = False,
        cleanup_instances: bool = False,
        skip_schema_init: bool = False,
        logger: Optional[logging.Logger] = None,
    ):
        """
        Initialize PBT Tuner.
        
        Parameters
        ----------
        knob_tier : str
            Knob space tier: 'minimal', 'core', 'standard', 'extensive'
        pbt_config : Optional[PBTConfig]
            PBT hyperparameters. If None, uses STANDARD_CONFIG
        workload_type : WorkloadType
            Workload type for optimization
        output_dir : str
            Directory for saving results
        workload_file : Optional[str]
            Path to custom workload file (JSON/YAML). If provided, overrides workload_type.
        force_recreate_instances : bool
            Force recreation of PostgreSQL instances
        cleanup_instances : bool
            Remove instance data after completion
        skip_schema_init : bool
            Skip schema initialization from template database
        """
        self.knob_tier = knob_tier
        self.pbt_config = pbt_config or STANDARD_CONFIG
        self.workload_type = workload_type
        self.workload_file = workload_file
        self.output_dir = Path(output_dir)
        self.output_dir.mkdir(parents=True, exist_ok=True)
        self.force_recreate_instances = force_recreate_instances
        self.cleanup_instances = cleanup_instances
        self.skip_schema_init = skip_schema_init
        self.logger = logger or get_logger(__name__)

        self.logger.debug("Loading knob space: %s", knob_tier)
        self.knob_space = get_knob_space(knob_tier)
        self.logger.debug("✓ Loaded %d knobs", len(self.knob_space))
        self.logger.debug(
            "Initializing population with %d workers",
            self.pbt_config.population_size
        )

        pop_config = PopulationConfig(
            population_size=self.pbt_config.population_size,
            ready_interval=self.pbt_config.ready_interval,
            exploit_quantile=self.pbt_config.exploit_quantile,
            perturbation_factors=self.pbt_config.perturbation_factors,
            convergence_threshold=0.05,
            max_generations=self.pbt_config.num_generations,
            early_stopping_patience=10,
        )
        # Create population without evaluator initially (will set it after evaluator creation)
        self.population = Population(self.knob_space, pop_config, evaluator=None)

        self.db_config = DatabaseConfig.from_env()

        self.metric_config = MetricConfig(
            workload_type=workload_type,
            weight_latency=0.45,
            weight_throughput=0.35,
            weight_memory=0.10,
            weight_error=0.10,
        )

        self.evaluator_config = EvaluatorConfig(
            workload_type=workload_type,
            metric_config=self.metric_config,
            db_config=self.db_config,
            warmup_queries=self.pbt_config.warmup_queries,
            measurement_duration=self.pbt_config.evaluation_duration,
            cooldown_duration=3.0,
            enable_restart=True,
            restart_interval=10,
        )

        workload_executor = self._create_workload_executor(workload_type, workload_file)
        self.evaluator = Evaluator(self.evaluator_config, workload_executor)

        # Inject evaluator reference into population for proper metric config access
        self.population.evaluator = self.evaluator

        self.instance_manager = PostgresInstanceManager(
            base_dir=Path('./pg_instances'),
            base_port=5440,
            template_db_config=None if skip_schema_init else self.db_config
        )

        self.start_time: Optional[float] = None
        self.best_score: float = 0.0
        self.best_config: Optional[Dict[str, Any]] = None
        self.generation_history = []

        self.current_generation: int = 0
        self.restart_count: int = 0

    def _validate_database_setup(self) -> None:
        """Validate that required database tables exist."""
        try:
            conn = get_connection(config=self.db_config)
            cursor = conn.cursor()

            cursor.execute(
                "SELECT EXISTS (SELECT FROM information_schema.tables "
                "WHERE table_schema = 'public' AND table_name = 'sbtest1')"
            )
            sbtest_exists = cursor.fetchone()[0]  # type: ignore

            cursor.close()
            conn.close()

            if not sbtest_exists and self.workload_type == WorkloadType.OLTP:
                log_section_header(self.logger, "❌ DATABASE SETUP REQUIRED")
                self.logger.error("\nThe 'sbtest1' table does not exist in the database.")
                self.logger.error("This table is required for OLTP workload execution.")

                print("\nYou have two options:")
                print("  1. Create the table now")
                print("  2. Abort and exit the program")
                print()
                response = input("Create sbtest1 table now? (yes/no): ").strip().lower()

                if response in ['yes', 'y', '1']:
                    print("\n📋 Creating sbtest1 table...")
                    setup_sysbench_table()
                    print("✅ Table created successfully! Continuing with optimization...\n")
                else:
                    print("\n🔴 Execution aborted by user.")
                    print("   Run manually: python -m src.scripts.setup_database sysbench")
                    self.logger.warning("Execution aborted by user due to missing sbtest1 table.")
                    sys.exit(1)

        except Exception as e:
            self.logger.warning("Could not validate database setup: %s", e)
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

        if workload_type == WorkloadType.OLTP:
            self.logger.debug("Using SYSBENCH OLTP executor")
            return SysbenchOLTPExecutor(
                table_size=500000,
                num_threads=8,
                read_write_ratio=0.8,  # 80% reads, 20% writes
            )

        elif workload_type == WorkloadType.OLAP:
            self.logger.debug("Using TPC-H OLAP executor")
            return TPCHOLAPExecutor(
                table_size=10000,
                complexity_mix="balanced",  # Mix of simple and complex queries
            )

        else:  # WorkloadType.MIXED
            # MIXED: Combination of OLTP and OLAP queries
            self.logger.debug("Using mixed workload executor")
            mixed_queries = [
                # OLTP-style point queries
                "SELECT * FROM sbtest1 WHERE id = 1000",
                "SELECT k FROM sbtest1 WHERE id = 5000",
                # OLTP-style updates
                "UPDATE sbtest1 SET k = k + 1 WHERE id = 2000",
                # OLAP-style aggregations
                "SELECT COUNT(*), AVG(k) FROM sbtest1 WHERE k > 50000",
                "SELECT k % 100 as bucket, COUNT(*) FROM sbtest1 GROUP BY bucket",
                # OLAP-style range scans
                "SELECT * FROM sbtest1 WHERE k BETWEEN 10000 AND 20000 LIMIT 100",
            ]
            weights = [0.25, 0.25, 0.15, 0.15, 0.1, 0.1]
            return CustomQueryExecutor(queries=mixed_queries, weights=weights)

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
        try:
            self.evaluator.worker_id = f"Worker-{worker.worker_id}"
            worker_logger = get_logger(__name__, worker_id=worker.worker_id)

            metrics, score, restart_occurred = self.evaluator.evaluate_worker(
                worker,
                apply_config=True,
                generation=self.current_generation
            )

            # Track restart occurrence (will be logged once per generation, not per worker)
            if restart_occurred and not hasattr(self, '_restart_logged_this_gen'):
                self._restart_logged_this_gen = True
                self.restart_count += 1

            worker_logger.info(
                "score=%.4f, latency_p95=%.2fms, throughput=%.1f QPS,\n "
                "Memory=%.2f%%, IO Read=%.2f MB, IO Write=%.2f MB, " 
                "Cache Hit=%.1f%%, Error Rate=%.2f%%",
                score,
                metrics.latency_p95,
                metrics.throughput,
                metrics.memory_utilization,
                metrics.io_read_mb,
                metrics.io_write_mb,
                metrics.cache_hit_ratio * 100.0,
                metrics.error_rate
            )

            return metrics, score

        except (ConnectionError, TimeoutError, RuntimeError) as e:
            worker_logger = get_logger(__name__, worker_id=worker.worker_id)
            worker_logger.error("Evaluation failed: %s", e)
            fallback_metrics = PerformanceMetrics(
                latency_p50=1000.0,
                latency_p95=2000.0,
                latency_p99=3000.0,
                throughput=1.0,
                memory_utilization=1.0,
                io_read_mb=0.0,
                io_write_mb=0.0,
                cache_hit_ratio=0.0,
                error_rate=1.0,
                total_queries=0,
                total_time=1.0,
            )
            return fallback_metrics, 0.0

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

        if hasattr(self, '_restart_logged_this_gen'):
            del self._restart_logged_this_gen

        result = self.population.train_generation(
            self.evaluate_worker,
            parallel=True,
            require_ready=True,
            verbose=True
        )

        if hasattr(self, '_restart_logged_this_gen'):
            self.logger.info("🟢 PostgreSQL restarted (total restarts: %d)", self.restart_count)

        if result.best_score > self.best_score:
            self.best_score = result.best_score
            self.best_config = result.best_config.copy()
            self.logger.info("🎉 NEW BEST SCORE: %.4f", self.best_score)

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
        self.logger.info("Knob Tier:       %s (%d knobs)", self.knob_tier, len(self.knob_space))
        self.logger.info("Population Size: %d", self.pbt_config.population_size)
        self.logger.info("Max Generations: %d", self.pbt_config.num_generations)
        self.logger.info("Workload Type:   %s", self.workload_type.value)
        self.logger.info("Output Dir:      %s", self.output_dir)
        self.start_time = time.time()

        log_section_header(self.logger, "Setting Up PostgreSQL Instances")
        self.logger.info("Creating %d PostgreSQL instances for parallel execution...",
                   self.pbt_config.population_size)

        try:
            instances = self.instance_manager.setup_instances(
                num_workers=self.pbt_config.population_size,
                force_recreate=self.force_recreate_instances
            )
            self.logger.info("✓ Created %d instances", len(instances))

            verification = self.instance_manager.verify_instances()
            failed = [wid for wid, status in verification.items() if not status]
            if failed:
                self.logger.error("❌ Failed to verify instances: %s", failed)
                raise RuntimeError(f"Instance verification failed for workers: {failed}")

            self.logger.info("✓ All instances verified and accessible\n")
        except Exception as e:
            self.logger.error("❌ Failed to setup instances: %s", e)
            raise

        self.logger.debug("Initializing population with random configurations...")
        self.population.initialize()

        self.logger.debug("Assigning instance configurations to workers...")
        self.population.setup_worker_instances(
            instances=instances,
            dbname=self.db_config.dbname,
            user=self.db_config.user,
            password=self.db_config.password
        )
        self.logger.info("✓ Initialized %d workers with dedicated instances\n",
                   len(self.population.workers))

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
            'best_config': convert_numpy_types(self.best_config),
            'elapsed_time': time.time() - self.start_time if self.start_time else 0,
        }

        with open(filename, 'w', encoding='utf-8') as f:
            json.dump(results, f, indent=2)

        self.logger.info("💾 Saved intermediate results to %s", filename)

    def save_final_results(self, total_time: float) -> Dict[str, Any]:
        """Save final tuning results"""
        if self.best_config is None:
            best_config, best_score = self.population.get_best_configuration()
            self.best_config = best_config
            self.best_score = best_score

        best_worker = get_best_worker(self.population.workers)
        best_metrics = best_worker.metrics

        results = {
            'tuning_session': {
                'knob_tier': self.knob_tier,
                'num_knobs': len(self.knob_space),
                'workload_type': self.workload_type.value,
                'population_size': self.pbt_config.population_size,
                'total_generations': self.population.current_generation,
                'total_time_seconds': total_time,
                'timestamp': datetime.now().isoformat(),
            },
            'best_configuration': {
                'score': float(self.best_score) if self.best_score else 0.0,
                'knobs': convert_numpy_types(self.best_config),
            },
            'best_configuration_metrics': convert_numpy_types(
                best_metrics.to_dict() if best_metrics else {}
            ),
            'generation_history': convert_numpy_types(self.generation_history),
            'convergence': {
                'converged': bool(self.population.history[-1].converged)
                if self.population.history else False,
                'generations_without_improvement': int(
                    self.population.generations_without_improvement
                ),
            }
        }

        json_file = self.output_dir / f"pbt_results_{datetime.now().strftime('%Y%m%d_%H%M%S')}.json"

        with open(json_file, 'w', encoding='utf-8') as f:
            json.dump(results, f, indent=2)
        self.logger.info("💾 Saved results to %s", json_file)

        best_config_file = self.output_dir / "best_config.json"

        with open(best_config_file, 'w', encoding='utf-8') as f:
            json.dump(convert_numpy_types(self.best_config), f, indent=2)
        self.logger.info("💾 Saved best config to %s", best_config_file)

        return results

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
  
  # Standard tuning session (10-15 minutes)
  python -m src.tuner.main --tier core --config standard
  
  # Comprehensive tuning (30-60 minutes)
  python -m src.tuner.main --tier standard --config thorough
  
  # Custom configuration
  python -m src.tuner.main --tier minimal --population 8 --generations 50
        """
    )

    parser.add_argument(
        '--tier',
        type=str,
        default='minimal',
        choices=['minimal', 'core', 'standard', 'extensive'],
        help='Knob space tier (default: minimal)'
    )

    parser.add_argument(
        '--config',
        type=str,
        default='standard',
        choices=['rapid', 'standard', 'thorough'],
        help='PBT configuration profile (default: standard)'
    )

    parser.add_argument(
        '--population',
        type=int,
        help='Population size (overrides config)'
    )

    parser.add_argument(
        '--generations',
        type=int,
        help='Number of generations (overrides config)'
    )

    parser.add_argument(
        '--parallel-workers',
        type=int,
        help='Number of parallel workers (overrides config)'
    )

    parser.add_argument(
        '--duration',
        type=float,
        help='Evaluation duration in seconds per worker (overrides config)'
    )

    parser.add_argument(
        '--warmup',
        type=int,
        help='Number of warmup queries before measurement (overrides config)'
    )

    parser.add_argument(
        '--workload',
        type=str,
        default='oltp',
        choices=['oltp', 'olap', 'mixed'],
        help='Workload type (default: oltp)'
    )

    parser.add_argument(
        '--verbose',
        type=str,
        default='NORMAL',
        choices=['QUIET', 'NORMAL', 'VERBOSE', 'TRACE', 'DEBUG', 'INFO', 'WARNING', 'ERROR'],
        help="""
Verbosity level for logging output. Available levels:
  QUIET/WARNING - Warning & Errors messages
  NORMAL/INFO   - Info, Warning, and Error messages
  VERBOSE/DEBUG - Debug, Info, Warning, and Error messages
  TRACE         - Very detailed trace information
  ERROR         - Only Error messages
"""
    )
    parser.add_argument(
        '--workload-file',
        type=str,
        help='Path to custom workload file (JSON/YAML). Overrides --workload.'
    )

    parser.add_argument(
        '--output-dir',
        type=str,
        default='results',
        help='Output directory for results (default: results)'
    )

    parser.add_argument(
        '--force-recreate-instances',
        action='store_true',
        help='Force recreation of PostgreSQL instances (default: reuse existing)'
    )

    parser.add_argument(
        '--cleanup-instances',
        action='store_true',
        help='Remove PostgreSQL instance data after completion'
    )

    parser.add_argument(
        '--skip-schema-init',
        action='store_true',
        help='Skip schema initialization from template database (faster startup)'
    )

    return parser.parse_args()


def main():
    """Main entry point"""
    args = parse_args()

    log_file = Path('results') / 'pbt_tuning.log'
    log_file.parent.mkdir(parents=True, exist_ok=True)

    setup_logging(
        verbosity=args.verbose,
        enable_colors=True,
        show_module=True,
        log_file=str(log_file)
    )
    logger = get_logger(__name__)
    html_file = str(log_file).replace('.log', '.html')
    logger.info("📝 Logging to HTML file: %s (open in browser for colors)", html_file)

    config_map = {
        'rapid': RAPID_CONFIG,
        'standard': STANDARD_CONFIG,
        'thorough': THOROUGH_CONFIG,
    }
    pbt_config = config_map[args.config]

    if args.population or args.generations or args.parallel_workers or args.duration or args.warmup:
        config_dict = pbt_config.to_dict()
        if args.population:
            config_dict['population_size'] = args.population
        if args.generations:
            config_dict['num_generations'] = args.generations
        if args.parallel_workers:
            config_dict['num_parallel_workers'] = args.parallel_workers
        if args.duration:
            config_dict['evaluation_duration'] = args.duration
        if args.warmup:
            config_dict['warmup_queries'] = args.warmup

        pbt_config = PBTConfig(**config_dict)

    workload_map = {
        'oltp': WorkloadType.OLTP,
        'olap': WorkloadType.OLAP,
        'mixed': WorkloadType.MIXED,
    }
    workload_type = workload_map[args.workload]

    try:
        tuner = PBTTuner(
            knob_tier=args.tier,
            pbt_config=pbt_config,
            workload_type=workload_type,
            output_dir=args.output_dir,
            workload_file=args.workload_file,
            force_recreate_instances=args.force_recreate_instances,
            cleanup_instances=args.cleanup_instances,
            skip_schema_init=args.skip_schema_init,
            logger=logger
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
    