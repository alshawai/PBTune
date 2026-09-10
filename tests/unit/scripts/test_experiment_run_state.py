"""Tests for crash-safe experiment campaign state primitives."""

from __future__ import annotations

import json

import pytest

from scripts.experiments.run_state import (
    CampaignFileLock,
    CampaignLockError,
    RunnerIdentity,
    atomic_write_json,
)


def test_campaign_lock_rejects_second_owner(tmp_path):
    """Only one runner may own the shared fleet lock at a time."""
    path = tmp_path / "campaign.lock"
    first = CampaignFileLock(path, RunnerIdentity.create())
    second = CampaignFileLock(path, RunnerIdentity.create())

    first.acquire()
    try:
        with pytest.raises(CampaignLockError, match="Another experiment runner"):
            second.acquire()
        recorded_owner = json.loads(path.read_text(encoding="utf-8"))
        assert recorded_owner["runner_id"] == first.owner.runner_id
        assert recorded_owner["pid"] == first.owner.pid
    finally:
        first.release()


def test_campaign_lock_can_be_acquired_after_release(tmp_path):
    """A later runner can take ownership after the first process releases it."""
    path = tmp_path / "campaign.lock"
    first = CampaignFileLock(path, RunnerIdentity.create())
    second = CampaignFileLock(path, RunnerIdentity.create())

    with first.held():
        pass
    with second.held():
        recorded_owner = json.loads(path.read_text(encoding="utf-8"))
        assert recorded_owner["runner_id"] == second.owner.runner_id


def test_atomic_write_json_replaces_complete_document(tmp_path):
    """Atomic writes leave a complete JSON document and no temporary files."""
    path = tmp_path / "manifest.json"
    path.write_text('{"old": true}\n', encoding="utf-8")

    atomic_write_json(path, {"new": {"status": "done"}})

    assert json.loads(path.read_text(encoding="utf-8")) == {
        "new": {"status": "done"}
    }
    assert list(tmp_path.glob(".manifest.json.*.tmp")) == []


def test_atomic_write_failure_preserves_previous_document(tmp_path, monkeypatch):
    """Serialization failure cannot truncate the last durable manifest."""
    path = tmp_path / "manifest.json"
    original = {"runs": {"phase": {"status": "done"}}}
    path.write_text(json.dumps(original), encoding="utf-8")

    def _fail_dump(payload, handle, indent):
        handle.write('{"partial":')
        raise TypeError("not serializable")

    monkeypatch.setattr("scripts.experiments.run_state.json.dump", _fail_dump)

    with pytest.raises(TypeError, match="not serializable"):
        atomic_write_json(path, {"bad": object()})

    assert json.loads(path.read_text(encoding="utf-8")) == original
    assert list(tmp_path.glob(".manifest.json.*.tmp")) == []
