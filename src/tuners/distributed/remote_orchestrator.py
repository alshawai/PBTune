# Copyright (C) 2026 Ibrahim Al-Shawa and PBTune contributors
# Licensed under the GNU General Public License v3.0
# See LICENSE file for details

"""
Remote Workload Orchestrator
============================

Coordinator-side ``WorkloadOrchestrator`` whose ``evaluate_worker`` dispatches
the actual apply→run→measure work to the device that owns the worker (over
HTTP/JSON RPC) and then **scores centrally**.

Central scoring is deliberate: the composite scorer's adaptive normalisation
must see the whole population's metrics together, exactly as in single-device
mode. Devices therefore return raw :class:`PerformanceMetrics`; the score is
computed here with the same ``engine.compute_breakdown`` call the local
orchestrator uses (orchestrator.py), so scores are identical in meaning.
"""

from __future__ import annotations

import dataclasses
from typing import Any, Dict, Optional

from src.tuners.engine.orchestrator import (
    WorkloadOrchestrator,
    WorkloadOrchestratorConfig,
)
from src.tuners.engine.worker import BaseWorker
from src.tuners.distributed.agent_api import ROUTES, RunEvalRequest, RunEvalResponse
from src.tuners.distributed.transport import AgentClient, AgentRPCError
from src.utils.metrics import PerformanceMetrics
from src.utils.timing import TimingRecorder


def metrics_from_dict(d: Dict[str, Any]) -> PerformanceMetrics:
    """Reconstruct :class:`PerformanceMetrics` from its ``to_dict`` payload.

    Filters to known dataclass fields so protocol-forward extra keys are
    ignored rather than raising.
    """
    valid = {f.name for f in dataclasses.fields(PerformanceMetrics)}
    return PerformanceMetrics(**{k: v for k, v in d.items() if k in valid})


def _recorder_from_timing(timing: Optional[Dict[str, Any]]) -> TimingRecorder:
    """Best-effort reconstruction of a TimingRecorder from a serialised dict.

    Distributed timing provenance is thinner than local mode's; if the payload
    can't be rehydrated we fall back to a fresh recorder so downstream code
    (which only reads/serialises it) never sees ``None``.
    """
    from_dict = getattr(TimingRecorder, "from_dict", None)
    if timing and callable(from_dict):
        try:
            return from_dict(timing)
        except Exception:  # noqa: BLE001 — telemetry only
            pass
    return TimingRecorder()


class RemoteWorkloadOrchestrator(WorkloadOrchestrator):
    """Runs evaluations on remote devices; scores on the coordinator."""

    def __init__(
        self,
        config: WorkloadOrchestratorConfig,
        workload_executor: Any,
        env: Any,
        clients: Dict[int, AgentClient],
        eval_timeout_s: float = 1800.0,
        dead_failure_type: str = "EXECUTION_CRASH",
    ):
        super().__init__(config, workload_executor, env)
        self._clients = clients
        self._eval_timeout_s = eval_timeout_s
        self._dead_failure_type = dead_failure_type

    def _client_for(self, worker_id: int) -> AgentClient:
        try:
            return self._clients[worker_id]
        except KeyError as exc:
            raise RuntimeError(
                f"No device agent registered for worker_id={worker_id}"
            ) from exc

    def evaluate_worker(
        self,
        worker: BaseWorker,
        apply_config: bool = True,
        generation: Optional[int] = None,
        barriers: Optional[Any] = None,
        random_seed: Optional[int] = None,
        restore_due: bool = False,
        next_eval_will_restore: bool = False,
    ) -> tuple[PerformanceMetrics, float, bool, Dict[str, Any], TimingRecorder]:
        """Dispatch the evaluation to the owning device, then score centrally.

        ``barriers`` is accepted for signature compatibility but ignored:
        the B1–B17 substep lockstep runs *locally on each device*, and the
        coordinator's only synchronisation point is the generation boundary
        (the ThreadPoolExecutor join in ``Population.evaluate_generation``).
        Distributed runs therefore set ``synchronize_workers=False``.
        """
        client = self._client_for(worker.worker_id)
        req = RunEvalRequest(
            knob_config=dict(worker.knob_config or {}),
            generation=int(generation or 0),
            apply_config=apply_config,
            restore_due=restore_due,
            next_eval_will_restore=next_eval_will_restore,
        )

        restart_occurred = False
        actual_config: Dict[str, Any] = {}
        timing: Optional[Dict[str, Any]] = None
        try:
            resp = RunEvalResponse.from_dict(
                client.post(
                    ROUTES["run_eval"], req.to_dict(), timeout=self._eval_timeout_s
                )
            )
            if resp.ok and resp.metrics is not None:
                metrics = metrics_from_dict(resp.metrics)
                restart_occurred = resp.restart_occurred
                actual_config = resp.actual_config
                timing = resp.timing
            else:
                worker.logger.error(
                    " ➤ Remote eval reported failure: %s", resp.error or "unknown"
                )
                metrics = PerformanceMetrics(failure_type=self._dead_failure_type)
        except AgentRPCError as exc:
            # Transport/timeout/HTTP error => treat as a dead worker and let the
            # standard population rescue path (resample / config-clone) recover.
            worker.logger.error(" ➤ Remote eval RPC failed: %s", exc)
            metrics = PerformanceMetrics(failure_type=self._dead_failure_type)

        engine = self._get_scoring_engine()
        score_breakdown = engine.compute_breakdown(metrics, worker_logger=worker.logger)
        worker.score_breakdown = score_breakdown
        score = score_breakdown.final_score

        return (
            metrics,
            score,
            restart_occurred,
            actual_config,
            _recorder_from_timing(timing),
        )
