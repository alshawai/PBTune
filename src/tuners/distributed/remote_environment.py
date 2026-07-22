# Copyright (C) 2026 Ibrahim Al-Shawa and PBTune contributors
# Licensed under the GNU General Public License v3.0
# See LICENSE file for details

"""
Remote Environment
==================

A :class:`~src.utils.environments.base.DatabaseEnvironment` implementation that
represents the whole fleet from the coordinator's point of view. Every
lifecycle call is proxied to the owning device's HTTP agent, so the existing
``Population`` orchestration works unchanged — it only ever sees the ABC.

Key distributed semantics
-------------------------
- **One worker per device.** ``setup_instances`` fans out ``/setup`` to each
  agent; each returns the port of its single local PostgreSQL instance.
- **Per-substep restart/start/stop are on-device.** The device's own
  orchestrator drives them inside ``run_eval``; the coordinator's copies are
  no-ops.
- **Snapshot = every device snapshots its own identical baseline.**
  ``create_snapshot`` fans out so each device holds a local base snapshot.
- **Clone = config-only.** The elite's *knobs* are copied in-memory by the
  evolution step (coordinator RAM); ``clone_instances`` merely tells each
  target device to reset its data to the byte-identical local baseline — no
  gigabyte PGDATA transfer over the network.
"""

from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor, as_completed
from pathlib import Path
from typing import Any, Dict, List, Optional

from src.config.database import DatabaseConfig
from src.tuners.distributed.agent_api import (
    CleanupRequest,
    HealthResponse,
    ResetRequest,
    ROUTES,
    SetupRequest,
    SetupResponse,
    SnapshotResponse,
)
from src.tuners.distributed.inventory import DeviceSpec
from src.tuners.distributed.transport import AgentClient, AgentRPCError
from src.utils.environments.base import DatabaseEnvironment, InstanceConfig
from src.utils.logger import get_logger

LOGGER = get_logger("RemoteEnvironment")


