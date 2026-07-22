# Copyright (C) 2026 Ibrahim Al-Shawa and PBTune contributors
# Licensed under the GNU General Public License v3.0
# See LICENSE file for details

"""Population-Based Training tuner on the unified :class:`BaseTuner` lifecycle.

``PBTTuner`` is the PBT strategy expressed as a concrete
:class:`~src.tuners.base.BaseTuner` subclass. The strategy-agnostic lifecycle —
resource resolution, workload/executor construction, environment + orchestrator
wiring, instance bring-up, runtime knob pruning, the generation loop, teardown,
per-round record building, session-timing aggregation, and the professional
lifecycle logging — is owned by ``BaseTuner`` (levelled up to PBT's own
completeness in step 2d). This subclass contributes only what is genuinely
PBT-specific:

* the initial-config draw (default-anchored LHS, or a warm-start expansion),
* wiring the :class:`~src.tuners.pbt.population.Population` to the live instances
  and baseline snapshot (:meth:`build_optimizer`),
* one exploit/explore generation (:meth:`step`),
* the crash/dead-config failure ladder around a single worker evaluation
  (:meth:`evaluate_worker`),
* the PBT ``strategy_params`` / warm-start / scoring session sections.

The PBT CLI is ``python -m src.tuners pbt ...`` (routed door) or
``python -m src.tuners.pbt ...`` (direct door), wired in
:mod:`src.tuners.pbt.cli`. The legacy ``src/tuner/`` package has been removed
(see the 2026-07-17 addendum to ADR-006).
"""

from __future__ import annotations

import json
import math
import time
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

import numpy as np
import psycopg2

from src.config.data_root import resolve_data_root
from src.tuners.base import BaseTuner
from src.tuners.engine.barriers import GenerationBarrier
from src.tuners.pbt.config import PBTConfig
from src.tuners.pbt.population import Population, PopulationConfig
from src.tuners.pbt.worker import PBTWorker
from src.tuners.utils.exceptions import TunerConfigError
from src.tuners.utils.session_writer import build_scoring_block, convert_numpy_types
from src.tuners.utils.types import (
    GenerationOutcome,
    TunerLifecycleConfig,
    TuningStrategy,
    WorkerEvalResult,
)
from src.utils.logger import get_color_context, get_logger, log_section_header
from src.utils.metrics import PerformanceMetrics
from src.utils.types import BenchmarkConfig

LOGGER = get_logger("PBTune")
COLORS = get_color_context()


def _evolve_seconds(generation_timing: Optional[Any]) -> float:
    """Total seconds spent in the ``evolve`` span for one generation.

    PBT's only non-evaluation overhead is the exploit/explore step, recorded
    under the ``evolve`` component of the population's per-generation timing
    recorder. Returns ``0.0`` when timing is unavailable or the span is absent.
    """
    if generation_timing is None:
        return 0.0
    try:
        return float(generation_timing.aggregate().get("evolve", {}).get("total", 0.0))
    except (AttributeError, TypeError, ValueError):
        return 0.0


