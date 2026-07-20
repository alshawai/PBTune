"""Session-JSON serialization contract for the unified ``PBTTuner``.

Consolidates the two legacy ``save_final_results`` test files (timing block +
session_environment) and repoints them at the new serialization seam. The
legacy monolith's ``PBTTuner.save_final_results`` no longer exists — assembly is
now ``BaseTuner._assemble_results`` (the shared envelope: header, best config,
worker resources, generation history, bootstrap breakdown, timing summary)
merged with PBT's ``build_session_payload`` (scoring block, ``strategy_params``,
warm-start, ``session_environment``). Disk writing is the separate
``write_session_json`` seam.

These tests drive the *real* ``_assemble_results`` on a bare ``PBTTuner`` built
via ``__new__`` with exactly the attribute surface the seam reads — no
constructor/setup, no database, no environment factory — then optionally
round-trip through the real writer. They verify:

* ``tuning_session.timing_schema_version == "1.1"`` and the duration fields,
* the ``bootstrap_breakdown`` block with its four components,
* the ``timing_summary`` aggregated across per-generation and per-worker records,
* the ``session_environment`` block,
* filename parity between the session JSON and best-config JSON writers.
"""

from __future__ import annotations

import json
from pathlib import Path
from types import SimpleNamespace

import pytest

from src.tuners.pbt.tuner import PBTTuner
from src.tuners.utils.session_writer import write_session_json, write_best_config_json
from src.tuners.utils.types import TuningStrategy
from src.utils.timing import TimingRecorder
from src.utils.types import SessionEnvironment, WorkerResourceAllocation


class _FakeKnobSpace:
    def __init__(self) -> None:
        self.knobs = {"shared_buffers": object(), "work_mem": object()}
        self.worker_resources = None

    def __len__(self) -> int:
        return len(self.knobs)

    def config_to_fractions(self, cfg):
        return cfg or {}


class _FakeMetricConfig:
    def get_scoring_metadata(self):
        return {
            "scoring_policy": "feature_driven_v2",
            "scoring_policy_version": "1.0",
            "metric_reference_version": "v1",
            "workload_features": {},
            "normalization_metadata": {},
        }


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
            "timing": generation_timing(0.5),
            "worker_scores": [
                {"worker_id": 0, "score": 0.65, "timing": worker_timing("reload", 300.0)},
                {"worker_id": 1, "score": 0.70, "timing": worker_timing("reload", 301.5)},
            ],
        },
        {
            "generation": 1,
            "best_score": 0.78,
            "timing": generation_timing(0.7),
            "worker_scores": [
                {"worker_id": 0, "score": 0.72, "timing": worker_timing("restart", 305.0)},
                {"worker_id": 1, "score": 0.78, "timing": worker_timing("restart", 306.5)},
            ],
        },
    ]


def _make_tuner(tmp_path, *, generation_history) -> PBTTuner:
    """Bare PBTTuner exposing exactly what ``_assemble_results`` + PBT's
    ``build_session_payload`` read — no constructor, no setup, no DB."""
    output_root = tmp_path / "results"
    output_root.mkdir(parents=True, exist_ok=True)

    tuner = PBTTuner.__new__(PBTTuner)

    # Identity / header surface.
    tuner.strategy = TuningStrategy.PBT
    tuner.timestamp = "20260613_0900"
    tuner.output_root = output_root
    tuner._rounds_completed = 2
    tuner.start_time = 0.0
    tuner.tuning_start_time = 0.0

    tuner.lifecycle = SimpleNamespace(
        strategy=TuningStrategy.PBT,
        knob_tier="minimal",
        knob_source="expert",
        random_seed=11,
        num_parallel_workers=2,
        tuning_mode=SimpleNamespace(value="offline"),
        adaptive_restart_interval=10,
        snapshot_restore_interval=1,
    )

    # Bundle-resolved fields (normally set in BaseTuner.setup()).
    tuner._workload_type = SimpleNamespace(value="oltp")
    tuner._benchmark_name = "sysbench"
    tuner.enable_snapshots = False

    tuner.full_knob_space = _FakeKnobSpace()
    tuner.metric_config = _FakeMetricConfig()

    tuner.population = SimpleNamespace(
        get_best_configuration=lambda: ({"shared_buffers": 0.5, "work_mem": 0.1}, 0.78),
        best_overall_metrics=None,
        best_overall_score_breakdown=None,
        history=[],
        generations_without_improvement=0,
    )

    tuner.pbt_config = SimpleNamespace(
        population_size=2,
        num_generations=2,
        exploit_quantile=0.25,
        perturbation_factors=(0.8, 1.2),
        ready_interval=1,
        dead_config_threshold=3,
    )
    tuner.benchmark_config = SimpleNamespace(
        scale_factor=0.1,
        sysbench_workload="oltp_read_write",
    )

    tuner.worker_resources = SimpleNamespace(
        ram_bytes=8 * 1024**3,
        cpu_cores=4,
        disk_type="SSD",
        disk_read_bps=100_000_000,
        disk_write_bps=50_000_000,
        disk_read_iops=10_000,
        disk_write_iops=8_000,
        disk_class="sata_ssd",
    )

    tuner.warm_start_provenance = {"enabled": False}
    tuner.ablation_variable = None
    tuner.ablation_value = None
    tuner.generation_history = generation_history
    tuner.bootstrap_timing = _make_bootstrap_timing()
    tuner.system_info = {
        "cpu_model": "Test CPU",
        "cpu_cores": {"physical": 4, "logical": 8},
        "ram": {"total_bytes": 16 * 1024**3, "total_gb": 16.0},
        "disk_type": "SSD",
        "pg_version": "PostgreSQL 18.0",
        "os": {"system": "Linux", "release": "6.5.0", "version": "#1 SMP", "machine": "x86_64"},
    }
    tuner.session_environment = _make_session_environment()
    return tuner


