# Copyright (C) 2026 Ibrahim Al-Shawa and PBTune contributors
# Licensed under the GNU General Public License v3.0
# See LICENSE file for details

"""
Device Agent
============

A long-running HTTP/JSON server that runs **on each device** and owns exactly
one local PostgreSQL instance. It exposes the small RPC surface defined in
:mod:`src.tuners.distributed.agent_api` and executes today's proven
``WorkloadOrchestrator`` pipeline *locally* — so the benchmark client always
runs next to the database and no network latency ever enters the measurement
window (the crux of the fairness guarantee).

Scoring deliberately does **not** happen here: the agent returns raw
``PerformanceMetrics`` and the coordinator scores centrally, so adaptive
normalisation spans the whole population exactly as in single-device mode.

The evaluation logic sits behind the :class:`EvaluationBackend` seam so the
HTTP/dispatch layer can be unit-tested with a fake backend, while
:class:`LocalDeviceBackend` performs the real DB wiring (mirroring
``DatabaseTuner``'s single-machine setup for one worker).

Run standalone::

    python -m src.tuners.distributed.device_agent --worker-id 0 --port 8770
"""

from __future__ import annotations

import argparse
import logging
import threading
from abc import ABC, abstractmethod
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from typing import Any, Dict, Optional, Tuple

from src.tuners.distributed import AGENT_PROTOCOL_VERSION
from src.tuners.distributed.agent_api import (
    AckResponse,
    CleanupRequest,
    ErrorResponse,
    HealthResponse,
    ResetRequest,
    ROUTES,
    RunEvalRequest,
    RunEvalResponse,
    SetupRequest,
    SetupResponse,
    SnapshotResponse,
)
from src.tuners.distributed.transport import read_json_body, write_json

LOGGER = logging.getLogger("DeviceAgent")

AGENT_VERSION = "1.0.0"


# --------------------------------------------------------------------------- #
# Backend seam
# --------------------------------------------------------------------------- #
class EvaluationBackend(ABC):
    """Everything the agent needs to evaluate a worker on this device.

    Implemented for real by :class:`LocalDeviceBackend`; a lightweight fake is
    used in tests to exercise the HTTP/dispatch layer without Docker/PG.
    """

    @abstractmethod
    def setup(self, req: SetupRequest) -> SetupResponse:
        """Create/prepare the single local instance and return its coordinates."""

    @abstractmethod
    def create_snapshot(self) -> str:
        """Create the local base snapshot; return its identifier."""

    @abstractmethod
    def reset(self, snapshot_id: str) -> None:
        """Restore the instance's data to the given (or default) base snapshot."""

    @abstractmethod
    def run_eval(
        self, req: RunEvalRequest
    ) -> Tuple[Dict[str, Any], bool, Dict[str, Any], Optional[Dict[str, Any]]]:
        """Run one evaluation locally.

        Returns ``(metrics_dict, restart_occurred, actual_config, timing_dict)``.
        """

    @abstractmethod
    def cleanup(self, remove_data: bool) -> None:
        """Tear down the local instance."""

    # -- introspection (defaults are safe no-ops) ------------------------- #
    def pg_running(self) -> bool:
        return False

    def pg_server_version(self) -> Optional[str]:
        return None

    def backend_name(self) -> Optional[str]:
        return None

    def hardware(self) -> Dict[str, Any]:
        return {}


