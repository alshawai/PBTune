"""Regression tests for per-experiment manifest plumbing in ExperimentRunner."""

from __future__ import annotations

import json
import subprocess
from pathlib import Path
from unittest.mock import patch

import pytest

from scripts.experiments.runner import (
    DEFAULT_MANIFEST_DIR,
    LEGACY_MANIFEST_PATH,
    ExperimentRunner,
)


@pytest.fixture
def runner_factory(tmp_path, monkeypatch):
    """Build an ExperimentRunner whose manifests live under tmp_path."""

    def _make(manifest_path: Path | None = None, manifest_dir: Path | None = None):
        # Skip the real hardware probe so tests don't touch fio/disk.
        with patch(
            "scripts.experiments.runner.detect_worker_resources"
        ) as mock_detect:
            mock_detect.return_value = type(
                "WR", (), {"ram_bytes": 1024 * 1024 * 1024, "cpu_cores": 2}
            )()
            return ExperimentRunner(
                dry_run=False,
                no_push=True,
                manifest_dir=manifest_dir or (tmp_path / "manifests"),
                manifest_path=manifest_path,
            )

    return _make


def test_per_experiment_manifest_path_derivation(runner_factory, tmp_path):
    """Without --manifest, each experiment id maps to its own file."""
    runner = runner_factory()
    expected = tmp_path / "manifests" / "t3_exploit_020.json"
    assert runner._resolve_manifest_path("t3_exploit_020") == expected
    # Different experiment ids never collide on the same file.
    assert runner._resolve_manifest_path("t3_exploit_025") != expected


def test_manifest_path_override_wins_over_per_experiment(runner_factory, tmp_path):
    """--manifest <path> forces every experiment to share the same file."""
    override = tmp_path / "single.json"
    runner = runner_factory(manifest_path=override)
    assert runner._resolve_manifest_path("t3_exploit_020") == override
    assert runner._resolve_manifest_path("t3_exploit_025") == override


def test_active_manifest_isolation(runner_factory, tmp_path):
    """Writes to one experiment's manifest never bleed into another's file."""
    runner = runner_factory()

    # Activate experiment A and mark a phase done.
    runner._active_manifest_path = runner._resolve_manifest_path("expA")
    runner._active_manifest = {"started_at": "2026-01-01", "runs": {}}
    runner._mark_status("expA/seed_1/pbt", "done", session_json="path_A.json")

    # Activate experiment B — fresh load, must not see A's runs.
    runner._active_manifest_path = runner._resolve_manifest_path("expB")
    runner._active_manifest = runner._load_manifest(runner._active_manifest_path)
    assert "expA/seed_1/pbt" not in runner._active_manifest["runs"]
    runner._mark_status("expB/seed_1/pbt", "done", session_json="path_B.json")

    # Files on disk are also separate.
    file_a = json.loads((tmp_path / "manifests" / "expA.json").read_text())
    file_b = json.loads((tmp_path / "manifests" / "expB.json").read_text())
    assert "expA/seed_1/pbt" in file_a["runs"]
    assert "expA/seed_1/pbt" not in file_b["runs"]
    assert "expB/seed_1/pbt" in file_b["runs"]
    assert "expB/seed_1/pbt" not in file_a["runs"]


def test_phase_attempt_records_owner_and_attempt_history(runner_factory, tmp_path):
    """Each execution attempt is durably tied to one runner process."""
    runner = runner_factory()
    runner._active_manifest_path = tmp_path / "attempt.json"
    key = "exp/seed_42/pbt"

    with runner._phase_attempt(key) as attempt_id:
        entry = runner._active_manifest["runs"][key]
        assert entry["status"] == "running"
        assert entry["attempt_id"] == attempt_id
        assert entry["attempt_number"] == 1
        assert entry["owner"] == runner._runner_identity.to_dict()
        runner._mark_status(key, "done", session_json="results/trace.json")

    persisted = json.loads(runner._active_manifest_path.read_text())
    attempt = persisted["runs"][key]["attempts"][0]
    assert attempt["status"] == "done"
    assert attempt["owner"]["runner_id"] == runner._runner_identity.runner_id


def test_phase_attempt_persists_keyboard_interruption(runner_factory, tmp_path):
    """Ctrl-C changes a running attempt to interrupted before propagating."""
    runner = runner_factory()
    runner._active_manifest_path = tmp_path / "interrupted.json"
    key = "exp/seed_42/bo"

    with pytest.raises(KeyboardInterrupt, match="SIGINT"):
        with runner._phase_attempt(key):
            raise KeyboardInterrupt("SIGINT")

    persisted = json.loads(runner._active_manifest_path.read_text())
    entry = persisted["runs"][key]
    assert entry["status"] == "interrupted"
    assert entry["attempts"][0]["status"] == "interrupted"
    assert entry["error"] == "SIGINT"
    assert "finished_at" in entry


