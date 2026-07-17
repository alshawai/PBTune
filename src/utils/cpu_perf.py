"""
CPU Performance State Control
=============================

Reproducible per-core throughput for fair benchmarking.

When PBT runs N workers in parallel and the BO baseline runs (even under matched
co-tenancy), per-core delivered performance must not silently vary with how many
cores are active. The two confounds this module removes are:

1. **CPU frequency scaling** — the ``ondemand``/``schedutil``/``powersave``
   governors clock cores down when load is low, so a lightly-loaded instance
   runs faster per-core than a heavily-loaded one for reasons unrelated to the
   knob configuration. Pinning the ``performance`` governor holds clocks high.

2. **Turbo / boost** — single-/few-core turbo lets an under-subscribed host hit
   higher clocks than a saturated one. Disabling turbo makes the per-core clock
   ceiling independent of how many cores are busy.

The control is **save → set → restore**: :func:`read_cpu_perf_state` snapshots
the host's current governor/turbo, :func:`set_performance_mode` applies the
benchmarking state, and :func:`restore_cpu_perf_state` returns the host to
*exactly* its original state. Restore is best-effort and never raises — a failed
restore is logged loudly but must not mask the experiment's own outcome.

Linux-only (sysfs). On non-Linux or when sysfs is unwritable (no root /
unsupported), every function degrades gracefully: reads return ``supported=False``
and set/restore become no-ops that log a warning.

Sysfs paths
-----------
- Governor (per CPU): ``/sys/devices/system/cpu/cpu<N>/cpufreq/scaling_governor``
- Intel turbo (inverted): ``/sys/devices/system/cpu/intel_pstate/no_turbo``
  (``1`` == turbo disabled)
- Generic/AMD boost: ``/sys/devices/system/cpu/cpufreq/boost``
  (``1`` == boost enabled)
- Current freq (per CPU): ``/sys/devices/system/cpu/cpu<N>/cpufreq/scaling_cur_freq``
"""

from __future__ import annotations

import glob
import os
import platform
import shlex
from dataclasses import dataclass, field
from pathlib import Path
from typing import Dict, List, Optional, Tuple

from src.utils.logger import get_logger

LOGGER = get_logger("CPUPerf")

_CPU_ROOT = Path("/sys/devices/system/cpu")
_INTEL_NO_TURBO = _CPU_ROOT / "intel_pstate" / "no_turbo"
_GENERIC_BOOST = _CPU_ROOT / "cpufreq" / "boost"

_PERFORMANCE_GOVERNOR = "performance"


@dataclass
class CPUPerfState:
    """Snapshot of host CPU performance state, sufficient to restore it.

    Attributes
    ----------
    supported:
        Whether this host exposes a controllable governor/turbo interface.
        When ``False`` the other fields are best-effort/empty and set/restore
        are no-ops.
    governors:
        Map of ``cpu<N>`` → governor string at snapshot time.
    turbo_enabled:
        Whether turbo/boost was enabled at snapshot time, or ``None`` if the
        host exposes no turbo control.
    turbo_mechanism:
        ``"intel_pstate"`` | ``"boost"`` | ``None`` — which knob drives turbo.
    cur_freqs_khz:
        Current per-CPU frequency (kHz) at snapshot time, for the session
        metadata record (not used by restore).
    """

    supported: bool
    governors: Dict[str, str] = field(default_factory=dict)
    turbo_enabled: Optional[bool] = None
    turbo_mechanism: Optional[str] = None
    cur_freqs_khz: List[int] = field(default_factory=list)

    def to_metadata(self) -> Dict[str, object]:
        """Serialise for the session-environment JSON record."""
        unique_governors = sorted(set(self.governors.values()))
        return {
            "supported": self.supported,
            "governors": unique_governors,
            "governor_uniform": len(unique_governors) <= 1,
            "turbo_enabled": self.turbo_enabled,
            "turbo_mechanism": self.turbo_mechanism,
            "cur_freq_khz_min": min(self.cur_freqs_khz) if self.cur_freqs_khz else None,
            "cur_freq_khz_max": max(self.cur_freqs_khz) if self.cur_freqs_khz else None,
        }


