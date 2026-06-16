"""Regression tests for the legacy → per-experiment manifest migration."""

from __future__ import annotations

import json
from pathlib import Path

from scripts.experiments.migrate_manifest import (
    _experiment_id_from_key,
    split_manifest,
)


def test_experiment_id_extraction():
    """Run keys are <exp_id>/seed_<n>/<phase>; we want the head."""
    assert _experiment_id_from_key("t3_exploit_020/seed_42/pbt") == "t3_exploit_020"
    assert _experiment_id_from_key("t1_pbt_oltp/seed_99/eval") == "t1_pbt_oltp"
    # Malformed keys return None so the caller can skip rather than crash.
    assert _experiment_id_from_key("") is None


def test_split_manifest_groups_by_experiment(tmp_path: Path):
    """Every legacy run key lands in its experiment-id-named manifest."""
    legacy_path = tmp_path / "experiment_manifest.json"
    manifest_dir = tmp_path / "manifests"
    legacy_path.write_text(
        json.dumps(
            {
                "started_at": "2026-01-01T00:00:00Z",
                "runs": {
                    "t3_exploit_020/seed_42/pbt": {"status": "done"},
                    "t3_exploit_020/seed_42/eval": {"status": "done"},
                    "t3_exploit_025/seed_42/pbt": {"status": "failed"},
                    "t1_pbt_oltp/seed_42/pbt": {"status": "done"},
                },
            }
        )
    )

    counts = split_manifest(legacy_path, manifest_dir)
    assert counts == {
        "t3_exploit_020": 2,
        "t3_exploit_025": 1,
        "t1_pbt_oltp": 1,
    }

    file_a = json.loads((manifest_dir / "t3_exploit_020.json").read_text())
    assert set(file_a["runs"].keys()) == {
        "t3_exploit_020/seed_42/pbt",
        "t3_exploit_020/seed_42/eval",
    }
    # started_at carried forward so the file isn't ambiguous about its origin.
    assert file_a["started_at"] == "2026-01-01T00:00:00Z"

    file_b = json.loads((manifest_dir / "t3_exploit_025.json").read_text())
    assert "t3_exploit_025/seed_42/pbt" in file_b["runs"]
    assert "t3_exploit_020/seed_42/pbt" not in file_b["runs"]


def test_split_manifest_idempotent(tmp_path: Path):
    """Re-running migration must not clobber existing per-experiment state.

    Peer machines may have written newer keys to the per-experiment file
    after the first migration; the second run should add only what's
    actually missing.
    """
    legacy_path = tmp_path / "experiment_manifest.json"
    manifest_dir = tmp_path / "manifests"
    manifest_dir.mkdir()

    # Pre-existing per-experiment manifest with newer state.
    (manifest_dir / "t3_exploit_020.json").write_text(
        json.dumps(
            {
                "started_at": "2026-02-01T00:00:00Z",
                "runs": {
                    # Already-done — must not be overwritten by the legacy
                    # entry (which marks it merely 'failed', say from an
                    # earlier broken run).
                    "t3_exploit_020/seed_42/pbt": {"status": "done", "duration_s": 120},
                },
            }
        )
    )

    legacy_path.write_text(
        json.dumps(
            {
                "started_at": "2026-01-01T00:00:00Z",
                "runs": {
                    "t3_exploit_020/seed_42/pbt": {"status": "failed"},
                    "t3_exploit_020/seed_99/pbt": {"status": "done"},  # genuinely new
                },
            }
        )
    )

    counts = split_manifest(legacy_path, manifest_dir)
    assert counts == {"t3_exploit_020": 1}

    file_a = json.loads((manifest_dir / "t3_exploit_020.json").read_text())
    # Existing entry preserved (still 'done', not overwritten to 'failed').
    assert file_a["runs"]["t3_exploit_020/seed_42/pbt"]["status"] == "done"
    assert file_a["runs"]["t3_exploit_020/seed_42/pbt"]["duration_s"] == 120
    # New entry added.
    assert "t3_exploit_020/seed_99/pbt" in file_a["runs"]


def test_split_manifest_dry_run_writes_nothing(tmp_path: Path):
    """--dry-run reports counts without touching the filesystem."""
    legacy_path = tmp_path / "experiment_manifest.json"
    manifest_dir = tmp_path / "manifests"
    legacy_path.write_text(
        json.dumps(
            {
                "started_at": "2026-01-01T00:00:00Z",
                "runs": {"t3_exploit_020/seed_42/pbt": {"status": "done"}},
            }
        )
    )

    counts = split_manifest(legacy_path, manifest_dir, dry_run=True)
    assert counts == {"t3_exploit_020": 1}
    # No file created when dry-running.
    assert not (manifest_dir / "t3_exploit_020.json").exists()


def test_split_manifest_handles_missing_legacy(tmp_path: Path):
    """No legacy file → no work to do, no error."""
    counts = split_manifest(tmp_path / "missing.json", tmp_path / "manifests")
    assert counts == {}
