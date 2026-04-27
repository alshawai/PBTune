"""Tests for tuner CLI argument parsing."""

from __future__ import annotations

import sys

from src.tuner.main import parse_args


def test_parse_args_no_color_defaults_to_false(monkeypatch) -> None:
    """CLI should keep colors enabled unless --no-color is provided."""
    monkeypatch.setattr(sys, "argv", ["tuner"])

    args = parse_args()

    assert args.no_color is False


def test_parse_args_no_color_enables_plain_output(monkeypatch) -> None:
    """CLI should disable colors when --no-color is provided."""
    monkeypatch.setattr(sys, "argv", ["tuner", "--no-color"])

    args = parse_args()

    assert args.no_color is True


def test_parse_args_sysbench_workload_default_none(monkeypatch) -> None:
    """CLI keeps sysbench workload unset unless explicitly provided."""
    monkeypatch.setattr(sys, "argv", ["tuner"])

    args = parse_args()

    assert args.sysbench_workload is None


def test_parse_args_sysbench_workload_value(monkeypatch) -> None:
    """CLI should parse explicit sysbench workload mode."""
    monkeypatch.setattr(
        sys,
        "argv",
        ["tuner", "--sysbench-workload", "oltp_write_only"],
    )

    args = parse_args()

    assert args.sysbench_workload == "oltp_write_only"