def _governor_paths() -> List[Path]:
    return [
        Path(p)
        for p in glob.glob(str(_CPU_ROOT / "cpu[0-9]*" / "cpufreq" / "scaling_governor"))
    ]


def _cur_freq_paths() -> List[Path]:
    return [
        Path(p)
        for p in glob.glob(str(_CPU_ROOT / "cpu[0-9]*" / "cpufreq" / "scaling_cur_freq"))
    ]


def _read_text(path: Path) -> Optional[str]:
    try:
        return path.read_text().strip()
    except (OSError, ValueError):
        return None


def _write_text(path: Path, value: str) -> bool:
    """Direct sysfs write (works when running as root). Returns True on success."""
    try:
        path.write_text(value)
        return True
    except (OSError, ValueError) as exc:
        LOGGER.debug("sysfs write failed (%s ← %r): %s", path, value, exc)
        return False


def _cpu_id_from_governor_path(path: Path) -> str:
    # .../cpu/cpu7/cpufreq/scaling_governor -> "cpu7"
    return path.parent.parent.name


def _direct_write_all(writes: Dict[str, str]) -> bool:
    """Try to write all sysfs values directly. Returns True only if all succeed."""
    for path_str, value in writes.items():
        if not _write_text(Path(path_str), value):
            return False
    return True


def _sudo_write_batch(writes: Dict[str, str]) -> bool:
    """Write multiple sysfs values in a single ``sudo sh -c`` invocation.

    Batching all writes into one ``sudo`` call means the user is prompted for
    their password exactly once. Uses ``os.system`` so the child process gets
    full terminal control for password entry — ``subprocess.run`` inherits
    Python's stdio which can corrupt interactive password input.
    """
    if not writes:
        return True
    lines = []
    for path_str, value in writes.items():
        lines.append(
            f"printf %s {shlex.quote(value)} > {shlex.quote(path_str)}"
        )
    script = " && ".join(lines)
    ret = os.system(f"sudo sh -c {shlex.quote(script)}")
    ok = (ret == 0)
    if ok:
        LOGGER.debug("sudo batch write succeeded for %d sysfs path(s)", len(writes))
    else:
        LOGGER.debug("sudo batch write failed (exit %d) for %d path(s)", ret, len(writes))
    return ok


def _collect_perf_writes(saved: CPUPerfState) -> Dict[str, str]:
    """Build the sysfs write set for performance mode."""
    writes: Dict[str, str] = {}
    for cpu_id in saved.governors:
        gov_path = str(_CPU_ROOT / cpu_id / "cpufreq" / "scaling_governor")
        writes[gov_path] = _PERFORMANCE_GOVERNOR
    if saved.turbo_mechanism == "intel_pstate":
        writes[str(_INTEL_NO_TURBO)] = "1"
    elif saved.turbo_mechanism == "boost":
        writes[str(_GENERIC_BOOST)] = "0"
    return writes


def _collect_restore_writes(saved: CPUPerfState) -> Dict[str, str]:
    """Build the sysfs write set that restores the snapshot."""
    writes: Dict[str, str] = {}
    for cpu_id, gov in saved.governors.items():
        gov_path = str(_CPU_ROOT / cpu_id / "cpufreq" / "scaling_governor")
        writes[gov_path] = gov
    if saved.turbo_enabled is not None:
        if saved.turbo_mechanism == "intel_pstate":
            writes[str(_INTEL_NO_TURBO)] = "0" if saved.turbo_enabled else "1"
        elif saved.turbo_mechanism == "boost":
            writes[str(_GENERIC_BOOST)] = "1" if saved.turbo_enabled else "0"
    return writes


