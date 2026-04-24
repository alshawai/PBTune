"""
Bayesian Optimization Baseline Runner for PBT Comparison
=========================================================

Provides a controlled BO baseline that reuses the exact same evaluation
pipeline (knob space, scoring formula, workload executor, environment
management) as the PBT tuner, ensuring a fair apples-to-apples comparison.

Usage:
------
# Quick BO baseline with minimal knobs
python -m src.scripts.run_bo_comparison --tier minimal --config rapid

# Standard BO baseline matching PBT setup
python -m src.scripts.run_bo_comparison --tier core --config standard

# Custom BO parameters
python -m src.scripts.run_bo_comparison --tier core --max-evaluations 50 --seed 42

Research Context:
-----------------
BO is the dominant paradigm for DB auto-tuning (OtterTune, LlamaTune, GPTuner).
This runner enables direct comparison of PBT's parallel evolutionary approach
against BO's sequential sample-efficient strategy on identical hardware,
workloads, and scoring rules.

References:
-----------
- Aken et al., 2017. OtterTune (SIGMOD)
- Kanellis et al., 2022. LlamaTune (VLDB)
- Lao et al., 2024. GPTuner (VLDB)
- Lindauer et al., 2022. SMAC3 (JMLR)
"""

from __future__ import annotations

import argparse
import json
import logging
import sys
import time
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

import numpy as np

from src.scripts.bo_optimizer import (
    BOConfig,
    BOOptimizer,
    BOResult,
    build_configspace_from_knob_space,
    configspace_sample_to_knob_config,
)
from src.config.database import get_db_config
from src.tuner.config import (
    get_knob_space,
    PBTConfig,
    RAPID_CONFIG,
    STANDARD_CONFIG,
    THOROUGH_CONFIG,
    RESEARCH_CONFIG,
    EXTREME_CONFIG,
)
from src.tuner.core.worker import Worker
from src.tuner.evaluator.evaluator import Evaluator, EvaluatorConfig, WorkloadExecutor
from src.tuner.evaluator.workload import WorkloadFileLoader
from src.tuner.evaluator.restart_policy import TuningMode
from src.benchmarks.sysbench.executor import SysbenchExecutor
from src.benchmarks.tpch.executor import TPCHExecutor
from src.utils.environments import EnvironmentFactory
from src.utils.metrics import (
    PerformanceMetrics,
    WorkloadType,
    create_metric_config,
)
from src.utils.hardware_info import (
    get_system_info,
    log_system_info,
    detect_worker_resources,
)
from src.utils.logger import (
    setup_logging,
    get_logger,
    log_section_header,
    print_startup_banner,
    ColorCode,
    ColorPalette,
)
from src.utils.rescoring import rescore_metrics_globally


logger = get_logger(__name__)


def _convert_numpy_types(obj: Any) -> Any:
    """Recursively convert numpy types to Python native types for JSON serialization."""
    if isinstance(obj, np.bool_):
        return bool(obj)
    elif isinstance(obj, (np.integer,)):
        return int(obj)
    elif isinstance(obj, (np.floating,)):
        return float(obj)
    elif isinstance(obj, np.ndarray):
        return obj.tolist()
    elif isinstance(obj, dict):
        return {key: _convert_numpy_types(value) for key, value in obj.items()}
    elif isinstance(obj, (list, tuple)):
        return [_convert_numpy_types(item) for item in obj]
    return obj


