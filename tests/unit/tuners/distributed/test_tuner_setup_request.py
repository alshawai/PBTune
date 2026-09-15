# Copyright (C) 2026 Ibrahim Al-Shawa and PBTune contributors
# Licensed under the GNU General Public License v3.0
# See LICENSE file for details

"""Tests that the coordinator ships its measurement window to the fleet.

Each device rebuilds its own ``WorkloadOrchestratorConfig`` from the setup
request, so anything the coordinator does not put on that request reverts to a
dataclass default on the device. That is how a run configured to measure 180
seconds ended up benchmarking 90 (issue #142).

These tests pin the coordinator's half of the contract: the setup request must
carry the same values as the orchestrator config the coordinator built for
itself. The request is populated *from* that config rather than from a second
hand-kept list of literals, so these assertions are what keep the two from
drifting apart again.
"""

from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import MagicMock, patch

import pytest

from src.config.database import DatabaseConfig
from src.tuners.distributed.agent_api import ORCHESTRATOR_SETUP_FIELDS
from src.tuners.pbt.tuner import PBTTuner
from src.utils.hardware_info import WorkerResources
from src.utils.metrics import WorkloadType, create_metric_config
from src.utils.types import TuningMode

DB_CONFIG = DatabaseConfig(
    user="postgres", password="", host="127.0.0.1", port=5432, dbname="test_dataset"
)


@pytest.fixture
def captured_setup() -> SimpleNamespace:
    """Run the distributed environment build and capture what it sent.

    No agents are contacted: ``Coordinator`` is replaced with a recorder and the
    remaining collaborators are stubs.
    """
    tuner = PBTTuner.__new__(PBTTuner)
    tuner.lifecycle = SimpleNamespace(
        distributed=True,
        inventory="fleet.yaml",
        agent_timeout=60,
        eval_timeout=1800,
        use_docker=True,
        force_recreate_baseline=False,
        docker_image="pbt-eval",
        bootstrap=False,
        tuning_mode=TuningMode.OFFLINE,
        adaptive_restart_interval=10,
        random_seed=42,
        knob_tier="minimal",
        knob_source="expert",
        remote_install_deps=False,
    )
    tuner.benchmark_config = SimpleNamespace(
        warmup_duration=60.0,
        evaluation_duration=120.0,
        warmup_passes=0,
        sysbench_workload="oltp_read_write",
        sysbench_tables=10,
        sysbench_table_size=100000,
        scale_factor=None,
    )
    tuner.pbt_config = SimpleNamespace(population_size=2)
    tuner.worker_resources = WorkerResources(
        ram_bytes=32 * 1024**3, cpu_cores=8, disk_type="SSD"
    )
    tuner.metric_config = create_metric_config("oltp")
    tuner._workload_type = WorkloadType.OLTP
    tuner.benchmark = "sysbench"
    tuner.snapshot_identifier = "sysbench_oltp_read_write_t10_s100000"
    tuner._workload_executor = MagicMock()

    coordinator = MagicMock()
    recorder = MagicMock(return_value=coordinator)

    with (
        patch("src.config.database.get_db_config", return_value=DB_CONFIG),
        patch("src.tuners.distributed.coordinator.Coordinator", recorder),
        patch(
            "src.tuners.distributed.config.DistributedConfig.from_inventory_path",
            return_value=MagicMock(),
        ),
    ):
        tuner._create_environment()

    return SimpleNamespace(
        setup_template=recorder.call_args.args[1],
        orchestrator_config=coordinator.make_orchestrator.call_args.args[0],
    )


def test_setup_request_carries_the_configured_window(captured_setup) -> None:
    """The durations the device needs must be on the request, not defaulted."""
    req = captured_setup.setup_template

    assert req.measurement_duration == 120.0
    assert req.warmup_duration == 60.0


def test_setup_request_matches_the_orchestrator_config(captured_setup) -> None:
    """Every shaping field must agree with what the coordinator built itself.

    This is the invariant that makes a silent divergence impossible: the request
    is derived from the orchestrator config, so a new field added to one shows
    up in the other.
    """
    req = captured_setup.setup_template
    config = captured_setup.orchestrator_config

    assert req.measurement_duration == config.measurement_duration
    assert req.warmup_duration == config.warmup_duration
    assert req.cooldown_duration == config.cooldown_duration
    assert req.warmup_passes == config.warmup_passes
    assert req.tuning_mode == config.tuning_mode.value
    assert req.adaptive_restart_interval == config.adaptive_restart_interval
    assert req.random_seed == config.random_seed
    assert (
        req.vacuum_analyze_timeout_seconds == config.vacuum_analyze_timeout_seconds
    )


def test_no_shaping_field_is_left_unsent(captured_setup) -> None:
    """A field left as None would silently take the device's own default."""
    echo = captured_setup.setup_template.orchestrator_echo()

    unsent = sorted(name for name, value in echo.items() if value is None)
    assert unsent == [], f"fields not sent to the device: {unsent}"
    assert set(echo) == set(ORCHESTRATOR_SETUP_FIELDS)


def test_restart_policy_reaches_the_device(captured_setup) -> None:
    """tuning_mode must be transmitted, not left to the device's default.

    The coordinator defaults to OFFLINE (keeping restart-required knobs in the
    search space) while the orchestrator dataclass defaults to ONLINE (which
    never restarts). A device left on its own default would write postmaster
    knobs and then measure without them.
    """
    assert captured_setup.setup_template.tuning_mode == "offline"
    assert captured_setup.orchestrator_config.tuning_mode is TuningMode.OFFLINE


def test_seed_reaches_the_device(captured_setup) -> None:
    """A real seed is configured by default, so dropping it loses reproducibility."""
    assert captured_setup.setup_template.random_seed == 42
