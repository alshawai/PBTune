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
    """git pathspecs cover the active manifest + experiment subtree only."""
    runner = runner_factory()
    runner._active_manifest_path = runner._resolve_manifest_path("t3_exploit_020")
    paths = runner._paths_to_stage("t3_exploit_020")
    assert "t3_exploit_020" in paths
    # The manifest path is rendered relative to RESULTS_DIR when possible;
    # tmp_path lives outside RESULTS_DIR, so it gets dropped — only the
    # experiment subtree remains. That is the correct, conservative
    # behavior (git-add still picks up the result subtree, manifest
    # commit is handled by a separate flow when scoped outside).
    for p in paths:
        assert "experiment_manifest.json" not in p, (
            "Per-experiment paths must not include the legacy global file"
        )


def test_default_manifest_dir_is_under_results():
    """The default manifest dir lives under results/ so it lands in the
    same git-tracked tree the runner already commits to."""
    assert DEFAULT_MANIFEST_DIR.name == "manifests"
    assert DEFAULT_MANIFEST_DIR.parent.name == "results"
    # Legacy single-file path still exists as a constant for back-compat.
    assert LEGACY_MANIFEST_PATH.name == "experiment_manifest.json"
