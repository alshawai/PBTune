"""Tests for the ``--all-workloads`` discovery helper in ``analyze_knob_importance``."""

from __future__ import annotations

import json
from pathlib import Path

from src.scripts.analyze_knob_importance import _discover_workloads


def _stage(root: Path, workload: str, tier: str, has_results: bool) -> Path:
    """Create the canonical results layout for a single workload+tier."""
    traces_dir = root / "sessions" / workload / "pbt" / tier / "traces"
    traces_dir.mkdir(parents=True)
    if has_results:
        (traces_dir / "pbt_results_smoke.json").write_text(json.dumps({}))
    return traces_dir


def test_discover_iterates_canonical_layout(tmp_path: Path):
    a = _stage(tmp_path, "oltp_read_write", "extensive", has_results=True)
    b = _stage(tmp_path, "olap", "extensive", has_results=True)
    discovered = _discover_workloads(
        tmp_path, glob_pattern="sessions/*/pbt/extensive/traces"
    )
    labels = sorted(label for label, _ in discovered)
    paths = sorted(path for _, path in discovered)
    assert labels == ["olap", "oltp_read_write"]
    assert sorted([a, b]) == paths


def test_discover_skips_empty_session_dirs(tmp_path: Path):
    _stage(tmp_path, "oltp_read_write", "extensive", has_results=True)
    _stage(tmp_path, "olap", "extensive", has_results=False)
    discovered = _discover_workloads(
        tmp_path, glob_pattern="sessions/*/pbt/extensive/traces"
    )
    assert [label for label, _ in discovered] == ["oltp_read_write"]


def test_discover_returns_empty_when_root_missing(tmp_path: Path):
    discovered = _discover_workloads(
        tmp_path / "no_such_root",
        glob_pattern="sessions/*/pbt/extensive/traces",
    )
    assert discovered == []
