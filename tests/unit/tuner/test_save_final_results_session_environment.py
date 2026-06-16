"""Integration-style test for SessionEnvironment + timing_schema_version in PBT JSON.

This test invokes ``PBTTuner.save_final_results`` against a mock-driven
tuner stand-in. It verifies that the produced JSON contains the new
``session_environment`` block and the ``timing_schema_version`` field
without spinning up any database or running an actual tuning loop.
"""

from __future__ import annotations

import json
from pathlib import Path
from types import MethodType, SimpleNamespace

import pytest

from src.tuner.main import PBTTuner
from src.utils.types import (
    SessionEnvironment,
    WorkerResourceAllocation,
)


class _ScoringPayload(dict):
    """Stand-in for ``_build_scoring_payload`` output."""


class _FakeKnobSpace:
    def __init__(self) -> None:
        self.knobs = {"shared_buffers": object(), "work_mem": object()}
        self.worker_resources = None

    def __len__(self) -> int:
        return len(self.knobs)

    def config_to_fractions(self, cfg):
        return cfg or {}


def _make_session_environment() -> SessionEnvironment:
    return SessionEnvironment(
        cpu_model="Test CPU",
        cpu_cores_physical=4,
        cpu_cores_logical=8,
        ram_bytes_total=16 * 1024**3,
        disk_type="SSD",
        data_disk_type=None,
        kernel_version="6.5.0-test",
        os_system="Linux",
        os_release="6.5.0",
        os_version="#1 SMP",
        os_machine="x86_64",
        pg_client_version="PostgreSQL 18.0",
        pg_server_version="18.0",
        docker_version="25.0.3",
        use_docker=True,
        num_parallel_workers=2,
        population_size=4,
        cpu_pinning_scheme="cpuset",
        per_worker_resources=[
            WorkerResourceAllocation(
                worker_id=i,
                cpu_cores=2,
                cpuset_cpus=f"{2*i},{2*i+1}",
                ram_bytes=4 * 1024**3,
                docker_memory_limit_bytes=4 * 1024**3,
            )
            for i in range(2)
        ],
    )


@pytest.fixture
def fake_tuner(tmp_path):
    """Build a minimal stand-in object exposing the attributes ``save_final_results`` uses."""
    output_dir = tmp_path / "results"
    output_dir.mkdir(parents=True, exist_ok=True)

    population = SimpleNamespace(
        best_overall_metrics=None,
        best_overall_score_breakdown=None,
        current_generation=0,
        history=[],
        generations_without_improvement=0,
    )

    pbt_config = SimpleNamespace(
        benchmark_config=SimpleNamespace(
            scale_factor=0.1,
            warmup_passes=0,
            sysbench_tables=2,
            sysbench_table_size=10000,
            sysbench_workload="oltp_read_write",
            evaluation_duration=10.0,
            warmup_duration=5.0,
            tuning_mode=SimpleNamespace(value="online"),
            adaptive_restart_interval=10,
        ),
        population_size=4,
        num_parallel_workers=2,
        exploit_quantile=0.25,
        perturbation_factors=(0.8, 1.2),
        ready_interval=1,
        dead_config_threshold=3,
    )

    full_knob_space = _FakeKnobSpace()

    worker_resources = SimpleNamespace(
        ram_bytes=8 * 1024**3,
        cpu_cores=4,
        disk_type="SSD",
        disk_read_bps=100_000_000,
        disk_write_bps=50_000_000,
        disk_read_iops=10_000,
        disk_write_iops=8_000,
        disk_class="sata_ssd",
    )

    env = SimpleNamespace(
        pg_server_version="18.0",
        docker_version="25.0.3",
        get_resource_allocations=lambda: [
            WorkerResourceAllocation(
                worker_id=0,
                cpu_cores=2,
                cpuset_cpus="0,1",
                ram_bytes=4 * 1024**3,
                docker_memory_limit_bytes=4 * 1024**3,
            )
        ],
    )

    tuner = SimpleNamespace(
        population=population,
        pbt_config=pbt_config,
        full_knob_space=full_knob_space,
        worker_resources=worker_resources,
        knob_tier="minimal",
        knob_source="auto",
        workload_type=SimpleNamespace(value="oltp"),
        benchmark_name="sysbench",
        random_seed=11,
        timestamp="20260609_1300",
        warm_start_provenance={"enabled": False},
        generation_history=[],
        system_info={
            "cpu_model": "Test CPU",
            "cpu_cores": {"physical": 4, "logical": 8},
            "ram": {"total_bytes": 16 * 1024**3, "total_gb": 16.0},
            "disk_type": "SSD",
            "pg_version": "PostgreSQL 18.0",
            "os": {
                "system": "Linux",
                "release": "6.5.0",
                "version": "#1 SMP",
                "machine": "x86_64",
            },
        },
        session_environment=_make_session_environment(),
        env=env,
        output_dir=output_dir,
        best_score=0.5,
        best_config={"shared_buffers": 0.5, "work_mem": 0.1},
    )

    # Stub out _build_scoring_payload — it's invoked unconditionally.
    def _stub_scoring_payload(self, metrics, score_breakdown):  # noqa: ARG001
        return _ScoringPayload(
            scoring_policy="default",
            scoring_policy_version="1.0",
            metric_reference_version="1.0",
            workload_features={},
            normalization_metadata={},
            score_breakdown=None,
        )

    tuner._build_scoring_payload = MethodType(_stub_scoring_payload, tuner)
    tuner._aggregate_session_timing = MethodType(
        PBTTuner._aggregate_session_timing, tuner
    )
    return tuner


def test_save_final_results_emits_session_environment_and_timing_schema(fake_tuner):
    results = PBTTuner.save_final_results.__get__(fake_tuner, type(fake_tuner))(
        total_time=12.5
    )

    assert results["tuning_session"]["timing_schema_version"] == "1.1"
    assert "session_environment" in results
    se = results["session_environment"]
    assert se["cpu_model"] == "Test CPU"
    assert se["pg_server_version"] == "18.0"
    assert se["docker_version"] == "25.0.3"
    assert se["use_docker"] is True
    assert se["num_parallel_workers"] == 2
    assert se["population_size"] == 4
    assert se["cpu_pinning_scheme"] == "cpuset"
    assert se["per_worker_resources"][0]["cpuset_cpus"] == "0,1"

    # Backwards-compat: existing fields still populated.
    assert results["worker_resources"]["ram_bytes"] == 8 * 1024**3
    assert results["system_info"]["cpu_model"] == "Test CPU"

    json_files = list(
        Path(fake_tuner.output_dir).glob("tuning_sessions/pbt_results_*.json")
    )
    assert len(json_files) == 1
    written = json.loads(json_files[0].read_text(encoding="utf-8"))
    assert written["tuning_session"]["timing_schema_version"] == "1.1"
    assert written["session_environment"]["pg_server_version"] == "18.0"


def test_save_final_results_filename_uses_session_timestamp(fake_tuner):
    fake_tuner.timestamp = "20260609_1300"
    PBTTuner.save_final_results.__get__(fake_tuner, type(fake_tuner))(total_time=1.0)

    json_files = list(
        Path(fake_tuner.output_dir).glob("tuning_sessions/pbt_results_*.json")
    )
    best_files = list(
        Path(fake_tuner.output_dir).glob("best_configs/best_config_*.json")
    )
    assert len(json_files) == 1
    assert len(best_files) == 1
    # Timestamp string must match across both writers.
    assert "20260609_1300" in json_files[0].name
    assert "20260609_1300" in best_files[0].name
