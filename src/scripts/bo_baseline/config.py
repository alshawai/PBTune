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

    # Pilot+Freeze: number of initial design iterations before freezing ranges
    range_update_interval: int = 10

    # SMAC surrogate model
    bo_surrogate: str = "rf"  # gp, rf

    # PBT parity configuration
    pbt_session_path: Optional[Path] = None
    pbt_knob_names: Optional[tuple[str, ...]] = None

    # Snapshot configuration
    enable_snapshots: bool = False
    snapshot_restore_interval: int = 1

    # Worker configuration (strictly sequential — single worker)
    max_workers: int = 1
    pbt_worker_resources: Optional[Dict[str, Any]] = None
    resource_division: int = 1

    # Scoring policy
    # Available options:
    # - "fixed_v1": Legacy static weights based on workload type (OLTP/OLAP/MIXED)
    # - "feature_driven_v2": Dynamic weights based on workload features evaluating variance, tail amplification, and DB stats
    scoring_policy: str = "default"

    # Early stopping — stop the BO loop if the incumbent does not improve for
    # `early_stopping_patience` consecutive iterations.  Scales with budget.
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

        self.pbt_session_path = path
        self.knob_tier = str(session.get("knob_tier", self.knob_tier))
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
            population_size = int(session.get("population_size", 0) or 0)

            # Use the actual completed generation count, not the configured max.
            # PBT writes current_generation into total_generations at save time,
            # so this reflects real work done (even if PBT early-stopped).
            # Fallback: len(generation_history) is ground truth.
            actual_generations = int(session.get("total_generations", 0) or 0)
            if actual_generations <= 0:
                gen_hist = payload.get("generation_history", [])
                actual_generations = len(gen_hist)

            if population_size > 0 and actual_generations > 0:
                self.n_iterations = population_size * actual_generations
                LOGGER.info(
                    "PBT session: pop_size=%d × actual_generations=%d → n_iterations=%d",
                    population_size,
                    actual_generations,
                    self.n_iterations,
                )
            else:
                LOGGER.warning(
                    "PBT session is missing positive population_size or "
                    "actual completed generations; keeping configured BO iteration budget"
                )

            # Auto-scale early stopping patience to ~20% of the resolved budget
            if self.early_stopping_enabled:
                self.early_stopping_patience = max(5, int(self.n_iterations * 0.20))
                LOGGER.info(
                    "Auto-scaled early_stopping_patience to %d (20%% of %d iterations)",
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

        # Extract num_parallel_workers for parallel BO (only if set_max_workers=True)
        if set_max_workers:
            num_parallel_workers = int(session.get("num_parallel_workers", 0) or 0)
            if num_parallel_workers > 0:
                self.resource_division = num_parallel_workers
            else:
                LOGGER.warning(
                    "PBT session is missing positive num_parallel_workers; "
                    "keeping configured BO resource division"
                )

        # Extract snapshot settings
        if "enable_snapshots" in session:
            self.enable_snapshots = bool(session["enable_snapshots"])

        if self.enable_snapshots and "snapshot_restore_interval" in session:
            # PBT restores every N generations.
            # BO now operates per-generation as well, so no pop_size multiplier.
            pbt_interval = int(session["snapshot_restore_interval"])
            self.snapshot_restore_interval = pbt_interval
            LOGGER.info(
                f"Extracted PBT snapshot interval ({pbt_interval} gens) "
                f"-> BO interval: {self.snapshot_restore_interval} iterations"
            )

        # Extract scoring policy from PBT session if present
        if "scoring_policy" in session:
            self.scoring_policy = str(session["scoring_policy"])

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
            knob_tier=args.tier or base_config.knob_tier,
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

        return config


RAPID_BO_CONFIG = BOConfig(
    n_iterations=40,
    range_update_interval=10,
    early_stopping_patience=8,
    benchmark_config=clone_benchmark_config(RAPID_BENCHMARK_CONFIG),
)
STANDARD_BO_CONFIG = BOConfig(
    n_iterations=120,
    range_update_interval=10,
    early_stopping_patience=20,
    benchmark_config=clone_benchmark_config(STANDARD_BENCHMARK_CONFIG),
)
THOROUGH_BO_CONFIG = BOConfig(
    n_iterations=400,
    range_update_interval=15,
    early_stopping_patience=60,
    benchmark_config=clone_benchmark_config(THOROUGH_BENCHMARK_CONFIG),
)
RESEARCH_BO_CONFIG = BOConfig(
    n_iterations=1600,
    range_update_interval=20,
    early_stopping_patience=200,
    benchmark_config=clone_benchmark_config(RESEARCH_BENCHMARK_CONFIG),
)
EXTREME_BO_CONFIG = BOConfig(
    n_iterations=3200,
    range_update_interval=25,
    early_stopping_patience=400,
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
