# Copyright (C) 2026 Ibrahim Al-Shawa and PBTune contributors
# Licensed under the GNU General Public License v3.0
# See LICENSE file for details

"""Regression tests for the distributed measurement-window fix (issue #142).

The coordinator used to omit the measurement/warmup durations from the setup
request, so each device rebuilt its orchestrator from dataclass defaults and
benchmarked a 90s window regardless of configuration. These tests pin the three
links that make that impossible now: the durations survive the wire, the device
refuses to guess when they are absent, and the coordinator rejects an agent that
does not adopt them.
"""

from __future__ import annotations

from unittest.mock import patch

import pytest

from src.tuners.distributed import AGENT_PROTOCOL_VERSION
from src.tuners.distributed.agent_api import (
    HealthResponse,
    ORCHESTRATOR_SETUP_FIELDS,
    REQUIRED_ORCHESTRATOR_SETUP_FIELDS,
    RunEvalRequest,
    SetupRequest,
    SetupResponse,
)
from src.tuners.distributed.config import DistributedConfig
from src.tuners.distributed.coordinator import Coordinator
from src.tuners.distributed.device_agent import LocalDeviceBackend
from src.tuners.distributed.inventory import parse_inventory
from src.tuners.distributed.remote_environment import verify_orchestrator_echo
from src.config.database import DatabaseConfig
from src.utils.hardware_info import WorkerResources
from src.utils.types import TuningMode


def _coordinator_stub() -> Coordinator:
    """A Coordinator wired to one nominal device; no agent is contacted."""
    inventory = parse_inventory(
        {"devices": [{"host": "127.0.0.1", "agent_port": 8770}]}
    )
    return Coordinator(
        DistributedConfig(inventory=inventory),
        _setup_request(),
        DatabaseConfig(
            user="postgres", password="", host="ignored", port=0, dbname="test"
        ),
        population_size=1,
    )


def _setup_request(**overrides) -> SetupRequest:
    base = dict(
        run_id="test",
        benchmark="sysbench",
        workload_type="oltp_read_write",
        measurement_duration=120.0,
        warmup_duration=60.0,
        cooldown_duration=3.0,
        warmup_passes=0,
        tuning_mode="offline",
        adaptive_restart_interval=10,
        random_seed=7,
        vacuum_analyze_timeout_seconds=45.0,
    )
    base.update(overrides)
    return SetupRequest(**base)  # type: ignore[arg-type]


# --------------------------------------------------------------------------- #
# Wire round-trip
# --------------------------------------------------------------------------- #
def test_setup_request_durations_survive_round_trip() -> None:
    """The measurement window must survive to_dict -> from_dict unchanged."""
    req = _setup_request()
    restored = SetupRequest.from_dict(req.to_dict())

    assert restored.measurement_duration == 120.0
    assert restored.warmup_duration == 60.0
    assert restored.tuning_mode == "offline"
    assert restored.random_seed == 7
    assert restored.warmup_passes == 0


def test_orchestrator_echo_covers_every_shaping_field() -> None:
    """The echo the device returns must span exactly the shaping fields."""
    echo = _setup_request().orchestrator_echo()
    assert set(echo) == set(ORCHESTRATOR_SETUP_FIELDS)
    assert echo["measurement_duration"] == 120.0


def test_setup_response_carries_effective_orchestrator() -> None:
    """SetupResponse must round-trip the device's effective config."""
    resp = SetupResponse(
        ok=True,
        port=5440,
        data_dir="/tmp",
        backend="fake",
        effective_orchestrator={"measurement_duration": 120.0},
    )
    restored = SetupResponse.from_dict(resp.to_dict())
    assert restored.effective_orchestrator == {"measurement_duration": 120.0}


# --------------------------------------------------------------------------- #
# Coordinator-side guard
# --------------------------------------------------------------------------- #
def test_verify_echo_accepts_matching_device() -> None:
    """A device that adopted the coordinator's window passes silently."""
    req = _setup_request()
    resp = SetupResponse(
        ok=True,
        port=5440,
        data_dir="/tmp",
        backend="fake",
        effective_orchestrator=req.orchestrator_echo(),
    )
    verify_orchestrator_echo(0, req, resp)  # must not raise


def test_verify_echo_rejects_silent_agent() -> None:
    """An agent that reports no effective config is treated as stale."""
    req = _setup_request()
    resp = SetupResponse(ok=True, port=5440, data_dir="/tmp", backend="fake")

    with pytest.raises(RuntimeError, match="did not report"):
        verify_orchestrator_echo(0, req, resp)


