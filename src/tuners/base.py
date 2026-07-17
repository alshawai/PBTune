"""
``BaseTuner`` — the shared lifecycle ABC for all tuning strategies.

The three strategies (PBT, BO, LHS-design) differ only in *how they propose
configurations* and *when they stop*. Everything around that — resource
resolution, workload/executor construction, environment + orchestrator wiring,
instance bring-up, runtime knob pruning, the generation loop, instance
teardown, optional global recalibration, and session serialization — is
identical. This ABC encodes that invariant lifecycle as a concrete ``run()``
template method (Template Method pattern) plus a concrete ``setup()`` that
drives the shared bring-up, delegating the strategy-specific decisions to a few
abstract hooks.

Concrete subclasses implement:
  - ``propose_initial_configs``  — draw the configurations to evaluate
  - ``step``                     — run one generation and report its outcome
  - ``should_stop``              — decide whether to halt after a generation
  - ``collect_best``             — surface the best (config, score, metrics)
  - ``build_session_payload``    — assemble the strategy-specific JSON sections

Subclasses set a handful of strategy inputs on ``self`` before ``run()``
(``benchmark``, ``benchmark_config``, ``workload_file``, ``data_root``); the
concrete ``setup()`` reads those to build the shared environment and populates
``self.knob_space`` / ``self.full_knob_space`` / ``self.orchestrator`` /
``self.worker_resources`` for the strategy hooks to use.

The incumbent PBT/BO tuners are NOT retrofitted onto this ABC in this change
(copy-not-refactor); ``LHSDesignTuner`` is its first concrete user. See
ADR-006 for the migration boundary.
"""

from __future__ import annotations

import time
from abc import ABC, abstractmethod
from pathlib import Path
from typing import Any, Dict, List, Optional, Sequence, Tuple

from src.config.database import get_db_config
from src.tuners.engine.orchestrator import (
    WorkloadOrchestrator,
    WorkloadOrchestratorConfig,
)
from src.knobs import get_knob_space
from src.tuners.utils.executors import build_workload_bundle
from src.tuners.utils.knob_filter import (
    apply_tuning_mode_filter,
    compute_unsupported_knobs,
    log_pruning_summary,
    query_runtime_supported_knobs,
)
from src.tuners.utils.resources import resolve_worker_resources
from src.tuners.utils.session_assembly import (
    aggregate_session_timing,
    build_generation_record,
    safe_breakdown,
)
from src.tuners.utils.tuner_logging import (
    format_design_points,
    log_optimization_header,
    log_round_end,
    log_round_start,
    log_worker_metrics,
)
from src.tuners.utils.session_writer import (
    build_session_header,
    worker_resources_to_dict,
    write_best_config_json,
    write_session_json,
)
from src.tuners.utils.exceptions import KnobSpaceEmptyError
from src.tuners.utils.types import (
    GenerationOutcome,
    TunerLifecycleConfig,
    TuningStrategy,
    WorkerEvalResult,
)
from src.utils.environments import EnvironmentFactory
from src.utils.hardware_info import WorkerResources, get_system_info
from src.utils.logger import (
    get_color_context,
    get_logger,
    log_final_summary,
    log_section_header,
    print_startup_banner,
)
from src.utils.metrics import (
    PerformanceMetrics,
    WorkloadType,
    create_metric_config,
)
from src.utils.scoring.contracts import ScoreBreakdown
from src.utils.timing import TimingRecorder
from src.utils.types import build_session_environment

LOGGER = get_logger("Tuner")
COLORS = get_color_context()


