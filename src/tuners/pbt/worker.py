# Copyright (C) 2026 Ibrahim Al-Shawa and PBTune contributors
# Licensed under the GNU General Public License v3.0
# See LICENSE file for details

"""
PBTWorker - Individual Population Member
=========================================

``PBTWorker`` extends :class:`~src.tuners.engine.worker.BaseWorker` with the
state and operators Population-Based Training needs on top of the plain
evaluation vehicle: a composite performance score, measurement history, the
"ready" step counter, evolutionary lineage, and the exploit/explore mutators.

Worker Lifecycle in PBT
-----------------------
1. **Initialization**: random configuration sampled from the knob space
2. **Evaluation**: configuration applied to database, workload executed,
   metrics folded in via :meth:`update_metrics`
3. **Ready Check**: after ``ready_interval`` evaluations, the worker becomes
   eligible for exploit/explore
4. **Exploit**: if a poor performer, copy configuration from an elite worker
   (:meth:`clone_from`)
5. **Explore**: perturb the copied configuration (:meth:`perturb`)
6. **Repeat**: return to evaluation with the new configuration

Example
-------
>>> from src.knobs import get_knob_space
>>> knob_space = get_knob_space('minimal')
>>> worker = PBTWorker(worker_id=0, knob_space=knob_space, ready_interval=1)
>>> worker.is_ready()  # False initially
False
>>> from src.utils.metrics import PerformanceMetrics
>>> worker.update_metrics(PerformanceMetrics(latency_p95=50.0, throughput=100.0), 0.85)
>>> worker.is_ready()  # True after one evaluation
True
"""

from dataclasses import dataclass, field
from typing import Dict, Any, Optional, Tuple, List
import copy

import numpy as np

from src.tuners.engine.worker import BaseWorker
from src.utils.metrics import PerformanceMetrics
from src.utils.scoring.contracts import ScoreBreakdown


