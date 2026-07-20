"""Bayesian Optimization tuner on the unified :class:`BaseTuner` lifecycle.

``BOTuner`` is the SMAC3-backed Bayesian Optimization strategy expressed as a
concrete :class:`~src.tuners.base.BaseTuner` subclass. The strategy-agnostic
lifecycle — resource resolution, workload/executor construction, environment +
orchestrator wiring, instance bring-up, runtime knob pruning, teardown,
session-timing aggregation, and the professional lifecycle logging — is owned by
``BaseTuner``. This subclass contributes only what is genuinely BO-specific:

* building the ConfigSpace and pre-generating the Sobol pilot design
  (:meth:`propose_initial_configs`),
* constructing the SMAC facade (surrogate + acquisition) and wiring the
  co-tenant load controller against the live instances (:meth:`build_optimizer`),
* the full sequential ask-tell loop with Pilot+Freeze normalization, dynamic
  relabeling, and read-back merge, run as a single :meth:`step`,
* the BO ``strategy_params`` / scoring / co-tenancy session sections.

Design decision A1 (see the migration plan): the entire ask-tell loop lives
inside one ``step()`` with ``max_rounds == 1``. BO is strictly sequential with a
single foreground worker; co-tenancy adds ``degree - 1`` background load
instances so each measurement window sees the same single-host contention a PBT
generation does.

The BO CLI is ``python -m src.tuners bo ...`` (routed door) or
``python -m src.tuners.bo ...`` (direct door), wired in
:mod:`src.tuners.bo.cli`.
"""

from __future__ import annotations

import json
import shutil
import time
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

from ConfigSpace import Configuration, ConfigurationSpace
from smac import BlackBoxFacade, HyperparameterOptimizationFacade
from smac.initial_design import SobolInitialDesign
from smac.random_design import ProbabilityRandomDesign
from smac.scenario import Scenario
from smac.runhistory.dataclasses import TrialInfo, TrialValue
from smac.runhistory.enumerations import StatusType

from src.config.data_root import resolve_data_root
from src.config.database import get_db_config
from src.tuners.base import BaseTuner
from src.tuners.engine.worker import BaseWorker
from src.tuners.engine.orchestrator import WorkloadOrchestrator
from src.tuners.utils.metrics_table import build_worker_metric_row
from src.tuners.utils.session_writer import build_scoring_block, convert_numpy_types
from src.tuners.utils.types import (
    GenerationOutcome,
    TunerLifecycleConfig,
    TuningStrategy,
)
from src.tuners.bo.config import BOConfig
from src.tuners.bo.search_space import (
    build_configspace,
    configspace_to_knobs,
    get_config_drift,
    knobs_to_configspace,
)
from src.tuners.bo.objective import evaluate_config
from src.tuners.bo.cotenant import CoTenantLoadController
from src.utils.applicator import KnobApplicator
from src.utils.logger import (
    get_color_context,
    get_logger,
    log_section_header,
    log_worker_metrics_table,
)
from src.utils.metrics import PerformanceMetrics
from src.utils.timing import TimingRecorder
from src.utils.types import BenchmarkConfig

LOGGER = get_logger("BOBaseline")
COLORS = get_color_context()


@dataclass
class EvalRecord:
    """Parallel history entry used for dynamic SMAC cost relabeling.

    Every evaluated configuration is stored here so that when the
    normalization bounds expand, all past costs can be recomputed and
    overwritten in SMAC's RunHistory via ``force_update=True``.
    """

    config: Configuration  # resolved (DB-quantized) ConfigSpace config
    raw_metrics: "PerformanceMetrics | None"  # raw metrics object (None on crash)
    trial_info: TrialInfo  # TrialInfo used in the matching tell() call
    eval_time: float  # wall-clock seconds
    status: StatusType = field(default=StatusType.SUCCESS)