class PBTTuner(BaseTuner):
    """Population-Based Training as a :class:`BaseTuner` strategy.

    Parameters
    ----------
    lifecycle
        Strategy-agnostic lifecycle config (forced to ``TuningStrategy.PBT``).
        Carries the cross-cutting knobs — parallel workers, tuning mode, snapshot
        cadence, per-worker resources, scoring provenance, ``synchronize_workers``
        and ``disable_early_stopping``.
    pbt_config
        PBT hyperparameter bundle (population size, generations, exploit
        quantile, perturbation factors, dead-config scoring, ...). The analogue
        of LHS's ``design_size`` scalar.
    benchmark
        Workload driver: 'sysbench', 'tpch', or None for a custom template.
    benchmark_config
        Benchmark/workload settings (durations, scale factor, ...).
    timestamp
        Session id used in output filenames.
    output_root
        Base results directory for this run (already strategy/tier-scoped).
    workload_file
        Path to a custom workload file (only for non-sysbench/tpch runs).
    data_root
        Override for the data directory (defaults to ``resolve_data_root()``).
    warm_start_path
        Optional path to a previous ``best_config``/session JSON to warm-start
        from. When set, half the population is seeded from perturbed variants of
        the recovered config and the rest from LHS.
    ablation_variable, ablation_value
        Optional ablation-study tags (recorded in ``strategy_params``).
    """

    def __init__(
        self,
        lifecycle: TunerLifecycleConfig,
        *,
        pbt_config: PBTConfig,
        benchmark: Optional[str],
        benchmark_config: BenchmarkConfig,
        timestamp: str,
        output_root: Path,
        workload_file: Optional[str] = None,
        data_root: Optional[Path] = None,
        warm_start_path: Optional[str] = None,
        ablation_variable: Optional[str] = None,
        ablation_value: Optional[str] = None,
    ) -> None:
        lifecycle.strategy = TuningStrategy.PBT
        super().__init__(lifecycle, timestamp=timestamp, output_root=output_root)

        # Strategy inputs consumed by the shared BaseTuner.setup().
        self.benchmark = benchmark
        self.benchmark_config = benchmark_config
        self.workload_file = workload_file
        self.data_root = Path(data_root) if data_root else resolve_data_root()

        # PBT strategy state.
        self.pbt_config = pbt_config
        self.warm_start_path = warm_start_path
        self.warm_start_provenance: Dict[str, Any] = {"enabled": False}
        self.ablation_variable = ablation_variable
        self.ablation_value = ablation_value

        # Built in build_optimizer(), after instances are live.
        self.population: Optional[Population] = None

        # Per-generation restart accounting (mirrors the incumbent tuner).
        self.current_generation: int = 0
        self.restart_count: int = 0
        self._restarted_this_generation: bool = False

        # Distributed multi-device mode (populated in _create_environment()).
        # Typed Any: the Coordinator/FleetBootstrapper classes are imported
        # lazily inside _create_environment().
        self._coordinator: Any = None
        self._bootstrapper: Any = None
        if self.lifecycle.distributed:
            if not self.lifecycle.inventory:
                raise TunerConfigError(
                    "--distributed requires --inventory <devices.yaml>"
                )
            # One worker per dedicated device => no co-tenancy, so the B1–B17
            # substep barriers (which only equalise co-tenancy) are pointless;
            # the generation boundary is the sole sync point. Run the whole
            # fleet concurrently — never batch.
            self.lifecycle.synchronize_workers = False
            self.pbt_config.synchronize_workers = False  # session provenance
            if self.lifecycle.num_parallel_workers < self.pbt_config.population_size:
                self.lifecycle.num_parallel_workers = self.pbt_config.population_size

    @property
    def max_rounds(self) -> int:
        """PBT runs a fixed number of generations."""
        return self.pbt_config.num_generations

    @property
    def seeded_config_count(self) -> int:
        """One worker (and one instance) per population member."""
        return self.pbt_config.population_size

    @property
    def round_label(self) -> str:
        """PBT breeds a new *generation* of the population each pass."""
        return "Generation"

    @property
    def emits_stop_status(self) -> bool:
        """PBT halts on a criterion (max-gens / early-stop / convergence)."""
        return True

    @property
    def num_instances(self) -> int:
        """PBT dedicates a PostgreSQL instance to every population member.

        This is the one structural difference from a batched sweep (LHS reuses
        ``num_parallel_workers`` instances across batches): PBT evaluates the
        whole population each generation, so it needs ``population_size``
        instances.
        """
        return self.pbt_config.population_size

    def config_summary_lines(self) -> List[Tuple[str, str]]:
        return [
            ("Population Size:", str(self.pbt_config.population_size)),
            ("Max Generations:", str(self.pbt_config.num_generations)),
        ]

    def best_config_fractions(self, best_config: Dict[str, Any]) -> Dict[str, Any]:
        if not best_config or self.full_knob_space is None:
            return {}
        return self.full_knob_space.config_to_fractions(best_config)

    # ------------------------------------------------------------------
    # Config draw + optimizer wiring
    # ------------------------------------------------------------------
    def propose_initial_configs(self) -> List[Dict[str, Any]]:
        """Draw the population's initial configs (default-anchored or warm-start).

        Pure draw — no ``Population`` is built here (that is
        :meth:`build_optimizer`'s job, once instances are live). When warm-start
        is requested, half the population is seeded from perturbed variants of
        the recovered config, the remainder from LHS; otherwise worker 0 is
        anchored to the PostgreSQL default (mirroring BO's pilot seed) and the
        rest are space-filled by LHS.
        """
        population_size = self.pbt_config.population_size
        if self.warm_start_path:
            LOGGER.info(" Warm-starting from %s", self.warm_start_path)
            warm_configs = self._build_warm_start_configs(
                warm_start_path=Path(self.warm_start_path),
                population_size=population_size,
                seed=42,
            )
            num_lhs = population_size - len(warm_configs)
            if num_lhs > 0:
                warm_configs.extend(
                    self.full_knob_space.sample_diverse_configs(
                        num_samples=num_lhs, seed=self.lifecycle.random_seed
                    )
                )
            
            LOGGER.debug(
                "➤ Warm-started with %d configs, %d LHS configs",
                len(warm_configs), num_lhs
            )
            return warm_configs[:population_size]

        # Mirror BO's pilot-seed convention: worker 0 starts from the
        # PostgreSQL default so both algorithms share the same known-reasonable
        # anchor; the rest are LHS for diverse coverage.
        default_config = self.full_knob_space.get_default_config()
        lhs_configs = self.full_knob_space.sample_diverse_configs(
            num_samples=population_size, seed=self.lifecycle.random_seed
        )
        initial_configs = [default_config] + [
            c for c in lhs_configs if c != default_config
        ]

        LOGGER.debug(
            "➤ Proposed %d initial configs (1 default + %d LHS)",
            len(initial_configs), len(initial_configs) - 1
        )
        return initial_configs[:population_size]

    # ------------------------------------------------------------------
    # Distributed lifecycle overrides (no-ops unless --distributed)
    # ------------------------------------------------------------------
    def _create_environment(self) -> None:
        """Build the RemoteEnvironment + coordinator when distributed; else base.

        No agents are contacted here — only local objects are built. The SSH
        bootstrap and health handshake happen in :meth:`_bring_up_instances`.
        """
        if not self.lifecycle.distributed:
            super()._create_environment()
            return

        from src.config.database import get_db_config
        from src.tuners.distributed.agent_api import SetupRequest
        from src.tuners.distributed.bootstrap import FleetBootstrapper
        from src.tuners.distributed.config import DistributedConfig
        from src.tuners.distributed.coordinator import Coordinator
        from src.tuners.engine.orchestrator import WorkloadOrchestratorConfig

        assert self.worker_resources is not None
        # Guaranteed by the __init__ guard when distributed is set.
        assert self.lifecycle.inventory is not None
        db_config = get_db_config()
        pop_size = self.pbt_config.population_size

        dist_cfg = DistributedConfig.from_inventory_path(
            self.lifecycle.inventory,
            request_timeout_s=float(self.lifecycle.agent_timeout),
            eval_timeout_s=float(self.lifecycle.eval_timeout),
        )
        dist_cfg.validate_for_population(pop_size)

        bc = self.benchmark_config
        if self.benchmark == "sysbench":
            wl_str = bc.sysbench_workload
        elif self.benchmark == "tpch":
            wl_str = "olap"
        else:
            wl_str = self._workload_type.value
        setup_template = SetupRequest(
            run_id=self.snapshot_identifier,
            benchmark=self.benchmark or "sysbench",
            workload_type=wl_str,
            use_docker=self.lifecycle.use_docker,
            force_recreate_baseline=self.lifecycle.force_recreate_baseline,
            tables=getattr(bc, "sysbench_tables", None),
            table_size=getattr(bc, "sysbench_table_size", None),
            scale_factor=getattr(bc, "scale_factor", None),
            image_name=self.lifecycle.docker_image,
            dbname=db_config.dbname,
            db_user=db_config.user,
        )

        coordinator = Coordinator(dist_cfg, setup_template, db_config, pop_size)
        self._coordinator = coordinator
        if self.lifecycle.bootstrap:
            self._bootstrapper = FleetBootstrapper(
                inventory=dist_cfg.inventory,
                population_size=pop_size,
                local_repo_dir=str(Path(__file__).resolve().parents[3]),
                knob_tier=self.lifecycle.knob_tier,
                knob_source=self.lifecycle.knob_source,
                install_deps=self.lifecycle.remote_install_deps,
                env_exports=(
                    {"DB_PASSWORD": db_config.password}
                    if db_config.password
                    else None
                ),
            )

        self.env = coordinator.make_environment(
            self._workload_executor, self.snapshot_identifier
        )
        orchestrator_config = WorkloadOrchestratorConfig(
            workload_type=self._workload_type,
            metric_config=self.metric_config,
            db_config=db_config,
            warmup_duration=self.benchmark_config.warmup_duration,
            measurement_duration=self.benchmark_config.evaluation_duration,
            cooldown_duration=3.0,
            tuning_mode=self.lifecycle.tuning_mode,
            adaptive_restart_interval=self.lifecycle.adaptive_restart_interval,
            random_seed=self.lifecycle.random_seed,
            warmup_passes=self.benchmark_config.warmup_passes,
            worker_memory_budget_bytes=self.worker_resources.ram_bytes,
        )
        self.orchestrator = coordinator.make_orchestrator(
            orchestrator_config, self._workload_executor, self.env
        )

    def _bring_up_instances(self) -> None:
        """Bring up the device fleet when distributed; else the local flow."""
        if not self.lifecycle.distributed:
            super()._bring_up_instances()
            return

        from src.utils.hardware_info import get_system_info

        self.system_info = get_system_info(data_path=self.data_root)
        log_section_header(
            LOGGER, "Bringing up distributed device fleet", top_separator=False
        )

        # SSH bootstrap (optional) + health/protocol handshake.
        with self.bootstrap_timing.span("bootstrap_fleet"):
            if self._bootstrapper is not None:
                LOGGER.info("Bootstrapping device fleet over SSH...")
                self._bootstrapper.bootstrap_all()
            LOGGER.info("Waiting for device agents to report healthy...")
            self._coordinator.wait_for_agents()

        with self.bootstrap_timing.span("setup_instances"):
            self._instances = self.env.setup_instances(
                num_workers=self.num_instances,
                force_recreate=self.lifecycle.force_recreate_instances,
                num_parallel_workers=self.num_instances,
            )
        with self.bootstrap_timing.span("verify_instances"):
            self.env.verify_instances()

        # Resolve hardware-aware knob ranges from DEVICE hardware (reported by
        # the agents), not the coordinator's — so the coordinator can run on any
        # light machine.
        self._resolve_device_hardware_ranges()

        # Version-based knob pruning is skipped: the coordinator has no direct
        # TCP path to the remote instances; the identical-fleet assumption holds
        # (every device runs the same PostgreSQL).
        LOGGER.info(
            "Distributed mode: skipping direct-connection knob prune "
            "(identical-fleet assumption)."
        )
        LOGGER.info(
            "%s%sDevice fleet is ready.%s", COLORS.bold, COLORS.green, COLORS.reset
        )

    def _resolve_device_hardware_ranges(self) -> None:
        """Re-resolve hardware-aware knob ranges from DEVICE hardware."""
        import dataclasses

        from src.utils.hardware_info import WorkerResources

        get_res = getattr(self.env, "representative_resources", None)
        res = get_res() if callable(get_res) else None
        if not res:
            LOGGER.warning(
                "No device resources reported; keeping coordinator-derived knob "
                "ranges. If coordinator hardware differs from the devices, pass "
                "--worker-ram/--worker-cpus to match the fleet."
            )
            return
        valid = {f.name for f in dataclasses.fields(WorkerResources)}
        device_res = WorkerResources(**{k: v for k, v in res.items() if k in valid})
        # Resolve knob ranges against device hardware. We intentionally do NOT
        # reassign self.worker_resources (an inherited base attribute) here —
        # the knob space carries the device resources directly.
        self.full_knob_space.resolve_hardware_ranges(device_res)
        self.knob_space.worker_resources = device_res
        LOGGER.info(
            "Resolved knob hardware ranges from DEVICE hardware "
            "(RAM=%.1f GB, CPU=%s cores, disk=%s) — coordinator hardware ignored.",
            (device_res.ram_bytes or 0) / 1e9,
            device_res.cpu_cores,
            device_res.disk_type,
        )

    def teardown(self) -> None:
        """Stop instances, then shut the device fleet down (distributed only)."""
        try:
            super().teardown()
        finally:
            if getattr(self.lifecycle, "distributed", False) and getattr(
                self, "_coordinator", None
            ) is not None:
                try:
                    self._coordinator.shutdown_agents()
                    if self._bootstrapper is not None:
                        self._bootstrapper.teardown_all()
                except (RuntimeError, OSError) as exc:
                    LOGGER.warning("Device fleet shutdown issue: %s", exc)

    def build_optimizer(self) -> None:
        """Wire the :class:`Population` to the live instances and baseline snapshot.

        Runs at the end of :meth:`BaseTuner.setup`, after
        :meth:`propose_initial_configs` drew the seed configs and the shared
        bring-up left instances + orchestrator in place. This is exactly the
        seam the ``build_optimizer`` hook exists for.
        """
        LOGGER.info("")
        log_section_header(
            LOGGER, "Initializing PBT population", top_separator=False,
        )

        pop_config = PopulationConfig(
            population_size=self.pbt_config.population_size,
            ready_interval=self.pbt_config.ready_interval,
            exploit_quantile=self.pbt_config.exploit_quantile,
            perturbation_factors=self.pbt_config.perturbation_factors,
            convergence_threshold=0.05,
            max_generations=self.pbt_config.num_generations,
            early_stopping_patience=10,
            disable_early_stopping=self.lifecycle.disable_early_stopping,
            dead_config_threshold=self.pbt_config.dead_config_threshold,
            resample_probability=self.pbt_config.resample_probability,
        )
        self.population = Population(
            self.knob_space, pop_config, orchestrator=self.orchestrator
        )

        LOGGER.info(
            "Initializing %d worker configurations", self.pbt_config.population_size
        )
        self.population.initialize(
            initial_configs=list(self.initial_configs),
            random_seed=self.lifecycle.random_seed,
        )

        LOGGER.info("Assigning instance configurations to workers...")
        db_config = self.env.get_db_config(0)
        self.population.setup_worker_instances(
            instances=self._instances,
            dbname=db_config.dbname,
            user=db_config.user,
            password=db_config.password,
        )

        LOGGER.info("Configuring snapshot restoration...")
        self.pbt_config.enable_snapshots = self.enable_snapshots
        self.pbt_config.snapshot_restore_interval = (
            self.lifecycle.snapshot_restore_interval
        )
        with self.bootstrap_timing.span("setup_snapshots"):
            self.population.setup_snapshots(env=self.env, pbt_config=self.pbt_config)

        LOGGER.info(
            "Initialized %s%s%d%s workers with dedicated instances.",
            COLORS.bold,
            COLORS.cyan,
            len(self.population.workers),
            COLORS.reset,
        )

    def step(self, generation: int) -> GenerationOutcome:
        """Run one exploit/explore generation and record its uniform history."""
        assert self.population is not None  # built in build_optimizer()
        self.current_generation = generation
        self._restarted_this_generation = False

        gen_start = time.time()
        result = self.population.train_generation(
            self.evaluate_worker,
            parallel=True,
            require_ready=True,
            max_workers=self.lifecycle.num_parallel_workers,
            synchronize_workers=self.lifecycle.synchronize_workers,
        )
        gen_elapsed = time.time() - gen_start

        worker_results = [
            WorkerEvalResult(
                worker_id=w.worker_id,
                knob_config=dict(w.knob_config) if w.knob_config else {},
                score=(
                    float(w.performance_score)
                    if w.performance_score is not None
                    else None
                ),
                metrics=w.metrics,
                score_breakdown=w.score_breakdown,
                timing=w.last_eval_timing,
            )
            for w in self.population.workers
        ]

        gen_timing = getattr(self.population, "generation_timing", None)
        self.generation_history.append(
            self._build_generation_record(
                generation=generation,
                best_score_this_round=result.best_score,
                worker_results=worker_results,
                generation_elapsed_seconds=gen_elapsed,
                restart_count=self.restart_count,
                generation_timing=gen_timing,
                mean_score=result.mean_score,
                std_score=result.std_score,
                num_exploited=result.num_exploited,
                overhead_seconds=_evolve_seconds(gen_timing),
                extra={"exploitations": result.exploitations},
            )
        )

        return GenerationOutcome(
            index=generation,
            best_score_this_generation=result.best_score,
            converged=result.converged,
            payload={
                "restart_count": self.restart_count,
                "mean_score": result.mean_score,
                "std_score": result.std_score,
                "num_exploited": result.num_exploited,
            },
        )

    def should_stop(self, outcome: GenerationOutcome) -> bool:
        assert self.population is not None
        stop = self.population.should_stop()
        # Surface the population's stopping criterion as the round-summary
        # Status line (base reads self.stop_reason when emits_stop_status).
        self.stop_reason = self.population.stop_reason
        return stop

    def collect_best(self) -> Tuple[Dict[str, Any], float, Optional[Any]]:
        if self.population is None:
            return {}, 0.0, None
        config, score = self.population.get_best_configuration()
        return config, score, self.population.best_overall_metrics

    def total_evaluations(self) -> int:
        """Population members benchmarked across every completed generation."""
        return self.pbt_config.population_size * self._rounds_completed

    def converged(self) -> bool:
        if self.population is None or not self.population.history:
            return False
        return bool(self.population.history[-1].converged)

    def strategy_overhead_seconds(self) -> float:
        """Total PBT machinery time: the ``evolve`` (exploit/explore) phase.

        Summed from each generation record's flat ``pbt_overhead_seconds``
        field so the top-level ``tuning_session.strategy_overhead_seconds``
        reflects PBT's real non-evaluation cost instead of ``0.0``.
        """
        return float(
            sum(
                rec.get("pbt_overhead_seconds", 0.0) or 0.0
                for rec in self.generation_history
            )
        )

    def evaluate_worker(
        self,
        worker: PBTWorker,
        *,
        barriers: Optional[GenerationBarrier] = None,
    ) -> Tuple[PerformanceMetrics, float]:
        """Evaluate a single worker, mapping failures to PBT fallback scores.

        Passed to ``Population.train_generation`` as the ``evaluate_fn``. The
        happy path delegates to the shared ``orchestrator.evaluate_worker``
        (which returns ``(metrics, score, ...)`` and never mutates the worker);
        connection/timeout/runtime failures are mapped to the dead-config or
        crash score so a broken config scores poorly instead of aborting the run.
        """
        assert self.orchestrator is not None and self.population is not None
        try:
            worker.logger.info(
                "Evaluating configuration on instance port %d...", worker.port or 0
            )
            restore_due = getattr(self.population, "_restore_due_this_gen", False)
            next_eval_will_restore = getattr(
                self.population, "_restore_due_next_gen", False
            )

            metrics, score, restart_occurred, _actual_db_config, eval_timing = (
                self.orchestrator.evaluate_worker(
                    worker,
                    apply_config=True,
                    generation=self.current_generation,
                    barriers=barriers,
                    restore_due=restore_due,
                    next_eval_will_restore=next_eval_will_restore,
                )
            )

            if restart_occurred and not self._restarted_this_generation:
                self.restart_count += 1
                self._restarted_this_generation = True

            worker.last_eval_timing = eval_timing
            return metrics, score

        except (ConnectionError, psycopg2.Error) as exc:
            if barriers is not None:
                barriers.drain_remaining("connected", worker_id=worker.worker_id)
            self._attempt_recovery(worker)
            return self._build_failure_result(
                worker=worker,
                reason="connection",
                exception=exc,
                failure_type="crash_dead",
                score=self.pbt_config.dead_config_score,
            )
        except TimeoutError as exc:
            if barriers is not None:
                barriers.drain_remaining("connected", worker_id=worker.worker_id)
            return self._build_failure_result(
                worker=worker,
                reason="timeout",
                exception=exc,
                failure_type="crash_timeout",
                score=self.pbt_config.crash_score,
            )
        except RuntimeError as exc:
            if barriers is not None:
                barriers.drain_remaining("connected", worker_id=worker.worker_id)
            return self._build_failure_result(
                worker=worker,
                reason="runtime",
                exception=exc,
                failure_type="crash_runtime",
                score=self.pbt_config.crash_score,
            )
        except Exception as exc:  # noqa: BLE001 - last-resort fallback
            if barriers is not None:
                barriers.drain_remaining("connected", worker_id=worker.worker_id)
            worker.logger.error(
                "Unexpected error evaluating worker %s: %s",
                worker.worker_id,
                exc,
                exc_info=True,
            )
            return self._build_failure_result(
                worker=worker,
                reason="unexpected",
                exception=exc,
                failure_type="crash_unexpected",
                score=self.pbt_config.crash_score,
            )

    def _attempt_recovery(self, worker: PBTWorker) -> None:
        """Best-effort immediate instance recovery after a connection failure."""
        if self.env is None:
            worker.logger.error(" ➤ No environment available for immediate recovery")
            return
        try:
            recovered = self.env.recover_instance(worker.worker_id)
        except (ConnectionError, RuntimeError, OSError) as recovery_error:
            worker.logger.error(
                " ➤ Immediate recovery raised an unexpected error: %s",
                recovery_error,
                exc_info=True,
            )
            return
        if recovered:
            worker.logger.debug(
                " ➤ Immediate instance recovery succeeded after connection failure"
            )
        else:
            worker.logger.error(
                " ➤ Immediate instance recovery failed after connection failure"
            )

    def _build_failure_result(
        self,
        *,
        worker: PBTWorker,
        reason: str,
        exception: Exception,
        failure_type: str,
        score: float,
    ) -> Tuple[PerformanceMetrics, float]:
        """Build standardized fallback metrics + score for a failed evaluation."""
        worker.logger.warning("➤ Evaluation failed (%s): %s", reason, exception)
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
        assert self.orchestrator is not None
        worker.score_breakdown = self.orchestrator.scorer.compute_breakdown(
            fallback_metrics, worker_logger=worker.logger
        )
        return fallback_metrics, score

    def build_session_payload(self) -> Dict[str, Any]:
        """Assemble PBT's strategy-specific session sections (nested schema)."""
        scoring_metadata = self.metric_config.get_scoring_metadata()
        best_breakdown = (
            self.population.best_overall_score_breakdown
            if self.population is not None
            else None
        )
        gens_without_improvement = (
            int(self.population.generations_without_improvement)
            if self.population is not None
            else 0
        )

        strategy_params: Dict[str, Any] = {
            "population_size": self.pbt_config.population_size,
            "generations": self.pbt_config.num_generations,
            "exploit_quantile": self.pbt_config.exploit_quantile,
            "perturbation_factors": list(self.pbt_config.perturbation_factors),
            "ready_interval": self.pbt_config.ready_interval,
            "dead_config_threshold": self.pbt_config.dead_config_threshold,
            "adaptive_restart_interval": self.lifecycle.adaptive_restart_interval,
            "enable_snapshots": self.enable_snapshots,
            "snapshot_restore_interval": self.lifecycle.snapshot_restore_interval,
            "generations_without_improvement": gens_without_improvement,
            "warm_start": self.warm_start_provenance,
            "effective_seed": getattr(self.population, "master_seed", None),
        }
        if self.ablation_variable is not None:
            strategy_params["ablation_variable"] = self.ablation_variable
            strategy_params["ablation_value"] = self.ablation_value

        payload: Dict[str, Any] = {
            "tuning_session": {
                "scoring": build_scoring_block(
                    scoring_metadata,
                    convert_numpy_types(best_breakdown.to_dict())
                    if best_breakdown is not None
                    else {},
                ),
                "strategy_params": strategy_params,
            },
            "warm_start": self.warm_start_provenance,
        }
        return payload

    def _compute_warm_start_perturbation_factors(
        self, num_variants: int
    ) -> List[Tuple[float, float]]:
        """Graduated perturbation spreads for warm-start variants."""
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
        """Build seed configs from a previous best-config / session artifact.

        Accepts either a flat ``best_config_*.json`` (knob -> fraction) or a
        ``pbt_results_*.json`` (nested at ``best_configuration.knobs``).
        """
        with open(warm_start_path, "r", encoding="utf-8") as f:
            warm_start_data = json.load(f)

        if not isinstance(warm_start_data, dict):
            raise TunerConfigError(
                "Warm-start file must be a JSON object containing knob fractions"
            )

        if "best_configuration" in warm_start_data:
            best_configuration = warm_start_data.get("best_configuration")
            if not isinstance(best_configuration, dict):
                raise TunerConfigError(
                    "Warm-start tuning session file has invalid best_configuration block"
                )
            knobs = best_configuration.get("knobs")
            if not isinstance(knobs, dict):
                raise TunerConfigError(
                    "Warm-start tuning session file is missing best_configuration.knobs"
                )
            best_config_frac: Dict[str, Any] = knobs
        else:
            best_config_frac = warm_start_data

        self._validate_warm_start_fractions(best_config_frac)
        base_config = self.full_knob_space.fractions_to_config(best_config_frac)

        missing_knobs = [
            k for k in self.full_knob_space.knobs if k not in base_config
        ]
        if missing_knobs:
            LOGGER.warning(
                " Warm-start config missing knobs, filling with random values: %s",
                missing_knobs,
            )
            template = self.full_knob_space.sample_random_config(seed=seed)
            for k in missing_knobs:
                base_config[k] = template[k]

        dropped_knobs = [
            k for k in base_config if k not in self.full_knob_space.knobs
        ]
        for k in dropped_knobs:
            del base_config[k]
        if dropped_knobs:
            LOGGER.warning(" Warm-start config dropped extra knobs: %s", dropped_knobs)

        is_valid, errors = self.full_knob_space.validate_config(base_config)
        if not is_valid:
            LOGGER.warning(
                " Warm-start base config validation issues: %s. Repairing.", errors
            )
        base_config = self.full_knob_space.repair_config_dependencies(
            base_config, worker_id=0
        )

        num_warm_start = math.ceil(population_size / 2)
        warm_configs = [base_config]
        factors = self._compute_warm_start_perturbation_factors(num_warm_start - 1)
        warm_rng = np.random.default_rng(seed)
        for f_min, f_max in factors:
            warm_configs.append(
                self.full_knob_space.perturb_config(
                    base_config, perturbation_factor=(f_min, f_max), rng=warm_rng
                )
            )

        self.warm_start_provenance = {
            "enabled": True,
            "source_path": str(warm_start_path),
            "num_warm_start_workers": num_warm_start,
            "num_lhs_workers": population_size - num_warm_start,
            "perturbation_factors": factors,
        }
        return warm_configs

    def _validate_warm_start_fractions(
        self, best_config_frac: Dict[str, Any]
    ) -> None:
        """Reject a warm-start file that stored absolute values as fractions."""
        resources = self.full_knob_space.worker_resources
        for knob_name, knob_val in best_config_frac.items():
            if knob_name not in self.full_knob_space.knobs:
                continue
            knob = self.full_knob_space.knobs[knob_name]
            if not knob.hardware_relative or knob.resource_type == "disk_type":
                continue
            raw_abs = None
            if resources is not None:
                if knob.resource_type == "ram":
                    bytes_per_unit = self.full_knob_space._get_bytes_per_unit(knob)
                    raw_abs = (knob_val * resources.ram_bytes) / bytes_per_unit
                elif knob.resource_type == "cpu":
                    raw_abs = knob_val * resources.cpu_cores
            if raw_abs is not None and knob.max_value is not None:
                if raw_abs > knob.max_value * 1.05:  # 5% rounding tolerance
                    raise TunerConfigError(
                        f"Warm-start config contains absolute value for "
                        f"hardware-relative knob {knob_name}. Fraction {knob_val} "
                        f"resolves to {raw_abs:.0f}, exceeding max {knob.max_value}."
                    )
