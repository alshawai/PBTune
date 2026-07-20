"""Regression tests for per-experiment manifest plumbing in ExperimentRunner."""

from __future__ import annotations

import json
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


def test_paths_to_stage_scoped_to_experiment(runner_factory, tmp_path):
    """git pathspecs cover the active manifest + workload subtree only.

    The experiment id (``t3_exploit_020``) is a label, not a directory
    name. The result subtree is workload-keyed
    (``oltp/<sysbench_workload>`` for sysbench, ``olap`` for TPC-H),
    matching the on-disk convention every writer in src/ uses.
    Staging ``exp.id`` directly (the previous behavior) caused
    ``fatal: pathspec '<exp.id>' did not match any files``.
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
    paths = runner._paths_to_stage(exp)
    assert "oltp/oltp_read_write" in paths
    # Experiment id must NOT be staged as a path — it's a label.
    assert exp.id not in paths
    for p in paths:
        assert "experiment_manifest.json" not in p, (
            "Per-experiment paths must not include the legacy global file"
        )


def test_paths_to_stage_tpch_maps_to_olap(runner_factory, tmp_path):
    """TPC-H experiments land under ``results/olap/...`` on disk."""
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
    paths = runner._paths_to_stage(exp)
    assert "olap" in paths
    assert exp.id not in paths


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