class AgentState:
    """Holds the backend, its assigned worker id, and a lifecycle lock."""

    def __init__(self, worker_id: int, backend: EvaluationBackend):
        self.worker_id = worker_id
        self.backend = backend
        self.lock = threading.Lock()  # serialise mutating ops on the instance
        self.is_set_up = False
        self.shutdown_event = threading.Event()

    def health(self) -> HealthResponse:
        try:
            return HealthResponse(
                status="ok" if self.is_set_up else "starting",
                protocol_version=AGENT_PROTOCOL_VERSION,
                agent_version=AGENT_VERSION,
                worker_id=self.worker_id,
                pg_running=self.backend.pg_running(),
                pg_server_version=self.backend.pg_server_version(),
                backend=self.backend.backend_name(),
                hardware=self.backend.hardware(),
            )
        except Exception as exc:  # noqa: BLE001 — health must never raise
            return HealthResponse(
                status="error",
                protocol_version=AGENT_PROTOCOL_VERSION,
                agent_version=AGENT_VERSION,
                worker_id=self.worker_id,
                detail=str(exc),
            )


# --------------------------------------------------------------------------- #
# HTTP request handler
# --------------------------------------------------------------------------- #
class _AgentHandler(BaseHTTPRequestHandler):
    # Injected by DeviceAgent via functools.partial-style subclass attribute.
    state: AgentState = None  # type: ignore[assignment]

    server_version = f"PBTDeviceAgent/{AGENT_VERSION}"

    def log_message(self, fmt: str, *args: Any) -> None:  # quieter default logging
        LOGGER.debug("%s - " + fmt, self.address_string(), *args)

    # -- helpers ---------------------------------------------------------- #
    def _fail(self, status: int, message: str, detail: Optional[str] = None) -> None:
        write_json(
            self,
            status,
            ErrorResponse(
                error=message, detail=detail, worker_id=self.state.worker_id
            ).to_dict(),
        )

    # -- routing ---------------------------------------------------------- #
    def do_GET(self) -> None:  # noqa: N802 (stdlib naming)
        if self.path == ROUTES["health"]:
            write_json(self, 200, self.state.health().to_dict())
        else:
            self._fail(404, f"unknown route: GET {self.path}")

    def do_POST(self) -> None:  # noqa: N802
        try:
            body = read_json_body(self)
        except ValueError as exc:
            self._fail(400, "malformed JSON body", str(exc))
            return

        try:
            self._dispatch_post(self.path, body)
        except NotImplementedError as exc:
            self._fail(501, "not implemented", str(exc))
        except Exception as exc:  # noqa: BLE001 — surface as structured 500
            LOGGER.exception("Handler error on %s", self.path)
            self._fail(500, "agent handler error", str(exc))

    def _dispatch_post(self, path: str, body: Dict[str, Any]) -> None:
        state = self.state
        if path == ROUTES["setup"]:
            with state.lock:
                resp = state.backend.setup(SetupRequest.from_dict(body))
                state.is_set_up = resp.ok
            write_json(self, 200 if resp.ok else 500, resp.to_dict())

        elif path == ROUTES["snapshot"]:
            with state.lock:
                snap_id = state.backend.create_snapshot()
            write_json(self, 200, SnapshotResponse(ok=True, snapshot_id=snap_id).to_dict())

        elif path == ROUTES["reset"]:
            reset_req = ResetRequest.from_dict(body)
            with state.lock:
                state.backend.reset(reset_req.snapshot_id)
            write_json(self, 200, AckResponse(ok=True).to_dict())

        elif path == ROUTES["run_eval"]:
            eval_req = RunEvalRequest.from_dict(body)
            # run_eval holds the lock: one evaluation at a time per device.
            with state.lock:
                metrics, restart_occurred, actual_cfg, timing = state.backend.run_eval(
                    eval_req
                )
            write_json(
                self,
                200,
                RunEvalResponse(
                    ok=True,
                    metrics=metrics,
                    restart_occurred=restart_occurred,
                    actual_config=actual_cfg,
                    timing=timing,
                ).to_dict(),
            )

        elif path == ROUTES["cleanup"]:
            cleanup_req = CleanupRequest.from_dict(body)
            with state.lock:
                state.backend.cleanup(cleanup_req.remove_data)
                state.is_set_up = False
            write_json(self, 200, AckResponse(ok=True).to_dict())

        elif path == ROUTES["shutdown"]:
            write_json(self, 200, AckResponse(ok=True, detail="shutting down").to_dict())
            state.shutdown_event.set()

        else:
            self._fail(404, f"unknown route: POST {path}")