@dataclass
class PBTWorker(BaseWorker):
    """
    Single member of the PBT population.

    Adds PBT optimizer state to :class:`BaseWorker`: the performance score PBT
    maximizes, measurement history, the ready-mechanism step counter, and
    evolutionary lineage — plus the exploit (:meth:`clone_from`) and explore
    (:meth:`perturb`) operators.

    Attributes
    ----------
    ready_interval : int
        How many evaluations before the worker can be exploited/explored.
        From the PBT paper: prevents premature convergence. Typical values:
        1 (aggressive), 3-5 (conservative).

    performance_score : float
        Composite performance score (higher = better) — what PBT maximizes.
        Default 0.0 (not yet evaluated).

    metrics : Optional[PerformanceMetrics]
        Detailed measurements from the last evaluation. None until first eval.

    performance_history : List[PerformanceMetrics]
        Every measurement this worker has produced, in evaluation order.

    step_count : int
        Number of times this worker has been evaluated (drives the ready
        mechanism).

    parent_id : Optional[int]
        Worker ID this configuration was copied from (set during exploit).
        None for initial random configs.

    generation_created : int
        Which generation this worker was created/last exploited.

    config_history : list
        Optional log of configuration changes over time (exploit/explore).
    """

    ready_interval: int = 1

    performance_score: float = 0.0
    metrics: Optional[PerformanceMetrics] = None

    performance_history: List[PerformanceMetrics] = field(default_factory=list)

    step_count: int = 0
    parent_id: Optional[int] = None
    generation_created: int = 0

    config_history: list = field(default_factory=list)

    _rng: Optional[np.random.Generator] = field(default=None, repr=False)

    @property
    def rng(self) -> np.random.Generator:
        """Per-worker RNG stream. Seeded by Population; lazy fallback for tests."""
        if self._rng is None:
            self._rng = np.random.default_rng()
        return self._rng

    def is_ready(self) -> bool:
        """
        Check if worker is ready for exploit/explore operations.

        Workers must complete at least ``ready_interval`` evaluations before
        they can participate in exploit/explore. This is the "ready mechanism"
        from the PBT paper.

        Returns
        -------
        bool
            True if step_count >= ready_interval, False otherwise.
        """
        return self.step_count >= self.ready_interval

    def clone_from(
        self,
        other: "PBTWorker",
        current_generation: int,
        exclude_knobs: Optional[List[str]] = None,
    ) -> None:
        """
        Copy configuration from another worker (EXPLOIT phase).

        Parameters
        ----------
        other : PBTWorker
            The elite worker to copy from (must be in the top quantile).

        current_generation : int
            The current generation number (for tracking).

        exclude_knobs : Optional[List[str]]
            Knobs to exclude from copying (e.g., restart-required knobs between
            restart intervals).

        Notes
        -----
        What gets copied: ``knob_config`` (excluding ``exclude_knobs``).
        What does NOT: ``performance_score``/``metrics`` (remeasured),
        ``step_count`` (maintained), ``worker_id`` (identity preserved).
        """
        if exclude_knobs:
            if other.knob_config:
                for knob_name, value in other.knob_config.items():
                    if knob_name not in exclude_knobs:
                        if self.knob_config:
                            self.knob_config[knob_name] = copy.deepcopy(value)
        else:
            self.knob_config = copy.deepcopy(other.knob_config)

        self.parent_id = other.worker_id
        self.generation_created = current_generation

        if self.config_history is not None:
            self.config_history.append(
                {
                    "generation": current_generation,
                    "action": "exploit",
                    "parent_id": other.worker_id,
                    "config": copy.deepcopy(self.knob_config),
                }
            )

    def perturb(
        self,
        perturbation_factors: Tuple[float, float] = (0.8, 1.2),
        current_generation: Optional[int] = None,
        exclude_knobs: Optional[List[str]] = None,
        resample_probability: float = 0.0,
    ) -> None:
        """
        Perturb configuration (EXPLORE phase).

        Parameters
        ----------
        perturbation_factors : Tuple[float, float]
            (min_factor, max_factor) for perturbation. Default (0.8, 1.2)
            multiplies each knob by a random value in [0.8, 1.2].

        current_generation : Optional[int]
            Current generation number for history tracking.

        exclude_knobs : Optional[List[str]]
            Knobs to exclude from perturbation (keep constant).

        resample_probability : float
            Probability of fully resampling a knob from its prior instead of
            perturbing it. Default 0.0.

        Notes
        -----
        Without perturbation, all workers would eventually converge to the
        same configuration. Perturbation maintains diversity and explores
        nearby configurations.
        """
        self.knob_config = self.knob_space.perturb_config(
            config=self.knob_config,  # type: ignore
            perturbation_factor=perturbation_factors,
            rng=self.rng,
            worker_id=self.worker_id,
            exclude_knobs=exclude_knobs,
            resample_probability=resample_probability,
        )

        if self.config_history is not None and current_generation is not None:
            self.config_history.append(
                {
                    "generation": current_generation,
                    "action": "explore",
                    "perturbation_factors": perturbation_factors,
                    "config": copy.deepcopy(self.knob_config),
                }
            )

    def update_metrics(
        self,
        metrics: PerformanceMetrics,
        score: float,
        score_breakdown: Optional[ScoreBreakdown] = None,
    ) -> None:
        """
        Update the worker's performance after an evaluation.

        Called after a workload evaluation completes: records the metrics and
        score, and increments the step counter (driving the ready mechanism).

        Parameters
        ----------
        metrics : PerformanceMetrics
            Detailed performance measurements.

        score : float
            Composite performance score computed from metrics (higher = better).

        score_breakdown : Optional[ScoreBreakdown]
            The score breakdown, if available.
        """
        self.metrics = metrics
        self.performance_score = score
        if score_breakdown is not None:
            self.score_breakdown = score_breakdown
        self.step_count += 1
        self.performance_history.append(metrics)

    def reset_to_random(self, seed: Optional[int] = None) -> None:
        """
        Reset the worker to a new random configuration.

        Useful for restarting a worker or implementing advanced evolution
        strategies (e.g., periodic random restarts to avoid local optima).

        Parameters
        ----------
        seed : Optional[int]
            Random seed for reproducibility.
        """
        self.knob_config = self.knob_space.sample_random_config(seed=seed)
        self.performance_score = 0.0
        self.metrics = None
        self.score_breakdown = None
        self.parent_id = None
        self.force_restart_next_eval = False

    def __repr__(self) -> str:
        """Human-readable representation."""
        status = "ready" if self.is_ready() else "not ready"
        parent_str = f", parent={self.parent_id}" if self.parent_id is not None else ""
        port_str = f", port={self.port}" if self.port is not None else ""
        return (
            f"PBTWorker(id={self.worker_id}, "
            f"score={self.performance_score:.4f}, "
            f"steps={self.step_count}, "
            f"status={status}"
            f"{parent_str}"
            f"{port_str})"
        )

    def __str__(self) -> str:
        """Simple string representation."""
        return f"Worker-{self.worker_id} (score={self.performance_score:.4f})"

    def to_dict(self) -> Dict[str, Any]:
        """
        Convert the worker to a dictionary for serialization.

        Useful for logging, checkpointing, and analysis.

        Returns
        -------
        Dict[str, Any]
            Dictionary containing worker state.
        """
        return {
            "worker_id": self.worker_id,
            "knob_config": self.knob_config,
            "performance_score": self.performance_score,
            "metrics": self.metrics.to_dict() if self.metrics else None,
            "step_count": self.step_count,
            "ready_interval": self.ready_interval,
            "is_ready": self.is_ready(),
            "parent_id": self.parent_id,
            "generation_created": self.generation_created,
            "port": self.port,
            "force_restart_next_eval": self.force_restart_next_eval,
        }
