"""Regression tests for the fio-probe sanity floor.

A short fio probe can land entirely inside a burst-credit-starved or
cold-cache window on cloud-attached block storage (GCP Persistent Disk,
AWS EBS gp3), returning a few percent of the disk's actual sustained
capacity. Before the trust floor was added, that tiny probe became the
session-long host budget — every worker was throttled to <1 MB/s for
the rest of the run.

These tests pin the two halves of the defence:

1. Probe results below ``_DISK_PROBE_TRUST_FLOOR`` of the disk-class
   heuristic are rejected; the heuristic is used instead.
2. Probe results at or above the floor are kept verbatim.

Extended fio runtimes (8s write, 5s read vs the original 3s/2s) make
the rejected branch rarer in practice but cannot eliminate it on
freshly attached cloud volumes — the validator must still hold the
line.
"""

from __future__ import annotations

from unittest.mock import patch


from src.utils.hardware_info import (
    _DISK_CLASS_BUDGETS,
    _DISK_PROBE_IOPS_SANITY_MIN,
    _DISK_PROBE_TRUST_FLOOR,
    _probe_passes_trust_floor,
    _resolve_host_disk_budget,
)


# ── _probe_passes_trust_floor pure-function semantics ──────────────


def test_probe_passes_when_above_floor():
    """Probe at 50% of heuristic on every field passes (BPS floor is 25%,
    and 50% of the heuristic IOPS clears the absolute sanity floor)."""
    heuristic = _DISK_CLASS_BUDGETS["sata_ssd"]
    probed = {k: int(v * 0.5) for k, v in heuristic.items()}
    passes, failed = _probe_passes_trust_floor(probed, heuristic)
    assert passes is True
    assert failed == []


def test_probe_passes_exactly_at_floor():
    """At exactly the BPS floor, we accept. The check is strict-less-than."""
    heuristic = _DISK_CLASS_BUDGETS["sata_ssd"]
    probed = {k: int(v * _DISK_PROBE_TRUST_FLOOR) for k, v in heuristic.items()}
    passes, failed = _probe_passes_trust_floor(probed, heuristic)
    assert passes is True


def test_probe_fails_below_floor_on_any_field():
    """One BPS field below the relative floor rejects the whole probe — a
    60× under-read on read_bps is the GCP PD pathology this defends against."""
    heuristic = _DISK_CLASS_BUDGETS["sata_ssd"]
    probed = dict(heuristic)
    probed["read_bps"] = int(heuristic["read_bps"] * 0.10)  # 10% << 25%
    passes, failed = _probe_passes_trust_floor(probed, heuristic)
    assert passes is False
    assert "read_bps" in failed
    # Other fields are still healthy; only the suspicious one is listed.
    assert "write_bps" not in failed


def test_probe_iops_judged_absolutely_not_relatively():
    """IOPS are NOT judged against the (unreachable high-QD) heuristic
    ceilings. A read_iops at 10% of the 80k heuristic (8000 IOPS) is a
    perfectly real SSD measurement and must pass — only the below-floor
    BPS field should be listed."""
    heuristic = _DISK_CLASS_BUDGETS["sata_ssd"]
    probed = dict(heuristic)
    probed["read_bps"] = int(heuristic["read_bps"] * 0.05)   # below BPS floor
    probed["read_iops"] = int(heuristic["read_iops"] * 0.10)  # 8000 IOPS — fine
    passes, failed = _probe_passes_trust_floor(probed, heuristic)
    assert passes is False
    assert set(failed) == {"read_bps"}


def test_probe_rejects_iops_below_absolute_floor():
    """An IOPS reading below the absolute sanity floor (broken probe /
    O_DIRECT rejected) is rejected even when bandwidth looks healthy."""
    heuristic = _DISK_CLASS_BUDGETS["sata_ssd"]
    probed = dict(heuristic)
    probed["read_iops"] = _DISK_PROBE_IOPS_SANITY_MIN - 1
    probed["write_iops"] = _DISK_PROBE_IOPS_SANITY_MIN - 1
    passes, failed = _probe_passes_trust_floor(probed, heuristic)
    assert passes is False
    assert set(failed) == {"read_iops", "write_iops"}


def test_probe_accepts_real_ssd_profile():
    """The bug report profile: a real SATA SSD delivering healthy
    sequential bandwidth and tens-of-thousands of IOPS (well below the
    80k/60k heuristic ceilings) must be ACCEPTED, where the old
    relative-IOPS floor rejected every such probe."""
    heuristic = _DISK_CLASS_BUDGETS["sata_ssd"]
    probed = {
        "read_bps": int(450 * 1024 * 1024),
        "write_bps": int(300 * 1024 * 1024),
        "read_iops": 40_000,
        "write_iops": 20_000,
    }
    passes, failed = _probe_passes_trust_floor(probed, heuristic)
    assert passes is True
    assert failed == []


# ── _resolve_host_disk_budget end-to-end behavior ──────────────────


@patch("src.utils.hardware_info._detect_disk_class", return_value="sata_ssd")
@patch("src.utils.hardware_info._resolve_block_device_node", return_value="/dev/sda")
@patch("src.utils.hardware_info._probe_disk_with_fio")
def test_resolve_keeps_plausible_probe(mock_probe, _mock_dev, _mock_class, tmp_path):
    """A probe that comes in at ~80% of heuristic is real-world plausible
    and must NOT be overridden by the class heuristic."""
    heuristic = _DISK_CLASS_BUDGETS["sata_ssd"]
    plausible = {k: int(v * 0.8) for k, v in heuristic.items()}
    mock_probe.return_value = plausible

    budget, disk_class = _resolve_host_disk_budget(tmp_path, probe_disk=True)
    assert disk_class == "sata_ssd"
    assert budget["read_bps"] == plausible["read_bps"]
    assert budget != heuristic