class BaseTuner(ABC):
    """
    Abstract base class encoding the shared tuner lifecycle.

    Subclasses provide strategy-specific behavior through the abstract hooks;
    the concrete ``run()`` and ``setup()`` methods drive the invariant
    lifecycle and own the timing instrumentation, instance lifecycle, and
    result serialization.
    """

    def __init__(
        self,
        lifecycle: TunerLifecycleConfig,
        *,
        timestamp: str,
        output_root: Path,
    ) -> None:
        self.lifecycle = lifecycle
        self.strategy: TuningStrategy = lifecycle.strategy
        self.timestamp = timestamp
        self.output_root = Path(output_root)

        # Strategy inputs — subclasses set these in __init__ before run().
        # The shared setup() reads them to build the environment.
        self.benchmark: Optional[str] = None
        self.benchmark_config: Any = None
        self.workload_file: Optional[str] = None
        self.data_root: Path = self.output_root

        # Populated by the shared setup() during run().
        self.worker_resources: Optional[WorkerResources] = None
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
        self.initial_configs: List[Dict[str, Any]] = []

        self.generation_history: List[Dict[str, Any]] = []
        self.start_time: float = 0.0
        self.tuning_start_time: float = 0.0
        self.bootstrap_timing = TimingRecorder()

        self._best_score_so_far: float = 0.0

        # Human-facing reason the loop halted, set by ``should_stop`` for
        # strategies that report one (see ``emits_stop_status``). Rendered as
        # the generation-summary ``Status`` line; ``None`` until a stop fires.
        self.stop_reason: Optional[str] = None

        # Count of generations (a.k.a. rounds / batches / iterations) whose
        # step() actually ran. Tracked centrally by run() — it is the one
        # depth axis every strategy shares — and serialized as
        # ``tuning_session.num_rounds``.
        self._rounds_completed: int = 0

    @abstractmethod
    def propose_initial_configs(self) -> List[Dict[str, Any]]:
        """
        Draw the initial configurations to evaluate.

        Called by the shared ``setup()`` once the (full) knob space has been
        built and hardware ranges resolved, so implementations may sample from
        ``self.full_knob_space``. The returned list is stored on
        ``self.initial_configs`` for the strategy's ``step()`` to consume.
        """

    @abstractmethod
    def step(self, generation: int) -> GenerationOutcome:
        """
        Run a single generation and return its outcome.

        Implementations evaluate configurations, update internal best-state,
        and append any strategy-specific record to ``generation_history``.
        """

    @abstractmethod
    def should_stop(self, outcome: GenerationOutcome) -> bool:
        """Return True to halt the generation loop after ``outcome``."""

    @abstractmethod
    def collect_best(self) -> Tuple[Dict[str, Any], float, Optional[Any]]:
        """Return ``(best_config, best_score, best_metrics)``."""

    @abstractmethod
    def build_session_payload(self) -> Dict[str, Any]:
        """
        Return strategy-specific sections to merge into the session JSON.

        The base class supplies the shared header, ``best_configuration`` and
        ``worker_resources`` blocks; this hook contributes everything else
        (generation history, score breakdown, provenance, ...).
        """

    def total_evaluations(self) -> int:
        """Number of configurations actually benchmarked across the run.

        This is the fair cross-strategy *budget* axis (PBT evaluates
        population×generations, BO one config per iteration, LHS one per design
        point), serialized as ``tuning_session.total_evaluations``. Distinct
        from ``num_rounds`` (the count of ``step()`` calls). The base default
        returns 0; every concrete strategy overrides it.
        """
        return 0

    def converged(self) -> bool:
        """Whether the strategy reached its planned terminal state.

        Serialized as the shared ``tuning_session.converged`` flag. Each
        strategy defines convergence in its own terms (PBT: early-stop on no
        improvement; LHS: the full design was swept). The base default is
        ``False`` so a strategy that never converges needs no override.
        """
        return False

    @property
    def emits_stop_status(self) -> bool:
        """Whether this strategy has a meaningful stopping-criterion concept.

        Optimization tuners (PBT, BO) halt on a *criterion* — max rounds, an
        early-stop patience budget, or population convergence — and the
        per-round summary reports that as a ``Status`` line (``running`` while
        the loop continues, ``stopped - <reason>`` on the terminal round). A
        pure design sweep (LHS) has no such notion: it evaluates a fixed sample
        to completion, so it returns ``False`` and the ``Status`` row is
        omitted entirely. Override to ``True`` in optimization strategies.
        """
        return False

    def build_optimizer(self) -> None:
        """
        Construct and wire the strategy's persistent optimizer core.

        Called once at the end of :meth:`setup`, *after*
        :meth:`propose_initial_configs` has drawn the initial configs and the
        shared bring-up has left live instances, snapshots, and the
        orchestrator in place. This is the seam where a strategy that carries
        optimizer state across generations builds it: PBT wires its
        :class:`Population` (binding workers to the live instances and the
        baseline snapshot) from ``self.initial_configs``; BO constructs its
        surrogate here.

        The base default is a no-op — LHS-design evaluates a fixed sample with
        no persistent optimizer to build, so it does not override.
        """
        return None

    @property
    def max_rounds(self) -> int:
        """Upper bound on generation rounds (the ``run()`` loop budget).

        Named to parallel the serialized ``tuning_session.num_rounds`` (the
        count of rounds that *actually ran*, which is lower when early-stopping
        fires): ``max_rounds`` is the planned ceiling, ``num_rounds`` the
        realized count. Override for finite-budget strategies.
        """
        return 1

    @property
    def round_label(self) -> str:
        """Human-facing noun for one iteration of the ``run()`` loop.

        The loop is strategy-neutral (``num_rounds`` is the serialized count),
        but each strategy has its own natural vocabulary for one pass, and the
        *display* logs should speak it. The default is the generic ``"Round"``;
        concrete strategies override: PBT → ``"Generation"`` (an EA breeds a new
        generation each pass), LHS → ``"Batch"`` (one parallel slice of a fixed
        design), BO → ``"Iteration"`` (one sequential propose-evaluate step).

        This governs only user-facing log strings ("GENERATION 3", "Generation
        3 Summary"); the on-disk ``generation_history`` / ``generation`` JSON
        keys are the fixed analysis-loader schema and are intentionally *not*
        renamed.
        """
        return "Round"

    @property
    def num_instances(self) -> int:
        """
        Number of PostgreSQL instances to bring up.

        Defaults to one per parallel worker (parallel strategies sweep in
        batches of this size, so more is never needed). Override only if a
        strategy needs a different instance-to-worker ratio.
        """
        return self.lifecycle.num_parallel_workers

    @property
    def num_knobs(self) -> int:
        """Number of knobs in the full search space."""
        return len(self.full_knob_space) if self.full_knob_space is not None else 0

    @property
    def workload_type_value(self) -> str:
        """Workload type string for the session header (resolved from bundle)."""
        return self._workload_type.value

    @property
    def benchmark_name(self) -> str:
        """
        Canonical benchmark/driver name for the session header.

        Sourced from the workload bundle. This legitimately differs from the
        input selector ``self.benchmark`` for custom/template workloads (where
        ``self.benchmark`` is None but the bundle resolves a concrete name), so
        both are kept intentionally.
        """
        return self._benchmark_name

    def config_summary_lines(self) -> List[Tuple[str, str]]:
        """
        Strategy-specific ``(label, value)`` rows for the startup summary.

        Rendered in ``run()``'s initialization block (bold label, cyan value),
        matching PBT's banner. The base returns the generic round budget;
        concrete tuners override to name their own budget line (PBT:
        "Population Size", BO: "Iterations", LHS: "Design Size").
        """
        return [("Max Rounds:", str(self.max_rounds))]

    def session_filename(self) -> str:
        """Filename for the session trace JSON (``trace_{ts}.json``).

        Strategy-agnostic by design: the strategy is already encoded in the
        ``sessions/<workload>/<strategy>/`` path, so the filename does not
        restate it. Every reader can glob ``traces/trace_*.json`` uniformly
        across PBT, BO, and LHS.
        """
        return f"trace_{self.timestamp}.json"

    def best_config_filename(self) -> str:
        """Filename for the best-config JSON (``best_{ts}.json``)."""
        return f"best_{self.timestamp}.json"

    def best_config_fractions(self, best_config: Dict[str, Any]) -> Dict[str, Any]:
        """
        Convert a best-config dict to hardware-relative fractions.

        Default is identity; concrete tuners with a ``KnobSpace`` override
        this to serialize cross-host-portable fractions.
        """
        return best_config

    def setup(self) -> None:
        """
        Build the shared tuning environment and seed the initial design.

        Delegates to named sub-methods so each phase is independently
        readable, testable, and overridable by concrete tuners.
        """
        self._load_knob_space()
        self._resolve_resources()
        self._build_workload_and_metrics()
        self._create_environment()
        self._bring_up_instances()
        self._seed_initial_configs()

    def _load_knob_space(self) -> None:
        """Resolve workload type and build the full + filtered knob spaces."""
        resolved_workload_type = self._resolve_granular_workload_type()

        LOGGER.info(
            "Loading knob space: %s%s%s (source: %s, workload: %s)",
            COLORS.cyan,
            self.lifecycle.knob_tier.capitalize(),
            COLORS.reset,
            self.lifecycle.knob_source,
            resolved_workload_type,
        )
        self.knob_space = get_knob_space(
            self.lifecycle.knob_tier,
            knob_source=self.lifecycle.knob_source,
            workload_type=resolved_workload_type,
        )
        self.full_knob_space = self.knob_space

    def _resolve_resources(self) -> None:
        """Detect hardware and resolve knob ranges against worker resources."""
        LOGGER.info(
            "Detecting hardware resources for %s%d%s parallel workers...",
            COLORS.bold,
            self.lifecycle.num_parallel_workers,
            COLORS.reset,
        )
        self.worker_resources = resolve_worker_resources(
            num_workers=self.lifecycle.num_parallel_workers,
            data_path=self.data_root,
            worker_ram=self.lifecycle.worker_ram,
            worker_cpus=self.lifecycle.worker_cpus,
            worker_disk_read_bps=self.lifecycle.worker_disk_read_bps,
            worker_disk_write_bps=self.lifecycle.worker_disk_write_bps,
            worker_disk_read_iops=self.lifecycle.worker_disk_read_iops,
            worker_disk_write_iops=self.lifecycle.worker_disk_write_iops,
            probe_disk=self.lifecycle.probe_disk,
        )
        LOGGER.info(
            "Resolving hardware-relative knob ranges based on detected worker "
            "resources..."
        )
        self.full_knob_space.resolve_hardware_ranges(self.worker_resources)
        self.knob_space.worker_resources = self.worker_resources

        # ONLINE knob-view: keep full_knob_space complete, narrow knob_space
        # to the restart-free view the loop refines.
        self.knob_space = apply_tuning_mode_filter(
            self.full_knob_space, self.lifecycle.tuning_mode
        )

    def _build_workload_and_metrics(self) -> None:
        """Build workload bundle and metric config from scoring overrides."""
        assert self.worker_resources is not None
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
        self.enable_snapshots = self.lifecycle.enable_snapshots and bundle.enable_snapshots
        self._workload_executor = bundle.executor

        scoring_overrides: Dict[str, Any] = {
            "workload_features": dict(self.workload_features),
        }
        if self.lifecycle.scoring_policy is not None:
            scoring_overrides["scoring_policy"] = self.lifecycle.scoring_policy
        if self.lifecycle.scoring_policy_version is not None:
            scoring_overrides["scoring_policy_version"] = (
                self.lifecycle.scoring_policy_version
            )
        if self.lifecycle.metric_reference_version is not None:
            scoring_overrides["metric_reference_version"] = (
                self.lifecycle.metric_reference_version
            )
        self.metric_config = create_metric_config(
            self._workload_type.value,
            **scoring_overrides,
        )

    def _create_environment(self) -> None:
        """Create environment backend and workload orchestrator."""
        assert self.worker_resources is not None
        db_config = get_db_config()

        self.env = EnvironmentFactory.create(
            schema_provider=self._workload_executor,
            use_docker=self.lifecycle.use_docker,
            base_dir=self.data_root,
            base_port=5440,
            db_config=db_config,
            worker_resources=self.worker_resources,
            run_id=self.snapshot_identifier,
            image_name=self.lifecycle.docker_image,
            force_recreate_baseline=self.lifecycle.force_recreate_baseline,
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
        self.orchestrator = WorkloadOrchestrator(
            orchestrator_config, self._workload_executor, self.env
        )

    def _bring_up_instances(self) -> None:
        """Start PostgreSQL instances, verify, and prune unsupported knobs."""
        LOGGER.info("Collecting system hardware and software information...")
        self.system_info = get_system_info(data_path=self.data_root)

        LOGGER.info("")
        log_section_header(
            LOGGER,
            "Setting Up PostgreSQL Instances",
            top_separator=False,
        )
        LOGGER.info(
            "Creating %d PostgreSQL instances (force_recreate=%s)",
            self.num_instances,
            self.lifecycle.force_recreate_instances,
        )
        with self.bootstrap_timing.span("setup_instances"):
            self._instances = self.env.setup_instances(
                num_workers=self.num_instances,
                force_recreate=self.lifecycle.force_recreate_instances,
                num_parallel_workers=self.num_instances,
            )
        LOGGER.info("Verifying instance accessibility and configurations...")
        with self.bootstrap_timing.span("verify_instances"):
            self.env.verify_instances()
        LOGGER.info("Pruning unsupported knobs based on runtime version...")
        with self.bootstrap_timing.span("prune_knobs"):
            self._prune_unsupported_runtime_knobs()
        LOGGER.info(
            "%s%sPostgreSQL instances are ready.%s",
            COLORS.bold,
            COLORS.green,
            COLORS.reset,
        )

    def _seed_initial_configs(self) -> None:
        """Build session environment, propose initial configs, build optimizer."""
        self.session_environment = build_session_environment(
            env=self.env,
            num_parallel_workers=self.lifecycle.num_parallel_workers,
            population_size=self.seeded_config_count,
            system_info=self.system_info,
            use_docker=self.lifecycle.use_docker,
        )

        LOGGER.info("")
        LOGGER.info(
            "Seeding initial configurations for %s tuning session...",
            self.strategy.upper()
        )
        self.initial_configs = self.propose_initial_configs()

        self.build_optimizer()

        LOGGER.info(
            "%s%s%s Tuner Initialization Complete!%s",
            COLORS.bold, COLORS.green, self.strategy.upper(), COLORS.reset
        )

    @property
    def seeded_config_count(self) -> int:
        """
        Number of configurations seeded for ``session_environment``.

        Defaults to the count of initial configs drawn (parallel-workers if
        not yet drawn). Override when the design size is known up front. This
        is the in-code analogue of PBT's "population size"; it is serialized
        under ``strategy_params`` by strategies that carry the notion, never as
        a shared header field.
        """
        return len(self.initial_configs) or self.lifecycle.num_parallel_workers

    def _resolve_granular_workload_type(self) -> str:
        """Map the benchmark selector to the granular knob-space workload type."""
        if self.benchmark == "sysbench":
            return self.benchmark_config.sysbench_workload
        if self.benchmark == "tpch":
            return "olap"
        return self.benchmark_config.workload_type

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

    def _safe_breakdown(
        self, metrics: Optional[PerformanceMetrics]
    ) -> Optional[ScoreBreakdown]:
        """Compute a score breakdown, tolerating scorer failures."""
        scorer = self.orchestrator.scorer if self.orchestrator is not None else None
        return safe_breakdown(metrics, scorer)

    def _build_generation_record(
        self,
        *,
        generation: int,
        best_score_this_round: float,
        converged: bool,
        worker_results: Sequence[WorkerEvalResult],
        generation_elapsed_seconds: float,
        restart_count: int = 0,
        generation_timing: Optional[Any] = None,
        mean_score: Optional[float] = None,
        std_score: Optional[float] = None,
        num_exploited: Optional[int] = None,
        extra: Optional[Dict[str, Any]] = None,
    ) -> Dict[str, Any]:
        """Assemble one uniform ``generation_history`` entry for any strategy."""
        scorer = (
            self.orchestrator.scorer if self.orchestrator is not None else None
        )
        return build_generation_record(
            generation=generation,
            best_score_this_round=best_score_this_round,
            converged=converged,
            worker_results=worker_results,
            generation_elapsed_seconds=generation_elapsed_seconds,
            tuning_start_time=self.tuning_start_time,
            start_time=self.start_time,
            scorer=scorer,
            restart_count=restart_count,
            generation_timing=generation_timing,
            mean_score=mean_score,
            std_score=std_score,
            num_exploited=num_exploited,
            extra=extra,
        )

    def _aggregate_session_timing(self) -> Dict[str, Any]:
        """Aggregate per-component timing across every (round, worker) tuple."""
        return aggregate_session_timing(self.generation_history)

    def teardown(self) -> None:
        """Stop instances and optionally clean up data. Always called."""
        if self.env is None:
            return
        try:
            self.env.stop_all()
        finally:
            if self.lifecycle.cleanup_instances:
                self.env.cleanup(remove_data=True)

    def run(self) -> Dict[str, Any]:
        """Drive the full tuning lifecycle and return the session results."""
        strategy_label = self.strategy.value.upper()
        print_startup_banner(self.strategy)
        log_section_header(
            LOGGER,
            "%sStarting %s Tuner initialization%s",
            COLORS.bold,
            strategy_label,
            COLORS.reset,
        )

        self.start_time = time.time()
        try:
            LOGGER.info("")
            log_section_header(
                LOGGER,
                "Setting up tuning environment",
                top_separator=False,
            )
            with self.bootstrap_timing.span("setup"):
                self.setup()

            bootstrap_seconds = time.time() - self.start_time
            LOGGER.info(
                "Bootstrap completed in %s%s%.1fs%s (excluded from tuning "
                "wall-clock)",
                COLORS.cyan,
                COLORS.bold,
                bootstrap_seconds,
                COLORS.reset,
            )

            self._log_optimization_header(strategy_label)

            self.tuning_start_time = time.time()
            log_section_header(
                LOGGER,
                "%sStarting %s Optimization Loop%s",
                COLORS.bold,
                strategy_label,
                COLORS.reset,
            )
            for generation in range(self.max_rounds):
                self._log_round_start(generation)
                prev_best = self._safe_best_score()
                outcome = self.step(generation)
                self._rounds_completed += 1

                # Decide the stop *before* logging the round summary so the
                # summary's Status line can report which criterion fired on the
                # terminal round (should_stop sets self.stop_reason).
                stop = self.should_stop(outcome)
                self._log_round_end(outcome, prev_best, stopped=stop)
                if stop:
                    break
        except KeyboardInterrupt:
            LOGGER.warning(
                "%sInterrupted by user; saving partial results...%s",
                COLORS.orange,
                COLORS.reset,
            )
        finally:
            try:
                self.teardown()
            except (RuntimeError, ValueError, ConnectionError, OSError) as exc:
                LOGGER.warning(
                    "%sTeardown encountered an error: %s%s",
                    COLORS.orange,
                    exc,
                    COLORS.reset,
                )

        total_time = time.time() - self.start_time
        tuning_time = time.time() - (self.tuning_start_time or self.start_time)
        bootstrap_seconds = total_time - tuning_time

        results = self._assemble_results(
            total_time=total_time,
            tuning_time=tuning_time,
            bootstrap_seconds=bootstrap_seconds,
        )

        write_session_json(
            results,
            output_dir=self.output_root,
            filename=self.session_filename(),
        )
        best_config, best_score, _ = self.collect_best()
        write_best_config_json(
            self.best_config_fractions(best_config or {}),
            output_dir=self.output_root,
            filename=self.best_config_filename(),
        )
        log_final_summary(LOGGER, results)
        LOGGER.info(
            "Output Dir:      %s%s%s", COLORS.cyan, self.output_root, COLORS.reset
        )
        return results

    def _log_optimization_header(self, strategy_label: str) -> None:
        """Emit the PBT-grade system-info + configuration summary block."""
        log_optimization_header(
            strategy_label=strategy_label,
            system_info=self.system_info,
            knob_tier=self.lifecycle.knob_tier,
            knob_count=len(self.knob_space) if self.knob_space is not None else 0,
            config_summary_lines=self.config_summary_lines(),
            workload_type_value=self.workload_type_value,
            output_root=self.output_root,
        )

    def _safe_best_score(self) -> float:
        """Best score so far, tolerating a not-yet-populated optimizer."""
        try:
            return float(self.collect_best()[1] or 0.0)
        except (RuntimeError, ValueError, AttributeError, IndexError):
            return float(self._best_score_so_far)

    def _log_round_start(self, generation: int) -> None:
        """Emit the per-round section header and the live scoring-weight table."""
        scorer = (
            self.orchestrator.scorer
            if self.orchestrator is not None and hasattr(self.orchestrator, "scorer")
            else None
        )
        log_round_start(
            generation,
            round_label=self.round_label,
            scorer=scorer,
        )

    def _log_round_end(
        self,
        outcome: GenerationOutcome,
        prev_best: float,
        *,
        stopped: bool = False,
    ) -> None:
        """Announce a new best and log the generation summary."""
        log_round_end(
            outcome_index=outcome.index,
            outcome_best_score=outcome.best_score_this_generation,
            outcome_payload=outcome.payload,
            prev_best=prev_best,
            current_best=self._safe_best_score(),
            elapsed_seconds=time.time() - (self.tuning_start_time or self.start_time),
            emits_stop_status=self.emits_stop_status,
            stopped=stopped,
            stop_reason=self.stop_reason,
            round_label=self.round_label,
        )

    @staticmethod
    def _format_design_points(evaluated: Optional[Sequence[int]]) -> Optional[str]:
        """Render a list of evaluated design indices as a compact range string."""
        return format_design_points(evaluated)

    def log_worker_metrics_table(
        self,
        worker_results: Sequence[WorkerEvalResult],
        *,
        title: Optional[str] = None,
    ) -> None:
        """Render the end-of-round per-worker performance table."""
        log_worker_metrics(worker_results, title=title)

    def _assemble_results(
        self,
        *,
        total_time: float,
        tuning_time: float,
        bootstrap_seconds: float,
    ) -> Dict[str, Any]:
        """Compose the shared envelope and merge strategy-specific sections."""
        best_config, best_score, best_metrics = self.collect_best()

        header = build_session_header(
            strategy=self.strategy,
            knob_tier=self.lifecycle.knob_tier,
            knob_source=self.lifecycle.knob_source,
            num_knobs=self.num_knobs,
            workload_type=self.workload_type_value,
            benchmark_name=self.benchmark_name,
            timestamp=self.timestamp,
            seed=self.lifecycle.random_seed,
        )
        header.update(
            {
                "num_rounds": self._rounds_completed,
                "total_evaluations": self.total_evaluations(),
                "tuning_mode": self.lifecycle.tuning_mode.value,
                "converged": self.converged(),
                "total_time_seconds": total_time,
                "tuning_time_seconds": tuning_time,
                "bootstrap_seconds": bootstrap_seconds,
                "num_parallel_workers": self.lifecycle.num_parallel_workers,
            }
        )

        results: Dict[str, Any] = {
            "tuning_session": header,
            "best_configuration": {
                "score": float(best_score) if best_score else 0.0,
                "knobs": self.best_config_fractions(best_config or {}),
                "metrics": (
                    best_metrics.to_dict()
                    if best_metrics is not None and hasattr(best_metrics, "to_dict")
                    else {}
                ),
            },
            "worker_resources": (
                worker_resources_to_dict(self.worker_resources)
                if self.worker_resources is not None
                else {}
            ),
            "generation_history": self.generation_history,
            "bootstrap_breakdown": self.bootstrap_timing.to_dict(),
            "timing_summary": self._aggregate_session_timing(),
        }

        # Merge strategy-specific sections last so subclasses can override or
        # extend the shared envelope (e.g. add score_breakdown, warm_start).
        payload = self.build_session_payload()
        for key, value in payload.items():
            if key == "tuning_session" and isinstance(value, dict):
                results["tuning_session"].update(value)
            else:
                results[key] = value
        return results