def test_phase_attempt_preserves_remote_completion_when_sync_fails(
    runner_factory, tmp_path
):
    """A coordinator sync failure remains recoverable rather than execution-failed."""
    runner = runner_factory()
    runner._active_manifest_path = tmp_path / "syncing.json"
    key = "exp/seed_42/bo"

    with pytest.raises(RuntimeError, match="rsync failed"):
        with runner._phase_attempt(key):
            runner._mark_status(key, "syncing", remote_completed_at="now")
            raise RuntimeError("rsync failed")

    persisted = json.loads(runner._active_manifest_path.read_text())
    entry = persisted["runs"][key]
    assert entry["status"] == "syncing"
    assert entry["attempts"][0]["status"] == "syncing"
    assert entry["error"] == "RuntimeError: rsync failed"
    assert "sync_failed_at" in entry


def test_new_attempt_supersedes_abandoned_running_attempt(runner_factory):
    """An incomplete prior owner is retained as stale before a retry starts."""
    runner = runner_factory()
    key = "exp/seed_42/eval"

    first_id = runner._start_phase(key)
    second_id = runner._start_phase(key)

    entry = runner._active_manifest["runs"][key]
    assert first_id != second_id
    assert entry["attempt_number"] == 2
    assert entry["attempts"][0]["status"] == "stale"
    assert entry["attempts"][1]["status"] == "running"


def test_publication_failure_does_not_change_execution_status(
    runner_factory, monkeypatch
):
    """A Git failure cannot make an expensive completed phase rerun."""
    runner = runner_factory()
    key = "exp/seed_42/pbt"
    runner._active_manifest["runs"][key] = {"status": "done"}
    monkeypatch.setattr(
        runner,
        "_commit_and_push",
        lambda exp, seed, phase: ("failed", "push rejected"),
    )

    runner._publish_phase(_smoke_exp(), 42, "pbt", key)

    entry = runner._active_manifest["runs"][key]
    assert entry["status"] == "done"
    assert entry["publication"] == {
        "status": "failed",
        "updated_at": entry["publication"]["updated_at"],
        "error": "push rejected",
    }


def test_completed_phase_retries_only_publication(runner_factory, monkeypatch):
    """Resume retries a pending push without launching phase execution."""
    runner = runner_factory()
    key = "exp/seed_42/pbt"
    runner._active_manifest["runs"][key] = {
        "status": "done",
        "publication": {"status": "failed"},
    }
    calls = []
    monkeypatch.setattr(
        runner,
        "_publish_phase",
        lambda exp, seed, phase, run_key: calls.append(
            (exp.id, seed, phase, run_key)
        ),
    )

    runner._resume_publication_if_needed(_smoke_exp(), 42, "pbt", key)

    assert calls == [("smoke_sysbench_rw", 42, "pbt", key)]


def test_cross_manifest_index_aggregates_peer_files(runner_factory, tmp_path):
    """Read-only index must surface entries written by other experiments.

    Warm-start lookups depend on this: the source experiment may have
    been run on a peer machine and committed under its own manifest.
    """
    manifests_dir = tmp_path / "manifests"
    manifests_dir.mkdir(parents=True)

    (manifests_dir / "t1_pbt_oltp.json").write_text(
        json.dumps(
            {
                "started_at": "2026-01-01",
                "runs": {
                    "t1_pbt_oltp/seed_42/pbt": {
                        "status": "done",
                        "session_json": "results/oltp/.../pbt_results_xyz.json",
                    }
                },
            }
        )
    )
    (manifests_dir / "t3_warmstart.json").write_text(
        json.dumps({"started_at": "2026-01-02", "runs": {}})
    )

    runner = runner_factory(manifest_dir=manifests_dir)
    assert "t1_pbt_oltp/seed_42/pbt" in runner._cross_manifest_index
    assert (
        runner._cross_manifest_index["t1_pbt_oltp/seed_42/pbt"]["status"]
        == "done"
    )


def test_paths_to_stage_scoped_to_current_session_layout(runner_factory, tmp_path):
    """Tuner pathspecs cover the manifest and exact current session root.

    The experiment id (``t3_exploit_020``) is a label, not a directory
    name. Sysbench tuner output uses the single workload key beneath
    ``sessions/``; the obsolete ``oltp/<workload>`` root must not return.
    """
    from scripts.experiments.experiment_matrix import Experiment

    runner = runner_factory()
    exp = Experiment(
        id="t3_exploit_020",
        tier=3,
        description="",
        benchmark="sysbench",
        sysbench_workload="oltp_read_write",
        scale_factor=None,
        config_profile="thorough",
        knob_tier="extensive",
        knob_source="expert",
        tuning_mode="offline",
        seeds=(42,),
        eval_repetitions=5,
        run_bo=False,
    )
    runner._active_manifest_path = runner._resolve_manifest_path(exp.id)
    paths = runner._paths_to_stage(exp, "pbt")
    assert "sessions/oltp_read_write/pbt/extensive" in paths
    # Experiment id must NOT be staged as a path — it's a label.
    assert exp.id not in paths
    for p in paths:
        assert "experiment_manifest.json" not in p, (
            "Per-experiment paths must not include the legacy global file"
        )


