"""Tests for the SCALPEL Lorenz / nuisance / DBA-audit helpers.

The orchestrator itself is end-to-end-tested in ``test_scalpel.py`` with
a synthetic ``LoadedData`` fixture. These unit tests cover the small
building blocks so a regression in one layer surfaces quickly.
"""

from __future__ import annotations

import numpy as np
import pandas as pd

from src.analysis.scalpel_stability import (
    apply_nuisance_filter,
    assign_lorenz_tiers,
    audit_dba_prior,
    group_clustered_stability,
)
from src.knobs.knob_metadata import TuningMetadata


def _make_frame(columns: list[str], n: int = 4) -> pd.DataFrame:
    rng = np.random.default_rng(0)
    return pd.DataFrame(rng.uniform(size=(n, len(columns))), columns=columns)


def test_apply_nuisance_filter_drops_exact_matches():
    X = _make_frame(["array_nulls", "shared_buffers", "work_mem"])
    exclusions = {
        "array_nulls": ("catalog_compat", "compat flag"),
    }
    prefixes: dict[str, tuple[str, str]] = {}
    result = apply_nuisance_filter(
        X, exclusions=exclusions, prefixes=prefixes, overrides=set()
    )
    assert "array_nulls" not in result.filtered.columns
    assert {"shared_buffers", "work_mem"} <= set(result.filtered.columns)
    assert result.dropped == ["array_nulls"]
    assert result.reasons["array_nulls"] == "catalog_compat"


def test_apply_nuisance_filter_drops_prefix_matches():
    X = _make_frame(["log_min_duration_statement", "track_io_timing", "shared_buffers"])
    prefixes = {
        "log_": ("observability", "log toggles"),
        "track_": ("observability", "track toggles"),
    }
    result = apply_nuisance_filter(
        X, exclusions={}, prefixes=prefixes, overrides=set()
    )
    assert set(result.filtered.columns) == {"shared_buffers"}
    assert set(result.dropped) == {"log_min_duration_statement", "track_io_timing"}


def test_apply_nuisance_filter_honors_overrides():
    X = _make_frame(["array_nulls", "shared_buffers"])
    exclusions = {"array_nulls": ("catalog_compat", "compat flag")}
    result = apply_nuisance_filter(
        X,
        exclusions=exclusions,
        prefixes={},
        overrides={"array_nulls"},
    )
    assert "array_nulls" in result.filtered.columns
    assert result.dropped == []


def test_assign_lorenz_tiers_renormalizes_to_confirmed_mass():
    """Lorenz cuts must be computed over the CONFIRMED subset's mass only."""
    importances = {
        "a": 0.40,  # high but NOT confirmed → must be excluded entirely
        "b": 0.30,  # rank 1 within confirmed
        "c": 0.15,  # rank 2 within confirmed
        "d": 0.10,  # rank 3 within confirmed
        "e": 0.05,  # rank 4 within confirmed
    }
    confirmed = ["b", "c", "d", "e"]
    result = assign_lorenz_tiers(
        importances, confirmed=confirmed, coverage_minimal=0.50, coverage_core=0.80
    )
    # Total confirmed mass = 0.60; 50% cut = 0.30 → 'b' alone is in minimal.
    assert result.tier_assignments == {
        "b": "minimal",
        "c": "core",
        "d": "core",
        "e": "standard",
    }
    # Non-confirmed knob must be absent (NOT labelled 'extensive')
    assert "a" not in result.tier_assignments


def test_assign_lorenz_tiers_zero_importance_alphabetical():
    """Zero-mass ties must break by knob name asc (deterministic)."""
    importances = {"b": 0.0, "a": 0.0, "c": 0.0}
    result = assign_lorenz_tiers(
        importances, confirmed=["a", "b", "c"], coverage_minimal=0.5, coverage_core=0.8
    )
    # All zero → every knob lands in minimal (degenerate path)
    assert result.tier_assignments == {"a": "minimal", "b": "minimal", "c": "minimal"}


def test_assign_lorenz_tiers_no_confirmed_returns_empty():
    result = assign_lorenz_tiers({"a": 0.5}, confirmed=[])
    assert result.tier_assignments == {}
    assert result.cumulative_coverage == {}


def test_audit_dba_prior_flags_expert_minimal_outside_data_minimal():
    metadata = {
        "shared_buffers": TuningMetadata(impact_tier="minimal"),
        "work_mem": TuningMetadata(impact_tier="minimal"),
        "array_nulls": TuningMetadata(impact_tier="extensive"),
    }
    assignments = {
        "shared_buffers": "standard",  # violation
        "work_mem": "minimal",         # OK
        # array_nulls absent (nuisance-filtered) — must NOT flag because it
        # is not expert-minimal in the first place.
    }
    violations = audit_dba_prior(assignments, metadata)
    knobs = [v["knob"] for v in violations]
    assert "shared_buffers" in knobs
    assert "work_mem" not in knobs
    assert "array_nulls" not in knobs


def test_audit_dba_prior_flags_not_confirmed_minimal_knobs():
    """An expert-minimal knob absent from tier_assignments == data_tier=not_confirmed."""
    metadata = {"shared_buffers": TuningMetadata(impact_tier="minimal")}
    assignments: dict[str, str] = {}
    violations = audit_dba_prior(assignments, metadata)
    assert len(violations) == 1
    assert violations[0]["data_tier"] == "not_confirmed"


def test_group_clustered_stability_returns_probabilities_in_unit_interval():
    rng = np.random.default_rng(0)
    X = pd.DataFrame(rng.uniform(size=(40, 4)), columns=list("abcd"))
    y = pd.Series(rng.uniform(size=40))
    groups = pd.Series(np.repeat(np.arange(8), 5))  # 8 clusters of 5 rows

    # tier_fn: deterministic stub that assigns the first half of the
    # cleaned columns to minimal and the rest to core.
    def fake_tier_fn(X_sub, y_sub, groups_sub, seed):
        cols = list(X_sub.columns)
        mid = max(1, len(cols) // 2)
        return {c: "minimal" if i < mid else "core" for i, c in enumerate(cols)}

    result = group_clustered_stability(
        X,
        y,
        groups,
        n_subsamples=10,
        subsample_frac=0.5,
        random_state=42,
        tier_fn=fake_tier_fn,
    )

    assert result.n_successful > 0
    for knob, prob in result.selection_probability.items():
        assert 0.0 <= prob <= 1.0
        assert knob in {"a", "b", "c", "d"}