class BOComparisonRunner:
    """
    Bayesian Optimization comparison runner for PBT vs BO experiments.

    Reuses the same evaluation infrastructure as PBTTuner:
    - Same KnobSpace (tier system, hardware-aware ranges)
    - Same Evaluator (workload execution, metric collection)
    - Same MetricConfig (scoring formula, normalization)
    - Same EnvironmentFactory (Docker/bare-metal PostgreSQL instances)

    Key difference: BO evaluates configurations sequentially (one at a time)
    using a single PostgreSQL instance, whereas PBT evaluates in parallel.
    """

    def __init__(
        self,
        knob_tier: str = "minimal",
        pbt_config: Optional[PBTConfig] = None,
        bo_config: Optional[BOConfig] = None,
        benchmark: Optional[str] = None,
        workload_type: WorkloadType = WorkloadType.OLTP,
        workload_file: Optional[str] = None,
        random_seed: Optional[int] = None,
        **kwargs: Any,
    ) -> None:
        """
        Initialize BO Comparison Runner.

        Args:
            knob_tier: Knob space tier (minimal, core, standard, extensive).
            pbt_config: PBT config for workload/benchmark settings reuse.
            bo_config: BO-specific configuration (max evals, initial design, etc.).
            benchmark: Benchmark name ('sysbench', 'tpch', or None for custom).
            workload_type: Workload type for optimization.
            workload_file: Path to custom workload file (JSON/YAML).
            random_seed: Seed for reproducibility.
            **kwargs: Additional keyword arguments (no_docker, docker_image, output_dir, etc.).
        """
        self.knob_tier = knob_tier
        self.pbt_config = pbt_config or STANDARD_CONFIG
        self.bo_config = bo_config or BOConfig()
        self.random_seed = random_seed or 42

        self.no_docker = kwargs.get("no_docker", False)
        self.docker_image = kwargs.get("docker_image", None)
        self.force_recreate_instances = kwargs.get("force_recreate_instances", False)
        self.force_recreate_baseline = kwargs.get("force_recreate_baseline", False)
        self.cleanup_instances = kwargs.get("cleanup_instances", False)
        self.enable_colors = kwargs.get("enable_colors", True)

        self.timestamp = kwargs.get(
            "timestamp", datetime.now().strftime("%Y%m%d_%H%M")
        )
        self.logger = kwargs.get("logger", get_logger(__name__))

        # Load knob space (same as PBT)
        self.logger.debug("Loading knob space: %s", knob_tier.upper())
        self.knob_space = get_knob_space(knob_tier)

        # Detect hardware and resolve ranges (same as PBT)
        self.worker_resources = detect_worker_resources(max_parallel_workers=1)
        self.knob_space.resolve_hardware_ranges(self.worker_resources)
        self.logger.debug("✓ Loaded %d knobs", len(self.knob_space))

        self.db_config = get_db_config()
        self.metric_config = create_metric_config(workload_type.value)

        self.evaluator_config = EvaluatorConfig(
            workload_type=workload_type,
            metric_config=self.metric_config,
            db_config=self.db_config,
            warmup_duration=self.pbt_config.warmup_duration,
            measurement_duration=self.pbt_config.evaluation_duration,
            cooldown_duration=3.0,
            tuning_mode=TuningMode.OFFLINE,  # BO always restarts for full knob coverage
            adaptive_restart_interval=1,
            random_seed=random_seed,
            warmup_passes=self.pbt_config.warmup_passes,
            worker_memory_budget_bytes=self.worker_resources.ram_bytes,
        )

        # Create workload executor (same logic as PBTTuner)
        if benchmark == "sysbench":
            self.benchmark_name = "sysbench"
            self.workload_type = WorkloadType.OLTP
            workload_executor = SysbenchExecutor(
                tables=self.pbt_config.sysbench_tables,
                table_size=self.pbt_config.sysbench_table_size,
            )
            self.snapshot_identifier = (
                f"sysbench_t{self.pbt_config.sysbench_tables}_"
                f"s{self.pbt_config.sysbench_table_size}"
            )
        elif benchmark == "tpch":
            self.benchmark_name = "tpch"
            self.workload_type = WorkloadType.OLAP
            workload_executor = TPCHExecutor(
                scale_factor=self.pbt_config.scale_factor
            )
            self.snapshot_identifier = f"tpch_sf{self.pbt_config.scale_factor}"
        else:
            self.benchmark_name = workload_type.value
            self.workload_type = workload_type
            if workload_file:
                workload_executor = WorkloadFileLoader.load_from_file(workload_file)
            else:
                template_map = {
                    WorkloadType.OLTP: "workloads/oltp.json",
                    WorkloadType.OLAP: "workloads/olap.json",
                    WorkloadType.MIXED: "workloads/mixed.json",
                }
                workload_executor = WorkloadFileLoader.load_from_file(
                    template_map[workload_type]
                )
            self.snapshot_identifier = (
                f"{self.benchmark_name}_sf{self.pbt_config.scale_factor}"
            )

        # Normalize snapshot identifier for filesystem/Docker compatibility
        import re

        self.snapshot_identifier = (
            re.sub(r"[^a-z0-9_.-]+", "-", self.snapshot_identifier.lower()).strip("-")
            or "default"
        )

        # Create environment (single instance for sequential BO)
        self.env = EnvironmentFactory.create(
            schema_provider=workload_executor,
            use_docker=not self.no_docker,
            base_dir=Path(f"./pg_instances/{self.benchmark_name}_bo"),
            base_port=5460,  # Different port range to avoid conflicts with PBT
            db_config=self.db_config,
            worker_resources=self.worker_resources,
            run_id=self.snapshot_identifier,
            image_name=self.docker_image,
            force_recreate_baseline=self.force_recreate_baseline,
        )

        # Output directory structure mirrors PBT but under bo_runs/
        self.output_dir = (
            Path(kwargs.get("output_dir", "results"))
            / self.workload_type.value
            / "bo_runs"
            / self.knob_tier
        )
        self.output_dir.mkdir(parents=True, exist_ok=True)

        # Create evaluator (same as PBT)
        self.evaluator = Evaluator(
            self.evaluator_config, workload_executor, self.env
        )

        self.system_info = get_system_info()
        self.evaluation_history: List[Dict[str, Any]] = []
        self.start_time: Optional[float] = None

    def _objective_function(
        self,
        knob_config: Dict[str, Any],
        evaluation_number: int,
    ) -> Tuple[float, PerformanceMetrics]:
        """
        Evaluate a single knob configuration via the shared Evaluator pipeline.

        This wraps the same Evaluator.evaluate_worker() used by PBT, ensuring
        identical workload execution, metric collection, and scoring.

        Args:
            knob_config: Knob name → value mapping from BO suggestion.
            evaluation_number: Sequential evaluation counter (for logging).

        Returns:
            Tuple of (score, metrics). Score is on [0, 100] scale (higher = better).

        Raises:
            RuntimeError: If evaluation fails irrecoverably.
        """
        worker = Worker(
            worker_id=0,
            knob_space=self.knob_space,
            knob_config=knob_config,
        )

        # Assign instance connection info
        db_config = self.env.get_db_config(0)
        worker.db_config = db_config
        worker.port = db_config.port

        self.logger.info(
            "BO Evaluation %d: evaluating configuration...", evaluation_number
        )

        try:
            metrics, score, restart_occurred = self.evaluator.evaluate_worker(
                worker, apply_config=True, generation=evaluation_number
            )

            self.logger.info(
                "BO Evaluation %d: score=%.4f, throughput=%.1f, latency_p95=%.2f",
                evaluation_number,
                score,
                metrics.throughput,
                metrics.latency_p95,
            )

            return score, metrics

        except Exception as e:
            self.logger.error(
                "BO Evaluation %d failed: %s", evaluation_number, e
            )
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
                failure_type="crash_bo_eval",
            )
            return 0.0, fallback_metrics

    def run(self) -> Dict[str, Any]:
        """
        Run the complete BO optimization process.

        Returns:
            Final results dictionary with session metadata, best config,
            iteration history, and convergence statistics.
        """
        log_section_header(self.logger, "BO Baseline Runner - Starting Optimization")
        log_system_info(self.logger, self.system_info)
        self.logger.info("Knob Tier:          %s (%d knobs)", self.knob_tier, len(self.knob_space))
        self.logger.info("Max Evaluations:    %d", self.bo_config.max_evaluations)
        self.logger.info("Initial Design:     %s", self.bo_config.initial_design_size or "auto")
        self.logger.info("Optimizer Backend:  %s", self.bo_config.optimizer_backend)
        self.logger.info("Workload Type:      %s", self.workload_type.value)
        self.logger.info("Output Dir:         %s", self.output_dir)

        self.start_time = time.time()

        # Initialize tracking variables before try block so they're always
        # available in the finally/save path even if setup fails early.
        best_score: float = -float("inf")
        best_config: Dict[str, Any] = {}
        best_metrics: Optional[PerformanceMetrics] = None
        convergence_history: List[float] = []
        observed_metrics: List[PerformanceMetrics] = []

        try:
            # Setup single PostgreSQL instance
            log_section_header(self.logger, "Setting Up PostgreSQL Instance")
            instances = self.env.setup_instances(
                num_workers=1,
                force_recreate=self.force_recreate_instances,
            )
            self.logger.info("✓ Created %d instance(s)", len(instances))

            verification = self.env.verify_instances()
            failed = [wid for wid, status in verification.items() if not status]
            if failed:
                raise RuntimeError(f"Instance verification failed: {failed}")
            self.logger.info("✓ Instance verified and accessible")

            # Build ConfigSpace from KnobSpace
            cs = build_configspace_from_knob_space(self.knob_space)
            self.logger.info(
                "✓ Built ConfigSpace with %d hyperparameters", len(cs)
            )

            # Create BO optimizer
            optimizer = BOOptimizer(
                config_space=cs,
                bo_config=self.bo_config,
                seed=self.random_seed,
            )

            # Main BO loop
            log_section_header(self.logger, "Starting Bayesian Optimization")

            # Collect metrics for adaptive normalization (mirrors PBT's approach)
            metric_config = self.metric_config

            # PBT waits for max(8, 2 * population_size) samples before
            # calibrating adaptive ranges.  For BO we use the same formula
            # with the PBT config's population_size to ensure identical
            # calibration timing relative to exploration budget.
            pbt_pop_size = self.pbt_config.population_size
            adaptive_threshold = max(8, 2 * pbt_pop_size)
            # Never exceed the total budget — degrade gracefully.
            adaptive_threshold = min(
                adaptive_threshold, self.bo_config.max_evaluations
            )
            self.logger.info(
                "Adaptive normalization will activate after %d healthy observations "
                "(PBT equivalent: 2 × generation size)",
                adaptive_threshold,
            )

            for i in range(self.bo_config.max_evaluations):
                eval_start = time.time()

                # Get next suggestion from BO
                cs_config = optimizer.suggest()

                # Convert ConfigSpace config to knob config
                knob_config = configspace_sample_to_knob_config(
                    cs_config, self.knob_space
                )

                # Validate and repair
                knob_config = self.knob_space.repair_config_dependencies(knob_config)

                # Evaluate
                score, metrics = self._objective_function(knob_config, i + 1)

                eval_time = time.time() - eval_start

                # Collect healthy metrics for adaptive normalization
                if metrics.failure_type is None:
                    observed_metrics.append(metrics)

                # === Adaptive normalization — mirrors PBT exactly ===
                ranges_initialized = getattr(
                    metric_config, "_ranges_initialized", False
                )

                # Phase 1: Initial calibration (PBT: update_metric_ranges_if_needed)
                # Wait for adaptive_threshold healthy observations, then calibrate
                # ranges from 5th/95th percentiles + 20 % padding.
                if not ranges_initialized and len(observed_metrics) >= adaptive_threshold:
                    self.logger.info(
                        "Activating adaptive normalization from %d observations "
                        "(threshold was %d)...",
                        len(observed_metrics),
                        adaptive_threshold,
                    )
                    metric_config.update_ranges(observed_metrics)

                    # Rescore the current evaluation with calibrated ranges
                    if metrics.failure_type is None:
                        score = metric_config.compute_score(metrics)

                    # Reset best tracker and rescore all prior evaluations
                    # (PBT resets best_overall_score to 0.0 after calibration)
                    best_score = -float("inf")
                    best_config = {}
                    best_metrics = None
                    for entry in self.evaluation_history:
                        entry_metrics_dict = entry.get("metrics", {})
                        if entry_metrics_dict.get("failure_type") is not None:
                            continue
                        rescored = metric_config.compute_score(
                            PerformanceMetrics(**{
                                k: v
                                for k, v in entry_metrics_dict.items()
                                if k in PerformanceMetrics.__dataclass_fields__
                            })
                        )
                        entry["score"] = float(rescored)
                        if rescored > best_score:
                            best_score = rescored
                            best_config = entry["config"].copy()

                    # Rebuild convergence history
                    convergence_history.clear()
                    running_best = -float("inf")
                    for entry in self.evaluation_history:
                        running_best = max(running_best, entry["score"])
                        entry["best_score_so_far"] = float(running_best)
                        convergence_history.append(running_best)

                    self.logger.info(
                        "✓ Adaptive normalization active — rescored %d prior "
                        "evaluations, new best=%.4f",
                        len(self.evaluation_history),
                        best_score if best_score > -float("inf") else 0.0,
                    )

                # Phase 2: Bounds-exceedance expansion
                # (PBT: _check_and_handle_saturation)
                # PBT checks if any worker's RAW metric falls outside the
                # current [min, max] bounds.  np.clip silently destroys
                # ranking between configs when values exceed bounds, so we
                # must expand before that happens.
                elif ranges_initialized and metrics.failure_type is None:
                    latency_val = getattr(
                        metrics, f"latency_{metric_config.latency_metric}"
                    )
                    throughput_val = metrics.throughput

                    exceeds_bounds = (
                        (latency_val > 0 and latency_val < metric_config.latency_min)
                        or (latency_val > 0 and latency_val > metric_config.latency_max)
                        or (throughput_val > 0 and throughput_val < metric_config.throughput_min)
                        or (throughput_val > 0 and throughput_val > metric_config.throughput_max)
                    )

                    if exceeds_bounds:
                        self.logger.info(
                            "⚠️  Metric bounds exceeded at evaluation %d "
                            "(lat=%.2f, thr=%.1f vs ranges lat=[%.2f,%.2f] "
                            "thr=[%.1f,%.1f]) — expanding...",
                            i + 1,
                            latency_val,
                            throughput_val,
                            metric_config.latency_min,
                            metric_config.latency_max,
                            metric_config.throughput_min,
                            metric_config.throughput_max,
                        )
                        healthy_metrics = [
                            m for m in observed_metrics if m.failure_type is None
                        ]
                        expanded = metric_config.expand_ranges_for_metrics(
                            healthy_metrics,
                            expansion_factor=0.25,  # 25 % headroom, same as PBT
                        )
                        if expanded:
                            # Rescore current eval
                            score = metric_config.compute_score(metrics)

                            # Rescore all prior evaluations
                            best_score = -float("inf")
                            best_config = {}
                            best_metrics = None
                            for entry in self.evaluation_history:
                                entry_metrics_dict = entry.get("metrics", {})
                                if entry_metrics_dict.get("failure_type") is not None:
                                    continue
                                rescored = metric_config.compute_score(
                                    PerformanceMetrics(**{
                                        k: v
                                        for k, v in entry_metrics_dict.items()
                                        if k in PerformanceMetrics.__dataclass_fields__
                                    })
                                )
                                entry["score"] = float(rescored)
                                if rescored > best_score:
                                    best_score = rescored
                                    best_config = entry["config"].copy()

                            convergence_history.clear()
                            running_best = -float("inf")
                            for entry in self.evaluation_history:
                                running_best = max(running_best, entry["score"])
                                entry["best_score_so_far"] = float(running_best)
                                convergence_history.append(running_best)

                            self.logger.info(
                                "♻️  Rescored %d evaluations after range expansion, "
                                "best=%.4f",
                                len(self.evaluation_history),
                                best_score if best_score > -float("inf") else 0.0,
                            )

                # Report result to BO (SMAC minimizes, so negate score)
                optimizer.report(cs_config, -score)

                # Track best
                if score > best_score:
                    best_score = score
                    best_config = knob_config.copy()
                    best_metrics = metrics
                    self.logger.info(
                        "🎉 NEW BEST SCORE: %.4f (evaluation %d)", best_score, i + 1
                    )

                convergence_history.append(best_score)

                # Record history entry
                history_entry = {
                    "evaluation": i + 1,
                    "score": float(score),
                    "best_score_so_far": float(best_score),
                    "elapsed_seconds": eval_time,
                    "wall_clock_seconds": time.time() - self.start_time,
                    "config": _convert_numpy_types(knob_config),
                    "metrics": _convert_numpy_types(
                        metrics.to_dict() if metrics else {}
                    ),
                    "timestamp": datetime.now().isoformat(),
                }
                self.evaluation_history.append(history_entry)

                # Log progress
                elapsed_total = time.time() - self.start_time
                self.logger.info(
                    "Evaluation %d/%d: score=%.4f, best=%.4f, "
                    "eval_time=%.1fs, total_time=%.1fs",
                    i + 1,
                    self.bo_config.max_evaluations,
                    score,
                    best_score,
                    eval_time,
                    elapsed_total,
                )

        except KeyboardInterrupt:
            self.logger.info("⚠ Interrupted by user. Saving results...")

        except Exception as e:
            self.logger.error("❌ Error during BO optimization: %s", e, exc_info=True)

        finally:
            self.logger.info("Stopping PostgreSQL instances...")
            try:
                self.env.stop_all()
            except (RuntimeError, ValueError, ConnectionError, OSError) as e:
                self.logger.warning("⚠ Failed to stop instances cleanly: %s", e)

            if self.cleanup_instances:
                try:
                    self.env.cleanup(remove_data=True)
                    self.logger.info("✓ Instance data removed")
                except (RuntimeError, ValueError, ConnectionError, OSError) as e:
                    self.logger.warning("⚠ Failed to clean up: %s", e)

        # === Post-hoc global rescoring ===
        # Within-loop adaptive calibration sets ranges from only the first
        # `adaptive_threshold` observations (typically 8).  PBT's final
        # reported scores benefit from ranges that expand across ALL
        # generations (120+ evaluations), producing naturally wider bounds
        # that push top scores away from the 100-ceiling.  To produce a
        # comparable score scale we recalibrate from the *complete*
        # observation set using the same percentile + padding methodology.
        if len(observed_metrics) >= 3:
            self.logger.info(
                "Applying post-hoc global rescoring from %d observations...",
                len(observed_metrics),
            )
            posthoc_config, posthoc_scores, posthoc_meta = rescore_metrics_globally(
                observed_metrics,
                workload=self.workload_type.value,
                padding_factor=0.2,  # Same as PBT's update_ranges default
            )

            # Map rescored values back to evaluation history.
            # posthoc_scores aligns 1:1 with observed_metrics (failures excluded).
            score_idx = 0
            best_score = -float("inf")
            best_config = {}
            best_metrics = None

            for entry in self.evaluation_history:
                entry_metrics = entry.get("metrics", {})
                if entry_metrics.get("failure_type") is not None:
                    continue
                entry["score"] = float(posthoc_scores[score_idx])
                if posthoc_scores[score_idx] > best_score:
                    best_score = float(posthoc_scores[score_idx])
                    best_config = entry["config"].copy()
                    best_metrics = observed_metrics[score_idx]
                score_idx += 1

            # Rebuild convergence history from rescored evaluation data
            convergence_history = []
            running_best = -float("inf")
            for entry in self.evaluation_history:
                entry_score = entry.get("score", 0.0)
                if entry_score > running_best:
                    running_best = entry_score
                entry["best_score_so_far"] = float(running_best)
                convergence_history.append(float(running_best))

            # Update metric config so saved normalization_ranges reflect post-hoc
            self.metric_config = posthoc_config

            self.logger.info(
                "✓ Post-hoc rescoring complete: best=%.4f "
                "(latency [%.2f, %.2f] ms, throughput [%.1f, %.1f] TPS)",
                best_score,
                posthoc_config.latency_min,
                posthoc_config.latency_max,
                posthoc_config.throughput_min,
                posthoc_config.throughput_max,
            )

        total_time = time.time() - self.start_time if self.start_time else 0
        results = self._save_results(
            total_time=total_time,
            best_config=best_config,
            best_score=best_score,
            best_metrics=best_metrics,
            convergence_history=convergence_history,
        )

        self._print_summary(results)
        return results

    def _save_results(
        self,
        total_time: float,
        best_config: Dict[str, Any],
        best_score: float,
        best_metrics: Optional[PerformanceMetrics],
        convergence_history: List[float],
    ) -> Dict[str, Any]:
        """
        Save BO results in a JSON format compatible with PBT results for comparison.

        The schema mirrors PBT's pbt_results_*.json structure where applicable,
        with BO-specific additions (evaluation history, BO hyperparameters).

        Args:
            total_time: Total wall-clock time in seconds.
            best_config: Best knob configuration found.
            best_score: Best score achieved.
            best_metrics: Performance metrics for the best configuration.
            convergence_history: Best score at each evaluation step.

        Returns:
            Complete results dictionary.
        """
        worker_resources = self.knob_space.worker_resources

        results: Dict[str, Any] = {
            "optimizer": "bayesian_optimization",
            "optimizer_backend": self.bo_config.optimizer_backend,
            "tuning_session": {
                "knob_tier": self.knob_tier,
                "num_knobs": len(self.knob_space),
                "workload_type": self.workload_type.value,
                "benchmark_name": self.benchmark_name,
                "tpch_scale_factor": self.pbt_config.scale_factor,
                "tpch_warmup_passes": self.pbt_config.warmup_passes,
                "sysbench_tables": self.pbt_config.sysbench_tables,
                "sysbench_table_size": self.pbt_config.sysbench_table_size,
                "sysbench_duration_seconds": self.pbt_config.evaluation_duration,
                "sysbench_warmup_seconds": self.pbt_config.warmup_duration,
                "max_evaluations": self.bo_config.max_evaluations,
                "initial_design_size": self.bo_config.initial_design_size,
                "total_evaluations": len(self.evaluation_history),
                "total_time_seconds": total_time,
                "timestamp": self.timestamp,
            },
            "bo_config": {
                "optimizer_backend": self.bo_config.optimizer_backend,
                "max_evaluations": self.bo_config.max_evaluations,
                "initial_design_size": self.bo_config.initial_design_size,
                "acquisition_function": self.bo_config.acquisition_function,
                "random_seed": self.random_seed,
            },
            "best_configuration": {
                "score": float(best_score) if best_score > -float("inf") else 0.0,
                "knobs": _convert_numpy_types(
                    self.knob_space.config_to_fractions(best_config)
                    if best_config
                    else {}
                ),
                "metrics": _convert_numpy_types(
                    best_metrics.to_dict() if best_metrics else {}
                ),
            },
            "worker_resources": {
                "ram_bytes": worker_resources.ram_bytes,
                "cpu_cores": worker_resources.cpu_cores,
                "disk_type": worker_resources.disk_type,
            },
            "evaluation_history": _convert_numpy_types(self.evaluation_history),
            "convergence": {
                "history": [float(x) for x in convergence_history],
                "final_best_score": float(best_score)
                if best_score > -float("inf")
                else 0.0,
                "total_evaluations": len(self.evaluation_history),
            },
            "system_info": self.system_info,
            "normalization_ranges": {
                "adaptive": getattr(self.metric_config, "_ranges_initialized", False),
                "latency_min": self.metric_config.latency_min,
                "latency_max": self.metric_config.latency_max,
                "throughput_min": self.metric_config.throughput_min,
                "throughput_max": self.metric_config.throughput_max,
                "latency_metric": self.metric_config.latency_metric,
            },
        }

        # Save main results JSON
        tuning_output_dir = self.output_dir / "tuning_sessions"
        tuning_output_dir.mkdir(parents=True, exist_ok=True)
        json_file = tuning_output_dir / f"bo_results_{self.timestamp}.json"

        with open(json_file, "w", encoding="utf-8") as f:
            json.dump(results, f, indent=2)
        self.logger.info("💾 Saved BO results to %s", json_file)

        # Save best config separately (same format as PBT best_config)
        best_config_dir = self.output_dir / "best_configs"
        best_config_dir.mkdir(parents=True, exist_ok=True)
        best_config_file = best_config_dir / f"bo_best_config_{self.timestamp}.json"

        with open(best_config_file, "w", encoding="utf-8") as f:
            json.dump(
                _convert_numpy_types(
                    self.knob_space.config_to_fractions(best_config)
                    if best_config
                    else {}
                ),
                f,
                indent=2,
            )
        self.logger.info("💾 Saved BO best config to %s", best_config_file)

        return results

    def _print_summary(self, results: Dict[str, Any]) -> None:
        """Print a human-readable summary of BO results."""
        log_section_header(self.logger, "BO Optimization Complete")

        session = results["tuning_session"]
        best = results["best_configuration"]

        self.logger.info("Total Time:           %.1fs", session["total_time_seconds"])
        self.logger.info("Total Evaluations:    %d", session["total_evaluations"])
        self.logger.info("Knobs Tuned:          %d", session["num_knobs"])
        self.logger.info("Workload Type:        %s", session["workload_type"])
        self.logger.info("Best Score:           %.4f", best["score"])

        self.logger.info("Best Knob Configuration:")
        for knob_name, value in sorted(best["knobs"].items()):
            self.logger.info("    %-40s = %s", knob_name, value)


