"""
Tests for the GET /logs endpoint and log-buffer mechanics.

Spins up a real DeviceAgent HTTP server backed by a FakeBackend, issues a
``POST /run_eval`` and then ``GET /logs``, and asserts that:

* The endpoint returns a valid LogsResponse.
* Lines emitted by the DeviceAgent logger hierarchy during the eval appear
  in the buffer.
* The buffer is cleared before each eval (so two successive evals do not
  accumulate lines from the first).
"""

import logging
import threading
from typing import Any, Dict, Optional, Tuple

import pytest

from src.tuners.distributed.agent_api import (
    LogsResponse,
    RunEvalRequest,
    SetupRequest,
    SetupResponse,
    ROUTES,
)
from src.tuners.distributed.device_agent import DeviceAgent, EvaluationBackend
from src.tuners.distributed.transport import AgentClient


class LoggingFakeBackend(EvaluationBackend):
    """A FakeBackend whose run_eval emits a recognisable log line."""

    def __init__(self, worker_id: int):
        self.worker_id = worker_id
        self.eval_calls = 0

    def setup(self, req: SetupRequest) -> SetupResponse:
        return SetupResponse(
            ok=True,
            port=5440 + self.worker_id,
            data_dir="/tmp/fake",
            backend="fake",
        )

    def create_snapshot(self) -> str:
        return f"snap-{self.worker_id}"

    def reset(self, snapshot_id: str) -> None:
        pass

    def run_eval(
        self, req: RunEvalRequest
    ) -> Tuple[Dict[str, Any], bool, Dict[str, Any], Optional[Dict[str, Any]]]:
        self.eval_calls += 1
        # Emit a recognisable line through the DeviceAgent logger so the
        # buffer handler captures it.
        logging.getLogger("DeviceAgent").info(
            "sentinel-eval-line worker=%d call=%d", self.worker_id, self.eval_calls
        )
        return (
            {
                "throughput": 100.0,
                "latency_p50": 5.0,
                "latency_p95": 10.0,
                "latency_p99": 20.0,
                "cache_hit_ratio": 0.99,
                "error_rate": 0.0,
                "total_queries": 1000,
                "total_time": 60.0,
            },
            False,
            {"shared_buffers": 1.0},
            None,
        )

    def cleanup(self, remove_data: bool) -> None:
        pass

    def pg_running(self) -> bool:
        return True

    def backend_name(self) -> Optional[str]:
        return "fake"


@pytest.fixture
def agent_and_client():
    """Start a single DeviceAgent on an ephemeral port and yield (agent, client, backend)."""
    backend = LoggingFakeBackend(worker_id=7)
    agent = DeviceAgent(worker_id=7, backend=backend, host="127.0.0.1", port=0)
    t = threading.Thread(target=agent.httpd.serve_forever, daemon=True)
    t.start()
    client = AgentClient(f"http://127.0.0.1:{agent.port}")
    try:
        yield agent, client, backend
    finally:
        agent.close()


def test_logs_endpoint_initially_empty(agent_and_client):
    """GET /logs before any eval returns an empty line list."""
    _, client, _ = agent_and_client
    resp = LogsResponse.from_dict(client.get(ROUTES["logs"]))
    assert resp.worker_id == 7
    assert isinstance(resp.lines, list)
    # No eval has run yet — buffer should be empty.
    assert resp.lines == []


def test_logs_endpoint_captures_eval_lines(agent_and_client):
    """After POST /run_eval, GET /logs contains the sentinel log line."""
    _, client, backend = agent_and_client
    req = RunEvalRequest(knob_config={"shared_buffers": 1}, generation=0)
    client.post(ROUTES["run_eval"], req.to_dict())

    resp = LogsResponse.from_dict(client.get(ROUTES["logs"]))
    assert resp.worker_id == 7
    assert any("sentinel-eval-line" in line for line in resp.lines), (
        f"Expected sentinel line in logs, got: {resp.lines}"
    )


def test_logs_buffer_cleared_between_evals(agent_and_client):
    """The buffer is cleared before each eval so lines do not accumulate."""
    _, client, _ = agent_and_client
    req = RunEvalRequest(knob_config={"shared_buffers": 1}, generation=0)

    # First eval — expect exactly one sentinel line.
    client.post(ROUTES["run_eval"], req.to_dict())
    first = LogsResponse.from_dict(client.get(ROUTES["logs"]))
    first_sentinel = [line for line in first.lines if "sentinel-eval-line" in line]
    assert len(first_sentinel) == 1

    # Second eval — buffer is cleared before it runs, so there should still
    # be exactly one sentinel line (from the second eval, call=2).
    client.post(ROUTES["run_eval"], req.to_dict())
    second = LogsResponse.from_dict(client.get(ROUTES["logs"]))
    second_sentinel = [line for line in second.lines if "sentinel-eval-line" in line]
    assert len(second_sentinel) == 1
    assert "call=2" in second_sentinel[0]