@pytest.mark.parametrize(
    ("phase", "expected"),
    [
        ("pbt", "sessions/olap/pbt/extensive"),
        ("bo", "sessions/olap/bo/extensive"),
        ("eval", "comparisons/olap/extensive"),
    ],
)
def test_paths_to_stage_tpch_maps_current_phase_layout(
    runner_factory, tmp_path, phase, expected
):
    """TPC-H pathspecs target each current phase output precisely."""
    from scripts.experiments.experiment_matrix import Experiment

    runner = runner_factory()
    exp = Experiment(
        id="t1_tpch_sf1",
        tier=1,
        description="",
        benchmark="tpch",
        sysbench_workload=None,
        scale_factor=1.0,
        config_profile="thorough",
        knob_tier="extensive",
        knob_source="expert",
        tuning_mode="offline",
        seeds=(42,),
        eval_repetitions=5,
        run_bo=True,
    )
    runner._active_manifest_path = runner._resolve_manifest_path(exp.id)
    paths = runner._paths_to_stage(exp, phase)
    assert expected in paths
    assert exp.id not in paths


def test_paths_to_stage_data_driven_ablation_session(runner_factory):
    """Data-driven tuner paths preserve tier slugging and ablation scope."""
    from scripts.experiments.experiment_matrix import Experiment

    runner = runner_factory()
    exp = Experiment(
        id="t3_source_dd_core",
        tier=3,
        description="",
        benchmark="sysbench",
        sysbench_workload="oltp_read_write",
        scale_factor=None,
        config_profile="thorough",
        knob_tier="core",
        knob_source="data_driven",
        tuning_mode="offline",
        seeds=(42,),
        eval_repetitions=5,
        run_bo=False,
        ablation_variable="knob_source",
        ablation_value="data_driven_core",
    )

    paths = runner._paths_to_stage(exp, "pbt")

    assert (
        "sessions/oltp_read_write/pbt/core@scalpel-v1/"
        "ablations/knob_source/data_driven_core"
    ) in paths


def test_paths_to_stage_rejects_unknown_phase(runner_factory):
    """Unknown phases fail before invoking git with an invalid pathspec."""
    with pytest.raises(ValueError, match="Unknown experiment phase"):
        runner_factory()._paths_to_stage(_smoke_exp(), "unknown")


def test_default_manifest_dir_is_under_results():
    """The default manifest dir lives under results/ so it lands in the
    same git-tracked tree the runner already commits to."""
    assert DEFAULT_MANIFEST_DIR.name == "manifests"
    assert DEFAULT_MANIFEST_DIR.parent.name == "results"
    # Legacy single-file path still exists as a constant for back-compat.
    assert LEGACY_MANIFEST_PATH.name == "experiment_manifest.json"


# ---------------------------------------------------------------------------
# Smoke suite (pre-flight) matrix
# ---------------------------------------------------------------------------


def test_smoke_suite_shape():
    """The smoke suite is exactly two minimal-budget PBT→BO→EVAL experiments."""
    from scripts.experiments.experiment_matrix import build_smoke_experiments

    smoke = build_smoke_experiments()
    ids = {e.id for e in smoke}
    assert ids == {"smoke_sysbench_rw", "smoke_tpch_sf01"}

    for e in smoke:
        assert e.config_profile == "rapid"
        assert e.knob_tier == "minimal"
        assert e.generations == 1
        assert e.population == 2
        assert e.eval_repetitions >= 2  # src.evaluation requires >= 2
        assert e.run_bo is True
        assert e.tier == 0  # pre-flight, never under --tier {1,2,3}

    by_id = {e.id: e for e in smoke}
    assert by_id["smoke_sysbench_rw"].benchmark == "sysbench"
    assert by_id["smoke_sysbench_rw"].sysbench_workload == "oltp_read_write"
    assert by_id["smoke_tpch_sf01"].benchmark == "tpch"
    assert by_id["smoke_tpch_sf01"].scale_factor == 0.1


def test_smoke_experiments_excluded_from_main_matrix():
    """Smoke runs must never sneak into the publication matrix."""
    from scripts.experiments.experiment_matrix import (
        build_all_experiments,
        build_smoke_experiments,
        get_experiment_by_id,
        get_experiments_by_tier,
    )

    main_ids = {e.id for e in build_all_experiments()}
    smoke_ids = {e.id for e in build_smoke_experiments()}
    assert main_ids.isdisjoint(smoke_ids)

    # Not reachable via --tier 1/2/3 ...
    for tier in (1, 2, 3):
        assert smoke_ids.isdisjoint({e.id for e in get_experiments_by_tier(tier)})
    # ... but reachable by explicit id.
    assert get_experiment_by_id("smoke_sysbench_rw") is not None
    assert get_experiment_by_id("smoke_tpch_sf01") is not None


