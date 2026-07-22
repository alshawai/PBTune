"""
End-to-end RPC integration test for distributed mode (Phases 0-2).

Spins up two *real* device-agent HTTP servers on localhost backed by an
in-memory :class:`FakeBackend`, then drives them through the full coordinator
stack: health handshake -> setup -> snapshot -> run_eval (central scoring) ->
config-only clone/reset -> cleanup. No Docker or PostgreSQL required — this
exercises the transport, dispatch, RemoteEnvironment, and RemoteWorkload
Orchestrator wiring exactly as a real fleet would.
"""

import threading
from typing import Any, Dict, Optional, Tuple

import pytest

from src.config.database import DatabaseConfig
from src.tuners.distributed.agent_api import (
    RunEvalRequest,
    SetupRequest,
    SetupResponse,
)
from src.tuners.distributed.config import DistributedConfig
from src.tuners.distributed.coordinator import Coordinator
from src.tuners.distributed.device_agent import DeviceAgent, EvaluationBackend
from src.tuners.distributed.inventory import parse_inventory
from src.tuners.pbt.worker import PBTWorker as Worker
from src.tuners.engine.orchestrator import WorkloadOrchestratorConfig
from src.utils.metrics import WorkloadType, create_metric_config


class FakeBackend(EvaluationBackend):
    """Deterministic in-memory backend — throughput scales with a knob value."""

    def __init__(self, worker_id: int):
        self.worker_id = worker_id
        self.setup_calls = 0
        self.snapshot_calls = 0
        self.reset_calls = 0
        self.eval_calls = 0
        self.last_knob_config: Dict[str, Any] = {}

    def setup(self, req: SetupRequest) -> SetupResponse:
        self.setup_calls += 1
        return SetupResponse(
            ok=True,
            port=5440 + self.worker_id,
            data_dir="/tmp/fake",
            backend="fake",
            resources={
                "ram_bytes": 256 * 1024**3,
                "cpu_cores": 64,
                "disk_type": "SSD",
            },
        )

    def create_snapshot(self) -> str:
        self.snapshot_calls += 1
        return f"base-{self.worker_id}"

    def reset(self, snapshot_id: str) -> None:
        self.reset_calls += 1

    def run_eval(
        self, req: RunEvalRequest
    ) -> Tuple[Dict[str, Any], bool, Dict[str, Any], Optional[Dict[str, Any]]]:
        self.eval_calls += 1
        self.last_knob_config = dict(req.knob_config)
        # Throughput scales with the (fake) knob so scoring can differentiate.
        knob_val = float(req.knob_config.get("shared_buffers", 1))
        metrics = {
            "throughput": 100.0 * knob_val,
            "latency_p50": 5.0,
            "latency_p95": 10.0,
            "latency_p99": 20.0,
            "cache_hit_ratio": 0.99,
            "error_rate": 0.0,
            "total_queries": 1000,
            "total_time": 60.0,
        }
        return metrics, False, {"shared_buffers": knob_val}, None

    def cleanup(self, remove_data: bool) -> None:
        pass

    def pg_running(self) -> bool:
        return True

    def backend_name(self) -> Optional[str]:
        return "fake"


@pytest.fixture
def fleet():
    """Start two agent servers on ephemeral ports; yield (coordinator, backends)."""
    backends = {0: FakeBackend(0), 1: FakeBackend(1)}
    agents = {
        wid: DeviceAgent(wid, backends[wid], host="127.0.0.1", port=0)
        for wid in (0, 1)
    }
    threads = []
    for agent in agents.values():
        t = threading.Thread(target=agent.httpd.serve_forever, daemon=True)
        t.start()
        threads.append(t)

    inventory = parse_inventory(
        {
            "devices": [
                {"host": "127.0.0.1", "agent_port": agents[0].port},
                {"host": "127.0.0.1", "agent_port": agents[1].port},
            ]
        }
    )
    dist_cfg = DistributedConfig(
        inventory=inventory, health_timeout_s=10.0, health_poll_interval_s=0.1
    )
    setup_template = SetupRequest(
        run_id="test", benchmark="sysbench", workload_type="oltp_read_write"
    )
    db_config = DatabaseConfig(
        user="postgres", password="", host="ignored", port=0, dbname="test"
    )
    coordinator = Coordinator(dist_cfg, setup_template, db_config, population_size=2)

    try:
        yield coordinator, backends
    finally:
        for agent in agents.values():
            agent.close()


