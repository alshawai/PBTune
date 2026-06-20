"""
LHS-design importance-sampling tuner.

``LHSDesignTuner`` evaluates a *fixed* Latin Hypercube Sampling design over the
knob space — there is no evolution, no exploit/explore, and no perturbation.
The design is drawn once, sliced into parallel batches, and every batch is
evaluated under the same lockstep barriers PBT uses, so each configuration's
measurement window experiences identical contention.

Why this strategy exists
------------------------
The research framing (see ``docs/guides/scalpel-rollout.md`` and ADR-006) is
that SCALPEL applied to an LHS *design* over the knob space yields
DBA-competitive importance tiers, whereas applied to PBT's optimization
*trajectory* the per-knob variance is too narrow to separate signal from
noise. A clean, evolution-free design sweep is the experimental substrate
SCALPEL needs: a space-filling sample where every knob varies independently of
performance feedback.

Relationship to PBT/BO
----------------------
This tuner *composes* PBT's environment, orchestrator, and ``Population``
machinery (for parallel barrier-synchronized evaluation) but drives them
through the strategy-agnostic :class:`~src.tuners.base.BaseTuner` lifecycle.
PBT and BO themselves are not modified (ADR-006, copy-not-refactor).
"""

from __future__ import annotations

import math
import time
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

from concurrent.futures import ThreadPoolExecutor, as_completed

from src.config.data_root import resolve_data_root
from src.config.database import get_db_config
from src.tuner.benchmark.orchestrator import (
    WorkloadOrchestrator,
    WorkloadOrchestratorConfig,
)
from src.tuner.config import get_knob_space
from src.tuner.core.barriers import GenerationBarrier
from src.tuner.core.worker import Worker
from src.tuners.base import BaseTuner
from src.tuners.utils.executors import build_workload_bundle
from src.tuners.utils.knob_filter import (
    compute_unsupported_knobs,
    log_pruning_summary,
    query_runtime_supported_knobs,
)
from src.tuners.utils.resources import resolve_worker_resources
from src.tuners.utils.exceptions import (
    KnobSpaceEmptyError,
    TunerConfigError,
    GenerationEvaluationError,
)
from src.tuners.utils.types import (
    GenerationOutcome,
    TunerLifecycleConfig,
    TuningStrategy,
)
from src.utils.environments import EnvironmentFactory
from src.utils.logger import get_logger
from src.utils.metrics import (
    PerformanceMetrics,
    WorkloadType,
    create_metric_config,
)
from src.utils.scoring.contracts import ScoreBreakdown
from src.utils.types import (
    BenchmarkConfig,
    build_session_environment,
)
from src.utils.hardware_info import get_system_info

LOGGER = get_logger("LHSDesignTuner")


