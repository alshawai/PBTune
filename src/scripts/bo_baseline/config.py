"""Configuration dataclass for Bayesian Optimization baseline runner."""

import json
from dataclasses import dataclass, field, replace
from pathlib import Path
from typing import Any, Dict, Optional
import argparse

from src.utils.types import (
    BenchmarkConfig,
    TuningMode,
    RAPID_BENCHMARK_CONFIG,
    STANDARD_BENCHMARK_CONFIG,
    THOROUGH_BENCHMARK_CONFIG,
    RESEARCH_BENCHMARK_CONFIG,
    EXTREME_BENCHMARK_CONFIG,
    clone_benchmark_config,
)
from src.utils.logger import get_logger

LOGGER = get_logger("Config")


@dataclass
class BOConfig:
    """Configuration for Bayesian Optimization baseline tuning."""

    # BO Configuration
    n_iterations: int = 50
    random_seed: int = 42

    # Knob Space
    knob_tier: str = "core"  # minimal, core, standard, extensive
    knob_source: str = "expert"  # expert, data_driven

    # Benchmark/workload configuration
    benchmark_config: BenchmarkConfig = field(
        default_factory=lambda: clone_benchmark_config(STANDARD_BENCHMARK_CONFIG)
    )

    # Instance Configuration
    use_docker: bool = True
    docker_image: Optional[str] = None
    force_recreate_instances: bool = False
    force_recreate_baseline: bool = False
    data_dir: Optional[str] = None

    # Output Configuration
    output_dir: Path = field(default_factory=lambda: Path("results"))
    colocate_output: bool = False
    verbose: str = "INFO"  # DEBUG, INFO, WARNING, ERROR

    # Bootstrap size: number of initial Sobol iterations evaluated before the
    # normalizer is first calibrated and SMAC history is relabeled.
    # After calibration the normalizer continues to expand dynamically.
    range_update_interval: int = 10

    # SMAC surrogate model
    bo_surrogate: str = "rf"  # gp, rf

    # PBT parity configuration
    pbt_session_path: Optional[Path] = None
    pbt_knob_names: Optional[tuple[str, ...]] = None

    # Snapshot configuration
    enable_snapshots: bool = True
    snapshot_restore_interval: int = 1

    # Worker configuration (strictly sequential — single worker)
    max_workers: int = 1
    pbt_worker_resources: Optional[Dict[str, Any]] = None
    resource_division: int = 1
    worker_ram: Optional[str] = None
    worker_cpus: Optional[int] = None
    worker_disk_read_bps: Optional[int] = None
    worker_disk_write_bps: Optional[int] = None
    worker_disk_read_iops: Optional[int] = None
    worker_disk_write_iops: Optional[int] = None
    probe_disk: bool = True

    # Co-tenancy load: number of *total* concurrent instances (foreground BO
    # trial + background load) to run during each measurement window so the BO
    # baseline experiences the same single-host contention a PBT generation
    # does. ``1`` disables background load. When a PBT session is supplied this
    # is forced to the session's ``num_parallel_workers`` (the matched,
    # mandatory degree) unless ``no_cotenant`` is set. An explicit
    # ``--cotenancy-degree`` only applies when no session pins it.
    # See src/scripts/bo_baseline/cotenant.py.
    cotenancy_degree: int = 1
    no_cotenant: bool = False

    # Scoring policy
    # Available options:
    # - "fixed_v1": Legacy static weights based on workload type (OLTP/OLAP/MIXED)
    # - "feature_driven_v2": Dynamic weights based on workload features evaluating variance, tail amplification, and DB stats
    # When ``None``, the per-workload base config picks the canonical default
    # (``DEFAULT_SCORING_POLICY`` = ``feature_driven_v2``), matching PBT.
    scoring_policy: Optional[str] = None

    # Early stopping — stop the BO loop if the incumbent does not improve for
    # `early_stopping_patience` consecutive iterations. When applied via
    # ``apply_pbt_session(set_iteration_budget=True)``, patience is set to a
    # flat cap (50) rather than scaled to the budget; see that method.
    early_stopping_enabled: bool = True
    early_stopping_patience: int = 20  # default overridden by presets

    @staticmethod
    def _load_pbt_session(path: Path) -> Dict[str, Any]:
        """Load a PBT tuning-session JSON file."""
        if not path.exists():
            raise FileNotFoundError(f"PBT tuning session does not exist: {path}")

        try:
            with path.open("r", encoding="utf-8") as handle:
                payload = json.load(handle)
        except json.JSONDecodeError as exc:
            raise ValueError(f"PBT tuning session is not valid JSON: {path}") from exc

        if not isinstance(payload, dict):
            raise ValueError(f"PBT tuning session must contain a JSON object: {path}")

        session = payload.get("tuning_session")
        if not isinstance(session, dict):
            raise ValueError(
                f"PBT tuning session is missing tuning_session metadata: {path}"
            )

        return payload

    def apply_pbt_session(
        self,
        path: Path,
        set_iteration_budget: bool = True,
        set_max_workers: bool = True,
    ) -> None:
        """Apply comparable benchmark/search settings from a PBT tuning session."""
        payload = self._load_pbt_session(path)
        session = payload["tuning_session"]
        # Unified schema (2a′+) namespaces PBT hyperparameters under
        # ``strategy_params`` and scoring provenance under ``scoring``, renames
        # ``total_generations`` → ``num_rounds``. Read nested-first, then fall
        # back to the incumbent flat keys so both PBT session shapes size a BO
        # run identically.
        strategy_params = session.get("strategy_params") or {}
        scoring = session.get("scoring") or {}

        self.pbt_session_path = path
        self.knob_tier = str(session.get("knob_tier", self.knob_tier))
        self.knob_source = str(session.get("knob_source", self.knob_source))
        benchmark = str(session.get("benchmark_name", self.benchmark_config.benchmark))
        workload_type = str(
            session.get("workload_type", self.benchmark_config.workload_type)
        )
        tuning_mode = TuningMode(
            session.get("tuning_mode", self.benchmark_config.tuning_mode)
        )

        evaluation_duration = (
            float(session["sysbench_duration_seconds"])
            if "sysbench_duration_seconds" in session
            else self.benchmark_config.evaluation_duration
        )
        warmup_duration = (
            float(session["sysbench_warmup_seconds"])
            if "sysbench_warmup_seconds" in session
            else self.benchmark_config.warmup_duration
        )
        sysbench_tables = (
            int(session["sysbench_tables"])
            if "sysbench_tables" in session
            else self.benchmark_config.sysbench_tables
        )
        sysbench_table_size = (
            int(session["sysbench_table_size"])
            if "sysbench_table_size" in session
            else self.benchmark_config.sysbench_table_size
        )
        sysbench_workload = (
            str(session["sysbench_workload"])
            if "sysbench_workload" in session
            else self.benchmark_config.sysbench_workload
        )
        scale_factor = (
            float(session["tpch_scale_factor"])
            if "tpch_scale_factor" in session
            else self.benchmark_config.scale_factor
        )
        warmup_passes = (
            int(session["tpch_warmup_passes"])
            if "tpch_warmup_passes" in session
            else self.benchmark_config.warmup_passes
        )

        self.benchmark_config = replace(
            self.benchmark_config,
            benchmark=benchmark,
            workload_type=workload_type,
            tuning_mode=tuning_mode,
            evaluation_duration=evaluation_duration,
            warmup_duration=warmup_duration,
            sysbench_tables=sysbench_tables,
            sysbench_table_size=sysbench_table_size,
            sysbench_workload=sysbench_workload,
            scale_factor=scale_factor,
            warmup_passes=warmup_passes,
        )
        if set_iteration_budget:
            population_size = int(
                strategy_params.get(
                    "population_size", session.get("population_size", 0)
                )
                or 0
            )

            generation_history = payload.get("generation_history")
            if isinstance(generation_history, list) and generation_history:
                actual_generations = len(generation_history)
            else:
                # Legacy session without generation_history; fall back to the
                # configured target. Will overshoot when PBT early-stopped,
                # but that is preferable to silently swapping in the preset.
                # ``total_generations`` → ``num_rounds`` in the unified schema.
                actual_generations = int(
                    session.get(
                        "num_rounds", session.get("total_generations", 0)
                    )
                    or 0
                )

            if population_size > 0 and actual_generations > 0:
                preset_iterations = self.n_iterations
                self.n_iterations = population_size * actual_generations
                LOGGER.info(
                    "PBT session: pop_size=%d × actual_generations=%d → "
                    "n_iterations=%d (replaces preset=%d for equal-evaluation budget)",
                    population_size,
                    actual_generations,
                    self.n_iterations,
                    preset_iterations,
                )
            else:
                LOGGER.warning(
                    "PBT session is missing population_size or generation_history; "
                    "keeping preset BO iteration budget (pop=%d, gens=%d)",
                    population_size,
                    actual_generations,
                )

            # Flat patience cap: BO's GP overhead grows with iteration count,
            # so scaling patience with the budget pours cycles into a
            # saturated GP. 50 iterations is enough to detect a true
            # convergence plateau without burning compute on a saturated
            # surrogate.
            if self.early_stopping_enabled:
                self.early_stopping_patience = min(50, self.n_iterations)
                LOGGER.info(
                    "Set early_stopping_patience=%d (flat cap, budget=%d iterations)",
                    self.early_stopping_patience,
                    self.n_iterations,
                )

        best_configuration = payload.get("best_configuration", {})
        if isinstance(best_configuration, dict):
            knobs = best_configuration.get("knobs", {})
            if isinstance(knobs, dict) and knobs:
                self.pbt_knob_names = tuple(sorted(str(name) for name in knobs))

        # Extract worker resources for resource equalization
        worker_resources = payload.get("worker_resources")
        if isinstance(worker_resources, dict):
            self.pbt_worker_resources = worker_resources

        # Extract num_parallel_workers from the session. It drives two things:
        #   (1) resource_division for the legacy parallel-BO path (only when
        #       set_max_workers lets the session override the CLI), and
        #   (2) cotenancy_degree — the MANDATORY, matched co-tenancy load level
        #       so each BO measurement window experiences the same single-host
        #       contention a PBT generation generated. This is enforced
        #       unconditionally whenever the session reports a positive count.
        num_parallel_workers = int(session.get("num_parallel_workers", 0) or 0)
        if self.no_cotenant:
            self.cotenancy_degree = 1
            LOGGER.info(
                "Co-tenancy explicitly disabled (--no-cotenant); BO will run "
                "WITHOUT background load despite PBT session having "
                "num_parallel_workers=%d.",
                num_parallel_workers,
            )
            if set_max_workers and num_parallel_workers > 0:
                self.resource_division = num_parallel_workers
        elif num_parallel_workers > 0:
            self.cotenancy_degree = num_parallel_workers
            LOGGER.info(
                "Co-tenancy degree set to %d from matched PBT session "
                "(num_parallel_workers); BO trials will run with %d background "
                "load instance(s).",
                num_parallel_workers,
                num_parallel_workers - 1,
            )
            if set_max_workers:
                self.resource_division = num_parallel_workers
        else:
            LOGGER.warning(
                "PBT session is missing positive num_parallel_workers; cannot "
                "enforce matched co-tenancy — BO will run WITHOUT background "
                "load (this breaks the fair-comparison invariant)."
            )

        # Extract snapshot settings (strategy_params in the unified schema).
        if "enable_snapshots" in strategy_params:
            self.enable_snapshots = bool(strategy_params["enable_snapshots"])
        elif "enable_snapshots" in session:
            self.enable_snapshots = bool(session["enable_snapshots"])

        snapshot_interval_raw = strategy_params.get(
            "snapshot_restore_interval",
            session.get("snapshot_restore_interval"),
        )
        if self.enable_snapshots and snapshot_interval_raw is not None:
            # PBT restores every N generations.
            # BO now operates per-generation as well, so no pop_size multiplier.
            pbt_interval = int(snapshot_interval_raw)
            self.snapshot_restore_interval = pbt_interval
            LOGGER.info(
                f"Extracted PBT snapshot interval ({pbt_interval} gens) "
                f"-> BO interval: {self.snapshot_restore_interval} iterations"
            )

        # Extract scoring policy from PBT session if present (scoring block in
        # the unified schema, flat in incumbent/BO-flat sessions).
        if "scoring_policy" in scoring:
            self.scoring_policy = str(scoring["scoring_policy"])
        elif "scoring_policy" in session:
            self.scoring_policy = str(session["scoring_policy"])

        # ── Consolidated summary of everything copied from the PBT session ──
        LOGGER.info(
            "Applied PBT session '%s':\n"
            "  tier=%s, knob_source=%s, benchmark=%s/%s\n"
            "  tuning_mode=%s, eval=%.0fs, warmup=%.0fs\n"
            "  n_iterations=%d, knob_filter=%d knob(s), resource_division=%d\n"
            "  snapshots=%s (interval=%d), scoring_policy=%s",
            path.name,
            self.knob_tier,
            self.knob_source,
            self.benchmark_config.benchmark,
            self.benchmark_config.sysbench_workload
            or self.benchmark_config.workload_type,
            self.benchmark_config.tuning_mode,
            self.benchmark_config.evaluation_duration,
            self.benchmark_config.warmup_duration,
            self.n_iterations,
            len(self.pbt_knob_names) if self.pbt_knob_names else 0,
            self.resource_division,
            self.enable_snapshots,
            self.snapshot_restore_interval,
            self.scoring_policy,
        )

    @classmethod
    def from_args(cls, args: argparse.Namespace) -> "BOConfig":
        """Create BOConfig from argparse Namespace."""
        if args.tier is None and not args.pbt_session:
            raise ValueError("Either --tier or --pbt-session must be provided")

        config_name = args.config or "standard"
        base_config = BO_CONFIG_PRESETS[config_name]

        benchmark_preset = (
            BENCHMARK_CONFIG_PRESETS[args.benchmark_config]
            if args.benchmark_config
            else base_config.benchmark_config
        )
        benchmark_config = replace(
            clone_benchmark_config(benchmark_preset),
            benchmark=args.benchmark
            if args.benchmark is not None
            else benchmark_preset.benchmark,
            workload_type=args.workload
            if args.workload is not None
            else benchmark_preset.workload_type,
            evaluation_duration=args.duration
            if args.duration is not None
            else benchmark_preset.evaluation_duration,
            warmup_duration=args.warmup
            if args.warmup is not None
            else benchmark_preset.warmup_duration,
            tuning_mode=args.tuning_mode
            if args.tuning_mode is not None
            else benchmark_preset.tuning_mode,
            sysbench_tables=args.sysbench_tables
            if args.sysbench_tables is not None
            else benchmark_preset.sysbench_tables,
            sysbench_table_size=args.sysbench_table_size
            if args.sysbench_table_size is not None
            else benchmark_preset.sysbench_table_size,
            sysbench_workload=args.sysbench_workload
            if args.sysbench_workload is not None
            else benchmark_preset.sysbench_workload,
            scale_factor=args.scale_factor
            if args.scale_factor is not None
            else benchmark_preset.scale_factor,
            warmup_passes=args.tpch_warmup_passes
            if args.tpch_warmup_passes is not None
            else benchmark_preset.warmup_passes,
        )

        # Resolve early stopping settings
        early_stopping_enabled = base_config.early_stopping_enabled
        if hasattr(args, "disable_early_stopping") and args.disable_early_stopping:
            early_stopping_enabled = False

        early_stopping_patience = (
            args.early_stopping_patience
            if hasattr(args, "early_stopping_patience")
            and args.early_stopping_patience is not None
            else base_config.early_stopping_patience
        )

        config = replace(
            base_config,
            n_iterations=args.iterations
            if args.iterations is not None
            else base_config.n_iterations,
            random_seed=args.seed if args.seed is not None else base_config.random_seed,
            knob_tier=getattr(args, "tier", None) or base_config.knob_tier,
            knob_source=getattr(args, "knob_source", None) or base_config.knob_source,
            benchmark_config=benchmark_config,
            use_docker=not args.no_docker,
            docker_image=args.docker_image,
            force_recreate_instances=args.force_recreate_instances,
            force_recreate_baseline=args.force_recreate_baseline,
            data_dir=args.data_dir if hasattr(args, "data_dir") else None,
            output_dir=Path(args.output_dir)
            if args.output_dir is not None
            else base_config.output_dir,
            colocate_output=args.colocate_output
            if hasattr(args, "colocate_output")
            else False,
            verbose=args.verbose if args.verbose is not None else base_config.verbose,
            range_update_interval=args.range_update_interval
            if args.range_update_interval is not None
            else base_config.range_update_interval,
            bo_surrogate=args.bo_surrogate
            if args.bo_surrogate is not None
            else base_config.bo_surrogate,
            resource_division=args.resource_division
            if hasattr(args, "resource_division") and args.resource_division is not None
            else base_config.resource_division,
            scoring_policy=args.scoring_policy
            if hasattr(args, "scoring_policy") and args.scoring_policy is not None
            else base_config.scoring_policy,
            enable_snapshots=args.enable_snapshots
            if hasattr(args, "enable_snapshots") and args.enable_snapshots is not None
            else base_config.enable_snapshots,
            snapshot_restore_interval=args.snapshot_restore_interval
            if hasattr(args, "snapshot_restore_interval")
            and args.snapshot_restore_interval is not None
            else base_config.snapshot_restore_interval,
            early_stopping_enabled=early_stopping_enabled,
            early_stopping_patience=early_stopping_patience,
            worker_ram=args.worker_ram if hasattr(args, "worker_ram") else None,
            worker_cpus=args.worker_cpus if hasattr(args, "worker_cpus") else None,
            worker_disk_read_bps=(
                args.worker_disk_read_bps
                if hasattr(args, "worker_disk_read_bps")
                else None
            ),
            worker_disk_write_bps=(
                args.worker_disk_write_bps
                if hasattr(args, "worker_disk_write_bps")
                else None
            ),
            worker_disk_read_iops=(
                args.worker_disk_read_iops
                if hasattr(args, "worker_disk_read_iops")
                else None
            ),
            worker_disk_write_iops=(
                args.worker_disk_write_iops
                if hasattr(args, "worker_disk_write_iops")
                else None
            ),
            probe_disk=args.probe_disk if hasattr(args, "probe_disk") else True,
            cotenancy_degree=(
                args.cotenancy_degree
                if hasattr(args, "cotenancy_degree")
                and args.cotenancy_degree is not None
                else base_config.cotenancy_degree
            ),
            no_cotenant=getattr(args, "no_cotenant", False),
        )

        if args.pbt_session:
            try:
                config.apply_pbt_session(
                    Path(args.pbt_session),
                    set_iteration_budget=args.iterations is None,
                    set_max_workers=not hasattr(args, "resource_division")
                    or args.resource_division is None,
                )
            except Exception as e:
                LOGGER.warning(
                    f"Failed to load PBT session from {args.pbt_session}: {e}. "
                    f"Falling back to default or CLI-provided settings."
                )

        config.max_workers = 1  # Always sequential

        # Explicit CLI overrides session / defaults
        if getattr(args, "knob_source", None) is not None:
            config.knob_source = args.knob_source

        return config


