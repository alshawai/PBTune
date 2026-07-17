"""Unit tests for src.utils.cpu_perf — governor/turbo save→set→restore.

These exercise the pure control logic with sysfs reads/writes faked, so they
run on any host (including CI without cpufreq sysfs). The contract under test:

- ``read_cpu_perf_state`` snapshots governors + turbo.
- ``set_performance_mode`` writes ``performance`` to every governor and disables
  turbo via the detected mechanism.
- ``restore_cpu_perf_state`` returns every governor + turbo to the snapshot and
  never raises.
"""

from __future__ import annotations

from pathlib import Path

import pytest

import src.utils.cpu_perf as cpu_perf
from src.utils.cpu_perf import (
    CPUPerfState,
    read_cpu_perf_state,
    restore_cpu_perf_state,
    set_performance_mode,
)


class _FakeSysfs:
    """In-memory stand-in for the cpufreq sysfs tree."""

    def __init__(self, cpus, governor="ondemand", no_turbo="0"):
        # Map of governor path -> value
        self.values: dict[str, str] = {}
        self.cpus = cpus
        for n in cpus:
            self.values[
                f"/sys/devices/system/cpu/cpu{n}/cpufreq/scaling_governor"
            ] = governor
            self.values[
                f"/sys/devices/system/cpu/cpu{n}/cpufreq/scaling_cur_freq"
            ] = "2400000"
        self.values["/sys/devices/system/cpu/intel_pstate/no_turbo"] = no_turbo
        self.write_failures: set[str] = set()

    def governor_paths(self):
        return [
            Path(p)
            for p in self.values
            if p.endswith("scaling_governor")
        ]

    def cur_freq_paths(self):
        return [
            Path(p)
            for p in self.values
            if p.endswith("scaling_cur_freq")
        ]

    def read(self, path: Path):
        return self.values.get(str(path))

    def write(self, path: Path, value: str) -> bool:
        key = str(path)
        if key in self.write_failures:
            return False
        self.values[key] = value
        return True


@pytest.fixture
def fake_sysfs(monkeypatch):
    fs = _FakeSysfs(cpus=range(4), governor="ondemand", no_turbo="0")
    monkeypatch.setattr(cpu_perf.platform, "system", lambda: "Linux")
    monkeypatch.setattr(cpu_perf, "_governor_paths", fs.governor_paths)
    monkeypatch.setattr(cpu_perf, "_cur_freq_paths", fs.cur_freq_paths)
    monkeypatch.setattr(cpu_perf, "_read_text", fs.read)
    monkeypatch.setattr(cpu_perf, "_write_text", fs.write)
    # Fake _direct_write_all to use our in-memory store.
    def _fake_direct_write_all(writes):
        for path_str, value in writes.items():
            if not fs.write(Path(path_str), value):
                return False
        return True
    monkeypatch.setattr(cpu_perf, "_direct_write_all", _fake_direct_write_all)
    # sudo path should never be reached in tests.
    monkeypatch.setattr(cpu_perf, "_sudo_write_batch", lambda writes: False)
    # Point the turbo path constant at our fake key so reads/writes resolve.
    monkeypatch.setattr(
        cpu_perf, "_INTEL_NO_TURBO", Path("/sys/devices/system/cpu/intel_pstate/no_turbo")
    )
    return fs


def test_read_state_snapshots_governors_and_turbo(fake_sysfs):
    state = read_cpu_perf_state()
    assert state.supported is True
    assert set(state.governors.values()) == {"ondemand"}
    assert state.turbo_mechanism == "intel_pstate"
    assert state.turbo_enabled is True  # no_turbo == "0"
    assert len(state.cur_freqs_khz) == 4


def test_set_performance_mode_pins_governor_and_disables_turbo(fake_sysfs):
    saved = read_cpu_perf_state()
    ok = set_performance_mode(saved)
    assert ok is True
    govs = {
        v for k, v in fake_sysfs.values.items() if k.endswith("scaling_governor")
    }
    assert govs == {"performance"}
    # no_turbo flipped to "1" (turbo disabled).
    assert fake_sysfs.values["/sys/devices/system/cpu/intel_pstate/no_turbo"] == "1"


def test_restore_returns_exact_original_state(fake_sysfs):
    saved = read_cpu_perf_state()
    set_performance_mode(saved)
    restore_cpu_perf_state(saved)
    govs = {
        v for k, v in fake_sysfs.values.items() if k.endswith("scaling_governor")
    }
    assert govs == {"ondemand"}  # restored
    assert fake_sysfs.values["/sys/devices/system/cpu/intel_pstate/no_turbo"] == "0"


def test_restore_is_noop_and_silent_when_unsupported():
    # No fake_sysfs fixture → real read on a (possibly) non-Linux/no-sysfs host
    # is not used; construct an explicitly-unsupported state.
    state = CPUPerfState(supported=False)
    # Must not raise.
    restore_cpu_perf_state(state)
    assert set_performance_mode(state) is False


def test_set_partial_failure_reports_false_but_still_restorable(fake_sysfs):
    saved = read_cpu_perf_state()
    # Simulate one CPU's governor write failing (e.g. permission).
    fake_sysfs.write_failures.add(
        "/sys/devices/system/cpu/cpu2/cpufreq/scaling_governor"
    )
    # Direct writes fail (one path blocked), sudo batch also fails (mocked False).
    ok = set_performance_mode(saved)
    assert ok is False
    # Restore still drives every CPU back (remove the failure for restore);
    # the call does not raise.
    fake_sysfs.write_failures.clear()
    restore_cpu_perf_state(saved)