def test_smoke_commands_use_minimal_budget(runner_factory):
    """The built CLI commands carry the rapid/minimal/1-gen budget."""
    from scripts.experiments.experiment_matrix import build_smoke_experiments

    runner = runner_factory()
    smoke = {e.id: e for e in build_smoke_experiments()}
    exp = smoke["smoke_sysbench_rw"]

    pbt = runner._build_pbt_cmd(exp, seed=42)
    # Routes through the unified tuners entry point, not a legacy module.
    assert pbt[:4] == ["python", "-m", "src.tuners", "pbt"]
    assert pbt[pbt.index("--config") + 1] == "rapid"
    assert pbt[pbt.index("--tier") + 1] == "minimal"
    assert pbt[pbt.index("--generations") + 1] == "1"
    assert pbt[pbt.index("--population") + 1] == "2"

    bo = runner._build_bo_cmd(exp, pbt_session=None, seed=42)
    # BO must route through `src.tuners bo`; the legacy `src.scripts.bo_baseline`
    # package was removed in the unify-tuners refactor.
    assert bo[:4] == ["python", "-m", "src.tuners", "bo"]
    assert "src.scripts.bo_baseline" not in bo
    assert bo[bo.index("--config") + 1] == "rapid"
    assert bo[bo.index("--tier") + 1] == "minimal"

    ev = runner._build_eval_cmd(None, None, exp.eval_repetitions, seed=42)
    assert ev[ev.index("--repetitions") + 1] == "2"


def test_distributed_pbt_command_uses_fleet_not_coordinator_resources(tmp_path):
    """Distributed commands carry fleet controls and omit local resource flags."""
    from scripts.experiments.experiment_matrix import build_smoke_experiments

    inventory = tmp_path / "devices.yaml"
    inventory.write_text("devices: []\n")
    runner = ExperimentRunner(
        dry_run=True,
        no_push=True,
        execution_mode="distributed",
        inventory=inventory,
        bootstrap=False,
        remote_install_deps=False,
        eval_timeout=2400.0,
        agent_timeout=90.0,
    )
    exp = build_smoke_experiments()[0]

    cmd = runner._build_pbt_cmd(exp, seed=42)

    assert "--distributed" in cmd
    assert cmd[cmd.index("--inventory") + 1] == str(inventory)
    assert cmd[cmd.index("--eval-timeout") + 1] == "2400.0"
    assert cmd[cmd.index("--agent-timeout") + 1] == "90.0"
    assert "--no-bootstrap" in cmd
    assert "--no-remote-deps" in cmd
    assert "--worker-ram" not in cmd
    assert "--worker-cpus" not in cmd

    bo = runner._build_bo_cmd(exp, Path("/remote/trace.json"), seed=42)
    assert "--no-cotenant" in bo
    assert "--worker-ram" not in bo
    assert "--worker-cpus" not in bo


def test_distributed_mode_requires_inventory():
    """A distributed campaign cannot start without a fleet inventory."""
    with pytest.raises(ValueError, match="requires a fleet inventory"):
        ExperimentRunner(
            dry_run=True,
            no_push=True,
            execution_mode="distributed",
        )


def test_distributed_preflight_ignores_coordinator_disk(monkeypatch, tmp_path):
    """Dedicated-device runs never validate the coordinator's block device."""
    inventory = tmp_path / "devices.yaml"
    inventory.write_text("devices: []\n")
    runner = ExperimentRunner(
        dry_run=False,
        no_push=True,
        execution_mode="distributed",
        inventory=inventory,
    )

    def _boom(*args, **kwargs):  # pragma: no cover - must never be called
        raise AssertionError("coordinator disk resolver must not run")

    monkeypatch.setattr(
        "scripts.experiments.runner._resolve_block_device_node", _boom
    )

    runner._preflight_disk_isolation()


def test_remote_command_runs_on_selected_device(monkeypatch, tmp_path):
    """BO/EVAL execute remotely and always clean the selected DB instance."""
    inventory = tmp_path / "devices.yaml"
    inventory.write_text(
        """
fleet:
  ssh_user: pbt
  data_dir: /srv/pbt
  python: /srv/pbt/.venv/bin/python
devices:
  - host: 10.0.0.11
  - host: 10.0.0.12
""".strip()
    )
    runner = ExperimentRunner(
        dry_run=False,
        no_push=True,
        execution_mode="distributed",
        inventory=inventory,
    )
    captured: list[str] = []

    def _capture(cmd, cwd=Path(".")):
        captured.extend(cmd)
        return True

    monkeypatch.setattr(runner, "_run_command", _capture)

    assert runner._run_remote_command(
        runner._comparison_device(),
        ["python", "-m", "src.tuners", "bo", "--no-cotenant"],
    )
    rendered = " ".join(captured)
    assert "pbt@10.0.0.11" in rendered
    assert "ServerAliveInterval=30" in rendered
    assert "ServerAliveCountMax=6" in rendered
    assert "cd /srv/pbt/code" in rendered
    assert "/srv/pbt/.venv/bin/python -m src.tuners bo" in rendered
    assert "trap cleanup_comparison_instance EXIT" in rendered
    assert "src.scripts.cleanup_instances" in rendered
    assert "--data-dir /srv/pbt/instances --force --docker-only" in rendered


