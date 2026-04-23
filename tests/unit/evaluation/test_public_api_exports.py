"""Public API export contract tests for src.evaluation."""

from __future__ import annotations

import importlib

import pytest


def test_all_declared_exports_are_resolvable() -> None:
    """Every symbol listed in __all__ should be importable from src.evaluation."""
    module = importlib.import_module("src.evaluation")

    for export_name in module.__all__:
        assert hasattr(module, export_name), f"Missing export: {export_name}"


def test_performance_snapshot_is_intentionally_not_exported() -> None:
    """PerformanceSnapshot should remain absent from the public API contract."""
    module = importlib.import_module("src.evaluation")

    assert "PerformanceSnapshot" not in module.__all__

    with pytest.raises(ImportError):
        exec("from src.evaluation import PerformanceSnapshot")
