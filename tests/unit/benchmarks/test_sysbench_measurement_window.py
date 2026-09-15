# Copyright (C) 2026 Ibrahim Al-Shawa and PBTune contributors
# Licensed under the GNU General Public License v3.0
# See LICENSE file for details

"""Tests for the sysbench measurement window on the command line.

The distributed measurement-window defect (issue #142) showed up as a sysbench
process invoked with ``--time=90`` for a run configured to measure 180 seconds.
Nothing asserted on the emitted argv, so the symptom itself was invisible to the
suite. These tests pin it directly.

They also document the current warmup semantics: sysbench is invoked with a
single ``--time`` covering warmup *and* measurement, and no ``--warmup-time``,
so the configured warmup seconds are part of the averaged throughput rather
than a discarded ramp-up. That is a deliberate protocol decision, not an
oversight — changing it would re-baseline every recorded sysbench result — so it
is asserted here to keep the choice explicit and consistent across tuning,
BO and evaluation.
"""

from __future__ import annotations

from unittest.mock import MagicMock, patch

import pytest

from src.benchmarks.sysbench.executor import SysbenchExecutor
from src.config.database import DatabaseConfig


@pytest.fixture
def db_config() -> DatabaseConfig:
    return DatabaseConfig(
        user="postgres",
        password="",
        host="127.0.0.1",
        port=5440,
        dbname="test_dataset",
    )


def _captured_argv(executor: SysbenchExecutor, **kwargs) -> list[str]:
    """Run ``_run_sysbench`` against a stubbed process and return its argv."""
    process = MagicMock()
    process.communicate.return_value = ("", "")
    process.returncode = 0

    with patch(
        "src.benchmarks.sysbench.executor.subprocess.Popen", return_value=process
    ) as popen:
        executor._run_sysbench(**kwargs)

    return list(popen.call_args.args[0])


def _time_arg(argv: list[str]) -> int:
    for token in argv:
        if token.startswith("--time="):
            return int(token.split("=", 1)[1])
    raise AssertionError(f"no --time argument in argv: {argv}")


def test_measurement_window_spans_warmup_plus_measurement(db_config) -> None:
    """A 120s measurement with 60s warmup must run sysbench for 180s.

    The distributed bug produced 90 here (the orchestrator's 60+30 defaults)
    for exactly this configuration.
    """
    argv = _captured_argv(
        SysbenchExecutor(tables=2, table_size=1000),
        db_config=db_config,
        duration=120,
        warmup=60,
    )

    assert _time_arg(argv) == 180


def test_short_window_is_not_silently_substituted(db_config) -> None:
    """The defect's fingerprint — a 90s window — must not appear for a 180s run."""
    argv = _captured_argv(
        SysbenchExecutor(tables=2, table_size=1000),
        db_config=db_config,
        duration=120,
        warmup=60,
    )

    assert _time_arg(argv) != 90


def test_warmup_is_included_in_the_measured_window(db_config) -> None:
    """Warmup is part of the averaged window; no --warmup-time is emitted.

    Recorded results across the repository were all produced under these
    semantics, so switching to a true excluded warmup phase is a separate,
    schema-versioned decision rather than a fix.
    """
    argv = _captured_argv(
        SysbenchExecutor(tables=2, table_size=1000),
        db_config=db_config,
        duration=100,
        warmup=20,
    )

    assert _time_arg(argv) == 120
    assert not any(token.startswith("--warmup-time") for token in argv)


def test_zero_warmup_measures_only_the_configured_duration(db_config) -> None:
    """With no warmup the window is exactly the measurement duration."""
    argv = _captured_argv(
        SysbenchExecutor(tables=2, table_size=1000),
        db_config=db_config,
        duration=60,
        warmup=0,
    )

    assert _time_arg(argv) == 60


def test_random_seed_is_forwarded_when_set(db_config) -> None:
    """A configured seed must reach sysbench for reproducibility."""
    argv = _captured_argv(
        SysbenchExecutor(tables=2, table_size=1000),
        db_config=db_config,
        duration=60,
        warmup=0,
        seed=42,
    )

    assert "--rand-seed=42" in argv


def test_random_seed_is_omitted_when_unset(db_config) -> None:
    """No seed means no flag, rather than a hardcoded default."""
    argv = _captured_argv(
        SysbenchExecutor(tables=2, table_size=1000),
        db_config=db_config,
        duration=60,
        warmup=0,
    )

    assert not any(token.startswith("--rand-seed") for token in argv)
