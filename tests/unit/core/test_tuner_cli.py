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