def test_remote_command_writes_attempt_specific_artifact_receipt(
    monkeypatch, tmp_path
):
    """A successful remote phase records the exact newly-created artifact."""
    inventory = tmp_path / "devices.yaml"
    inventory.write_text(
        """
fleet:
  ssh_user: pbt
  data_dir: /srv/pbt
  python: /srv/pbt/.venv/bin/python
devices:
  - host: 10.0.0.11
""".strip()
    )
    runner = ExperimentRunner(
        dry_run=False,
        no_push=True,
        execution_mode="distributed",
        inventory=inventory,
    )
    spec = runner._remote_artifact_spec(_smoke_exp(), "bo", "attempt-1")
    captured: list[list[str]] = []
    monkeypatch.setattr(
        runner,
        "_run_command",
        lambda cmd, cwd=Path("."): captured.append(cmd) or True,
    )

    assert runner._run_remote_command(
        runner._comparison_device(),
        ["python", "-m", "src.tuners", "bo"],
        artifact=spec,
    )

    rendered = " ".join(captured[0])
    assert spec.output_dir.startswith("/srv/pbt/code/results/sessions/")
    assert spec.marker_path in rendered
    assert spec.receipt_path in rendered
    assert "find /srv/pbt/code/results/sessions/" in rendered
    assert "-newer" in rendered


def test_resume_reconciles_completed_remote_bo_without_rerun(
    monkeypatch, tmp_path
):
    """A receipt finalizes BO after coordinator loss without executing BO again."""
    inventory = tmp_path / "devices.yaml"
    inventory.write_text(
        """
fleet:
  ssh_user: pbt
  data_dir: /srv/pbt
devices:
  - host: 10.0.0.11
""".strip()
    )
    runner = ExperimentRunner(
        dry_run=False,
        no_push=True,
        execution_mode="distributed",
        inventory=inventory,
        manifest_dir=tmp_path / "manifests",
    )
    exp = _smoke_exp()
    key = runner._get_run_key(exp.id, 42, "bo")
    spec = runner._remote_artifact_spec(exp, "bo", "attempt-1")
    artifact = tmp_path / "trace_20260910.json"
    artifact.write_text(
        json.dumps({"tuning_session": {"seed": 42}}), encoding="utf-8"
    )
    monkeypatch.setattr("scripts.experiments.runner.PROJECT_ROOT", tmp_path)
    runner._active_manifest_path = tmp_path / "manifest.json"
    runner._active_manifest["runs"][key] = {
        "status": "syncing",
        "attempt_id": "attempt-1",
        "started_at": "2026-09-10T00:00:00Z",
        "remote_artifact": spec.to_dict(),
    }
    monkeypatch.setattr(
        runner,
        "_remote_artifact_from_receipt",
        lambda artifact_spec, started_at: "/srv/pbt/code/results/trace.json",
    )
    monkeypatch.setattr(
        runner,
        "_validate_remote_artifact_path",
        lambda remote_path, artifact_spec: artifact,
    )
    monkeypatch.setattr(runner, "_sync_comparison_output", lambda spec: None)
    published: list[str] = []
    monkeypatch.setattr(
        runner,
        "_publish_phase",
        lambda experiment, seed, phase, run_key: published.append(run_key),
    )

    recovered = runner._try_reconcile_remote_phase(
        exp, 42, "bo", key, retry_failed=False
    )

    assert recovered == artifact
    assert runner._active_manifest["runs"][key]["status"] == "done"
    assert runner._active_manifest["runs"][key]["reconciled"] is True
    assert runner._active_manifest["runs"][key]["session_json"] == artifact.name
    assert published == [key]


def test_reconciled_artifact_rejects_wrong_seed(tmp_path):
    """A stale result from another seed cannot satisfy a remote attempt."""
    artifact = tmp_path / "trace.json"
    artifact.write_text(
        json.dumps({"tuning_session": {"seed": 123}}), encoding="utf-8"
    )

    with pytest.raises(RuntimeError, match="artifact seed 123 != 42"):
        ExperimentRunner._validate_reconciled_json(artifact, "bo", 42)


def test_prepare_comparison_device_stops_other_agents(monkeypatch, tmp_path):
    """All fleet agents and PostgreSQL instances stop before solo BO begins."""
    inventory = tmp_path / "devices.yaml"
    inventory.write_text(
        """
fleet:
  ssh_user: pbt
  data_dir: /srv/pbt
devices:
  - host: 10.0.0.11
  - host: 10.0.0.12
  - host: 10.0.0.13
""".strip()
    )
    runner = ExperimentRunner(
        dry_run=False,
        no_push=True,
        execution_mode="distributed",
        inventory=inventory,
        bootstrap=False,
        comparison_worker_id=1,
    )
    targets: list[str] = []

    def _capture(cmd, cwd=Path(".")):
        targets.append(" ".join(cmd))
        return True

    monkeypatch.setattr(runner, "_run_command", _capture)

    runner._prepare_comparison_device()

    assert len(targets) == 3
    assert any("10.0.0.11" in cmd for cmd in targets)
    selected = next(cmd for cmd in targets if "10.0.0.12" in cmd)
    assert any("10.0.0.13" in cmd for cmd in targets)
    assert "docker ps -aq" in selected
    assert "docker rm -f" in selected
    assert "cleanup_instances" not in selected
    assert "agent-worker-1.pid" in selected
    assert "agent-worker-0.pid" in next(
        cmd for cmd in targets if "10.0.0.11" in cmd
    )
    assert "agent-worker-2.pid" in next(
        cmd for cmd in targets if "10.0.0.13" in cmd
    )