def _apply_writes(writes: Dict[str, str]) -> Tuple[bool, str]:
    """Try direct writes first; fall back to a single sudo call.

    Returns ``(success, method)`` where *method* is ``"direct"`` or ``"sudo"``.
    """
    if _direct_write_all(writes):
        return True, "direct"
    if _sudo_write_batch(writes):
        return True, "sudo"
    return False, "failed"


def read_cpu_perf_state() -> CPUPerfState:
    """Snapshot the current CPU governor, turbo, and per-core frequencies.

    Never raises. On a host without the sysfs interface (non-Linux, container
    without the cpufreq subsystem, etc.) returns ``CPUPerfState(supported=False)``.
    """
    if platform.system() != "Linux":
        return CPUPerfState(supported=False)

    gov_paths = _governor_paths()
    if not gov_paths:
        LOGGER.debug("No cpufreq scaling_governor entries; CPU perf control unavailable")
        return CPUPerfState(supported=False)

    governors: Dict[str, str] = {}
    for p in gov_paths:
        val = _read_text(p)
        if val is not None:
            governors[_cpu_id_from_governor_path(p)] = val

    # Turbo: prefer Intel's no_turbo (inverted), fall back to generic boost.
    turbo_enabled: Optional[bool] = None
    turbo_mechanism: Optional[str] = None
    no_turbo = _read_text(_INTEL_NO_TURBO)
    if no_turbo is not None:
        turbo_mechanism = "intel_pstate"
        turbo_enabled = no_turbo.strip() == "0"
    else:
        boost = _read_text(_GENERIC_BOOST)
        if boost is not None:
            turbo_mechanism = "boost"
            turbo_enabled = boost.strip() == "1"

    cur_freqs: List[int] = []
    for cur_path in _cur_freq_paths():
        val = _read_text(cur_path)
        if val and val.isdigit():
            cur_freqs.append(int(val))

    return CPUPerfState(
        supported=bool(governors),
        governors=governors,
        turbo_enabled=turbo_enabled,
        turbo_mechanism=turbo_mechanism,
        cur_freqs_khz=cur_freqs,
    )


def set_performance_mode(saved: CPUPerfState) -> bool:
    """Pin the ``performance`` governor on all CPUs and disable turbo.

    Tries direct sysfs writes first (works as root). On failure, falls back to
    a single ``sudo sh -c '...'`` that batches all writes — one password prompt
    covers everything. Returns ``True`` only if every write succeeded.
    """
    if not saved.supported:
        LOGGER.warning(
            "CPU performance control unsupported on this host; skipping "
            "governor/turbo pinning (per-core throughput may vary with load)."
        )
        return False

    writes = _collect_perf_writes(saved)
    ok, method = _apply_writes(writes)

    gov_count = sum(1 for k in writes if k.endswith("scaling_governor"))
    if ok:
        LOGGER.info(
            "Pinned '%s' governor on %d CPU(s)%s [via %s]",
            _PERFORMANCE_GOVERNOR,
            gov_count,
            "; turbo disabled" if saved.turbo_mechanism else "",
            method,
        )
    else:
        LOGGER.warning(
            "CPU performance pinning failed (need root or sudo to write "
            "/sys/devices/system/cpu/*). Per-core throughput may vary with load."
        )
    return ok


def restore_cpu_perf_state(saved: CPUPerfState) -> None:
    """Restore the host to ``saved`` exactly. Best-effort; never raises.

    Returns the governor of every CPU and the turbo state to what they were
    when :func:`read_cpu_perf_state` was called. A failure to restore is logged
    at WARNING (it leaves the host mutated) but never propagates, so it cannot
    mask the experiment's own result or exit path.
    """
    if not saved.supported:
        return

    writes = _collect_restore_writes(saved)
    ok, _method = _apply_writes(writes)

    if not ok:
        LOGGER.warning(
            "CPU perf restore failed. Host may remain in 'performance' mode "
            "— restore manually with: cpupower frequency-set -g <governor>.",
        )
    else:
        LOGGER.info(
            "Restored original CPU governor/turbo state on %d CPU(s).",
            len(saved.governors),
        )
