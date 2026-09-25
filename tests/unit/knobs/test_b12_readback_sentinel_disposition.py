# Copyright (C) 2026 Ibrahim Al-Shawa and PBTune contributors
# Licensed under the GNU General Public License v3.0
# See LICENSE file for details

"""B12 read-back-merge disposition, settled at the knob-space seam (ticket #167).

B12 concern: ``orchestrator._verify_and_capture_config`` runs
``worker.knob_config.update(verification.db_config)``, persisting the values
PostgreSQL reports back. For an auto-size knob whose request is a sentinel
(``wal_buffers = -1``, ``*_buffers = 0``) PostgreSQL reports the *resolved*
concrete value, so the merge could destroy the sentinel and ratchet the knob
across generations.

Finding (disposition (b) — neutralised upstream): the tuned sentinel knobs
have bounds that clamp their auto-sentinels to concrete in-domain values at
normalization time, BEFORE any evaluation or read-back. ``wal_buffers`` (min
64) and the ``*_buffers`` family (min 16, step 16) therefore never present a
sentinel to the merge — the merge only ever sees, and re-persists, an already
concrete value, which is stable. This matches the empirical #163 result of
zero cross-generation ratchets across 73 clean worker-generations, so B12 is
DROPPED (not a live defect). See ADR-009 for the "-1 means auto-tune" hazard
that would apply to any FUTURE code persisting a read-back over a sentinel.

The expected concrete floors below (64, 16) are the shipped tier bounds, a
known-good source of truth, not values recomputed the way the code computes
them.
"""

from src.knobs.knob_loader import load_knob_space_from_csv

EXTENSIVE = "data/expert_defined_knobs/extensive_knobs.csv"


def test_wal_buffers_auto_sentinel_is_clamped_to_concrete_floor():
    """wal_buffers=-1 (auto) normalizes to its concrete min, not the sentinel."""
    space = load_knob_space_from_csv(EXTENSIVE)
    knob = space.knobs["wal_buffers"]
    assert knob.default == -1  # raw auto sentinel from PostgreSQL
    assert knob.min_value == 64
    assert knob.normalize_value(-1) == 64


def test_buffer_family_zero_sentinel_is_clamped_to_step_floor():
    """*_buffers=0 (auto) normalizes up to the aligned min (16), not 0."""
    space = load_knob_space_from_csv(EXTENSIVE)
    for name in (
        "commit_timestamp_buffers",
        "subtransaction_buffers",
        "transaction_buffers",
    ):
        knob = space.knobs[name]
        assert knob.min_value == 16
        assert knob.normalize_value(0) == 16


def test_sentinel_does_not_survive_repair_to_reach_readback_merge():
    """A config carrying auto sentinels is repaired to concrete values.

    ``repair_config_dependencies`` is the last knob-space stage before a
    worker's config is applied and (later) read back; it must emit concrete,
    in-bounds values so no sentinel ever reaches the orchestrator merge.
    """
    space = load_knob_space_from_csv(EXTENSIVE)
    repaired = space.repair_config_dependencies(
        {
            "wal_buffers": -1,
            "commit_timestamp_buffers": 0,
            "transaction_buffers": 0,
        },
        quiet=True,
    )
    assert repaired["wal_buffers"] == 64
    assert repaired["commit_timestamp_buffers"] == 16
    assert repaired["transaction_buffers"] == 16