def test_prepare_comparison_device_syncs_code_when_pbt_is_skipped(
    monkeypatch, tmp_path
):
    """Comparison setup must not depend on PBT bootstrap running first."""
    inventory = tmp_path / "devices.yaml"
    inventory.write_text(
        """
fleet:
  ssh_user: pbt
  data_dir: /srv/pbt
  python: /srv/pbt/.venv/bin/python
devices:
  - host: 10.0.0.11
""".strip()
    )
    runner = ExperimentRunner(
        dry_run=False,
        no_push=True,
        execution_mode="distributed",
        inventory=inventory,
    )
    commands: list[list[str]] = []

    def _capture(cmd, cwd=Path(".")):
        commands.append(cmd)
        return True

    monkeypatch.setattr(runner, "_run_command", _capture)

    runner._prepare_comparison_device()
    runner._sync_comparison_device_code()

    rendered = [" ".join(command) for command in commands]
    assert any("docker ps -aq" in command for command in rendered)
    assert sum("rsync" in command for command in rendered) == 1
    assert any("pip install -q -r requirements.txt" in command for command in rendered)


def test_gcp_campaign_session_stops_all_worker_vms_on_exit(monkeypatch, tmp_path):
    """Every configured worker VM is verified stopped when the campaign raises."""
    inventory = tmp_path / "devices.yaml"
    inventory.write_text(
        """
fleet:
  gcp_project: pbt-research
  gcp_zone: us-central1-a
devices:
  - host: 10.0.0.11
    gcp_instance: pbt-worker-0
  - host: 10.0.0.12
    gcp_instance: pbt-worker-1
""".strip()
    )
    runner = ExperimentRunner(
        dry_run=False,
        no_push=True,
        execution_mode="distributed",
        inventory=inventory,
        stop_gcp_after_campaign=True,
    )
    commands: list[list[str]] = []

    def _capture(cmd, cwd=Path(".")):
        commands.append(cmd)
        return True

    monkeypatch.setattr(runner, "_run_command", _capture)
    statuses = iter(["RUNNING", "TERMINATED", "RUNNING", "TERMINATED"])
    monkeypatch.setattr(
        runner,
        "_capture_command",
        lambda cmd: subprocess.CompletedProcess(cmd, 0, next(statuses), ""),
    )

    with pytest.raises(RuntimeError, match="campaign failure"):
        with runner.gcp_campaign_session():
            raise RuntimeError("campaign failure")

    assert commands == [
        [
            "gcloud",
            "compute",
            "instances",
            "stop",
            "pbt-worker-0",
            "--project",
            "pbt-research",
            "--zone",
            "us-central1-a",
            "--quiet",
        ],
        [
            "gcloud",
            "compute",
            "instances",
            "stop",
            "pbt-worker-1",
            "--project",
            "pbt-research",
            "--zone",
            "us-central1-a",
            "--quiet",
        ],
    ]
    shutdown = runner._active_manifest["fleet_shutdown"]
    assert shutdown["status"] == "terminated"
    assert shutdown["devices"]["pbt-worker-0"]["status"] == "terminated"
    assert shutdown["devices"]["pbt-worker-1"]["status"] == "terminated"


def test_gcp_shutdown_requires_explicit_instance_identity(tmp_path):
    """Cloud shutdown fails closed instead of targeting VMs heuristically."""
    inventory = tmp_path / "devices.yaml"
    inventory.write_text("devices:\n  - host: 10.0.0.11\n")
    runner = ExperimentRunner(
        dry_run=True,
        no_push=True,
        execution_mode="distributed",
        inventory=inventory,
        stop_gcp_after_campaign=True,
    )

    with pytest.raises(ValueError, match="gcp_project, gcp_zone, and gcp_instance"):
        runner._stop_gcp_fleet()


def test_gcp_shutdown_retries_until_terminated_and_is_idempotent(
    monkeypatch, tmp_path
):
    """A VM is retried after an unverified stop and never stopped twice later."""
    inventory = tmp_path / "devices.yaml"
    inventory.write_text(
        """
fleet:
  gcp_project: pbt-research
  gcp_zone: us-central1-a
devices:
  - host: 10.0.0.11
    gcp_instance: pbt-worker-0
""".strip()
    )
    runner = ExperimentRunner(
        dry_run=False,
        no_push=True,
        execution_mode="distributed",
        inventory=inventory,
        stop_gcp_after_campaign=True,
        manifest_dir=tmp_path / "manifests",
    )
    runner._active_manifest_path = tmp_path / "manifest.json"
    commands: list[list[str]] = []
    statuses = iter(["RUNNING", "STOPPING", "STOPPING", "TERMINATED"])
    monkeypatch.setattr(
        runner,
        "_gcp_instance_status",
        lambda device: next(statuses),
    )
    monkeypatch.setattr(
        runner,
        "_run_command",
        lambda cmd, cwd=Path("."): commands.append(cmd) or True,
    )
    monkeypatch.setattr("scripts.experiments.runner.GCP_STOP_VERIFY_POLLS", 1)
    monkeypatch.setattr("scripts.experiments.runner.time.sleep", lambda _: None)

    runner._stop_gcp_fleet()
    runner._stop_gcp_fleet()

    assert len(commands) == 2
    shutdown = json.loads(runner._active_manifest_path.read_text())[
        "fleet_shutdown"
    ]
    assert shutdown["status"] == "terminated"
    assert shutdown["devices"]["pbt-worker-0"]["attempt"] == 2