def _assemble(tuner) -> dict:
    return tuner._assemble_results(
        total_time=650.0, tuning_time=620.0, bootstrap_seconds=30.0
    )


@pytest.fixture
def tuner_with_history(tmp_path):
    return _make_tuner(tmp_path, generation_history=_make_generation_history())


# ── Timing block ────────────────────────────────────────────────────


def test_assemble_carries_timing_schema_and_durations(tuner_with_history):
    results = _assemble(tuner_with_history)

    ts = results["tuning_session"]
    assert ts["timing_schema_version"] == "1.1"
    assert ts["tuning_strategy"] == "pbt"
    assert ts["total_time_seconds"] == 650.0
    assert ts["tuning_time_seconds"] == 620.0
    assert ts["bootstrap_seconds"] == 30.0


def test_assemble_emits_bootstrap_breakdown(tuner_with_history):
    results = _assemble(tuner_with_history)

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
    assert breakdown["summary"]["setup_instances"]["n"] == 1
    assert breakdown["summary"]["setup_instances"]["total"] == pytest.approx(12.5)


def test_assemble_emits_timing_summary_across_gens_and_workers(tuner_with_history):
    results = _assemble(tuner_with_history)

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


def test_assemble_round_trips_timing_through_json(tuner_with_history):
    results = _assemble(tuner_with_history)
    write_session_json(
        results,
        output_dir=tuner_with_history.output_root,
        filename=tuner_with_history.session_filename(),
    )

    written = _written_session(tuner_with_history.output_root)
    assert "bootstrap_breakdown" in written
    assert "timing_summary" in written
    # Per-generation timing preserved.
    assert written["history"][0]["timing"]["records"][0]["component"] == "evolve"
    # Per-worker timing preserved including metadata for activate_*.
    w0_records = written["history"][0]["worker_scores"][0]["timing"]["records"]
    activate_rec = next(r for r in w0_records if r["component"] == "activate_reload")
    assert activate_rec["metadata"]["strategy"] == "reload"


# ── Session environment ─────────────────────────────────────────────


def test_assemble_emits_session_environment(tuner_with_history):
    results = _assemble(tuner_with_history)

    # Unified schema: system_info + session_environment merge into one
    # tuning_session.environment block; the legacy top-level keys are gone.
    assert "session_environment" not in results
    assert "system_info" not in results
    env = results["tuning_session"]["environment"]
    # Session-level fields live at the environment top level.
    assert env["pg_server_version"] == "18.0"
    assert env["docker_version"] == "25.0.3"
    assert env["use_docker"] is True
    assert env["num_parallel_workers"] == 2
    assert env["population_size"] == 4
    assert env["cpu_pinning_scheme"] == "cpuset"
    # per_worker_resources dropped (redundant with top-level worker_resources).
    assert "per_worker_resources" not in env
    # Raw hardware snapshot lives ONLY under system_info now (flat duplicates
    # like a top-level env["cpu_model"] were removed as redundant).
    assert env["system_info"]["cpu_model"] == "Test CPU"
    assert "cpu_model" not in env
    assert "ram_bytes_total" not in env
    assert "os_system" not in env

    # worker_resources now nested under tuning_session (was top-level sibling).
    assert results["tuning_session"]["worker_resources"]["ram_bytes"] == 8 * 1024**3


def test_assemble_strategy_params_and_scoring_block(tuner_with_history):
    results = _assemble(tuner_with_history)

    ts = results["tuning_session"]
    # PBT's build_session_payload merges its nested sections into the header.
    assert ts["strategy_params"]["population_size"] == 2
    assert ts["strategy_params"]["generations"] == 2
    assert ts["strategy_params"]["exploit_quantile"] == 0.25
    assert "scoring" in ts
    assert ts["scoring"]["scoring_policy"] == "feature_driven_v2"


# ── Writer filename parity ──────────────────────────────────────────


def test_session_and_best_config_filenames_share_timestamp(tuner_with_history):
    results = _assemble(tuner_with_history)
    write_session_json(
        results,
        output_dir=tuner_with_history.output_root,
        filename=tuner_with_history.session_filename(),
    )
    best_config, _, _ = tuner_with_history.collect_best()
    write_best_config_json(
        tuner_with_history.best_config_fractions(best_config or {}),
        output_dir=tuner_with_history.output_root,
        filename=tuner_with_history.best_config_filename(),
    )

    json_files = list(
        Path(tuner_with_history.output_root).glob("traces/trace_*.json")
    )
    best_files = list(
        Path(tuner_with_history.output_root).glob("best_configs/best_*.json")
    )
    assert len(json_files) == 1
    assert len(best_files) == 1
    assert "20260613_0900" in json_files[0].name
    assert "20260613_0900" in best_files[0].name


def _written_session(output_root) -> dict:
    json_files = list(Path(output_root).glob("traces/trace_*.json"))
    assert len(json_files) == 1
    return json.loads(json_files[0].read_text(encoding="utf-8"))
