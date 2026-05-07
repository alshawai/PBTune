"""Configuration dataclass for Bayesian Optimization baseline runner."""

import json
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Dict, Optional
import argparse


@dataclass
class BOConfig:
    """Configuration for Bayesian Optimization baseline tuning."""

    # BO Configuration
    n_iterations: int = 50
    random_seed: int = 42

    # Knob Space
    knob_tier: str = "core"  # minimal, core, standard, extensive

    # Benchmark Configuration
    benchmark: str = "sysbench"  # sysbench or tpch
    workload_type: str = "oltp"  # oltp, olap, mixed
    evaluation_duration: float = 30.0  # seconds
    warmup_duration: float = 10.0  # seconds
    tuning_mode: str = "offline"  # offline, online, adaptive

    # Sysbench Configuration
    sysbench_tables: int = 4
    sysbench_table_size: int = 100000
    sysbench_workload: str = "oltp_read_write"  # oltp_read_only, oltp_read_write, oltp_write_only

    # TPC-H Configuration
    scale_factor: float = 1.0
    tpch_warmup_passes: int = 1

    # Instance Configuration
    use_docker: bool = True
    docker_image: Optional[str] = None
    force_recreate_instances: bool = False
    force_recreate_baseline: bool = False

    # Output Configuration
    output_dir: Path = field(default_factory=lambda: Path("results"))
    verbose: str = "INFO"  # DEBUG, INFO, WARNING, ERROR

    # Pilot+Freeze: number of initial design iterations before freezing ranges
    range_update_interval: int = 10

    # SMAC surrogate model
    bo_surrogate: str = "rf"  # gp, rf

    # PBT parity configuration
    pbt_session_path: Optional[Path] = None
    pbt_knob_names: Optional[tuple[str, ...]] = None

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

    def apply_pbt_session(self, path: Path, set_iteration_budget: bool = True) -> None:
        """Apply comparable benchmark/search settings from a PBT tuning session."""
        payload = self._load_pbt_session(path)
        session = payload["tuning_session"]

        self.pbt_session_path = path
        self.knob_tier = str(session.get("knob_tier", self.knob_tier))
        self.benchmark = str(session.get("benchmark_name", self.benchmark))
        self.workload_type = str(session.get("workload_type", self.workload_type))
        self.tuning_mode = str(session.get("tuning_mode", self.tuning_mode))

        if "sysbench_duration_seconds" in session:
            self.evaluation_duration = float(session["sysbench_duration_seconds"])
        if "sysbench_warmup_seconds" in session:
            self.warmup_duration = float(session["sysbench_warmup_seconds"])
        if "sysbench_tables" in session:
            self.sysbench_tables = int(session["sysbench_tables"])
        if "sysbench_table_size" in session:
            self.sysbench_table_size = int(session["sysbench_table_size"])
        if "sysbench_workload" in session:
            self.sysbench_workload = str(session["sysbench_workload"])
        if "tpch_scale_factor" in session:
            self.scale_factor = float(session["tpch_scale_factor"])
        if "tpch_warmup_passes" in session:
            self.tpch_warmup_passes = int(session["tpch_warmup_passes"])
        if set_iteration_budget:
            population_size = int(session.get("population_size", 0) or 0)
            total_generations = int(session.get("total_generations", 0) or 0)
            if population_size > 0 and total_generations > 0:
                self.n_iterations = population_size * total_generations

        best_configuration = payload.get("best_configuration", {})
        if isinstance(best_configuration, dict):
            knobs = best_configuration.get("knobs", {})
            if isinstance(knobs, dict) and knobs:
                self.pbt_knob_names = tuple(sorted(str(name) for name in knobs))

    @classmethod
    def from_args(cls, args: argparse.Namespace) -> "BOConfig":
        """Create BOConfig from argparse Namespace."""
        if args.tier is None and not args.pbt_session:
            raise ValueError("Either --tier or --pbt-session must be provided")

        config = cls(
            n_iterations=args.iterations if args.iterations is not None else cls.n_iterations,
            random_seed=args.seed,
            knob_tier=args.tier or cls.knob_tier,
            benchmark=args.benchmark,
            workload_type=args.workload,
            evaluation_duration=args.duration,
            warmup_duration=args.warmup,
            tuning_mode=args.tuning_mode,
            sysbench_tables=args.sysbench_tables,
            sysbench_table_size=args.sysbench_table_size,
            sysbench_workload=args.sysbench_workload,
            scale_factor=args.scale_factor,
            tpch_warmup_passes=args.tpch_warmup_passes,
            use_docker=not args.no_docker,
            docker_image=args.docker_image,
            force_recreate_instances=args.force_recreate_instances,
            force_recreate_baseline=args.force_recreate_baseline,
            output_dir=Path(args.output_dir),
            verbose=args.verbose,
            range_update_interval=args.range_update_interval,
            bo_surrogate=args.bo_surrogate,
        )

        if args.pbt_session:
            try:
                config.apply_pbt_session(
                    Path(args.pbt_session),
                    set_iteration_budget=args.iterations is None,
                )
            except Exception as e:
                from src.utils.logger import get_logger
                logger = get_logger(__name__)
                logger.warning(
                    f"Failed to load PBT session from {args.pbt_session}: {e}. "
                    f"Falling back to default or CLI-provided settings."
                )

        return config
