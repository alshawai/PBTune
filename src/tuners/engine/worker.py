"""
BaseWorker - Per-Instance Evaluation Vehicle
=============================================

``BaseWorker`` is the strategy-agnostic unit the evaluation engine operates
on: one database configuration bound to one PostgreSQL instance, plus the
handful of fields the orchestrator needs to apply that config, run a workload,
and hand back a measurement. It carries **no** optimizer state — no scores,
no step counting, no evolutionary lineage. Strategies that need those add them
in a subclass (see :class:`~src.tuners.pbt.worker.PBTWorker`).

Why this is the base
--------------------
The shared :class:`~src.tuners.engine.orchestrator.WorkloadOrchestrator`
*returns* ``(metrics, score, ...)`` from ``evaluate_worker`` — it never writes
the score back onto the worker. So every field a measurement produces
(``performance_score``, ``metrics``, ``step_count``, ...) is bookkeeping the
*strategy* owns, not the engine. LHS-design and BO both build a plain
``BaseWorker``, evaluate it, and read the returned triple; only PBT threads
per-worker measurement history and evolution through the worker object itself.

Example
-------
>>> from src.knobs import get_knob_space
>>> knob_space = get_knob_space('minimal')
>>> worker = BaseWorker(worker_id=0, knob_space=knob_space)
>>> worker.knob_config is not None  # random config sampled at construction
True
"""

from dataclasses import dataclass, field
from typing import Dict, Any, Optional
from logging import Logger
import copy

from src.knobs.knob_space import KnobSpace
from src.utils.scoring.contracts import ScoreBreakdown
from src.utils.timing import TimingRecorder
from src.config.database import DatabaseConfig
from src.utils.logger import get_logger, get_color_context

LOGGER = get_logger("Worker")
COLORS = get_color_context()


@dataclass
class BaseWorker:
    """
    Strategy-agnostic evaluation vehicle: one config on one instance.

    Holds only what the evaluation engine needs to apply a configuration,
    run a workload, and return a measurement. Optimizer state (scores,
    step counting, lineage) lives in strategy subclasses.

    Attributes
    ----------
    worker_id : int
        **Instance index** for this worker (0 to num_parallel_workers-1). This
        is the key the :class:`~src.utils.environments.base.DatabaseEnvironment`
        uses to resolve the worker's container/port/PGDATA, so it must always
        match an instance the environment actually provisioned.

    display_id : Optional[int]
        Identity used for *logging only*. Defaults to ``worker_id``. Distributed
        mode sets this to the fleet-global worker id while ``worker_id`` stays at
        the device's local instance index (each device provisions exactly one
        instance, at index 0), so logs remain globally meaningful without
        environment lookups being keyed on an index that does not exist locally.

    knob_space : KnobSpace
        The search space defining valid configurations.

    knob_config : Dict[str, Any]
        Current knob configuration (PostgreSQL parameters). If None at
        initialization, a random config is sampled from ``knob_space``.

    score_breakdown : Optional[ScoreBreakdown]
        The composite-score breakdown the orchestrator's scorer produced for
        the last evaluation. Set by the engine (not the strategy) so LHS/BO can
        read it back without re-running the scorer.

    port : Optional[int]
        PostgreSQL instance port for this worker. Set by the instance manager
        during initialization (each worker gets its own port).

    db_config : Optional[DatabaseConfig]
        Instance-specific database configuration (host, port, dbname, user,
        password). Set by the instance manager during initialization.

    force_restart_next_eval : bool
        Whether the orchestrator should force a PostgreSQL restart on the next
        configuration application. Used after dead-worker rescue to ensure
        restart-required knobs are actually activated before benchmarking.

    last_eval_timing : Optional[TimingRecorder]
        The per-evaluation timing recorder from the most recent
        ``evaluate_worker`` call, stashed for session serialization.

    last_actual_config : Optional[Dict[str, Any]]
        The SHOW-verified knob values that were in effect on the instance
        after the last evaluation completed.  ``None`` before the first eval
        and on evaluation failure.  Stashed here so the session assembler can
        persist it into the trace without touching the return signature of
        ``evaluate_worker``.
    """

    worker_id: int
    knob_space: KnobSpace

    knob_config: Optional[Dict[str, Any]] = None
    score_breakdown: Optional[ScoreBreakdown] = None

    display_id: Optional[int] = None

    port: Optional[int] = None
    db_config: Optional[DatabaseConfig] = None
    force_restart_next_eval: bool = True
    last_eval_timing: Optional[TimingRecorder] = None
    last_actual_config: Optional[Dict[str, Any]] = None

    logger: Logger = field(init=False, repr=False)

    def __post_init__(self):
        """Initialize worker with a random configuration if none provided."""
        if self.knob_config is None:
            self.knob_config = self.knob_space.sample_random_config(
                seed=None  # Different seed for each worker ensures diversity
            )
        self.logger = get_logger("Worker", worker_id=self.display_worker_id)

    @property
    def display_worker_id(self) -> int:
        """Identity to show in logs — the global id when one was supplied."""
        return self.worker_id if self.display_id is None else self.display_id

    def get_config_copy(self) -> Dict[str, Any]:
        """
        Get a deep copy of the current configuration.

        Returns a copy to prevent accidental modification of the worker's
        internal state.

        Returns
        -------
        Dict[str, Any]
            Deep copy of knob_config.
        """
        return copy.deepcopy(self.knob_config)  # type: ignore

    def __repr__(self) -> str:
        """Human-readable representation."""
        port_str = f", port={self.port}" if self.port is not None else ""
        return f"BaseWorker(id={self.display_worker_id}{port_str})"

    def __str__(self) -> str:
        """Simple string representation."""
        return f"Worker-{self.display_worker_id}"