# ---------------------------------------------------------------------------
# Version control: stash → pull → stash pop → commit → push (+ retry)
# ---------------------------------------------------------------------------


class _FakeGit:
    """Records git subcommands and scripts returncodes for push races."""

    def __init__(
        self,
        push_fail_times: int = 0,
        stash_saved: bool = True,
        upstream: str | None = "main/main",
    ):
        self.calls: list[tuple[str, ...]] = []
        self._push_fail_times = push_fail_times
        self._push_attempts = 0
        self._stash_saved = stash_saved
        # Simulated `git rev-parse --abbrev-ref main@{u}` output. None means
        # the branch has no configured upstream (resolver should fall back).
        self._upstream = upstream
        # Remotes seen on push/pull, in order — lets tests assert which remote
        # the resolver chose.
        self.push_remotes: list[str] = []
        self.pull_remotes: list[str] = []

    def __call__(self, cmd, cwd=None, check=False, text=False, capture_output=False):
        import subprocess as _sp

        assert cmd[0] == "git"
        sub = tuple(cmd[1:])
        self.calls.append(sub)

        rc, stdout, stderr = 0, "", ""
        if sub[:2] == ("rev-parse", "--abbrev-ref"):
            if self._upstream is None:
                rc, stderr = 128, "fatal: no upstream configured"
            else:
                stdout = self._upstream
        elif sub[:2] == ("stash", "push"):
            stdout = "Saved working directory" if self._stash_saved else "No local changes to save"
        elif sub[:2] == ("diff", "--cached"):
            rc = 1  # 1 == there ARE staged changes → proceed to commit
        elif sub[:1] == ("pull",):
            # ("pull", "--rebase", <remote>, <branch>)
            if len(sub) >= 3:
                self.pull_remotes.append(sub[2])
        elif sub[:1] == ("push",):
            # ("push", <remote>, <branch>)
            if len(sub) >= 2:
                self.push_remotes.append(sub[1])
            self._push_attempts += 1
            if self._push_attempts <= self._push_fail_times:
                rc, stderr = 1, "! [rejected] non-fast-forward"

        if check and rc != 0:
            raise _sp.CalledProcessError(rc, cmd, stdout, stderr)
        return _sp.CompletedProcess(cmd, rc, stdout, stderr)


def _verbs(calls: list[tuple[str, ...]]) -> list[tuple[str, ...]]:
    """Reduce recorded calls to a comparable verb sequence.

    ``rev-parse`` (the remote-resolution query) is dropped — it is not part
    of the stash → pull → pop → commit → push flow under test.
    """
    out = []
    for c in calls:
        if c[:2] == ("rev-parse", "--abbrev-ref"):
            continue
        if c[:2] in {("stash", "push"), ("stash", "pop"), ("diff", "--cached"),
                     ("pull", "--rebase")}:
            out.append(c[:2])
        else:
            out.append(c[:1])
    return out


@pytest.fixture
def pushing_runner_factory(tmp_path):
    """Runner with pushing enabled and git patched out."""

    def _make(monkeypatch, fake: _FakeGit):
        with patch("scripts.experiments.runner.detect_worker_resources") as md:
            md.return_value = type(
                "WR", (), {"ram_bytes": 1024 * 1024 * 1024, "cpu_cores": 2}
            )()
            runner = ExperimentRunner(
                dry_run=False, no_push=False,
                manifest_dir=tmp_path / "manifests",
            )
        monkeypatch.setattr("scripts.experiments.runner.subprocess.run", fake)
        runner._active_manifest_path = None  # paths reduce to the workload subtree
        return runner

    return _make


def _smoke_exp():
    from scripts.experiments.experiment_matrix import build_smoke_experiments

    return {e.id: e for e in build_smoke_experiments()}["smoke_sysbench_rw"]


def test_commit_push_order_stash_pull_pop_commit_push(pushing_runner_factory, monkeypatch):
    """Happy path follows stash → pull → pop → add → diff → commit → push."""
    fake = _FakeGit()
    runner = pushing_runner_factory(monkeypatch, fake)

    runner._commit_and_push(_smoke_exp(), seed=42, phase="pbt")

    assert _verbs(fake.calls) == [
        ("stash", "push"),
        ("pull", "--rebase"),
        ("stash", "pop"),
        ("add",),
        ("diff", "--cached"),
        ("commit",),
        ("push",),
    ]