class BOTuner(BaseTuner):
    """Bayesian Optimization as a :class:`BaseTuner` strategy.

    Parameters
    ----------
    lifecycle
        Strategy-agnostic lifecycle config (forced to ``TuningStrategy.BO``).
        For BO, ``num_parallel_workers`` carries the co-tenancy degree (the
        total concurrent instances on the host); the foreground optimizer is
        strictly sequential (``seeded_config_count == 1``).
    bo_config
        BO hyperparameter bundle (iterations, surrogate, pilot size, snapshot
        cadence, co-tenancy, early stopping, reference-PBT knob filter).
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
    """

    def __init__(
        self,
        lifecycle: TunerLifecycleConfig,
        *,
        bo_config: BOConfig,
        benchmark: Optional[str],
        benchmark_config: BenchmarkConfig,
        timestamp: str,
        output_root: Path,
        workload_file: Optional[str] = None,
        data_root: Optional[Path] = None,
    ) -> None:
        lifecycle.strategy = TuningStrategy.BO
        super().__init__(lifecycle, timestamp=timestamp, output_root=output_root)

        # Strategy inputs consumed by the shared BaseTuner.setup().
        self.benchmark = benchmark
        self.benchmark_config = benchmark_config
        self.workload_file = workload_file
        self.data_root = Path(data_root) if data_root else resolve_data_root()

        # BO strategy state.
        self.bo_config = bo_config
        # ``--disable-early-stopping`` (lifecycle) forces the gate off regardless
        # of the BO preset default.
        self._early_stopping_enabled = (
            bo_config.early_stopping_enabled and not lifecycle.disable_early_stopping
        )
        self._early_stopping_patience = bo_config.early_stopping_patience

        # BO control-loop recorder collects facade.ask / facade.tell spans and
        # bootstrap-calibration cost. Folded into the session timing_summary.
        self.bo_timing = TimingRecorder()

        # Populated during setup()/step().
        self.configspace: Optional[ConfigurationSpace] = None
        self.sobol_configs: List[Configuration] = []
        self.requested_pilot_size: int = 0
        self.actual_pilot_size: int = 0
        self.facade: Any = None
        self.worker: Optional[BaseWorker] = None
        self.cotenant: Optional[CoTenantLoadController] = None
        self.bo_surrogate: str = bo_config.bo_surrogate
        self.iteration_log: List[Dict[str, Any]] = []
        self._early_stopped: bool = False
        self._stale_counter: int = 0

        # ── Step-by-step ask-tell loop state ──────────────────────────────────
        # BO drives one ``step()`` per iteration (like PBT's one-generation
        # step), so the ask/tell state that used to be loop locals in the old
        # single-shot ``_run_sequential_optimization`` now persists on the
        # instance across steps. Reset in :meth:`build_optimizer`.
        self._eval_history: List[EvalRecord] = []
        self._previous_engine_config: Optional[Dict[str, Any]] = None
        self._bo_best_score: float = 0.0
        self._calibrated: bool = False
        self._stop_now: bool = False
        self._stop_reason_detail: str = ""

    # ── BaseTuner hook overrides ──────────────────────────────────────────────

    @property
    def max_rounds(self) -> int:
        """One ``step()`` per BO iteration — the planned ceiling is the budget.

        Bootstrap pilots and the adaptive ask/tell iterations are both counted
        as rounds so the base loop prints "ITERATION 0..N-1" with a per-iteration
        summary, matching PBT/LHS cadence. ``should_stop`` halts early when the
        budget is reached or early-stopping fires.
        """
        return max(1, int(self.bo_config.n_iterations))

    @property
    def round_label(self) -> str:
        return "Iteration"

    @property
    def emits_stop_status(self) -> bool:
        return True

    @property
    def seeded_config_count(self) -> int:
        """BO optimizes a single foreground stream (one config at a time)."""
        return 1

    @property
    def num_instances(self) -> int:
        """One foreground BO instance plus ``degree - 1`` co-tenant loaders.

        BO is strictly sequential, so the instance count is the matched
        co-tenancy degree (never ``num_parallel_workers``): every measurement
        window sees the same single-host contention a PBT generation of that
        width does. ``degree == 1`` brings up just the foreground instance.
        """
        return max(1, int(self.bo_config.cotenancy_degree))

    def config_summary_lines(self) -> List[Tuple[str, str]]:
        return [
            ("Iterations:", str(self.bo_config.n_iterations)),
            ("Surrogate:", self.bo_surrogate.upper()),
            ("Pilot Size:", str(self.requested_pilot_size or self.bo_config.range_update_interval)),
        ]

    def best_config_fractions(self, best_config: Dict[str, Any]) -> Dict[str, Any]:
        if not best_config or self.full_knob_space is None:
            return {}
        return self.full_knob_space.config_to_fractions(best_config)

    def _prune_unsupported_runtime_knobs(self) -> None:
        """Prune runtime-unsupported knobs, then apply the reference-PBT filter."""
        super()._prune_unsupported_runtime_knobs()
        self._apply_pbt_knob_filter()

    def propose_initial_configs(self) -> List[Dict[str, Any]]:
        """Build the ConfigSpace and pre-generate the Sobol pilot design.

        Runs after the shared bring-up has pruned knobs and applied the
        reference-PBT filter, so the ConfigSpace reflects the final search
        space. The Sobol pilot configs are generated directly (bypassing the
        ask-tell loop) so the facade stays clean until Phase 3 injection; the
        live DB default configuration is prepended as the first observation.
        """
        with self.bootstrap_timing.span("configspace_build"):
            self.configspace = build_configspace(
                self.knob_space, seed=self.bo_config.random_seed
            )
        LOGGER.debug(
            "ConfigSpace initialized with %d dimensions",
            len(self.configspace.get_hyperparameters()),
        )

        self.requested_pilot_size = min(
            self.bo_config.range_update_interval, self.bo_config.n_iterations
        )

        LOGGER.info(
            "Pre-generating %d Sobol pilot configurations...",
            self.requested_pilot_size,
        )
        with self.bootstrap_timing.span(
            "pilot_generation", requested=self.requested_pilot_size
        ):
            sobol_configs = self._generate_pilot_configs(
                self.configspace, self.requested_pilot_size
            )

        with self.bootstrap_timing.span("default_config_seed"):
            base_default_config = self.configspace.get_default_configuration()
            base_knobs = configspace_to_knobs(base_default_config, self.knob_space)

            applicator = KnobApplicator(
                db_config=self.env.get_db_config(0), worker_id=0
            )
            try:
                verify_result = applicator.verify(expected_config=base_knobs)
            except Exception as exc:  # noqa: BLE001
                LOGGER.warning(
                    "KnobApplicator.verify() failed (%s); "
                    "using static ConfigSpace defaults as pilot seed",
                    exc,
                )
                verify_result = type("_FakeVerify", (), {"db_config": {}})()  # type: ignore[assignment]

            default_drift = get_config_drift(base_knobs, verify_result.db_config)
            if default_drift:
                drift_preview = dict(list(default_drift.items())[:10])
                LOGGER.info(
                    "Live DB defaults differ from ConfigSpace defaults in %d knob(s): %s%s",
                    len(default_drift),
                    drift_preview,
                    " ..." if len(default_drift) > 10 else "",
                )
            else:
                LOGGER.debug("Live DB defaults match ConfigSpace defaults exactly.")

            base_knobs.update(verify_result.db_config)

            try:
                real_default_config = knobs_to_configspace(
                    base_knobs, self.knob_space, self.configspace
                )
            except Exception as e:  # noqa: BLE001
                LOGGER.warning(
                    "Could not build real default config, falling back to static "
                    "defaults: %s",
                    e,
                )
                real_default_config = base_default_config

            sobol_configs = [real_default_config] + [
                c for c in sobol_configs if c != real_default_config
            ]
            sobol_configs = sobol_configs[: self.requested_pilot_size]

        self.sobol_configs = sobol_configs
        self.actual_pilot_size = len(sobol_configs)
        LOGGER.info(
            "Generated %d pilot configs (including real DB default configuration)",
            self.actual_pilot_size,
        )

        return [
            configspace_to_knobs(c, self.knob_space) for c in self.sobol_configs
        ]

    def build_optimizer(self) -> None:
        """Construct the SMAC facade, foreground worker, and co-tenant loader."""
        assert self.configspace is not None
        assert self.orchestrator is not None

        LOGGER.info("")
        log_section_header(
            LOGGER, "Initializing Bayesian optimizer", top_separator=False,
        )

        db_config = get_db_config()

        # Single (foreground) worker — strictly sequential.
        self.worker = BaseWorker(worker_id=0, knob_space=self.knob_space)
        self.worker.db_config = self.env.get_db_config(0)

        # Co-tenant load controller drives the ``degree - 1`` background load
        # instances in lockstep with worker 0's measurement window. A no-op when
        # degree <= 1.
        degree = max(1, int(self.bo_config.cotenancy_degree))
        self.cotenant = CoTenantLoadController(
            degree=degree,
            env=self.env,
            orchestrator=self.orchestrator,
            knob_space=self.knob_space,
            base_db_config=db_config,
            seed=self.bo_config.random_seed,
        )

        # Dummy objective — the facade runs in ask-tell mode and never calls it.
        def objective(config, seed=0):
            raise NotImplementedError(
                "Objective should not be called directly in ask-tell mode"
            )

        LOGGER.info(
            "Creating SMAC scenario with generous budget for %d iterations...",
            self.bo_config.n_iterations,
        )
        with self.bootstrap_timing.span("smac_scenario_build"):
            scenario = Scenario(
                configspace=self.configspace,
                n_trials=self.bo_config.n_iterations * 3,
                seed=self.bo_config.random_seed,
                deterministic=False,
                n_workers=1,
                output_directory=(
                    self._build_smac_output_root()
                    / f"run_{self.timestamp}_{self.bo_config.random_seed}"
                ),
            )

            # Empty initial design: pilot observations are injected via
            # facade.tell() in Phase 3, so Phase 4's first ask() enters
            # acquisition mode immediately (no duplicate Sobol suggestions).
            empty_design = SobolInitialDesign(scenario=scenario, n_configs=0)

            num_knobs = len(self.knob_space.knobs)
            if self.bo_config.bo_surrogate.lower() == "gp":
                LOGGER.info(
                    "Using BlackBoxFacade (GP) for %d knobs, pilot_size=%d",
                    num_knobs,
                    self.actual_pilot_size,
                )
                self.facade = BlackBoxFacade(
                    scenario,
                    objective,
                    initial_design=empty_design,
                    logging_level=False,
                )
                self.bo_surrogate = "gp"
            else:
                LOGGER.info(
                    "Using HyperparameterOptimizationFacade (RF) for %d knobs, "
                    "pilot_size=%d",
                    num_knobs,
                    self.actual_pilot_size,
                )
                random_design = ProbabilityRandomDesign(
                    probability=0.2, seed=self.bo_config.random_seed
                )
                self.facade = HyperparameterOptimizationFacade(
                    scenario,
                    objective,
                    initial_design=empty_design,
                    random_design=random_design,
                    logging_level=False,
                )
                self.bo_surrogate = "rf"

        # Reset the step-by-step loop state for this run.
        self.iteration_log = []
        self._eval_history = []
        self._previous_engine_config = None
        self._bo_best_score = 0.0
        self._calibrated = False
        self._stop_now = False
        self._stop_reason_detail = ""
        self._early_stopped = False
        self._stale_counter = 0

        LOGGER.info(
            "=== Phase 1: Bootstrap (%d iterations, fallback anchors) ===",
            self.actual_pilot_size,
        )
        self._log_disk_usage("bootstrap start")

    def step(self, generation: int) -> GenerationOutcome:
        """Run one BO iteration (one bootstrap pilot or one adaptive ask/tell).

        The first ``actual_pilot_size`` steps evaluate the pre-generated Sobol
        pilots with fallback anchors; the transition step (once every pilot is
        in) calibrates the normalizer and relabels SMAC's RunHistory; every
        later step runs a standard ask / evaluate / tell with dynamic range
        expansion and early-stopping bookkeeping. State that used to be loop
        locals persists on ``self`` across steps.
        """
        assert self.facade is not None and self.worker is not None
        assert self.orchestrator is not None

        pilot_size = self.actual_pilot_size
        if generation < pilot_size:
            self._bootstrap_step(generation)
            # Calibrate once, right after the final pilot is evaluated.
            if generation == pilot_size - 1:
                self._calibrate_and_relabel()
        else:
            self._optimize_step(generation)

        self.generation_history = self._build_generation_history()
        best_score = max(
            (e.get("score", 0.0) or 0.0 for e in self.iteration_log), default=0.0
        )
        self._best_score_so_far = float(best_score)

        return GenerationOutcome(
            index=generation,
            best_score_this_generation=float(best_score),
            converged=self.converged(),
            payload={
                "iterations": len(self.iteration_log),
                "early_stopped": self._early_stopped,
                "phase": "bootstrap" if generation < pilot_size else "bo",
            },
        )

    def should_stop(self, outcome: GenerationOutcome) -> bool:
        """Halt when early-stopping fires or the iteration budget is reached.

        Runs after each step so the terminal round's Status line reports the
        actual criterion. ``KeyboardInterrupt`` is handled by the base ``run``
        loop, so it is never mislabeled "budget exhausted" here.
        """
        if self._early_stopped:
            self.stop_reason = (
                f"early stopping — no improvement for {self._stale_counter} "
                "consecutive iterations"
            )
            return True
        # Terminal iteration of the planned budget.
        if outcome.index >= self.bo_config.n_iterations - 1:
            self.stop_reason = "iteration budget exhausted"
            return True
        # SMAC exhausted its n_trials budget mid-run (set by _optimize_step).
        if self._stop_now:
            self.stop_reason = self._stop_reason_detail or "optimizer budget exhausted"
            return True
        return False

    def collect_best(self) -> Tuple[Dict[str, Any], float, Optional[Any]]:
        best_entry: Optional[Dict[str, Any]] = None
        best_score = -float("inf")
        for entry in self.iteration_log:
            score = entry.get("score", 0.0) or 0.0
            if score > best_score:
                best_score = score
                best_entry = entry
        if best_entry is None:
            return {}, 0.0, None
        return (
            dict(best_entry.get("config", {})),
            float(best_score),
            best_entry.get("metrics"),
        )

    def total_evaluations(self) -> int:
        """Iterations that produced a measurement (crashes excluded)."""
        return sum(1 for e in self.iteration_log if e.get("metrics") is not None)

    def converged(self) -> bool:
        """Early-stop fired, or the full iteration budget was consumed."""
        return self._early_stopped or (
            len(self.iteration_log) >= self.bo_config.n_iterations
        )

    def strategy_overhead_seconds(self) -> float:
        """Total BO machinery time: ask/tell/relabel/calibration per iteration."""
        return float(
            sum(e.get("bo_overhead_seconds", 0.0) for e in self.iteration_log)
        )

    def teardown(self) -> None:
        """Shut down the co-tenant pool, then run the shared teardown."""
        cotenant = getattr(self, "cotenant", None)
        if cotenant is not None:
            try:
                cotenant.shutdown()
            except Exception as e:  # noqa: BLE001
                LOGGER.warning("Error shutting down co-tenant pool: %s", e)
        super().teardown()

    def build_session_payload(self) -> Dict[str, Any]:
        scoring_metadata = self.metric_config.get_scoring_metadata()
        _, _, _ = self.collect_best()
        best_breakdown = self._best_score_breakdown()

        strategy_params: Dict[str, Any] = {
            "optimizer": "bayesian_optimization",
            "bo_library": "smac3",
            "bo_acquisition": "expected_improvement",
            "n_iterations": self.bo_config.n_iterations,
            "bo_surrogate": self.bo_surrogate,
            "pilot_size": self.actual_pilot_size,
            "range_update_interval": self.bo_config.range_update_interval,
            "enable_snapshots": self.enable_snapshots,
            "snapshot_restore_interval": self.lifecycle.snapshot_restore_interval,
            "cotenancy_degree": max(1, int(self.bo_config.cotenancy_degree)),
            "early_stopping_enabled": self._early_stopping_enabled,
            "early_stopping_patience": self._early_stopping_patience,
            "pbt_session_sync": (
                str(self.bo_config.pbt_session_path)
                if self.bo_config.pbt_session_path
                else None
            ),
            "reference_pbt_knobs": list(self.bo_config.pbt_knob_names or ()),
            "resource_equalization": self.bo_config.pbt_worker_resources is not None,
        }

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
            "convergence": {
                "converged": self._early_stopped,
                "generations_without_improvement": self._stale_counter,
                "early_stopped": self._early_stopped,
                "early_stopping_patience": self._early_stopping_patience,
            },
            "warm_start": {"enabled": False},
            "timing_summary": self._merged_timing_summary(),
        }
        if self.cotenant is not None:
            # Honest record of the co-tenant load applied during each
            # measurement window (degree, background worker ids, load seed).
            payload["cotenancy"] = convert_numpy_types(self.cotenant.to_metadata())
        return payload

    # ── BO-specific helpers (ported from the legacy runner) ───────────────────

    def _best_score_breakdown(self) -> Optional[Any]:
        """Score breakdown of the best iteration (for the scoring block)."""
        best_bd = None
        best_score = -float("inf")
        for entry in self.iteration_log:
            score = entry.get("score", 0.0) or 0.0
            if score > best_score:
                best_score = score
                best_bd = entry.get("score_breakdown")
        return best_bd

    def _merged_timing_summary(self) -> Dict[str, Any]:
        """Aggregate per-eval + BO control-loop + bootstrap timing.

        Mirrors PBT's ``timing_summary`` shape while folding in BO's ask/tell
        control-loop spans and the bootstrap-phase spans so every layer of work
        that contributed to wall clock is visible in one place.
        """
        merged = TimingRecorder()
        for gen in self.generation_history:
            for ws in gen.get("worker_scores", []) or []:
                ws_timing = ws.get("timing")
                if not ws_timing or not isinstance(ws_timing, dict):
                    continue
                for rec in ws_timing.get("records", []) or []:
                    merged.add(
                        rec.get("component", "unknown"),
                        float(rec.get("seconds", 0.0)),
                        **(rec.get("metadata") or {}),
                    )
        merged.merge(self.bo_timing)
        merged.merge(self.bootstrap_timing)
        return merged.aggregate()

    def _build_generation_history(self) -> List[Dict[str, Any]]:
        """Convert the per-iteration log into unified ``history`` records.

        Each BO iteration is one record with a single worker (id 0). The record
        shape matches PBT/LHS (shared top-level fields + ``worker_scores`` /
        ``worker_configs``, strategy-specific fields under ``strategy_params``)
        so a BO trace loads through the same analysis path.
        """
        history: List[Dict[str, Any]] = []
        best_score_so_far = -float("inf")
        for i, iteration in enumerate(self.iteration_log):
            score = iteration.get("score", 0.0) or 0.0
            if score > best_score_so_far:
                best_score_so_far = score

            bo_overhead = iteration.get("bo_overhead_seconds", 0.0)
            wall_clock = iteration.get("wall_clock_seconds", 0.0)
            generation_elapsed = wall_clock + bo_overhead

            metrics = iteration.get("metrics")
            iteration_timing = iteration.get("timing")

            worker_score_entry: Dict[str, Any] = {
                "worker_id": 0,
                "score": score,
                "metrics": metrics.to_dict() if metrics is not None else {},
                "score_breakdown": convert_numpy_types(
                    iteration.get("score_breakdown")
                ),
            }
            if iteration_timing is not None:
                worker_score_entry["timing"] = iteration_timing

            history.append(
                {
                    "iteration": i,
                    "best_score": best_score_so_far,
                    "restart_count": 1 if iteration.get("restarted", False) else 0,
                    "timestamp": datetime.fromtimestamp(
                        iteration.get("timestamp", 0.0)
                    ).isoformat(),
                    "wall_clock_seconds": wall_clock,
                    "iteration_elapsed_seconds": generation_elapsed,
                    "bo_overhead_seconds": bo_overhead,
                    # BO's bootstrap/ask-tell phase is a flat per-record field
                    # (not PBT-shaped ``strategy_params``); ``num_exploited`` /
                    # ``mean_score`` / ``std_score`` / ``best_worker_id`` /
                    # ``converged`` are PBT-specific or recomputed by loaders and
                    # are intentionally omitted for the single-config BO step.
                    "phase": iteration.get("phase", "bo"),
                    "worker_scores": [worker_score_entry],
                    "worker_configs": [
                        {
                            "worker_id": 0,
                            "config": convert_numpy_types(
                                self.full_knob_space.config_to_fractions(
                                    iteration.get("config", {})
                                )
                            ),
                        }
                    ],
                }
            )
        return history

    def _build_smac_output_root(self) -> Path:
        """Build the SMAC output directory root under the session output root."""
        smac_root = self.output_root / "smac_output"
        smac_root.mkdir(parents=True, exist_ok=True)
        return smac_root

    def _apply_pbt_knob_filter(self) -> None:
        """Restrict BO search to the knob names present in the reference PBT run."""
        if not self.bo_config.pbt_knob_names:
            return

        requested_knobs = set(self.bo_config.pbt_knob_names)
        available_knobs = set(self.knob_space.knobs.keys())
        missing_knobs = sorted(requested_knobs - available_knobs)
        if missing_knobs:
            raise RuntimeError(
                "Reference PBT run used knobs that are unavailable to BO after "
                f"tier/runtime pruning: {', '.join(missing_knobs)}"
            )

        removed_knobs = sorted(available_knobs - requested_knobs)
        for knob_name in removed_knobs:
            self.knob_space.knobs.pop(knob_name, None)

        LOGGER.info(
            "✓ Restricted BO search space to %d knobs from reference PBT session",
            len(self.knob_space.knobs),
        )

    def _relabel_smac_history(
        self, facade, orchestrator, eval_history: list, worker
    ) -> int:
        """Rescore every past evaluation and overwrite SMAC costs.

        Called immediately after ``metric_config.expand_ranges_for_metrics()``
        returns True so the surrogate model retrains on a consistent landscape.
        """
        engine = orchestrator._get_scoring_engine()
        relabeled = 0
        for record in eval_history:
            if record.status != StatusType.SUCCESS or record.raw_metrics is None:
                continue
            breakdown = engine.compute_breakdown(
                record.raw_metrics, worker_logger=worker.logger
            )
            new_cost = max(0.0, min(100.0, 100.0 - breakdown.final_score))
            LOGGER.debug(
                "  Relabeling entry %d: new_cost=%.4f (score=%.4f)",
                relabeled + 1,
                new_cost,
                breakdown.final_score,
            )
            facade.runhistory.add(
                config=record.config,
                cost=new_cost,
                time=record.eval_time,
                status=record.status,
                instance=record.trial_info.instance,
                seed=record.trial_info.seed,
                force_update=True,
            )
            relabeled += 1
        skipped = len(eval_history) - relabeled
        LOGGER.debug(
            "Relabeling complete: %d updated, %d skipped (CRASHED/None metrics)",
            relabeled,
            skipped,
        )
        return relabeled

    def _log_disk_usage(self, label: str) -> None:
        """Log disk usage of PGDATA directories and filesystem for diagnostics."""
        try:
            total, used, free = shutil.disk_usage("/")
            pct = used / total * 100
            LOGGER.info(
                "[disk] %s — filesystem: %.1f%% used (%.1f GB free)",
                label,
                pct,
                free / (1024**3),
            )
            if not hasattr(self, "env") or self.env is None:
                return
            base = getattr(self.env, "base_dir", None)
            if base is None:
                return
            base = Path(base)
            for child in sorted(base.rglob("pgdata")):
                if child.is_dir():
                    try:
                        size = sum(
                            f.stat().st_blocks * 512
                            for f in child.rglob("*")
                            if f.is_file()
                        )
                        LOGGER.info(
                            "[disk]   %s: %.1f MB",
                            child.relative_to(base),
                            size / (1024**2),
                        )
                    except OSError:
                        pass
        except Exception as exc:  # noqa: BLE001
            LOGGER.debug("[disk] usage probe failed: %s", exc)

    def _evaluate_with_cotenancy(
        self,
        config,
        worker: BaseWorker,
        orchestrator: WorkloadOrchestrator,
        previous_engine_config,
        seed=None,
        restore_due: bool = False,
        next_eval_will_restore: bool = False,
    ):
        """Evaluate one foreground BO trial under matched co-tenant load."""
        barriers = self.cotenant.make_barrier() if self.cotenant else None
        futures = self.cotenant.start_round(barriers) if self.cotenant else []
        try:
            return evaluate_config(
                config,
                worker,
                orchestrator,
                self.knob_space,
                previous_engine_config,
                seed=seed,
                restore_due=restore_due,
                next_eval_will_restore=next_eval_will_restore,
                barriers=barriers,
            )
        finally:
            if self.cotenant:
                self.cotenant.finish_round(futures)

    def _bootstrap_step(self, generation: int) -> None:
        """Evaluate one pilot config (fallback anchors) and inject it into SMAC.

        One pass of the old Phase-1 bootstrap loop, indexed by ``generation``.
        Cross-step state (``_previous_engine_config``, ``_eval_history``,
        ``iteration_log``) lives on ``self``.
        """
        facade = self.facade
        orchestrator = self.orchestrator
        worker = self.worker
        assert worker is not None and orchestrator is not None
        pilot_idx = generation
        pilot_size = self.actual_pilot_size
        sobol_config = self.sobol_configs[pilot_idx]

        if pilot_idx % 5 == 0:
            self._log_disk_usage(f"bootstrap {pilot_idx}")

        restore_due = (
            self.enable_snapshots
            and pilot_idx > 0
            and pilot_idx % self.lifecycle.snapshot_restore_interval == 0
        )
        next_idx = pilot_idx + 1
        next_eval_will_restore = (
            self.enable_snapshots
            and next_idx > 0
            and next_idx % self.lifecycle.snapshot_restore_interval == 0
        )

        LOGGER.info(
            "Bootstrap %d/%d: starting evaluation...",
            pilot_idx + 1,
            pilot_size,
        )

        try:
            (
                cost,
                knob_config,
                metrics,
                score,
                score_breakdown,
                restarted,
                wall_time,
                eval_timing,
            ) = self._evaluate_with_cotenancy(
                sobol_config,
                worker,
                orchestrator,
                self._previous_engine_config,
                restore_due=restore_due,
                next_eval_will_restore=next_eval_will_restore,
                seed=None,
            )
            self._previous_engine_config = dict(
                configspace_to_knobs(sobol_config, self.knob_space)
            )

            if metrics is not None:
                resolved_cs_config = knobs_to_configspace(
                    knob_config,
                    self.knob_space,
                    facade.scenario.configspace,
                )
                status = StatusType.SUCCESS
            else:
                resolved_cs_config = sobol_config
                status = StatusType.CRASHED
                cost = 100.0

        except Exception as exc:  # noqa: BLE001
            LOGGER.error(
                "Bootstrap iteration %d failed: %s", pilot_idx, exc, exc_info=True
            )
            resolved_cs_config = sobol_config
            metrics = None
            cost = 100.0
            score = 0.0
            score_breakdown = None
            wall_time = 0.0
            restarted = False
            status = StatusType.CRASHED
            eval_timing = TimingRecorder()

        trial_info = TrialInfo(
            config=resolved_cs_config, seed=self.bo_config.random_seed
        )
        t_tell = time.time()
        facade.tell(
            trial_info,
            TrialValue(cost=cost, time=wall_time, status=status),
        )
        tell_overhead = time.time() - t_tell
        self.bo_timing.add("bo_overhead_tell", tell_overhead, phase="bootstrap")

        self._eval_history.append(
            EvalRecord(
                config=resolved_cs_config,
                raw_metrics=metrics,
                trial_info=trial_info,
                eval_time=wall_time,
                status=status,
            )
        )

        iteration_score = score if score is not None else 0.0
        self.iteration_log.append(
            {
                "iteration": pilot_idx,
                "config": configspace_to_knobs(resolved_cs_config, self.knob_space),
                "metrics": metrics,
                "score": iteration_score,
                "score_breakdown": score_breakdown,
                "cost": cost,
                "bo_overhead_seconds": tell_overhead,
                "wall_clock_seconds": wall_time,
                "restarted": restarted,
                "timestamp": time.time(),
                "timing": eval_timing.to_dict(include_summary=False),
                "phase": "bootstrap",
            }
        )

        LOGGER.info(
            "Bootstrap %d/%d: status=%s, score=%.2f, wall_time=%.2fs",
            pilot_idx + 1,
            pilot_size,
            status.name,
            iteration_score,
            wall_time,
        )

        if metrics is not None:
            log_worker_metrics_table(
                LOGGER,
                [build_worker_metric_row(metrics, iteration_score)],
                worker_labels=[f"Bootstrap-{pilot_idx + 1}"],
                best_worker_metric=self._best_worker_metric_row(),
                title=f"\n🔷 Bootstrap {pilot_idx + 1}/{pilot_size} Metrics 🔷",
            )

    def _calibrate_and_relabel(self) -> None:
        """Calibrate the normalizer from all pilots and relabel SMAC history.

        Runs exactly once, right after the final bootstrap pilot is evaluated
        (guarded by ``_calibrated``). Ported verbatim from the old inline
        calibration block.
        """
        if self._calibrated:
            return
        facade = self.facade
        orchestrator = self.orchestrator
        worker = self.worker
        assert worker is not None and orchestrator is not None
        eval_history = self._eval_history
        pilot_size = self.actual_pilot_size

        t_calibration = time.monotonic()
        LOGGER.info("=== Bootstrap Calibration ===")
        successful_metrics = [
            r.raw_metrics
            for r in eval_history
            if r.status == StatusType.SUCCESS and r.raw_metrics is not None
        ]
        crash_count = sum(1 for r in eval_history if r.status == StatusType.CRASHED)
        if crash_count > 0:
            LOGGER.warning(
                "Bootstrap phase had %d/%d CRASHED iteration(s) — "
                "calibration quality is reduced. Check DB/benchmark logs above.",
                crash_count,
                pilot_size,
            )

        if len(successful_metrics) == 0:
            raise RuntimeError(
                "Zero bootstrap evaluations succeeded. Cannot calibrate "
                "normalization ranges. Check database connectivity and benchmark "
                "configuration."
            )
        elif len(successful_metrics) < 3:
            LOGGER.warning(
                "Only %d successful bootstrap evaluation(s) (minimum 3 "
                "recommended). Continuing with degraded calibration.",
                len(successful_metrics),
            )

        self.metric_config.update_ranges(successful_metrics)
        LOGGER.info(
            "Normalizer calibrated from %d bootstrap observations",
            len(successful_metrics),
        )
        LOGGER.debug(
            "Calibrated metric ranges: %s",
            getattr(self.metric_config, "ranges", "(not exposed by metric_config)"),
        )
        try:
            orchestrator.reload_scoring_engine()
        except Exception:
            LOGGER.error(
                "Failed to rebuild scoring engine after calibration", exc_info=True
            )
            raise

        n_relabeled = self._relabel_smac_history(
            facade, orchestrator, eval_history, worker
        )
        LOGGER.info(
            "Bootstrap relabeling: %d/%d entries updated in SMAC RunHistory",
            n_relabeled,
            len(eval_history),
        )

        # Update iteration_log bootstrap entries to reflect calibrated scores
        engine = orchestrator._get_scoring_engine()
        log_bootstrap = [e for e in self.iteration_log if e["phase"] == "bootstrap"]
        for log_entry, record in zip(log_bootstrap, eval_history, strict=False):
            if record.status == StatusType.SUCCESS and record.raw_metrics is not None:
                bd = engine.compute_breakdown(
                    record.raw_metrics, worker_logger=worker.logger
                )
                log_entry["score"] = bd.final_score
                log_entry["score_breakdown"] = bd
                log_entry["cost"] = max(0.0, min(100.0, 100.0 - bd.final_score))

        calibration_elapsed = time.monotonic() - t_calibration
        self.bo_timing.add(
            "bootstrap_calibration",
            calibration_elapsed,
            phase="bootstrap_calibration",
            n_observations=len(successful_metrics),
        )

        incumbents = facade.intensifier.get_incumbents()
        assert len(incumbents) > 0, (
            "No incumbent found after bootstrap injection. "
            "Verify that StatusType and Configuration identity are correct."
        )
        LOGGER.info(
            "Bootstrap complete: %d incumbent(s), %d observations injected",
            len(incumbents),
            len(eval_history),
        )

        # Seed the running best from the calibrated pilot scores so the first
        # adaptive step compares against a real incumbent.
        self._bo_best_score = max(
            (entry.get("score", 0.0) or 0.0 for entry in self.iteration_log),
            default=0.0,
        )
        self._stale_counter = 0
        self._calibrated = True

        remaining = self.bo_config.n_iterations - pilot_size
        LOGGER.info(
            "=== Phase 2: Adaptive BO Loop (%d iterations, early_stopping=%s, "
            "patience=%d) ===",
            remaining,
            self._early_stopping_enabled,
            self._early_stopping_patience,
        )

    def _optimize_step(self, generation: int) -> None:
        """Run one adaptive ask / evaluate / tell iteration.

        One pass of the old Phase-2 loop, indexed by ``generation`` (which is
        the absolute iteration count, ``>= actual_pilot_size``). Sets
        ``_early_stopped`` / ``_stop_now`` for :meth:`should_stop` to consume.
        """
        facade = self.facade
        orchestrator = self.orchestrator
        worker = self.worker
        assert worker is not None and orchestrator is not None
        iteration_count = generation

        if iteration_count % 5 == 0:
            self._log_disk_usage(f"iter {iteration_count}")

        restore_due = (
            self.enable_snapshots
            and iteration_count > 0
            and iteration_count % self.lifecycle.snapshot_restore_interval == 0
        )
        next_iter = iteration_count + 1
        next_eval_will_restore = (
            self.enable_snapshots
            and next_iter > 0
            and next_iter % self.lifecycle.snapshot_restore_interval == 0
        )

        try:
            LOGGER.debug(
                "Calling facade.ask() for iteration %d/%d...",
                iteration_count + 1,
                self.bo_config.n_iterations,
            )
            t_ask = time.time()
            trial_info = facade.ask()
            ask_overhead = time.time() - t_ask
            self.bo_timing.add("bo_overhead_ask", ask_overhead, phase="optimize")
            LOGGER.debug(
                "facade.ask() returned in %.3fs (seed=%s)",
                ask_overhead,
                trial_info.seed,
            )
        except StopIteration:
            LOGGER.warning(
                "SMAC exhausted its n_trials budget at iteration %d/%d "
                "(n_trials=%d, budget_multiplier=3x). "
                "Consider increasing n_iterations or the 3x multiplier.",
                iteration_count + 1,
                self.bo_config.n_iterations,
                self.bo_config.n_iterations * 3,
            )
            self._stop_now = True
            self._stop_reason_detail = "optimizer exhausted its trial budget"
            return

        iteration_bo_overhead = ask_overhead

        try:
            (
                cost,
                knob_config,
                metrics,
                score,
                score_breakdown,
                restarted,
                wall_time,
                eval_timing,
            ) = self._evaluate_with_cotenancy(
                trial_info.config,
                worker,
                orchestrator,
                self._previous_engine_config,
                seed=trial_info.seed,
                restore_due=restore_due,
                next_eval_will_restore=next_eval_will_restore,
            )
            self._previous_engine_config = dict(
                configspace_to_knobs(trial_info.config, self.knob_space)
            )
        except Exception as exc:  # noqa: BLE001
            LOGGER.error("Error evaluating config: %s", exc, exc_info=True)
            (
                cost,
                knob_config,
                metrics,
                score,
                score_breakdown,
                restarted,
                wall_time,
            ) = (100.0, {}, None, 0.0, None, False, 0.0)
            eval_timing = TimingRecorder()

        # Drift check: bracket the comparison for timing_breakdown.
        t_drift = time.monotonic()
        original_knob_config = configspace_to_knobs(
            trial_info.config, self.knob_space
        )

        if not knob_config and metrics is None:
            LOGGER.warning(
                "Iteration %d: evaluation crashed with empty knob_config — "
                "skipping repaired config injection to avoid corrupting SMAC "
                "surrogate",
                iteration_count + 1,
            )
            configs_differ = False
        else:
            configs_differ = bool(
                get_config_drift(original_knob_config, knob_config)
            )
        drift_elapsed = time.monotonic() - t_drift
        self.bo_timing.add("bo_drift_check", drift_elapsed, phase="optimize")
        iteration_bo_overhead += drift_elapsed

        repaired_cs_config = None
        if configs_differ:
            t_repair = time.monotonic()
            try:
                repaired_cs_config = knobs_to_configspace(
                    knob_config, self.knob_space, facade.scenario.configspace
                )
            except Exception as exc:  # noqa: BLE001
                knob_def_repr = {
                    k: str(self.knob_space.knobs.get(k)) for k in knob_config
                }
                LOGGER.warning(
                    "Failed to build repaired CS config at iteration %d: %s. "
                    "Knob definitions involved: %s",
                    iteration_count + 1,
                    exc,
                    knob_def_repr,
                    exc_info=True,
                )
            repair_elapsed = time.monotonic() - t_repair
            self.bo_timing.add("bo_repair_inject", repair_elapsed, phase="optimize")
            iteration_bo_overhead += repair_elapsed

        bo_status = StatusType.SUCCESS
        effective_config = (
            repaired_cs_config
            if repaired_cs_config is not None
            else trial_info.config
        )
        effective_trial_info = TrialInfo(
            config=effective_config, seed=trial_info.seed
        )

        t_tell = time.time()
        if repaired_cs_config is not None:
            facade.tell(
                effective_trial_info,
                TrialValue(cost=cost, time=wall_time, status=bo_status),
            )
        facade.tell(
            trial_info,
            TrialValue(cost=cost, time=wall_time, status=bo_status),
        )
        tell_overhead = time.time() - t_tell
        self.bo_timing.add("bo_overhead_tell", tell_overhead, phase="optimize")
        iteration_bo_overhead += tell_overhead

        self._eval_history.append(
            EvalRecord(
                config=effective_config,
                raw_metrics=metrics,
                trial_info=effective_trial_info,
                eval_time=wall_time,
                status=bo_status if metrics is not None else StatusType.CRASHED,
            )
        )

        # ── Dynamic Range Expansion & Relabeling ─────────────────────────
        if metrics is not None:
            ranges_expanded = self.metric_config.expand_ranges_for_metrics(
                [metrics]
            )
            if ranges_expanded:
                t_relabel = time.monotonic()
                orchestrator.reload_scoring_engine()
                n_relabeled = self._relabel_smac_history(
                    facade, orchestrator, self._eval_history, worker
                )
                new_engine = orchestrator._get_scoring_engine()
                new_bd = new_engine.compute_breakdown(
                    metrics, worker_logger=worker.logger
                )
                cost = max(0.0, min(100.0, 100.0 - new_bd.final_score))
                score = new_bd.final_score
                score_breakdown = new_bd
                LOGGER.info(
                    "🔄 Normalization ranges expanded — %d/%d history entries "
                    "relabeled (scores recalibrated on updated bounds)",
                    n_relabeled,
                    len(self._eval_history),
                )

                # Retroactively update prior iteration_log entries.
                for log_entry, record in zip(
                    self.iteration_log, self._eval_history, strict=False
                ):
                    if (
                        record.status == StatusType.SUCCESS
                        and record.raw_metrics is not None
                    ):
                        bd = new_engine.compute_breakdown(
                            record.raw_metrics, worker_logger=worker.logger
                        )
                        log_entry["score"] = bd.final_score
                        log_entry["score_breakdown"] = bd
                        log_entry["cost"] = max(
                            0.0, min(100.0, 100.0 - bd.final_score)
                        )
                relabel_elapsed = time.monotonic() - t_relabel
                self.bo_timing.add(
                    "bo_relabel",
                    relabel_elapsed,
                    phase="optimize",
                    n_relabeled=n_relabeled,
                )
                iteration_bo_overhead += relabel_elapsed

        iteration_score = score if score is not None else 0.0
        self.iteration_log.append(
            {
                "iteration": iteration_count,
                "config": knob_config,
                "metrics": metrics,
                "score": iteration_score,
                "score_breakdown": score_breakdown,
                "cost": cost,
                "bo_overhead_seconds": iteration_bo_overhead,
                "wall_clock_seconds": wall_time,
                "restarted": restarted,
                "timestamp": time.time(),
                "timing": eval_timing.to_dict(include_summary=False),
                "phase": "bo",
            }
        )

        LOGGER.info(
            "Iteration %d/%d [BO]: score=%.2f, cost=%.2f, wall_time=%.2fs",
            iteration_count + 1,
            self.bo_config.n_iterations,
            iteration_score,
            cost,
            wall_time,
        )

        if metrics is not None:
            log_worker_metrics_table(
                LOGGER,
                [build_worker_metric_row(metrics, iteration_score)],
                worker_labels=[f"Iter-{iteration_count + 1}"],
                best_worker_metric=self._best_worker_metric_row(),
                title=(
                    f"\n🔷 BO Iteration {iteration_count + 1}/"
                    f"{self.bo_config.n_iterations} Metrics 🔷"
                ),
            )

        # ── Early Stopping Check + Best/Stale Status ──────────────────────
        is_new_best = iteration_score > self._bo_best_score
        if is_new_best:
            self._bo_best_score = iteration_score
            self._stale_counter = 0
            LOGGER.info(
                "✅ New best score: %.4f  (iteration %d/%d)",
                self._bo_best_score,
                iteration_count + 1,
                self.bo_config.n_iterations,
            )
        else:
            self._stale_counter += 1 if self._early_stopping_enabled else 0
            LOGGER.info(
                "⏸  No improvement — best stays %.4f  (stale=%d/%s)",
                self._bo_best_score,
                self._stale_counter,
                self._early_stopping_patience
                if self._early_stopping_enabled
                else "∞",
            )

        if (
            self._early_stopping_enabled
            and self._stale_counter >= self._early_stopping_patience
        ):
            LOGGER.warning(
                "Early stopping triggered: no improvement for %d consecutive "
                "iterations (patience=%d). Best score=%.4f.",
                self._stale_counter,
                self._early_stopping_patience,
                self._bo_best_score,
            )
            self._early_stopped = True

    def _generate_pilot_configs(
        self,
        configspace: ConfigurationSpace,
        pilot_size: int,
    ) -> list[Configuration]:
        """Sample exactly ``pilot_size`` unique, constraint-valid configurations.

        ConfigSpace constraints reject most Sobol points in high-dimensional
        spaces, and SMAC's internal dedup collapses survivors further, so a
        single Sobol pass can silently truncate the pilot budget. Strategy:
        repeated doubling Sobol passes, then a ``sample_configuration`` fallback
        that loops until the requested count is reached.
        """
        if pilot_size <= 0:
            return []

        accepted: list[Configuration] = []
        seen: set[str] = set()

        def _key(cfg: Configuration) -> str:
            return json.dumps(dict(cfg), sort_keys=True, default=str)

        max_sobol_passes = 4
        for attempt in range(max_sobol_passes):
            if len(accepted) >= pilot_size:
                break
            n_request = pilot_size * 5 * (2**attempt)
            pass_seed = self.bo_config.random_seed + attempt
            scenario = Scenario(
                configspace=configspace,
                n_trials=n_request,
                seed=pass_seed,
                n_workers=1,
                output_directory=(
                    self._build_smac_output_root()
                    / f"_sobol_gen_attempt_{attempt}"
                ),
            )
            design = SobolInitialDesign(
                scenario=scenario,
                n_configs=n_request,
                max_ratio=1.0,
            )
            for cfg in design.select_configurations():
                k = _key(cfg)
                if k in seen:
                    continue
                seen.add(k)
                accepted.append(cfg)
                if len(accepted) >= pilot_size:
                    break

        if len(accepted) < pilot_size:
            shortfall = pilot_size - len(accepted)
            LOGGER.info(
                "Sobol exhausted after %d passes with %d/%d unique-valid configs; "
                "falling back to ConfigurationSpace.sample_configuration() for the "
                "remaining %d. The search space is constraint-dense.",
                max_sobol_passes,
                len(accepted),
                pilot_size,
                shortfall,
            )
            extras = configspace.sample_configuration(size=shortfall)
            if isinstance(extras, Configuration):
                extras = [extras]
            for cfg in extras:
                k = _key(cfg)
                if k in seen:
                    continue
                seen.add(k)
                accepted.append(cfg)
            while len(accepted) < pilot_size:
                cfg = configspace.sample_configuration()
                k = _key(cfg)
                if k in seen:
                    continue
                seen.add(k)
                accepted.append(cfg)

        return accepted[:pilot_size]