class LHSDesignTuner(BaseTuner):
    """
    Evaluate a fixed LHS design over the knob space in parallel batches.

    Parameters
    ----------
    lifecycle
        Strategy-agnostic lifecycle config (forced to ``TuningStrategy.LHS``).
    benchmark
        Workload driver: 'sysbench', 'tpch', or None for a custom template.
    benchmark_config
        Benchmark/workload settings (durations, scale factor, ...).
    design_size
        Number of configurations in the LHS design.
    timestamp
        Session id used in output filenames.
    output_root
        Base results directory for this run (already strategy/tier-scoped).
    workload_file
        Path to a custom workload file (only for non-sysbench/tpch runs).
    data_root
        Override for the data directory (defaults to ``resolve_data_root()``).
    resource_overrides
        Optional manual per-worker resource overrides forwarded to
        :func:`~src.tuners.utils.resources.resolve_worker_resources`.
    """

    def __init__(
        self,
        lifecycle: TunerLifecycleConfig,
        *,
        benchmark: Optional[str],
        benchmark_config: BenchmarkConfig,
        design_size: int,
        timestamp: str,
        output_root: Path,
        workload_file: Optional[str] = None,
        data_root: Optional[Path] = None,
        resource_overrides: Optional[Dict[str, Any]] = None,
    ) -> None:
        lifecycle.strategy = TuningStrategy.LHS
        super().__init__(lifecycle, timestamp=timestamp, output_root=output_root)

        if design_size < 1:
            raise TunerConfigError("design_size must be at least 1")

        self.benchmark = benchmark
        self.benchmark_config = benchmark_config
        self.design_size = design_size
        self.workload_file = workload_file
        self.data_root = Path(data_root) if data_root else resolve_data_root()
        self.resource_overrides = dict(resource_overrides or {})

        # Populated during setup().
        self.knob_space: Any = None
        self.full_knob_space: Any = None
        self.env: Any = None
        self._instances: List[Any] = []
        self.orchestrator: Optional[WorkloadOrchestrator] = None
        self.metric_config: Any = None
        self.workload_features: Dict[str, float] = {}
        self._benchmark_name: str = "unknown"
        self._workload_type: WorkloadType = WorkloadType.OLTP
        self.snapshot_identifier: str = ""
        self.enable_snapshots: bool = False
        self.system_info: Dict[str, Any] = {}
        self.session_environment: Any = None
        self.design: List[Dict[str, Any]] = []

        # Result accumulation.
        self.design_records: List[Dict[str, Any]] = []
        self._best_config: Optional[Dict[str, Any]] = None
        self._best_metrics: Optional[PerformanceMetrics] = None
        self._best_breakdown: Optional[ScoreBreakdown] = None

    @property
    def max_generations(self) -> int:
        """Number of parallel batches needed to cover the design."""
        return max(1, math.ceil(self.design_size / self.lifecycle.num_parallel_workers))

    @property
    def num_knobs(self) -> int:
        return len(self.full_knob_space) if self.full_knob_space is not None else 0

    @property
    def workload_type_value(self) -> str:
        return self._workload_type.value

    @property
    def benchmark_name(self) -> str:
        return self._benchmark_name

    def best_config_fractions(self, best_config: Dict[str, Any]) -> Dict[str, Any]:
        if not best_config or self.full_knob_space is None:
            return {}
        return self.full_knob_space.config_to_fractions(best_config)

    def setup(self) -> None:
        """Resolve resources, build workload + env, bring up instances, prune knobs."""
        # Resolve the granular workload type for the knob space (mirrors PBT).
        if self.benchmark == "sysbench":
            resolved_workload_type = self.benchmark_config.sysbench_workload
        elif self.benchmark == "tpch":
            resolved_workload_type = "olap"
        else:
            resolved_workload_type = self.benchmark_config.workload_type

        self.knob_space = get_knob_space(
            self.lifecycle.knob_tier,
            knob_source=self.lifecycle.knob_source,
            workload_type=resolved_workload_type,
        )
        self.full_knob_space = self.knob_space

        self.worker_resources = resolve_worker_resources(
            num_workers=self.lifecycle.num_parallel_workers,
            data_path=self.data_root,
            worker_ram=self.resource_overrides.get("worker_ram"),
            worker_cpus=self.resource_overrides.get("worker_cpus"),
            worker_disk_read_bps=self.resource_overrides.get("worker_disk_read_bps"),
            worker_disk_write_bps=self.resource_overrides.get("worker_disk_write_bps"),
            worker_disk_read_iops=self.resource_overrides.get("worker_disk_read_iops"),
            worker_disk_write_iops=self.resource_overrides.get(
                "worker_disk_write_iops"
            ),
            probe_disk=bool(self.resource_overrides.get("probe_disk", True)),
        )
        self.full_knob_space.resolve_hardware_ranges(self.worker_resources)
        self.knob_space.worker_resources = self.worker_resources

        db_config = get_db_config()

        bundle = build_workload_bundle(
            benchmark=self.benchmark,
            benchmark_config=self.benchmark_config,
            workload_type=self._infer_base_workload_type(),
            cpu_cores=int(self.worker_resources.cpu_cores or 1),
            workload_file=self.workload_file,
        )
        self._benchmark_name = bundle.benchmark_name
        self._workload_type = bundle.workload_type
        self.workload_features = bundle.workload_features
        self.snapshot_identifier = bundle.snapshot_identifier
        self.enable_snapshots = bundle.enable_snapshots
        workload_executor = bundle.executor

        self.metric_config = create_metric_config(
            self._workload_type.value,
            workload_features=dict(self.workload_features),
        )

        self.env = EnvironmentFactory.create(
            schema_provider=workload_executor,
            use_docker=self.lifecycle.use_docker,
            base_dir=self.data_root,
            base_port=5440,
            db_config=db_config,
            worker_resources=self.worker_resources,
            run_id=self.snapshot_identifier,
            force_recreate_baseline=False,
        )

        orchestrator_config = WorkloadOrchestratorConfig(
            workload_type=self._workload_type,
            metric_config=self.metric_config,
            db_config=db_config,
            warmup_duration=self.benchmark_config.warmup_duration,
            measurement_duration=self.benchmark_config.evaluation_duration,
            cooldown_duration=3.0,
            tuning_mode=self.benchmark_config.tuning_mode,
            adaptive_restart_interval=self.benchmark_config.adaptive_restart_interval,
            random_seed=self.lifecycle.random_seed,
            warmup_passes=self.benchmark_config.warmup_passes,
            worker_memory_budget_bytes=self.worker_resources.ram_bytes,
        )
        self.orchestrator = WorkloadOrchestrator(
            orchestrator_config, workload_executor, self.env
        )

        self.system_info = get_system_info(data_path=self.data_root)

        # Bring up one instance per parallel worker (the design is swept in
        # batches of num_parallel_workers, so we never need more instances).
        num_instances = self.lifecycle.num_parallel_workers
        with self.bootstrap_timing.span("setup_instances"):
            self._instances = self.env.setup_instances(
                num_workers=num_instances,
                force_recreate=False,
                num_parallel_workers=num_instances,
            )
        with self.bootstrap_timing.span("verify_instances"):
            self.env.verify_instances()
        with self.bootstrap_timing.span("prune_knobs"):
            self._prune_unsupported_runtime_knobs()

        self.session_environment = build_session_environment(
            env=self.env,
            num_parallel_workers=self.lifecycle.num_parallel_workers,
            population_size=self.design_size,
            system_info=self.system_info,
            use_docker=self.lifecycle.use_docker,
        )

        # Draw the fixed LHS design once. Worker 0's first slot is anchored to
        # the PostgreSQL default config (mirrors PBT/BO's pilot-seed
        # convention) so the design includes a known-reasonable reference.
        default_config = self.full_knob_space.get_default_config()
        lhs_configs = self.full_knob_space.sample_diverse_configs(
            num_samples=self.design_size,
            seed=self.lifecycle.random_seed,
        )
        design = [default_config] + [c for c in lhs_configs if c != default_config]
        self.design = design[: self.design_size]
        LOGGER.info(
            "Drew LHS design of %d configurations over %d knobs",
            len(self.design),
            self.num_knobs,
        )

    def _infer_base_workload_type(self) -> WorkloadType:
        """Map the benchmark to a base ``WorkloadType`` for feature extraction."""
        if self.benchmark == "tpch":
            return WorkloadType.OLAP
        if self.benchmark == "sysbench":
            return WorkloadType.OLTP
        try:
            return WorkloadType(self.benchmark_config.workload_type)
        except ValueError:
            return WorkloadType.OLTP

    def _prune_unsupported_runtime_knobs(self) -> None:
        """Drop knobs unavailable on the runtime PostgreSQL build."""
        db_config = self.env.get_db_config(0)
        supported, server_version = query_runtime_supported_knobs(
            db_config, fallback_knobs=self.knob_space.knobs.keys()
        )
        if server_version and server_version != "unknown":
            self.env.pg_server_version = server_version

        unsupported = compute_unsupported_knobs(
            self.knob_space.knobs.keys(), supported
        )
        for knob_name in unsupported:
            self.knob_space.knobs.pop(knob_name, None)
        log_pruning_summary(
            unsupported, server_version, remaining=len(self.knob_space)
        )
        if len(self.knob_space) == 0:
            raise KnobSpaceEmptyError(
                "No runtime-compatible knobs remain after pg_settings pruning."
            )

    def propose_initial_configs(self) -> List[Dict[str, Any]]:
        return list(self.design)

    def step(self, generation: int) -> GenerationOutcome:
        """Evaluate one parallel batch of the design under lockstep barriers."""
        batch_size = self.lifecycle.num_parallel_workers
        start = generation * batch_size
        end = min(start + batch_size, len(self.design))
        batch_configs = self.design[start:end]

        if not batch_configs:
            return GenerationOutcome(index=generation, converged=True)

        workers = self._build_batch_workers(batch_configs)
        barriers = GenerationBarrier(
            num_workers=len(workers),
            enabled=len(workers) > 1,
        )

        best_this_batch = 0.0
        gen_start = time.time()
        results = self._evaluate_batch_parallel(workers, barriers, generation)

        for design_index, worker, metrics, score in results:
            breakdown = self._safe_breakdown(metrics)
            record = {
                "design_index": design_index,
                "batch": generation,
                "score": float(score) if score is not None else None,
                "config": self.full_knob_space.config_to_fractions(worker.knob_config),
                "metrics": metrics.to_dict() if metrics is not None else None,
                "score_breakdown": (
                    breakdown.to_dict() if breakdown is not None else None
                ),
            }
            self.design_records.append(record)

            if score is not None and score > self._best_score_so_far:
                self._best_score_so_far = float(score)
                self._best_config = dict(worker.knob_config)  # type: ignore
                self._best_metrics = metrics
                self._best_breakdown = breakdown
            if score is not None:
                best_this_batch = max(best_this_batch, float(score))

        outcome = GenerationOutcome(
            index=generation,
            best_score_this_generation=best_this_batch,
            payload={
                "evaluated": [r["design_index"] for r in self.design_records[-len(batch_configs):]],
                "batch_elapsed_seconds": time.time() - gen_start,
            },
        )
        self.generation_history.append(outcome.to_dict())
        LOGGER.info(
            "Batch %d/%d complete: evaluated designs %d-%d, best-so-far=%.4f",
            generation + 1,
            self.max_generations,
            start,
            end - 1,
            self._best_score_so_far,
        )
        return outcome

    def _build_batch_workers(self, batch_configs: List[Dict[str, Any]]) -> List[Worker]:
        """Construct Workers bound to instances for one batch."""
        workers: List[Worker] = []
        for local_id, config in enumerate(batch_configs):
            instance = self._instances[local_id]
            worker = Worker(
                worker_id=local_id,
                knob_space=self.knob_space,
                knob_config=config,
            )
            worker.port = instance.port
            db_config = self.env.get_db_config(local_id)
            worker.db_config = db_config
            workers.append(worker)
        return workers

    def _evaluate_batch_parallel(
        self,
        workers: List[Worker],
        barriers: GenerationBarrier,
        generation: int,
    ) -> List[Tuple[int, Worker, Optional[PerformanceMetrics], Optional[float]]]:
        """
        Run one batch concurrently, returning per-worker results.

        Uses a thread pool sized to the batch so every config's measurement
        window overlaps under the shared barriers (identical contention).
        """
        batch_size = self.lifecycle.num_parallel_workers
        results: List[
            Tuple[int, Worker, Optional[PerformanceMetrics], Optional[float]]
        ] = []

        def _eval(worker: Worker):
            metrics, score, _restart, _cfg, timing = self.orchestrator.evaluate_worker(  # type: ignore
                worker,
                apply_config=True,
                generation=generation,
                barriers=barriers,
                restore_due=False,
                next_eval_will_restore=False,
            )
            worker.last_eval_timing = timing
            return metrics, score

        if len(workers) == 1:
            single = GenerationBarrier(num_workers=1, enabled=False)
            metrics, score, _r, _c, timing = self.orchestrator.evaluate_worker(  # type: ignore
                workers[0],
                apply_config=True,
                generation=generation,
                barriers=single,
                restore_due=False,
                next_eval_will_restore=False,
            )
            workers[0].last_eval_timing = timing
            return [(generation * batch_size, workers[0], metrics, score)]

        with ThreadPoolExecutor(max_workers=len(workers)) as executor:
            future_to_local = {
                executor.submit(_eval, w): local_id
                for local_id, w in enumerate(workers)
            }
            for future in as_completed(future_to_local):
                local_id = future_to_local[future]
                worker = workers[local_id]
                try:
                    metrics, score = future.result()
                except GenerationEvaluationError as exc:  # noqa: BLE001 - record + continue
                    LOGGER.error(
                        "Design config %d (worker %d) failed: %s",
                        generation * batch_size + local_id,
                        local_id,
                        exc,
                    )
                    barriers.abort()
                    metrics, score = None, None
                design_index = generation * batch_size + local_id
                results.append((design_index, worker, metrics, score))

        results.sort(key=lambda r: r[0])
        return results

    def _safe_breakdown(
        self, metrics: Optional[PerformanceMetrics]
    ) -> Optional[ScoreBreakdown]:
        if metrics is None or self.orchestrator is None:
            return None
        try:
            return self.orchestrator.scorer.compute_breakdown(metrics)
        except (RuntimeError, ValueError, AttributeError) as exc:
            LOGGER.debug("Failed to compute score breakdown: %s", exc)
            return None

    def should_stop(self, outcome: GenerationOutcome) -> bool:
        """Stop once the whole design has been evaluated."""
        evaluated = (outcome.index + 1) * self.lifecycle.num_parallel_workers
        return evaluated >= len(self.design)

    def collect_best(self) -> Tuple[Dict[str, Any], float, Optional[Any]]:
        return (
            self._best_config or {},
            self._best_score_so_far,
            self._best_metrics,
        )

    def build_session_payload(self) -> Dict[str, Any]:
        scoring_metadata = self.metric_config.get_scoring_metadata()
        payload: Dict[str, Any] = {
            "tuning_session": {
                "design_size": self.design_size,
                "tpch_scale_factor": self.benchmark_config.scale_factor,
                "sysbench_workload": self.benchmark_config.sysbench_workload,
                "tuning_mode": self.benchmark_config.tuning_mode.value,
                "scoring_policy": scoring_metadata.get("scoring_policy", "fixed_v1"),
                "scoring_policy_version": scoring_metadata.get(
                    "scoring_policy_version", "1.0"
                ),
                "metric_reference_version": scoring_metadata.get(
                    "metric_reference_version", "v1"
                ),
            },
            "design_records": self.design_records,
            "workload_features": scoring_metadata.get("workload_features", {}),
            "normalization_metadata": scoring_metadata.get(
                "normalization_metadata", {}
            ),
            "score_breakdown": (
                self._best_breakdown.to_dict()
                if self._best_breakdown is not None
                else {}
            ),
            "system_info": self.system_info,
        }
        if self.session_environment is not None:
            payload["session_environment"] = self.session_environment.to_dict()
        return payload

    def teardown(self) -> None:
        if self.env is None:
            return
        try:
            self.env.stop_all()
        finally:
            if self.lifecycle.cleanup_instances:
                self.env.cleanup(remove_data=True)


if __name__ == "__main__":
    # Allow `python -m src.tuners.lhs_design` to drive the CLI directly.
    from src.tuners.lhs_design_cli import main

    raise SystemExit(main())