RAPID_BO_CONFIG = BOConfig(
    n_iterations=40,
    range_update_interval=10,
    early_stopping_patience=20,
    benchmark_config=clone_benchmark_config(RAPID_BENCHMARK_CONFIG),
)
STANDARD_BO_CONFIG = BOConfig(
    n_iterations=80,
    range_update_interval=10,
    early_stopping_patience=50,
    benchmark_config=clone_benchmark_config(STANDARD_BENCHMARK_CONFIG),
)
THOROUGH_BO_CONFIG = BOConfig(
    n_iterations=400,
    range_update_interval=15,
    early_stopping_patience=200,
    benchmark_config=clone_benchmark_config(THOROUGH_BENCHMARK_CONFIG),
)
RESEARCH_BO_CONFIG = BOConfig(
    n_iterations=1600,
    range_update_interval=20,
    early_stopping_patience=800,
    benchmark_config=clone_benchmark_config(RESEARCH_BENCHMARK_CONFIG),
)
EXTREME_BO_CONFIG = BOConfig(
    n_iterations=3200,
    range_update_interval=25,
    early_stopping_patience=1600,
    benchmark_config=clone_benchmark_config(EXTREME_BENCHMARK_CONFIG),
)

BO_CONFIG_PRESETS = {
    "rapid": RAPID_BO_CONFIG,
    "standard": STANDARD_BO_CONFIG,
    "thorough": THOROUGH_BO_CONFIG,
    "research": RESEARCH_BO_CONFIG,
    "extreme": EXTREME_BO_CONFIG,
}

BENCHMARK_CONFIG_PRESETS = {
    "rapid": RAPID_BENCHMARK_CONFIG,
    "standard": STANDARD_BENCHMARK_CONFIG,
    "thorough": THOROUGH_BENCHMARK_CONFIG,
    "research": RESEARCH_BENCHMARK_CONFIG,
    "extreme": EXTREME_BENCHMARK_CONFIG,
}
