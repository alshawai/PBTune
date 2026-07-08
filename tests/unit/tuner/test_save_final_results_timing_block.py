"""Integration-style test for the timing block in PBT session JSON.

This test invokes ``PBTTuner.save_final_results`` against a mock-driven
tuner stand-in. It verifies that the produced JSON contains:

* ``tuning_session.timing_schema_version == "1.1"``
* ``tuning_session.tuning_time_seconds`` and ``tuning_session.bootstrap_seconds``
* Top-level ``bootstrap_breakdown`` with the four bootstrap components.
* Top-level ``timing_summary`` aggregating across per-generation and
  per-worker records.

It deliberately avoids spinning up any database, environment factory, or
running the actual tuning loop — the fixture mirrors the one in
``test_save_final_results_session_environment.py``.
"""

from __future__ import annotations

import json
from pathlib import Path
from types import MethodType, SimpleNamespace

import pytest

from src.tuner.main import PBTTuner
from src.utils.timing import TimingRecorder
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
        population_size=2,
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


def _make_bootstrap_timing() -> TimingRecorder:
    recorder = TimingRecorder()
    recorder.add("setup_instances", 12.5)
    recorder.add("verify_instances", 1.25)
    recorder.add("prune_knobs", 0.4)
    recorder.add("setup_snapshots", 6.0)
    return recorder


def _make_generation_history() -> list:
    """Two generations, two workers each, with realistic timing records."""

    def worker_timing(strategy: str, workload_seconds: float) -> dict:
        rec = TimingRecorder()
        rec.add("apply_only", 0.1)
        rec.add(f"activate_{strategy}", 0.08, strategy=strategy)
        rec.add("knob_verify", 0.2)
        rec.add("workload", workload_seconds, executor="benchmark")
        rec.add("score", 0.01)
        return rec.to_dict()

    def generation_timing(evolve_seconds: float) -> dict:
        rec = TimingRecorder()
        rec.add("evolve", evolve_seconds)
        return rec.to_dict()

    return [
        {
            "generation": 0,
            "best_score": 0.7,
            "mean_score": 0.65,
            "std_score": 0.05,
            "wall_clock_seconds": 305.0,
            "timing": generation_timing(0.5),
            "worker_scores": [
                {
                    "worker_id": 0,
                    "score": 0.65,
                    "timing": worker_timing("reload", 300.0),
                },
                {
                    "worker_id": 1,
                    "score": 0.70,
                    "timing": worker_timing("reload", 301.5),
                },
            ],
        },
        {
            "generation": 1,
            "best_score": 0.78,
            "mean_score": 0.74,
            "std_score": 0.04,
            "wall_clock_seconds": 310.0,
            "timing": generation_timing(0.7),
            "worker_scores": [
                {
                    "worker_id": 0,
                    "score": 0.72,
                    "timing": worker_timing("restart", 305.0),
                },
                {
                    "worker_id": 1,
                    "score": 0.78,
                    "timing": worker_timing("restart", 306.5),
                },
            ],
        },
    ]


@pytest.fixture
def fake_tuner(tmp_path):
    """Build a minimal stand-in object exposing the attributes ``save_final_results`` uses."""
    output_dir = tmp_path / "results"
    output_dir.mkdir(parents=True, exist_ok=True)

    population = SimpleNamespace(
        best_overall_metrics=None,
        best_overall_score_breakdown=None,
        current_generation=2,
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
            tuning_mode=SimpleNamespace(value="offline"),
            adaptive_restart_interval=10,
        ),
        population_size=2,
        num_parallel_workers=2,
        exploit_quantile=0.25,
        perturbation_factors=(0.8, 1.2),
        ready_interval=1,
        dead_config_threshold=3,
        enable_snapshots=False,
        snapshot_restore_interval=1,
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
        timestamp="20260613_0900",
        warm_start_provenance={"enabled": False},
        generation_history=_make_generation_history(),
        bootstrap_timing=_make_bootstrap_timing(),
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
        best_score=0.78,
        best_config={"shared_buffers": 0.5, "work_mem": 0.1},
    )

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


def _written_session(fake_tuner) -> dict:
    json_files = list(
        Path(fake_tuner.output_dir).glob("tuning_sessions/pbt_results_*.json")
    )
    assert len(json_files) == 1
    return json.loads(json_files[0].read_text(encoding="utf-8"))


def test_save_final_results_carries_timing_schema_and_durations(fake_tuner):
    results = PBTTuner.save_final_results.__get__(fake_tuner, type(fake_tuner))(
        total_time=650.0,
        tuning_time_seconds=620.0,
        bootstrap_seconds=30.0,
    )

    ts = results["tuning_session"]
    assert ts["timing_schema_version"] == "1.1"
    assert ts["total_time_seconds"] == 650.0
    assert ts["tuning_time_seconds"] == 620.0
    assert ts["bootstrap_seconds"] == 30.0

    written = _written_session(fake_tuner)
    assert written["tuning_session"]["tuning_time_seconds"] == 620.0
    assert written["tuning_session"]["bootstrap_seconds"] == 30.0


def test_save_final_results_emits_bootstrap_breakdown(fake_tuner):
    results = PBTTuner.save_final_results.__get__(fake_tuner, type(fake_tuner))(
        total_time=650.0,
        tuning_time_seconds=620.0,
        bootstrap_seconds=30.0,
    )

    breakdown = results["bootstrap_breakdown"]
    assert breakdown is not None
    assert set(breakdown.keys()) == {"records", "summary"}

    components = {r["component"] for r in breakdown["records"]}
    assert components == {
        "setup_instances",
        "verify_instances",
        "prune_knobs",
        "setup_snapshots",
    }

    summary = breakdown["summary"]
    assert summary["setup_instances"]["n"] == 1
    assert summary["setup_instances"]["total"] == pytest.approx(12.5)


def test_save_final_results_emits_timing_summary_across_gens_and_workers(fake_tuner):
    results = PBTTuner.save_final_results.__get__(fake_tuner, type(fake_tuner))(
        total_time=650.0,
        tuning_time_seconds=620.0,
        bootstrap_seconds=30.0,
    )

    summary = results["timing_summary"]
    # Two gens × two workers = 4 per-worker records for each worker-level component.
    assert summary["apply_only"]["n"] == 4
    assert summary["knob_verify"]["n"] == 4
    assert summary["workload"]["n"] == 4
    assert summary["score"]["n"] == 4
    # One per-gen evolve record per generation = 2.
    assert summary["evolve"]["n"] == 2
    # activate_reload and activate_restart each appear twice (gen 0 and gen 1).
    assert summary["activate_reload"]["n"] == 2
    assert summary["activate_restart"]["n"] == 2
    # workload totals match the synthetic fixture (300+301.5+305+306.5).
    assert summary["workload"]["total"] == pytest.approx(1213.0)


def test_save_final_results_round_trips_timing_through_json(fake_tuner):
    PBTTuner.save_final_results.__get__(fake_tuner, type(fake_tuner))(
        total_time=650.0,
        tuning_time_seconds=620.0,
        bootstrap_seconds=30.0,
    )

    written = _written_session(fake_tuner)
    assert "bootstrap_breakdown" in written
    assert "timing_summary" in written
    # Per-generation timing preserved.
    assert written["generation_history"][0]["timing"]["records"][0]["component"] == "evolve"
    # Per-worker timing preserved including metadata for activate_*.
    w0_records = written["generation_history"][0]["worker_scores"][0]["timing"]["records"]
    activate_rec = next(r for r in w0_records if r["component"] == "activate_reload")
    assert activate_rec["metadata"]["strategy"] == "reload"
