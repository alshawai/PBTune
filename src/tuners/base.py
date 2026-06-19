"""``BaseTuner`` — the shared lifecycle ABC for all tuning strategies.

The three strategies (PBT, BO, LHS-design) differ only in *how they propose
configurations* and *when they stop*. Everything around that — instance
bring-up, runtime knob pruning, the generation loop, instance teardown,
optional global recalibration, and session serialization — is identical. This
ABC encodes that invariant lifecycle as a concrete ``run()`` template method
(Template Method pattern) delegating the strategy-specific decisions to a few
abstract hooks.

Concrete subclasses implement:
  - ``propose_initial_configs``  — seed the first batch of configurations
  - ``step``                     — run one generation and report its outcome
  - ``should_stop``              — decide whether to halt after a generation
  - ``collect_best``             — surface the best (config, score, metrics)
  - ``build_session_payload``    — assemble the strategy-specific JSON sections

The incumbent PBT/BO tuners are NOT retrofitted onto this ABC in this change
(copy-not-refactor); ``LHSDesignTuner`` is its first concrete user. See
ADR-006 for the migration boundary.
"""

from __future__ import annotations

import time
from abc import ABC, abstractmethod
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

from src.tuners.utils.session_writer import (
    build_session_header,
    worker_resources_to_dict,
    write_best_config_json,
    write_session_json,
)
from src.tuners.utils.types import (
    GenerationOutcome,
    TunerLifecycleConfig,
    TuningStrategy,
)
from src.utils.hardware_info import WorkerResources
from src.utils.logger import get_logger
from src.utils.timing import TimingRecorder

LOGGER = get_logger("BaseTuner")


class BaseTuner(ABC):
    """Abstract base class encoding the shared tuner lifecycle.

    Subclasses provide strategy-specific behavior through the abstract hooks;
    the concrete ``run()`` method drives the invariant lifecycle and owns the
    timing instrumentation, instance lifecycle, and result serialization.
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

        # Populated by setup()/subclasses during run().
        self.worker_resources: Optional[WorkerResources] = None
        self.generation_history: List[Dict[str, Any]] = []
        self.start_time: float = 0.0
        self.tuning_start_time: float = 0.0
        self.bootstrap_timing = TimingRecorder()

        self._best_score_so_far: float = 0.0

    # ------------------------------------------------------------------
    # Abstract strategy hooks
    # ------------------------------------------------------------------
    @abstractmethod
    def setup(self) -> None:
        """Bring up instances, resolve resources, prune knobs, seed state.

        Implementations must populate ``self.worker_resources`` and ready
        whatever environment the generation loop needs. Called once at the
        start of ``run()`` inside the timing/teardown guard.
        """

    @abstractmethod
    def propose_initial_configs(self) -> List[Dict[str, Any]]:
        """Return the initial batch of configurations to evaluate."""

    @abstractmethod
    def step(self, generation: int) -> GenerationOutcome:
        """Run a single generation and return its outcome.

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
        """Return strategy-specific sections to merge into the session JSON.

        The base class supplies the shared header, ``best_configuration`` and
        ``worker_resources`` blocks; this hook contributes everything else
        (generation history, score breakdown, provenance, ...).
        """

    @abstractmethod
    def teardown(self) -> None:
        """Stop instances and optionally clean up data. Always called."""

    # ------------------------------------------------------------------
    # Optional hooks (sensible defaults)
    # ------------------------------------------------------------------
    @property
    def max_generations(self) -> int:
        """Upper bound on generations. Override for finite-budget strategies."""
        return 1

    @property
    def num_knobs(self) -> int:
        """Number of knobs in the (full) search space. Override as needed."""
        return 0

    @property
    def workload_type_value(self) -> str:
        """Workload type string for the session header. Override as needed."""
        return self.lifecycle.knob_source  # overridden by concrete tuners

    @property
    def benchmark_name(self) -> str:
        """Benchmark driver name for the session header. Override as needed."""
        return "unknown"

    def session_filename(self) -> str:
        """Filename for the session JSON (``{strategy}_results_{ts}.json``)."""
        return f"{self.strategy.value}_results_{self.timestamp}.json"

    def best_config_filename(self) -> str:
        """Filename for the best-config JSON."""
        return f"best_config_{self.timestamp}.json"

    def best_config_fractions(self, best_config: Dict[str, Any]) -> Dict[str, Any]:
        """Convert a best-config dict to hardware-relative fractions.

        Default is identity; concrete tuners with a ``KnobSpace`` override
        this to serialize cross-host-portable fractions.
        """
        return best_config

    # ------------------------------------------------------------------
    # Concrete lifecycle driver (Template Method)
    # ------------------------------------------------------------------
    def run(self) -> Dict[str, Any]:
        """Drive the full tuning lifecycle and return the session results."""
        LOGGER.info(
            "Starting %s tuner (tier=%s, source=%s, workers=%d)",
            self.strategy.value.upper(),
            self.lifecycle.knob_tier,
            self.lifecycle.knob_source,
            self.lifecycle.num_parallel_workers,
        )
        self.start_time = time.time()
        try:
            with self.bootstrap_timing.span("setup"):
                self.setup()

            self.tuning_start_time = time.time()
            for generation in range(self.max_generations):
                outcome = self.step(generation)
                self._best_score_so_far = max(
                    self._best_score_so_far, outcome.best_score_so_far
                )
                if self.should_stop(outcome):
                    LOGGER.info(
                        "Stopping criterion met after generation %d", generation
                    )
                    break
        except KeyboardInterrupt:
            LOGGER.warning("Interrupted by user; saving partial results...")
        finally:
            try:
                self.teardown()
            except (RuntimeError, ValueError, ConnectionError, OSError) as exc:
                LOGGER.warning("Teardown encountered an error: %s", exc)

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
        best_config, _, _ = self.collect_best()
        write_best_config_json(
            self.best_config_fractions(best_config or {}),
            output_dir=self.output_root,
            filename=self.best_config_filename(),
        )
        return results

    # ------------------------------------------------------------------
    # Result assembly
    # ------------------------------------------------------------------
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
