"""
Shared types for the unified tuners package.

This module defines the small, dependency-light value types that the
``BaseTuner`` lifecycle and its concrete subclasses share. It is deliberately
decoupled from the legacy ``src/tuner`` (PBT) and ``src/scripts/bo_baseline``
(BO) packages: those packages are NOT modified by the tuners extraction. The
types here are *copies* (by intent and shape) of the conventions used in those
packages, lifted into a single place so a third strategy (LHS-design sampling)
can reuse them without importing from either incumbent.

See ``docs/architecture/adr/ADR-006-unified-tuners-package.md`` for the
rationale behind the copy-not-refactor boundary.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum
from typing import Any, Dict, Optional

from src.tuners.utils.exceptions import TunerConfigError
from src.utils.types import TuningMode


class TuningStrategy(str, Enum):
    """The optimization strategy that produced a tuning session.

    This is orthogonal to the *benchmark* (the workload driver: sysbench,
    tpch, or a custom template). A session always has exactly one strategy
    and exactly one benchmark. The value is serialized into the
    ``tuning_session.tuning_strategy`` JSON field and consumed by the
    analysis and evaluation loaders.

    Members
    -------
    PBT
        Population-Based Training (``src/tuner``).
    BO
        Bayesian Optimization baseline (``src/scripts/bo_baseline``).
    LHS
        Latin Hypercube Sampling importance-design tuner (``src/tuners``).
    """

    PBT = "pbt"
    BO = "bo"
    LHS = "lhs"

    def __str__(self) -> str:  # pragma: no cover - trivial
        return self.value

    @classmethod
    def from_value(cls, value: Any) -> "TuningStrategy":
        """Coerce a string/enum into a ``TuningStrategy``.

        Raises
        ------
        ValueError
            If ``value`` does not name a known strategy.
        """
        if isinstance(value, cls):
            return value
        normalized = str(value).strip().lower()
        for member in cls:
            if member.value == normalized:
                return member

        valid = ", ".join(m.value for m in cls)
        raise ValueError(
            f"Unknown tuning strategy {value!r}; expected one of: {valid}"
        )


@dataclass
class GenerationOutcome:
    """
    Result of a single tuner generation (a.k.a. round / batch).

    Different strategies use different internal vocabulary — PBT calls these
    "generations", BO calls them "iterations", LHS calls them "design batches"
    — but they all share the same observable shape: an index, the best score
    seen *this round*, a converged flag, and an optional per-strategy payload
    for richer history.

    Attributes
    ----------
    index
        Zero-based generation index.
    best_score_this_generation
        Best composite score observed *within* this generation.
    converged
        Whether the strategy considers itself converged after this generation.
    payload
        Strategy-specific extra fields merged into ``generation_history``.
    """

    index: int
    best_score_this_generation: float = 0.0
    converged: bool = False
    payload: Dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> Dict[str, Any]:
        """Serialize for inclusion in ``generation_history``."""
        record = {
            "generation": self.index,
            "best_score": float(self.best_score_this_generation),
            "converged": bool(self.converged),
        }
        record.update(self.payload)
        return record


@dataclass
class TunerLifecycleConfig:
    """Strategy-agnostic knobs governing the ``BaseTuner.run`` driver.

    These are the cross-cutting settings every strategy needs regardless of
    its internal optimizer. Strategy-specific hyperparameters (population
    size, acquisition function, design size, ...) live on the concrete
    subclass, NOT here.

    Attributes
    ----------
    strategy
        Which optimization strategy this run uses.
    knob_tier
        Knob space tier ('minimal' | 'core' | 'standard' | 'extensive').
    knob_source
        'expert' or 'data_driven'.
    num_parallel_workers
        Number of PostgreSQL instances run concurrently.
    cleanup_instances
        Whether to remove instance data after the run.
    use_docker
        Whether to use the Docker environment backend.
    random_seed
        Seed for reproducible sampling.
    tuning_mode
        Restart policy mode (ONLINE / OFFLINE / ADAPTIVE). Threaded into the
        orchestrator config so every strategy honors the same restart policy.
    adaptive_restart_interval
        Restart cadence (in generations) for ``TuningMode.ADAPTIVE``.
    force_recreate_instances
        Rebuild instance data from scratch instead of reusing a baseline.
    worker_ram, worker_cpus, worker_disk_read_bps, worker_disk_write_bps,
    worker_disk_read_iops, worker_disk_write_iops, probe_disk
        Manual per-worker resource overrides. Any non-``None`` value routes
        resolution through the manual resolver; otherwise resources are
        auto-detected. Subsumes the former ad-hoc ``resource_overrides`` dict.
    scoring_policy, scoring_policy_version, metric_reference_version
        Scoring provenance overrides forwarded to global recalibration so the
        serialized session records exactly which scoring contract produced it.
    """

    strategy: TuningStrategy
    knob_tier: str = "minimal"
    knob_source: str = "expert"
    num_parallel_workers: int = 1
    cleanup_instances: bool = False
    use_docker: bool = True
    random_seed: Optional[int] = 42

    # Restart policy.
    tuning_mode: TuningMode = TuningMode.OFFLINE
    adaptive_restart_interval: int = 10
    force_recreate_instances: bool = False

    # Per-worker resource overrides (None => auto-detect).
    worker_ram: Optional[str] = None
    worker_cpus: Optional[int] = None
    worker_disk_read_bps: Optional[int] = None
    worker_disk_write_bps: Optional[int] = None
    worker_disk_read_iops: Optional[int] = None
    worker_disk_write_iops: Optional[int] = None
    probe_disk: bool = True

    # Scoring provenance (None => engine defaults).
    scoring_policy: Optional[str] = None
    scoring_policy_version: Optional[str] = None
    metric_reference_version: Optional[str] = None

    def __post_init__(self) -> None:
        self.strategy = TuningStrategy.from_value(self.strategy)
        if isinstance(self.tuning_mode, str):
            self.tuning_mode = TuningMode(self.tuning_mode)
        if self.num_parallel_workers < 1:
            raise TunerConfigError("num_parallel_workers must be at least 1")
        if self.adaptive_restart_interval < 1:
            raise TunerConfigError("adaptive_restart_interval must be at least 1")
