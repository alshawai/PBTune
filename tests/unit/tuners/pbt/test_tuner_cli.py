"""Tests for tuner CLI argument parsing.

Repointed from the retired legacy ``src.tuner.main.parse_args`` to the unified
PBT CLI (``src.tuners.pbt.cli.parse_args``) during refactor step 2e. The flags
exercised here (``--no-color``, ``--disable-early-stopping``,
``--sysbench-workload``) are strategy-agnostic and now live in the shared
argument groups (:mod:`src.tuners.cli`). This module physically relocates to
``tests/unit/tuners/`` in step 2f.
"""

from __future__ import annotations

from src.tuners.pbt.cli import parse_args


def test_parse_args_no_color_defaults_to_false() -> None:
    """CLI should keep colors enabled unless --no-color is provided."""
    args = parse_args([])

    assert args.no_color is False


def test_parse_args_no_color_enables_plain_output() -> None:
    """CLI should disable colors when --no-color is provided."""
    args = parse_args(["--no-color"])

    assert args.no_color is True


def test_parse_args_disable_early_stopping_defaults_to_false() -> None:
    """CLI should keep the no-improvement early stop enabled by default."""
    args = parse_args([])

    assert args.disable_early_stopping is False


def test_parse_args_disable_early_stopping_enabled() -> None:
    """CLI should parse the no-improvement early stopping disable flag."""
    args = parse_args(["--disable-early-stopping"])

    assert args.disable_early_stopping is True


def test_parse_args_sysbench_workload_default_none() -> None:
    """CLI keeps sysbench workload unset unless explicitly provided."""
    args = parse_args([])

    assert args.sysbench_workload is None


def test_parse_args_sysbench_workload_value() -> None:
    """CLI should parse explicit sysbench workload mode."""
    args = parse_args(["--sysbench-workload", "oltp_write_only"])

    assert args.sysbench_workload == "oltp_write_only"
