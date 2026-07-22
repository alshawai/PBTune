# Copyright (C) 2026 Ibrahim Al-Shawa and PBTune contributors
# Licensed under the GNU General Public License v3.0
# See LICENSE file for details

"""
Coordinator
===========

Control-plane glue for distributed mode. Builds an :class:`AgentClient` per
device from the fleet inventory, waits for every agent to report healthy, and
constructs the two drop-in components the existing ``Population`` loop needs:

- a :class:`RemoteEnvironment` (proxies lifecycle to the agents), and
- a :class:`RemoteWorkloadOrchestrator` (RPC eval + central scoring).

The PBT algorithm itself (``Population``, evolution, scoring) is untouched — the
coordinator only swaps ``env`` and ``orchestrator``. Distributed runs also set
``synchronize_workers=False`` because the B1–B17 substep barriers run locally on
each device; the coordinator's only sync point is the generation boundary.
"""

from __future__ import annotations

import time
from typing import Any, Dict, List

from src.config.database import DatabaseConfig
from src.tuners.distributed.agent_api import HealthResponse, ROUTES, SetupRequest
from src.tuners.distributed.config import DistributedConfig
from src.tuners.distributed.inventory import DeviceSpec
from src.tuners.distributed.remote_environment import RemoteEnvironment
from src.tuners.distributed.remote_orchestrator import RemoteWorkloadOrchestrator
from src.tuners.distributed.transport import AgentClient, AgentRPCError
from src.tuners.distributed import AGENT_PROTOCOL_VERSION
from src.utils.logger import get_logger

LOGGER = get_logger("Coordinator")


class Coordinator:
    """Manages the device fleet for a distributed tuning run."""

    def __init__(
        self,
        distributed_config: DistributedConfig,
        setup_template: SetupRequest,
        db_config: DatabaseConfig,
        population_size: int,
    ):
        distributed_config.validate_for_population(population_size)
        self.config = distributed_config
        self.setup_template = setup_template
        self.db_config = db_config
        self.population_size = population_size

        # Bind the first ``population_size`` devices to workers 0..N-1.
        self.devices: Dict[int, DeviceSpec] = {
            wid: distributed_config.inventory.device_for_worker(wid)
            for wid in range(population_size)
        }
        self.clients: Dict[int, AgentClient] = {
            wid: AgentClient(
                spec.agent_base_url, default_timeout=distributed_config.request_timeout_s
            )
            for wid, spec in self.devices.items()
        }

    # -- health handshake ------------------------------------------------- #
    def wait_for_agents(self) -> None:
        """Poll every agent's /health until all are reachable, or time out.

        Also enforces the protocol-version handshake so a coordinator/agent
        mismatch fails fast rather than corrupting a long run.
        """
        deadline = time.monotonic() + self.config.health_timeout_s
        pending = set(self.clients)
        last_err: Dict[int, str] = {}

        while pending and time.monotonic() < deadline:
            for worker_id in list(pending):
                try:
                    health = HealthResponse.from_dict(
                        self.clients[worker_id].get(
                            ROUTES["health"], timeout=self.config.request_timeout_s
                        )
                    )
                    self._check_protocol(worker_id, health)
                    pending.discard(worker_id)
                    LOGGER.info(
                        "➤ Agent for worker %d healthy on %s (backend=%s)",
                        worker_id,
                        self.devices[worker_id].display_name,
                        health.backend,
                    )
                except (AgentRPCError, RuntimeError) as exc:
                    last_err[worker_id] = str(exc)
            if pending:
                time.sleep(self.config.health_poll_interval_s)

        if pending:
            details = {wid: last_err.get(wid, "unreachable") for wid in pending}
            raise RuntimeError(
                f"Timed out waiting for device agents to become healthy: {details}"
            )

    def _check_protocol(self, worker_id: int, health: HealthResponse) -> None:
        got = (health.protocol_version or "").split(".")[0]
        want = AGENT_PROTOCOL_VERSION.split(".")[0]
        if got != want:
            raise RuntimeError(
                f"Protocol mismatch with worker {worker_id}: coordinator="
                f"{AGENT_PROTOCOL_VERSION} agent={health.protocol_version}"
            )

    # -- component factories --------------------------------------------- #
    def make_environment(self, schema_provider: Any, run_id: str) -> RemoteEnvironment:
        return RemoteEnvironment(
            run_id=run_id,
            db_config=self.db_config,
            schema_provider=schema_provider,
            clients=self.clients,
            devices=self.devices,
            setup_template=self.setup_template,
            request_timeout_s=self.config.request_timeout_s,
        )

    def make_orchestrator(
        self, orch_config: Any, executor: Any, env: RemoteEnvironment
    ) -> RemoteWorkloadOrchestrator:
        return RemoteWorkloadOrchestrator(
            config=orch_config,
            workload_executor=executor,
            env=env,
            clients=self.clients,
            eval_timeout_s=self.config.eval_timeout_s,
        )

    # -- teardown --------------------------------------------------------- #
    def shutdown_agents(self) -> None:
        """Best-effort /shutdown to every agent (used at end of a run)."""
        for worker_id, client in self.clients.items():
            try:
                client.post(ROUTES["shutdown"], {}, timeout=self.config.request_timeout_s)
            except AgentRPCError as exc:
                LOGGER.debug("Shutdown of worker %d agent failed: %s", worker_id, exc)

    def health_snapshot(self) -> List[HealthResponse]:
        out = []
        for worker_id in sorted(self.clients):
            try:
                out.append(
                    HealthResponse.from_dict(
                        self.clients[worker_id].get(ROUTES["health"])
                    )
                )
            except AgentRPCError:
                out.append(
                    HealthResponse(
                        status="error",
                        protocol_version="",
                        agent_version="",
                        worker_id=worker_id,
                        detail="unreachable",
                    )
                )
        return out