def test_health_handshake(fleet):
    coordinator, _ = fleet
    coordinator.wait_for_agents()  # raises on failure
    snapshot = coordinator.health_snapshot()
    assert {h.worker_id for h in snapshot} == {0, 1}
    assert all(h.status in ("ok", "starting") for h in snapshot)


def test_setup_and_snapshot_fan_out(fleet):
    coordinator, backends = fleet
    coordinator.wait_for_agents()
    env = coordinator.make_environment(schema_provider=None, run_id="test")

    instances = env.setup_instances(2)
    assert [i.worker_id for i in instances] == [0, 1]
    assert instances[0].host == "127.0.0.1"
    assert instances[0].port == 5440
    assert all(b.setup_calls == 1 for b in backends.values())

    # create_snapshot must fan out to EVERY device (baseline on all).
    env.create_snapshot()
    assert all(b.snapshot_calls == 1 for b in backends.values())


def test_device_resources_reported_for_knob_ranges(fleet):
    coordinator, _ = fleet
    coordinator.wait_for_agents()
    env = coordinator.make_environment(schema_provider=None, run_id="test")
    env.setup_instances(2)

    # The coordinator resolves knob ranges against DEVICE hardware, not its own.
    res = env.representative_resources()
    assert res is not None
    assert res["ram_bytes"] == 256 * 1024**3
    assert res["cpu_cores"] == 64
    assert res["disk_type"] == "SSD"


def test_run_eval_scores_centrally(fleet):
    coordinator, backends = fleet
    coordinator.wait_for_agents()
    env = coordinator.make_environment(schema_provider=None, run_id="test")
    env.setup_instances(2)

    orch_config = WorkloadOrchestratorConfig(
        workload_type=WorkloadType.OLTP,
        metric_config=create_metric_config("oltp"),
        db_config=coordinator.db_config,
    )
    orchestrator = coordinator.make_orchestrator(orch_config, executor=None, env=env)

    # Two workers with different knob values => different throughput => the
    # higher-throughput worker must score at least as high.
    w_low = Worker(worker_id=0, knob_space=None, knob_config={"shared_buffers": 1})
    w_high = Worker(worker_id=1, knob_space=None, knob_config={"shared_buffers": 4})

    m_low, s_low, restart_low, actual_low, timing_low = orchestrator.evaluate_worker(
        w_low, generation=0
    )
    m_high, s_high, *_ = orchestrator.evaluate_worker(w_high, generation=0)

    assert backends[0].eval_calls == 1 and backends[1].eval_calls == 1
    assert m_low.throughput == 100.0 and m_high.throughput == 400.0
    assert restart_low is False
    assert actual_low == {"shared_buffers": 1.0}
    assert timing_low is not None  # fresh TimingRecorder, never None
    assert s_high >= s_low  # central scoring rewards higher throughput


def test_config_only_clone_resets_targets(fleet):
    coordinator, backends = fleet
    coordinator.wait_for_agents()
    env = coordinator.make_environment(schema_provider=None, run_id="test")
    env.setup_instances(2)

    # Clone elite (worker 0) onto worker 1 => only a local reset on the target.
    assert env.clone_instances(source_worker_id=0, target_worker_ids=[1]) is True
    assert backends[1].reset_calls == 1
    assert backends[0].reset_calls == 0  # source is never reset


def test_rpc_failure_marks_worker_dead(fleet):
    coordinator, backends = fleet
    coordinator.wait_for_agents()
    env = coordinator.make_environment(schema_provider=None, run_id="test")
    env.setup_instances(2)
    orch_config = WorkloadOrchestratorConfig(
        workload_type=WorkloadType.OLTP,
        metric_config=create_metric_config("oltp"),
        db_config=coordinator.db_config,
    )
    orchestrator = coordinator.make_orchestrator(orch_config, executor=None, env=env)

    # Point worker 0's client at a dead port to force a transport error.
    from src.tuners.distributed.transport import AgentClient

    orchestrator._clients[0] = AgentClient("http://127.0.0.1:1", default_timeout=0.5)
    w = Worker(worker_id=0, knob_space=None, knob_config={"shared_buffers": 2})
    metrics, score, *_ = orchestrator.evaluate_worker(w, generation=0)

    assert metrics.failure_type == "EXECUTION_CRASH"  # handed to rescue path
    assert isinstance(score, float)