@patch("src.utils.hardware_info._detect_disk_class", return_value="sata_ssd")
@patch("src.utils.hardware_info._resolve_block_device_node", return_value="/dev/sda")
@patch("src.utils.hardware_info._probe_disk_with_fio")
def test_resolve_clamps_probe_above_ceiling(mock_probe, _mock_dev, _mock_class, tmp_path):
    """A probe that reports MORE than the class heuristic (warmed-up
    volume, brief burst) must be clamped down to the ceiling so a
    per-worker budget can never exceed sustained class capacity."""
    heuristic = _DISK_CLASS_BUDGETS["sata_ssd"]
    mock_probe.return_value = {k: v * 3 for k, v in heuristic.items()}

    budget, disk_class = _resolve_host_disk_budget(tmp_path, probe_disk=True)
    assert disk_class == "sata_ssd"
    # Every field is capped at the heuristic ceiling.
    assert budget == dict(heuristic)


@patch("src.utils.hardware_info._detect_disk_class", return_value="sata_ssd")
@patch("src.utils.hardware_info._resolve_block_device_node", return_value="/dev/sda")
@patch("src.utils.hardware_info._probe_disk_with_fio")
def test_resolve_rejects_pathological_probe(mock_probe, _mock_dev, _mock_class, tmp_path):
    """The exact GCP PD pathology: probe returns 2.1 MB/s read on a
    SATA-SSD-class device (heuristic = 500 MB/s, so 0.4% of expected).
    Must fall back to the heuristic and never propagate the bad numbers."""
    mock_probe.return_value = {
        "read_bps": int(2.1 * 1024 * 1024),    # 2.1 MB/s — observed in t1 logs
        "write_bps": int(190 * 1024 * 1024),
        "read_iops": 539,                      # well below 80_000 floor
        "write_iops": 189,
    }

    budget, disk_class = _resolve_host_disk_budget(tmp_path, probe_disk=True)
    assert disk_class == "sata_ssd"
    # Heuristic is the source of truth when the probe is implausible.
    assert budget == _DISK_CLASS_BUDGETS["sata_ssd"]


@patch("src.utils.hardware_info._detect_disk_class", return_value="sata_ssd")
@patch("src.utils.hardware_info._resolve_block_device_node", return_value="/dev/sda")
@patch("src.utils.hardware_info._probe_disk_with_fio", return_value=None)
def test_resolve_uses_heuristic_when_probe_unavailable(
    _mock_probe, _mock_dev, _mock_class, tmp_path
):
    """fio missing from PATH → probe returns None → heuristic must be used."""
    budget, disk_class = _resolve_host_disk_budget(tmp_path, probe_disk=True)
    assert disk_class == "sata_ssd"
    assert budget == _DISK_CLASS_BUDGETS["sata_ssd"]


@patch("src.utils.hardware_info._detect_disk_class", return_value="sata_ssd")
@patch("src.utils.hardware_info._resolve_block_device_node", return_value="/dev/sda")
@patch("src.utils.hardware_info._probe_disk_with_fio")
def test_resolve_skips_probe_when_disabled(mock_probe, _mock_dev, _mock_class, tmp_path):
    """probe_disk=False must never call fio, regardless of its output."""
    mock_probe.return_value = {
        "read_bps": 100 * 1024 * 1024 * 1024,  # absurdly high
        "write_bps": 100 * 1024 * 1024 * 1024,
        "read_iops": 10_000_000,
        "write_iops": 10_000_000,
    }
    budget, disk_class = _resolve_host_disk_budget(tmp_path, probe_disk=False)
    assert budget == _DISK_CLASS_BUDGETS["sata_ssd"]
    mock_probe.assert_not_called()


# ── Probe runtime documentation ─────────────────────────────────────


def test_fio_probe_runtimes_outlast_cloud_burst_credit_window():
    """The probe must use ``--runtime=8`` for write and ``--runtime=5``
    for read. Short 2-3s windows used historically were prone to
    landing inside the GCP PD burst-credit-starved window.

    This is a structural test against the source — runtime values are
    hardcoded fio CLI flags, not parameters, so an AST-level check is
    the right tool.
    """
    import ast
    from pathlib import Path

    src = Path(__file__).resolve().parents[3] / "src" / "utils" / "hardware_info.py"
    tree = ast.parse(src.read_text())
    runtimes_found: list[str] = []
    for node in ast.walk(tree):
        if isinstance(node, ast.Constant) and isinstance(node.value, str):
            if node.value.startswith("--runtime="):
                runtimes_found.append(node.value)

    assert "--runtime=8" in runtimes_found, (
        "Write probe must use --runtime=8 to outlast cloud-PD "
        "burst-credit starvation window"
    )
    assert "--runtime=5" in runtimes_found, (
        "Read probe must use --runtime=5 for the same reason"
    )
    # Guard against future shortening: no <=3s runtime should reappear.
    for rt in runtimes_found:
        seconds = int(rt.split("=")[1])
        assert seconds >= 5, (
            f"Probe runtime {rt!r} is too short; cloud-PD burst credits "
            "can mask sustained throughput for the first 5-10s after attach"
        )