class RemoteEnvironment(DatabaseEnvironment):
    """Fleet-wide environment backed by per-device HTTP agents."""

    def __init__(
        self,
        run_id: str,
        db_config: DatabaseConfig,
        schema_provider: Any,
        clients: Dict[int, AgentClient],
        devices: Dict[int, DeviceSpec],
        setup_template: SetupRequest,
        request_timeout_s: float = 60.0,
        force_recreate_baseline: bool = False,
    ):
        super().__init__(
            run_id=run_id,
            db_config=db_config,
            schema_provider=schema_provider,
            force_recreate_baseline=force_recreate_baseline,
        )
        self._clients = clients
        self._devices = devices
        self._setup_template = setup_template
        self._request_timeout_s = request_timeout_s
        self._instances: Dict[int, InstanceConfig] = {}
        self._hardware: Dict[int, Dict[str, Any]] = {}
        self._device_resources: Dict[int, Dict[str, Any]] = {}
        self.docker_version = None

    # -- helpers ---------------------------------------------------------- #
    def _client(self, worker_id: int) -> AgentClient:
        try:
            return self._clients[worker_id]
        except KeyError as exc:
            raise RuntimeError(f"No agent for worker_id={worker_id}") from exc

    def _worker_ids(self) -> List[int]:
        return sorted(self._clients.keys())

    def _fan_out(self, fn, worker_ids: Optional[List[int]] = None) -> Dict[int, Any]:
        """Run ``fn(worker_id)`` concurrently across devices; collect results."""
        ids = worker_ids if worker_ids is not None else self._worker_ids()
        results: Dict[int, Any] = {}
        with ThreadPoolExecutor(max_workers=max(1, len(ids))) as pool:
            futures = {pool.submit(fn, wid): wid for wid in ids}
            for fut in as_completed(futures):
                wid = futures[fut]
                results[wid] = fut.result()
        return results

    # -- lifecycle -------------------------------------------------------- #
    def setup_instances(
        self,
        num_workers: int,
        force_recreate: bool = False,
        num_parallel_workers: int = 1,
    ) -> List[InstanceConfig]:
        if num_workers > len(self._clients):
            raise RuntimeError(
                f"Requested {num_workers} workers but only {len(self._clients)} "
                f"device agents are registered"
            )

        def _setup(worker_id: int) -> SetupResponse:
            req = SetupRequest.from_dict(self._setup_template.to_dict())
            if force_recreate:
                req.force_recreate_baseline = True
            resp = SetupResponse.from_dict(
                self._client(worker_id).post(
                    ROUTES["setup"], req.to_dict(), timeout=None
                )
            )
            if not resp.ok:
                raise RuntimeError(
                    f"Device setup failed for worker {worker_id}: {resp.detail}"
                )
            return resp

        results = self._fan_out(_setup, list(range(num_workers)))
        for worker_id in range(num_workers):
            resp = results[worker_id]
            device = self._devices[worker_id]
            self._instances[worker_id] = InstanceConfig(
                worker_id=worker_id,
                port=resp.port,
                data_dir=Path(resp.data_dir or "/"),
                running=True,
                host=device.host,
            )
            if resp.resources:
                self._device_resources[worker_id] = resp.resources
            LOGGER.info(
                "➤ Worker %d ready on %s:%d (%s)",
                worker_id,
                device.host,
                resp.port,
                resp.backend,
            )
        return [self._instances[w] for w in range(num_workers)]

    def initialize_schema(self, worker_id: int) -> None:
        """No-op on the coordinator: schema is prepared on each device during
        its local ``/setup`` (the base implementation would connect directly to
        the remote PostgreSQL, which the coordinator cannot reach)."""
        return None

    def representative_resources(self) -> Optional[Dict[str, Any]]:
        """Return one device's detected resources (identical fleet ⇒ any device).

        Used by the coordinator to resolve hardware-aware knob ranges against the
        *device* hardware instead of its own. Returns ``None`` if no device has
        reported resources yet (e.g. before ``setup_instances``).
        """
        if not self._device_resources:
            return None
        return self._device_resources[min(self._device_resources)]

    # Per-instance process control lives on the device (driven by its own
    # orchestrator during run_eval); the coordinator's copies are no-ops.
    def start_instance(self, worker_id: int) -> bool:
        return True

    def stop_instance(self, worker_id: int, mode: str = "fast") -> bool:
        return True

    def stop_all(self, mode: str = "fast") -> bool:
        return True

    def restart_instance(self, worker_id: int, quiet: bool = False) -> bool:
        return True

    def recover_instance(self, worker_id: int) -> bool:
        """Best-effort recovery: re-run setup on the device."""
        try:
            return self.rebuild_worker_instance(worker_id)
        except AgentRPCError as exc:
            LOGGER.warning("Recovery of worker %d failed: %s", worker_id, exc)
            return False

    def verify_instances(self) -> None:
        def _health(worker_id: int) -> HealthResponse:
            return HealthResponse.from_dict(
                self._client(worker_id).get(
                    ROUTES["health"], timeout=self._request_timeout_s
                )
            )

        results = self._fan_out(_health)
        unhealthy = []
        for worker_id, health in results.items():
            self._hardware[worker_id] = health.hardware or {}
            if health.status != "ok":
                unhealthy.append((worker_id, health.status, health.detail))
        if unhealthy:
            raise RuntimeError(f"Unhealthy device agents: {unhealthy}")

    def cleanup(self, remove_data: bool = False) -> None:
        def _cleanup(worker_id: int) -> None:
            client = self._client(worker_id)
            try:
                client.post(
                    ROUTES["cleanup"],
                    CleanupRequest(remove_data=remove_data).to_dict(),
                    timeout=self._request_timeout_s,
                )
            except AgentRPCError as exc:
                LOGGER.warning("Cleanup failed for worker %d: %s", worker_id, exc)

        self._fan_out(_cleanup)

    # -- snapshots / clone ------------------------------------------------ #
    def create_snapshot(self, worker_id: int = 0) -> str:
        """Every device snapshots its own identical baseline.

        ``worker_id`` is ignored on purpose: in distributed mode the baseline
        must exist on *every* device so any device can later ``/reset`` to it.
        Returns the snapshot id reported by the lowest-numbered device.
        """
        def _snap(wid: int) -> str:
            return SnapshotResponse.from_dict(
                self._client(wid).post(ROUTES["snapshot"], {}, timeout=None)
            ).snapshot_id

        results = self._fan_out(_snap)
        first = min(results)
        return results[first]

    def restore_snapshot(
        self, worker_id: int, snapshot_id: str = "", quiet: bool = False
    ) -> bool:
        try:
            self._client(worker_id).post(
                ROUTES["reset"],
                ResetRequest(snapshot_id=snapshot_id).to_dict(),
                timeout=None,
            )
            return True
        except AgentRPCError as exc:
            LOGGER.warning("Snapshot restore failed for worker %d: %s", worker_id, exc)
            return False

    def clone_instances(
        self, source_worker_id: int, target_worker_ids: List[int]
    ) -> bool:
        """Config-only clone: reset each target device to its local baseline.

        The elite's knob configuration has already been copied into each target
        worker in coordinator RAM by the evolution step; here we only ensure the
        target's *data* is the byte-identical benchmark baseline again.
        """
        def _reset(wid: int) -> bool:
            return self.restore_snapshot(wid, "")

        results = self._fan_out(_reset, list(target_worker_ids))
        return all(results.values())

    def rebuild_worker_instance(self, worker_id: int) -> bool:
        req = SetupRequest.from_dict(self._setup_template.to_dict())
        req.force_recreate_baseline = True
        resp = SetupResponse.from_dict(
            self._client(worker_id).post(ROUTES["setup"], req.to_dict(), timeout=None)
        )
        if resp.ok:
            device = self._devices[worker_id]
            self._instances[worker_id] = InstanceConfig(
                worker_id=worker_id,
                port=resp.port,
                data_dir=Path(resp.data_dir or "/"),
                running=True,
                host=device.host,
            )
        return resp.ok

    # -- config / metrics ------------------------------------------------- #
    def get_db_config(self, worker_id: int) -> DatabaseConfig:
        device = self._devices[worker_id]
        instance = self._instances.get(worker_id)
        port = instance.port if instance else 5440
        return DatabaseConfig(
            user=self.base_config.user,
            password=self.base_config.password,
            host=device.host,
            port=port,
            dbname=self.base_config.dbname,
        )

    def collect_memory_utilization(self, worker_id: int) -> float:
        # Memory utilisation is measured on-device and already embedded in the
        # returned PerformanceMetrics, so there is nothing to collect centrally.
        return 0.0

    def get_resource_allocations(self):
        from src.utils.types import WorkerResourceAllocation

        allocations = []
        for worker_id in self._worker_ids():
            hw = self._hardware.get(worker_id, {})
            allocations.append(
                WorkerResourceAllocation(
                    worker_id=worker_id,
                    cpu_cores=int(hw.get("cpu_cores") or 0) or 1,
                    cpuset_cpus=None,
                    ram_bytes=int(hw.get("ram_bytes") or 0),
                    docker_memory_limit_bytes=None,
                )
            )
        return allocations