def parse_args() -> argparse.Namespace:
    """Parse command-line arguments for BO comparison runner."""
    parser = argparse.ArgumentParser(
        description="Bayesian Optimization Baseline for PBT Comparison",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
Examples:
  # Quick BO baseline with minimal knobs
  python -m src.scripts.run_bo_comparison --tier minimal --config rapid

  # Standard BO baseline matching PBT setup
  python -m src.scripts.run_bo_comparison --tier core --config standard

  # BO with custom evaluation budget
  python -m src.scripts.run_bo_comparison --tier core --max-evaluations 50

  # BO with Sysbench benchmark
  python -m src.scripts.run_bo_comparison --benchmark sysbench --tier core

  # BO with TPC-H benchmark
  python -m src.scripts.run_bo_comparison --benchmark tpch --tier standard
        """,
    )

    # BO-specific configuration
    bo_group = parser.add_argument_group("Bayesian Optimization Configuration")
    bo_group.add_argument(
        "--optimizer-backend",
        type=str,
        default="smac",
        choices=["smac"],
        help="BO optimizer backend (default: smac). SMAC3 is the recommended backend "
        "for its mature ConfigSpace support and proven track record in algorithm "
        "configuration (Lindauer et al., JMLR 2022).",
    )
    bo_group.add_argument(
        "--max-evaluations",
        type=int,
        default=30,
        help="Maximum number of BO evaluations (default: 30)",
    )
    bo_group.add_argument(
        "--initial-design-size",
        type=int,
        default=None,
        help="Number of random initial evaluations before BO model kicks in "
        "(default: auto = max(5, num_knobs))",
    )
    bo_group.add_argument(
        "--acquisition-function",
        type=str,
        default="EI",
        choices=["EI", "LCB", "PI"],
        help="Acquisition function for BO (default: EI = Expected Improvement)",
    )
    bo_group.add_argument(
        "--seed",
        type=int,
        default=42,
        help="Random seed for BO reproducibility (default: 42)",
    )

    # Reuse PBT-style configuration for fair comparison
    config_group = parser.add_argument_group("Tuning Configuration (shared with PBT)")
    config_group.add_argument(
        "--tier",
        type=str,
        default="minimal",
        choices=["minimal", "core", "standard", "extensive"],
        help="Knob space tier (default: minimal)",
    )
    config_group.add_argument(
        "--config",
        type=str,
        default="standard",
        choices=["rapid", "standard", "thorough", "research", "extreme"],
        help="PBT configuration profile for workload settings (default: standard)",
    )

    # Workload settings (mirrors PBT CLI)
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
        help="Path to custom workload file (JSON/YAML)",
    )
    workload_exclusive.add_argument(
        "--benchmark",
        type=str,
        default=None,
        choices=["sysbench", "tpch"],
        help="External benchmark (sysbench=OLTP, tpch=OLAP)",
    )

    workload_group.add_argument(
        "--duration",
        type=float,
        help="Evaluation duration in seconds per evaluation (overrides config)",
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
        help="TPC-H scale factor (only with --benchmark tpch)",
    )
    workload_group.add_argument(
        "--sysbench-tables",
        type=int,
        default=None,
        help="Number of Sysbench tables (only with --benchmark sysbench)",
    )
    workload_group.add_argument(
        "--sysbench-table-size",
        type=int,
        default=None,
        help="Sysbench rows per table (only with --benchmark sysbench)",
    )

    # Instance management
    instance_group = parser.add_argument_group("Instance Management")
    instance_group.add_argument(
        "--no-docker",
        action="store_true",
        help="Run on bare-metal PostgreSQL instead of Docker",
    )
    instance_group.add_argument(
        "--docker-image",
        type=str,
        default=None,
        help="Docker image override for PostgreSQL",
    )
    instance_group.add_argument(
        "--force-recreate-instances",
        action="store_true",
        help="Force recreation of PostgreSQL instances",
    )
    instance_group.add_argument(
        "--cleanup-instances",
        action="store_true",
        help="Remove PostgreSQL instance data after completion",
    )
    instance_group.add_argument(
        "--force-recreate-baseline",
        action="store_true",
        help="Force recreation of baseline snapshot",
    )

    # Output and logging
    output_group = parser.add_argument_group("Output & Logging")
    output_group.add_argument(
        "--verbose",
        type=str,
        default="INFO",
        choices=["DEBUG", "INFO", "WARNING", "ERROR", "TRACE"],
        help="Verbosity level (default: INFO)",
    )
    output_group.add_argument(
        "--output-dir",
        type=str,
        default="results",
        help="Base output directory (default: results)",
    )
    output_group.add_argument(
        "--no-color",
        action="store_true",
        help="Disable ANSI colors in terminal output",
    )

    return parser.parse_args()


def main() -> int:
    """Main entry point for BO comparison runner."""
    args = parse_args()
    timestamp = datetime.now().strftime("%Y%m%d_%H%M")
    enable_colors = not args.no_color

    # Structured log directory
    workload_for_dir = "olap" if args.benchmark == "tpch" else args.workload
    log_output_dir = (
        Path(args.output_dir) / workload_for_dir / "bo_runs" / args.tier
    )
    log_output_dir.mkdir(parents=True, exist_ok=True)
    output_file = log_output_dir / f"bo_tuning_{timestamp}.html"

    print_startup_banner(enable_colors=enable_colors)

    setup_logging(
        verbosity=args.verbose,
        enable_colors=enable_colors,
        show_module=True,
        output_file=output_file,
    )
    run_logger = get_logger(__name__)

    run_logger.info("Starting Bayesian Optimization Baseline Runner...")

    # Build PBT config (for workload/benchmark settings)
    config_map = {
        "rapid": RAPID_CONFIG,
        "standard": STANDARD_CONFIG,
        "thorough": THOROUGH_CONFIG,
        "research": RESEARCH_CONFIG,
        "extreme": EXTREME_CONFIG,
    }
    pbt_config = config_map[args.config]
    config_dict = pbt_config.to_dict()

    # Apply CLI overrides for workload settings
    cli_overrides = {
        "evaluation_duration": args.duration,
        "warmup_duration": args.warmup,
        "scale_factor": args.scale_factor,
        "sysbench_tables": args.sysbench_tables,
        "sysbench_table_size": args.sysbench_table_size,
    }
    config_dict.update(
        {k: v for k, v in cli_overrides.items() if v is not None}
    )
    config_dict["random_seed"] = args.seed
    pbt_config = PBTConfig(**config_dict)

    # Build BO config
    initial_design = args.initial_design_size
    bo_config = BOConfig(
        optimizer_backend=args.optimizer_backend,
        max_evaluations=args.max_evaluations,
        initial_design_size=initial_design,  # None → auto in BOOptimizer
        acquisition_function=args.acquisition_function,
    )

    # Determine workload type
    workload_type = {
        "oltp": WorkloadType.OLTP,
        "olap": WorkloadType.OLAP,
        "mixed": WorkloadType.MIXED,
    }[args.workload]

    if args.benchmark:
        workload_type = {
            "tpch": WorkloadType.OLAP,
            "sysbench": WorkloadType.OLTP,
        }.get(args.benchmark, workload_type)

    try:
        runner = BOComparisonRunner(
            knob_tier=args.tier,
            pbt_config=pbt_config,
            bo_config=bo_config,
            benchmark=args.benchmark,
            workload_type=workload_type,
            workload_file=args.workload_file,
            random_seed=args.seed,
            force_recreate_instances=args.force_recreate_instances,
            force_recreate_baseline=args.force_recreate_baseline,
            cleanup_instances=args.cleanup_instances,
            output_dir=args.output_dir,
            logger=run_logger,
            timestamp=timestamp,
            no_docker=args.no_docker,
            docker_image=args.docker_image,
            enable_colors=enable_colors,
        )

        runner.run()
        run_logger.info("🟢 BO baseline completed successfully!")
        return 0

    except (RuntimeError, ValueError, ConnectionError) as e:
        run_logger.error("🔴 Fatal error: %s", e)
        run_logger.debug("Exception details:", exc_info=True)
        return 1


if __name__ == "__main__":
    sys.exit(main())