def test_verify_echo_rejects_divergent_window() -> None:
    """A device that measured a different window fails loudly at setup."""
    req = _setup_request()
    echo = req.orchestrator_echo()
    echo["measurement_duration"] = 60.0  # the old default — the bug's fingerprint

    resp = SetupResponse(
        ok=True,
        port=5440,
        data_dir="/tmp",
        backend="fake",
        effective_orchestrator=echo,
    )
    with pytest.raises(RuntimeError, match="measurement_duration: sent 120.0"):
        verify_orchestrator_echo(0, req, resp)


def test_verify_echo_ignores_fields_the_coordinator_deferred() -> None:
    """A field the coordinator left unset is not compared."""
    req = _setup_request(cooldown_duration=None)
    echo = req.orchestrator_echo()
    echo["cooldown_duration"] = 5.0  # device's own default; coordinator did not care

    resp = SetupResponse(
        ok=True,
        port=5440,
        data_dir="/tmp",
        backend="fake",
        effective_orchestrator=echo,
    )
    verify_orchestrator_echo(0, req, resp)  # must not raise


# --------------------------------------------------------------------------- #
# Device-side construction
# --------------------------------------------------------------------------- #
def _backend_with_resources() -> LocalDeviceBackend:
    backend = LocalDeviceBackend(
        global_worker_id=3,
        knob_tier="minimal",
        base_dir="/fleet/worker-3",
    )
    backend._resources = WorkerResources(
        ram_bytes=32 * 1024**3,
        cpu_cores=8,
        disk_type="SSD",
    )
    return backend


def test_device_builds_orchestrator_from_sent_window() -> None:
    """The device honours the coordinator's durations and echoes them back."""
    backend = _backend_with_resources()
    req = _setup_request()

    with patch("src.utils.metrics.create_metric_config") as make_mc:
        metric_config = make_mc.return_value
        config = backend._build_orchestrator_config(
            req,
            workload_type=None,
            metric_config=metric_config,
            db_config=None,
        )

    assert config.measurement_duration == 120.0
    assert config.warmup_duration == 60.0
    assert config.tuning_mode == TuningMode.OFFLINE
    assert config.random_seed == 7
    assert config.worker_memory_budget_bytes == 32 * 1024**3
    # The echo the coordinator will verify against.
    assert backend._effective_orchestrator["measurement_duration"] == 120.0
    assert backend._effective_orchestrator["tuning_mode"] == "offline"


@pytest.mark.parametrize("missing", REQUIRED_ORCHESTRATOR_SETUP_FIELDS)
def test_device_rejects_missing_required_window(missing: str) -> None:
    """The device refuses to fall back to its own defaults for the window."""
    backend = _backend_with_resources()
    req = _setup_request(**{missing: None})

    with pytest.raises(ValueError, match=missing):
        backend._build_orchestrator_config(
            req,
            workload_type=None,
            metric_config=object(),
            db_config=None,
        )


# --------------------------------------------------------------------------- #
# Per-eval force_restart
# --------------------------------------------------------------------------- #
def test_force_restart_survives_run_eval_round_trip() -> None:
    """The rescue-restart decision is per-eval, so it must cross the wire.

    ``RunEvalRequest.from_dict`` enumerates its fields by hand, so a new field
    is silently dropped unless it is listed there too.
    """
    req = RunEvalRequest(knob_config={}, generation=3, force_restart=True)
    restored = RunEvalRequest.from_dict(req.to_dict())

    assert restored.force_restart is True


def test_force_restart_defaults_to_false_for_older_payloads() -> None:
    """A payload without the field must not imply a restart."""
    restored = RunEvalRequest.from_dict({"knob_config": {}, "generation": 1})

    assert restored.force_restart is False


# --------------------------------------------------------------------------- #
# Protocol version — the backstop against a fleet running older code
# --------------------------------------------------------------------------- #
def test_protocol_major_version_was_bumped_for_the_new_fields() -> None:
    """Adding required setup fields is a breaking wire change.

    A 1.x agent accepts the request, ignores the new fields, and measures its
    own default window — the exact defect being fixed. The major bump makes the
    health handshake reject such an agent before a run starts.
    """
    assert AGENT_PROTOCOL_VERSION.split(".")[0] == "2"


def test_coordinator_rejects_an_older_agent() -> None:
    """A major-version mismatch fails fast at the health handshake."""
    coordinator = _coordinator_stub()
    health = HealthResponse(
        status="ok",
        protocol_version="1.0",
        agent_version="1.0.0",
        worker_id=0,
    )

    with pytest.raises(RuntimeError, match="Protocol mismatch"):
        coordinator._check_protocol(0, health)


def test_coordinator_accepts_a_matching_agent() -> None:
    """A same-major agent passes the handshake."""
    coordinator = _coordinator_stub()
    health = HealthResponse(
        status="ok",
        protocol_version=AGENT_PROTOCOL_VERSION,
        agent_version="1.0.0",
        worker_id=0,
    )

    coordinator._check_protocol(0, health)  # must not raise