# --------------------------------------------------------------------------- #
# Server wrapper
# --------------------------------------------------------------------------- #
class DeviceAgent:
    """Owns the HTTP server and its :class:`AgentState`."""

    def __init__(self, worker_id: int, backend: EvaluationBackend, host: str, port: int):
        self.state = AgentState(worker_id, backend)
        handler_cls = type("_BoundAgentHandler", (_AgentHandler,), {"state": self.state})
        self.httpd = ThreadingHTTPServer((host, port), handler_cls)
        self.host, self.port = self.httpd.server_address[0], self.httpd.server_address[1]

    def serve_forever(self) -> None:
        """Serve until a /shutdown request (or Ctrl-C) arrives."""
        server_thread = threading.Thread(
            target=self.httpd.serve_forever, name="agent-http", daemon=True
        )
        server_thread.start()
        LOGGER.info(
            "Device agent (worker %d) listening on %s:%d",
            self.state.worker_id,
            self.host,
            self.port,
        )
        try:
            self.state.shutdown_event.wait()
        except KeyboardInterrupt:
            LOGGER.info("Interrupted; shutting down device agent")
        finally:
            self.close()

    def close(self) -> None:
        self.httpd.shutdown()
        self.httpd.server_close()


# --------------------------------------------------------------------------- #
# Real backend — mirrors DatabaseTuner's single-machine setup for one worker
# --------------------------------------------------------------------------- #
class LocalDeviceBackend(EvaluationBackend):
    """Real evaluation backend backed by a local Docker/bare-metal PG instance.

    Deliberately reuses the *same* building blocks as ``DatabaseTuner``
    (``get_knob_space``, ``detect_worker_resources``, ``create_metric_config``,
    the benchmark executors, ``EnvironmentFactory``, ``WorkloadOrchestrator``)
    so on-device evaluation is byte-for-byte consistent with local mode.

    Heavy imports are deferred to :meth:`setup` so the module stays importable
    (for tests and the coordinator) without the full DB/Docker stack present.
    """

    #: Local instance index on this device is always 0 (one worker per device);
    #: the *global* worker id it represents is carried separately for logging.
    LOCAL_WORKER_ID = 0

    def __init__(
        self,
        global_worker_id: int,
        knob_tier: str,
        base_dir: str,
        knob_source: str = "expert",
    ):
        self.global_worker_id = global_worker_id
        self.knob_tier = knob_tier
        self.knob_source = knob_source
        self.base_dir = base_dir

        # Built lazily in setup(); typed Any since the concrete env/orchestrator/
        # worker/resources classes are imported lazily inside setup().
        self._env: Any = None
        self._orchestrator: Any = None
        self._worker: Any = None
        self._knob_space: Any = None
        self._resources: Any = None
        self._port: int = 0
        self._data_dir: str = ""
        self._backend_name: str = ""
        self._snapshot_id: str = ""

    # -- lifecycle -------------------------------------------------------- #
    def setup(self, req: SetupRequest) -> SetupResponse:
        from pathlib import Path

        from src.benchmarks.sysbench.executor import SysbenchExecutor
        from src.benchmarks.tpch.executor import TPCHExecutor
        from src.config.database import DatabaseConfig
        from src.tuners.engine.orchestrator import (
            WorkloadOrchestrator,
            WorkloadOrchestratorConfig,
        )
        from src.knobs import get_knob_space
        from src.tuners.pbt.worker import PBTWorker as Worker
        from src.utils.environments import EnvironmentFactory
        from src.utils.hardware_info import detect_worker_resources
        from src.utils.metrics import WorkloadType, create_metric_config
        from src.utils.scoring.workload_features import WorkloadFeatureExtractor

        base_dir = Path(self.base_dir)

        # One worker per device => detect resources for a single worker.
        self._resources = detect_worker_resources(
            max_parallel_workers=1, data_path=base_dir
        )

        knob_space = get_knob_space(
            self.knob_tier,
            knob_source=self.knob_source,
            workload_type=req.workload_type,
        )
        knob_space.resolve_hardware_ranges(self._resources)
        knob_space.worker_resources = self._resources
        self._knob_space = knob_space

        workload_type = (
            WorkloadType.OLAP if req.benchmark == "tpch" else WorkloadType.OLTP
        )
        metric_config = create_metric_config(workload_type.value)

        features_extractor = WorkloadFeatureExtractor()
        if req.benchmark == "sysbench":
            tables = req.tables or 10
            table_size = req.table_size or 100000
            script = req.workload_type
            executor: Any = SysbenchExecutor(
                tables=tables, table_size=table_size, script=script
            )
            metric_config.workload_features = features_extractor.extract_sysbench_features(
                script=script,
                threads=int(getattr(executor, "threads", 8)),
                cpu_cores=int(self._resources.cpu_cores or 1),
                table_size=table_size,
                tables=tables,
            )
            run_id = f"sysbench_{script}_t{tables}_s{table_size}"
        elif req.benchmark == "tpch":
            scale_factor = req.scale_factor or 1.0
            executor = TPCHExecutor(scale_factor=scale_factor)
            run_id = f"tpch_sf{scale_factor}"
        else:
            raise NotImplementedError(
                f"LocalDeviceBackend does not yet wire benchmark={req.benchmark!r}; "
                "sysbench and tpch are supported."
            )

        import os

        db_config = DatabaseConfig(
            user=req.db_user,
            password=os.getenv("DB_PASSWORD", ""),
            host="127.0.0.1",
            port=5440,
            dbname=req.dbname,
        )

        env = EnvironmentFactory.create(
            schema_provider=executor,
            use_docker=req.use_docker,
            base_dir=base_dir,
            base_port=5440,
            db_config=db_config,
            worker_resources=self._resources,
            run_id=run_id,
            image_name=req.image_name,
            force_recreate_baseline=req.force_recreate_baseline,
        )
        env.setup_instances(1)
        env.initialize_schema(self.LOCAL_WORKER_ID)

        orch_config = WorkloadOrchestratorConfig(
            workload_type=workload_type,
            metric_config=metric_config,
            db_config=db_config,
            worker_memory_budget_bytes=self._resources.ram_bytes,
        )
        orchestrator = WorkloadOrchestrator(orch_config, executor, env)

        worker = Worker(
            worker_id=self.global_worker_id,
            knob_space=knob_space,
            knob_config={},
            db_config=env.get_db_config(self.LOCAL_WORKER_ID),
            port=env.get_db_config(self.LOCAL_WORKER_ID).port,
        )

        self._env = env
        self._orchestrator = orchestrator
        self._worker = worker
        cfg = env.get_db_config(self.LOCAL_WORKER_ID)
        self._port = cfg.port
        self._data_dir = str(getattr(env, "base_dir", base_dir))
        self._backend_name = env.__class__.__name__

        return SetupResponse(
            ok=True,
            port=self._port,
            data_dir=self._data_dir,
            backend=self._backend_name,
            resources=self._serialize_resources(),
        )

    def _serialize_resources(self) -> Dict[str, Any]:
        """Serialise the device's detected WorkerResources for the coordinator."""
        import dataclasses

        if self._resources is None:
            return {}
        try:
            return dataclasses.asdict(self._resources)
        except TypeError:
            # Not a dataclass for some reason — fall back to a minimal view.
            return {
                "ram_bytes": getattr(self._resources, "ram_bytes", 0),
                "cpu_cores": getattr(self._resources, "cpu_cores", 0),
                "disk_type": getattr(self._resources, "disk_type", "unknown"),
            }

    def create_snapshot(self) -> str:
        self._require_setup()
        self._snapshot_id = self._env.create_snapshot(self.LOCAL_WORKER_ID)
        return self._snapshot_id

    def reset(self, snapshot_id: str) -> None:
        self._require_setup()
        self._env.restore_snapshot(self.LOCAL_WORKER_ID, snapshot_id or "")

    def run_eval(
        self, req: RunEvalRequest
    ) -> Tuple[Dict[str, Any], bool, Dict[str, Any], Optional[Dict[str, Any]]]:
        self._require_setup()
        worker = self._worker
        worker.knob_config = dict(req.knob_config)

        # Optional coordinator-issued synchronised start: hold until the shared
        # epoch so every device begins its evaluation at the same wall-clock.
        # (An extra fairness guard against shared infrastructure; on a truly
        # shared-nothing identical fleet fairness already holds without it.)
        if req.measurement_start_epoch is not None:
            import time as _time

            delay = req.measurement_start_epoch - _time.time()
            if delay > 0:
                _time.sleep(min(delay, 300.0))

        metrics, _score, restart_occurred, actual_cfg, recorder = (
            self._orchestrator.evaluate_worker(
                worker,
                apply_config=req.apply_config,
                generation=req.generation,
                barriers=None,
                restore_due=req.restore_due,
                next_eval_will_restore=req.next_eval_will_restore,
            )
        )
        timing = None
        to_dict = getattr(recorder, "to_dict", None)
        if callable(to_dict):
            try:
                timing = to_dict()
            except Exception:  # noqa: BLE001 — timing is best-effort telemetry
                timing = None
        return metrics.to_dict(), bool(restart_occurred), dict(actual_cfg or {}), timing

    def cleanup(self, remove_data: bool) -> None:
        if self._env is not None:
            self._env.cleanup(remove_data=remove_data)

    # -- introspection ---------------------------------------------------- #
    def pg_running(self) -> bool:
        return self._env is not None

    def pg_server_version(self) -> Optional[str]:
        return getattr(self._env, "pg_server_version", None) if self._env else None

    def backend_name(self) -> Optional[str]:
        return self._backend_name or None

    def hardware(self) -> Dict[str, Any]:
        if self._resources is None:
            return {}
        return {
            "cpu_cores": getattr(self._resources, "cpu_cores", None),
            "ram_bytes": getattr(self._resources, "ram_bytes", None),
            "disk_type": getattr(self._resources, "disk_type", None),
        }

    def _require_setup(self) -> None:
        if self._env is None or self._orchestrator is None or self._worker is None:
            raise RuntimeError("backend not set up; call /setup first")


# --------------------------------------------------------------------------- #
# CLI entrypoint
# --------------------------------------------------------------------------- #
def main(argv: Optional[list[str]] = None) -> int:
    parser = argparse.ArgumentParser(description="PBT distributed device agent")
    parser.add_argument("--worker-id", type=int, required=True)
    parser.add_argument("--host", default="0.0.0.0")
    parser.add_argument("--port", type=int, default=8770)
    parser.add_argument("--knob-tier", default="core")
    parser.add_argument("--knob-source", default="expert")
    parser.add_argument("--base-dir", default="./.instances")
    parser.add_argument(
        "--log-level", default="INFO", choices=["DEBUG", "INFO", "WARNING", "ERROR"]
    )
    args = parser.parse_args(argv)

    logging.basicConfig(
        level=getattr(logging, args.log_level),
        format="%(asctime)s %(levelname)s %(name)s: %(message)s",
    )

    backend = LocalDeviceBackend(
        global_worker_id=args.worker_id,
        knob_tier=args.knob_tier,
        base_dir=args.base_dir,
        knob_source=args.knob_source,
    )
    agent = DeviceAgent(args.worker_id, backend, args.host, args.port)
    agent.serve_forever()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