def test_commit_push_retries_on_rejection(pushing_runner_factory, monkeypatch):
    """A rejected push triggers a re-pull --rebase and a retry."""
    fake = _FakeGit(push_fail_times=1)
    runner = pushing_runner_factory(monkeypatch, fake)

    runner._commit_and_push(_smoke_exp(), seed=42, phase="bo")

    # Tail of the sequence: push (rejected) → pull --rebase → push (ok).
    assert _verbs(fake.calls)[-3:] == [("push",), ("pull", "--rebase"), ("push",)]
    assert fake._push_attempts == 2


def test_sync_skips_pop_when_nothing_stashed(pushing_runner_factory, monkeypatch):
    """When stash saves nothing, we must not attempt a stash pop."""
    fake = _FakeGit(stash_saved=False)
    runner = pushing_runner_factory(monkeypatch, fake)

    runner._commit_and_push(_smoke_exp(), seed=42, phase="eval")

    assert ("stash", "pop") not in fake.calls
    assert ("stash", "push", "--include-untracked", "-m", "pbtune-autostash") in fake.calls


@pytest.mark.parametrize("kwargs", [{"no_push": True}, {"dry_run": True}])
def test_commit_push_noop_when_disabled(tmp_path, monkeypatch, kwargs):
    """--no-push and --dry-run must issue zero git commands."""
    fake = _FakeGit()
    with patch("scripts.experiments.runner.detect_worker_resources") as md:
        md.return_value = type(
            "WR", (), {"ram_bytes": 1024 * 1024 * 1024, "cpu_cores": 2}
        )()
        runner = ExperimentRunner(manifest_dir=tmp_path / "manifests", **kwargs)
    monkeypatch.setattr("scripts.experiments.runner.subprocess.run", fake)

    runner._commit_and_push(_smoke_exp(), seed=42, phase="pbt")
    assert fake.calls == []


def test_push_uses_branch_upstream_remote(pushing_runner_factory, monkeypatch):
    """The push/pull target is the branch's upstream remote, not literal origin.

    Regression: this repo's results clone names its remote ``main`` (not
    ``origin``), so a hardcoded ``git push origin main`` silently failed.
    """
    fake = _FakeGit(upstream="main/main")  # remote is named "main"
    runner = pushing_runner_factory(monkeypatch, fake)

    runner._commit_and_push(_smoke_exp(), seed=42, phase="pbt")

    assert fake.push_remotes == ["main"]
    assert fake.pull_remotes == ["main"]  # the sync pull also uses it


def test_push_falls_back_to_origin_without_upstream(pushing_runner_factory, monkeypatch):
    """With no configured upstream, the resolver falls back to origin."""
    fake = _FakeGit(upstream=None)
    runner = pushing_runner_factory(monkeypatch, fake)

    runner._commit_and_push(_smoke_exp(), seed=42, phase="pbt")

    assert fake.push_remotes == ["origin"]
    assert fake.pull_remotes == ["origin"]


# ── _preflight_disk_isolation: fail-fast Disk-IO parity guard ────────


def _build_runner(monkeypatch, *, dry_run: bool):
    """ExperimentRunner with the hardware probe stubbed, for guard tests."""
    with patch("scripts.experiments.runner.detect_worker_resources") as md:
        md.return_value = type(
            "WR", (), {"ram_bytes": 1024 * 1024 * 1024, "cpu_cores": 2}
        )()
        return ExperimentRunner(dry_run=dry_run, no_push=True)


def test_preflight_raises_when_block_device_unresolved(monkeypatch):
    """Non-dry run + unresolvable block device → hard stop, so a
    multi-hour run never proceeds without enforceable Disk-IO limits."""
    runner = _build_runner(monkeypatch, dry_run=False)
    monkeypatch.setattr(
        "scripts.experiments.runner.resolve_data_root", lambda *a, **k: Path("/tmp/x")
    )
    monkeypatch.setattr(
        "scripts.experiments.runner._resolve_block_device_node", lambda *a, **k: None
    )
    with pytest.raises(RuntimeError, match="Disk-IO isolation preflight FAILED"):
        runner._preflight_disk_isolation()


def test_preflight_passes_when_block_device_resolves(monkeypatch):
    """A resolvable block device → no raise (limits will be enforced)."""
    runner = _build_runner(monkeypatch, dry_run=False)
    monkeypatch.setattr(
        "scripts.experiments.runner.resolve_data_root", lambda *a, **k: Path("/tmp/x")
    )
    monkeypatch.setattr(
        "scripts.experiments.runner._resolve_block_device_node",
        lambda *a, **k: "/dev/sda",
    )
    runner._preflight_disk_isolation()  # must not raise


def test_preflight_skipped_in_dry_run(monkeypatch):
    """dry_run never launches real workers, so the guard is a no-op even
    when the device cannot be resolved (and must not call the resolver)."""
    runner = _build_runner(monkeypatch, dry_run=True)

    def _boom(*a, **k):  # pragma: no cover - must never be called
        raise AssertionError("resolver must not run under dry_run")

    monkeypatch.setattr(
        "scripts.experiments.runner._resolve_block_device_node", _boom
    )
    runner._preflight_disk_isolation()  # must not raise or call resolver
