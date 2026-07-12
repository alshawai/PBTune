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
from src.tuners.engine.barriers import GenerationBarrier
from src.tuners.engine.worker import BaseWorker
from src.tuners.base import BaseTuner
from src.tuners.utils.exceptions import (
    GenerationEvaluationError,
    TunerConfigError,
)
from src.tuners.utils.types import (
    GenerationOutcome,
    TunerLifecycleConfig,
    TuningStrategy,
    WorkerEvalResult,
)
from src.tuners.utils.session_writer import build_scoring_block
from src.utils.logger import get_color_context, get_logger
from src.utils.metrics import PerformanceMetrics
from src.utils.scoring.contracts import ScoreBreakdown
from src.utils.types import BenchmarkConfig

LOGGER = get_logger("LHSDesignTuner")
COLORS = get_color_context()


class LHSDesignTuner(BaseTuner):
    """
    Evaluate a fixed LHS design over the knob space in parallel batches.

    The strategy-agnostic environment lifecycle (knob space, worker resources,
    orchestrator, instance bring-up, runtime knob pruning) is owned by
    :class:`~src.tuners.base.BaseTuner`. This subclass contributes only the
    design-drawing seam (:meth:`propose_initial_configs`) and the per-batch
    evaluation (:meth:`step`).

    Parameters
    ----------
    lifecycle
        Strategy-agnostic lifecycle config (forced to ``TuningStrategy.LHS``).
        Per-worker resource overrides and tuning mode live here.
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
    ) -> None:
        lifecycle.strategy = TuningStrategy.LHS
        super().__init__(lifecycle, timestamp=timestamp, output_root=output_root)

        if design_size < 1:
            raise TunerConfigError("design_size must be at least 1")

        # Strategy inputs consumed by the shared BaseTuner.setup().
        self.benchmark = benchmark
        self.benchmark_config = benchmark_config
        self.workload_file = workload_file
        self.data_root = Path(data_root) if data_root else resolve_data_root()

        # LHS-specific state.
        self.design_size = design_size
        self.design: List[Dict[str, Any]] = []

        # Result accumulation.
        self.design_records: List[Dict[str, Any]] = []
        # ``_eval_metrics`` (parallel to ``design_records``, same index; failed
        # points contribute ``None``) feeds ``total_evaluations`` /
        # ``converged``. The decoded per-observation configs now flow through
        # the shared ``_build_generation_record`` seam directly, so no separate
        # config side-array is needed.
        self._eval_metrics: List[Optional[PerformanceMetrics]] = []
        self._best_config: Optional[Dict[str, Any]] = None
        self._best_metrics: Optional[PerformanceMetrics] = None
        self._best_breakdown: Optional[ScoreBreakdown] = None

    @property
    def max_rounds(self) -> int:
        """Number of parallel batches needed to cover the design."""
        return max(1, math.ceil(self.design_size / self.lifecycle.num_parallel_workers))

    @property
    def seeded_config_count(self) -> int:
        """The design size is known up front, so seed it directly."""
        return self.design_size

    @property
    def round_label(self) -> str:
        """One LHS pass evaluates one parallel *batch* of the fixed design."""
        return "Batch"

    def config_summary_lines(self) -> List[Tuple[str, str]]:
        """Name the LHS budget line ("Design Size") in the startup summary."""
        return [
            ("Design Size:", str(self.design_size)),
            ("Design Batches:", str(self.max_rounds)),
        ]

    def best_config_fractions(self, best_config: Dict[str, Any]) -> Dict[str, Any]:
        if not best_config or self.full_knob_space is None:
            return {}
        return self.full_knob_space.config_to_fractions(best_config)

    def propose_initial_configs(self) -> List[Dict[str, Any]]:
        """Draw the fixed LHS design once and return it for evaluation.

        Called by the shared :meth:`BaseTuner.setup` after the knob space and
        environment are built. Worker 0's first slot is anchored to the
        PostgreSQL default config (mirrors PBT/BO's pilot-seed convention) so
        the design includes a known-reasonable reference.
        """
        default_config = self.full_knob_space.get_default_config()
        lhs_configs = self.full_knob_space.sample_diverse_configs(
            num_samples=self.design_size,
            seed=self.lifecycle.random_seed,
        )
        design = [default_config] + [c for c in lhs_configs if c != default_config]
        self.design = design[: self.design_size]
        LOGGER.info(
            "Drew LHS design of %s%d%s configurations over %s%d%s knobs",
            COLORS.cyan,
            len(self.design),
            COLORS.reset,
            COLORS.cyan,
            self.num_knobs,
            COLORS.reset,
        )
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
            enabled=self.lifecycle.synchronize_workers and len(workers) > 1,
        )

        best_this_batch = 0.0
        gen_start = time.time()
        results = self._evaluate_batch_parallel(workers, barriers, generation)

        # Per-worker results fed to the shared record builder. ``worker_id`` is
        # the batch-local offset so the analysis loader's join-by-worker_id
        # aligns configs with scores within this batch.
        worker_results: List[WorkerEvalResult] = []
        evaluated_indices: List[int] = []
        for design_index, worker, metrics, score in results:
            # The orchestrator already scored this worker during
            # evaluate_worker and stashed the breakdown on the worker (the
            # returned ``score`` IS ``worker.score_breakdown.final_score``).
            # Reuse it instead of re-running the composite scorer.
            breakdown = worker.score_breakdown if metrics is not None else None
            # LHS-native, fraction-encoded artifact (unchanged shape).
            self.design_records.append(
                {
                    "design_index": design_index,
                    "batch": generation,
                    "score": float(score) if score is not None else None,
                    "config": self.full_knob_space.config_to_fractions(
                        worker.knob_config
                    ),
                    "metrics": metrics.to_dict() if metrics is not None else None,
                    "score_breakdown": (
                        breakdown.to_dict() if breakdown is not None else None
                    ),
                }
            )
            self._eval_metrics.append(metrics)
            evaluated_indices.append(design_index)

            worker_results.append(
                WorkerEvalResult(
                    worker_id=design_index - start,
                    knob_config=dict(worker.knob_config),  # type: ignore
                    score=score,
                    metrics=metrics,
                    score_breakdown=breakdown,
                    timing=worker.last_eval_timing,
                )
            )

            if score is not None and score > self._best_score_so_far:
                self._best_score_so_far = float(score)
                self._best_config = dict(worker.knob_config)  # type: ignore
                self._best_metrics = metrics
                self._best_breakdown = breakdown
            if score is not None:
                best_this_batch = max(best_this_batch, float(score))

        # Emit the per-worker metrics table (LHS's optimizer has none of its
        # own); parity with PBT's end-of-generation table.
        self.log_worker_metrics_table(
            worker_results,
            title=(
                f"\n{COLORS.bold}🔷 Batch {generation} Worker Metrics 🔷"
                f"{COLORS.reset}"
            ),
        )

        # Append the UNIFORM generation record (shared shape, incl. per-worker
        # timing + score_breakdown) so an LHS trace loads exactly like a PBT one.
        self.generation_history.append(
            self._build_generation_record(
                generation=generation,
                best_score_this_round=best_this_batch,
                converged=(end >= len(self.design)),
                worker_results=worker_results,
                generation_elapsed_seconds=time.time() - gen_start,
                extra={"evaluated": evaluated_indices},
            )
        )

        outcome = GenerationOutcome(
            index=generation,
            best_score_this_generation=best_this_batch,
            payload={
                "evaluated": evaluated_indices,
                "batch_elapsed_seconds": time.time() - gen_start,
            },
        )
        LOGGER.info(
            "%sBatch %d/%d complete%s: evaluated designs %d-%d, "
            "best-so-far=%s%.4f%s",
            COLORS.bold,
            generation + 1,
            self.max_rounds,
            COLORS.reset,
            start,
            end - 1,
            COLORS.teal,
            self._best_score_so_far,
            COLORS.reset,
        )
        return outcome

    def _build_batch_workers(self, batch_configs: List[Dict[str, Any]]) -> List[BaseWorker]:
        """Construct Workers bound to instances for one batch."""
        workers: List[BaseWorker] = []
        for local_id, config in enumerate(batch_configs):
            instance = self._instances[local_id]
            worker = BaseWorker(
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
        workers: List[BaseWorker],
        barriers: GenerationBarrier,
        generation: int,
    ) -> List[Tuple[int, BaseWorker, Optional[PerformanceMetrics], Optional[float]]]:
        """
        Run one batch concurrently, returning per-worker results.

        Uses a thread pool sized to the batch so every config's measurement
        window overlaps under the shared barriers (identical contention).
        """
        batch_size = self.lifecycle.num_parallel_workers
        results: List[
            Tuple[int, BaseWorker, Optional[PerformanceMetrics], Optional[float]]
        ] = []

        # Baseline-snapshot restore cadence (PBT/BO parity). ``generation`` is
        # the batch index; a restore is due at the start of a batch whose index
        # is a positive multiple of the interval, and ``next_eval_will_restore``
        # leads by one so the orchestrator can prep the next window.
        interval = self.lifecycle.snapshot_restore_interval
        restore_due = (
            self.enable_snapshots and generation > 0 and generation % interval == 0
        )
        next_eval_will_restore = (
            self.enable_snapshots
            and (generation + 1) > 0
            and (generation + 1) % interval == 0
        )

        def _eval(worker: BaseWorker):
            metrics, score, _restart, _cfg, timing = self.orchestrator.evaluate_worker(  # type: ignore
                worker,
                apply_config=True,
                generation=generation,
                barriers=barriers,
                restore_due=restore_due,
                next_eval_will_restore=next_eval_will_restore,
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
                restore_due=restore_due,
                next_eval_will_restore=next_eval_will_restore,
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
                        "%sDesign config %d (worker %d) failed%s: %s",
                        COLORS.red,
                        generation * batch_size + local_id,
                        local_id,
                        COLORS.reset,
                        exc,
                    )
                    barriers.abort()
                    metrics, score = None, None
                design_index = generation * batch_size + local_id
                results.append((design_index, worker, metrics, score))

        results.sort(key=lambda r: r[0])
        return results

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

    def total_evaluations(self) -> int:
        """Design points that produced a measurement (failures excluded)."""
        return sum(1 for m in self._eval_metrics if m is not None)

    def converged(self) -> bool:
        """LHS always sweeps the full design, so a completed run has converged."""
        return len(self.design_records) >= self.design_size

    def build_session_payload(self) -> Dict[str, Any]:
        scoring_metadata = self.metric_config.get_scoring_metadata()
        payload: Dict[str, Any] = {
            "tuning_session": {
                "tpch_scale_factor": self.benchmark_config.scale_factor,
                "sysbench_workload": self.benchmark_config.sysbench_workload,
                "scoring": build_scoring_block(
                    scoring_metadata,
                    self._best_breakdown.to_dict()
                    if self._best_breakdown is not None
                    else {},
                ),
                "strategy_params": {
                    "design_size": self.design_size,
                },
            },
            "design_records": self.design_records,
            "system_info": self.system_info,
        }
        if self.session_environment is not None:
            payload["session_environment"] = self.session_environment.to_dict()
        return payload
