"""Tests for the tuning_strategy field migration.

The writers (PBT, BO) emit ``tuning_session.tuning_strategy`` as a literal
string ("pbt" | "bo" | "lhs"). The readers (analysis/data_loader,
evaluation/loader) prefer the explicit field; legacy sessions written
before the field existed fall back to a path heuristic so analysis tools
don't crash mid-run on a mixed corpus.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from src.analysis.data_loader import (
    _build_session_metadata,
    _infer_tuning_strategy,
)
from src.evaluation.loader import _infer_tuning_strategy as _eval_infer


class TestAnalysisInferTuningStrategy:
    """src.analysis.data_loader._infer_tuning_strategy"""

    def test_explicit_field_wins_over_path(self):
        meta = {"tuning_strategy": "pbt"}
        path = Path("/results/oltp/bo_runs/extensive/baseline_sessions/x.json")
        assert _infer_tuning_strategy(meta, path) == "pbt"

    def test_explicit_field_passes_through_unknown_label(self):
        meta = {"tuning_strategy": "custom_x"}
        path = Path("/results/x.json")
        assert _infer_tuning_strategy(meta, path) == "custom_x"

    def test_pbt_runs_path_fallback(self):
        meta: dict = {}
        path = Path("/results/oltp/oltp_read_write/pbt_runs/extensive/tuning_sessions/x.json")
        assert _infer_tuning_strategy(meta, path) == "pbt"

    def test_bo_runs_path_fallback(self):
        meta: dict = {}
        path = Path("/results/oltp/oltp_read_write/bo_runs/extensive/baseline_sessions/x.json")
        assert _infer_tuning_strategy(meta, path) == "bo"

    def test_lhs_runs_path_fallback(self):
        meta: dict = {}
        path = Path("/results/oltp/oltp_read_write/lhs_runs/extensive/lhs_sessions/x.json")
        assert _infer_tuning_strategy(meta, path) == "lhs"

    def test_no_field_no_match_returns_unknown(self):
        meta: dict = {}
        path = Path("/tmp/some_session.json")
        assert _infer_tuning_strategy(meta, path) == "unknown"

    def test_empty_string_field_falls_through_to_path(self):
        meta = {"tuning_strategy": ""}
        path = Path("/results/oltp/pbt_runs/extensive/tuning_sessions/x.json")
        assert _infer_tuning_strategy(meta, path) == "pbt"


class TestBuildSessionMetadataPropagatesStrategy:
    """_build_session_metadata stores the resolved tuning_strategy."""

    def test_explicit_strategy_emitted_in_metadata(self):
        meta = _build_session_metadata(
            file_path=Path("mock.json"),
            session_meta={
                "workload_type": "oltp",
                "benchmark_name": "sysbench",
                "tuning_strategy": "bo",
            },
            data={},
            default_workload_type="oltp",
        )
        assert meta["tuning_strategy"] == "bo"

    def test_path_fallback_emitted_for_legacy_sessions(self):
        meta = _build_session_metadata(
            file_path=Path("/results/oltp/oltp_read_write/pbt_runs/extensive/tuning_sessions/x.json"),
            session_meta={
                "workload_type": "oltp",
                "benchmark_name": "sysbench",
            },
            data={},
            default_workload_type="oltp",
        )
        assert meta["tuning_strategy"] == "pbt"

    def test_unknown_emitted_when_no_field_and_no_path_match(self):
        meta = _build_session_metadata(
            file_path=Path("/tmp/orphan.json"),
            session_meta={
                "workload_type": "oltp",
                "benchmark_name": "sysbench",
            },
            data={},
            default_workload_type="oltp",
        )
        assert meta["tuning_strategy"] == "unknown"

    def test_strategy_not_double_stored_via_session_meta_loop(self):
        """The catch-all session_meta loop must not overwrite the resolved value."""
        meta = _build_session_metadata(
            file_path=Path("/results/oltp/oltp_read_write/pbt_runs/extensive/x.json"),
            session_meta={
                "workload_type": "oltp",
                "benchmark_name": "sysbench",
                "tuning_strategy": "pbt",
                "other": "keep_me",
            },
            data={},
            default_workload_type="oltp",
        )
        assert meta["tuning_strategy"] == "pbt"
        assert meta["other"] == "keep_me"


class TestEvaluationInferTuningStrategy:
    """src.evaluation.loader._infer_tuning_strategy mirrors the analysis loader."""

    @pytest.mark.parametrize(
        "meta, path, expected",
        [
            ({"tuning_strategy": "pbt"}, Path("/x.json"), "pbt"),
            ({"tuning_strategy": "bo"}, Path("/x.json"), "bo"),
            ({"tuning_strategy": "lhs"}, Path("/x.json"), "lhs"),
            ({}, Path("/r/pbt_runs/x.json"), "pbt"),
            ({}, Path("/r/bo_runs/x.json"), "bo"),
            ({}, Path("/r/lhs_runs/x.json"), "lhs"),
            ({}, Path("/r/nothing/x.json"), "unknown"),
        ],
    )
    def test_strategy_resolution(self, meta, path, expected):
        assert _eval_infer(meta, path) == expected

    def test_explicit_field_beats_conflicting_path(self):
        """A BO session written to a pbt_runs/ directory by accident still
        identifies as BO because the explicit field wins."""
        meta = {"tuning_strategy": "bo"}
        path = Path("/results/oltp/pbt_runs/extensive/x.json")
        assert _eval_infer(meta, path) == "bo"
