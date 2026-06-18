"""End-to-end tests for :mod:`src.analysis.scalpel`."""

from __future__ import annotations

import numpy as np
import pandas as pd
import pytest

from src.analysis.scalpel import (
    DEFAULT_LORENZ_BREAKPOINTS,
    SCALPEL_ALGORITHM_SLUG,
    SCALPELHyperparameters,
    lorenz_tier_from_importances,
    scalpel_tier,
)


def _synthetic_loaded(
    n_clusters: int = 16,
    obs_per_cluster: int = 15,
    seed: int = 0,
) -> tuple[pd.DataFrame, pd.Series, pd.Series]:
    """Build a tiny synthetic ``(X, y, sample_groups)`` triple.

    Two knobs (``signal_*``) drive the score; everything else is noise
    (including one nuisance-listed knob and one prefix-matched knob).
    """
    rng = np.random.default_rng(seed)
    n = n_clusters * obs_per_cluster
    knobs = sorted(
        [
            "signal_a",
            "signal_b",
            "noise_c",
            "noise_d",
            # nuisance knob (exact match in IMPORTANCE_NUISANCE_EXCLUSIONS)
            "array_nulls",
            # nuisance prefix (log_*)
            "log_min_duration_statement",
        ]
    )
    X = pd.DataFrame(rng.uniform(0.0, 1.0, size=(n, len(knobs))), columns=knobs)
    y = pd.Series(4.0 * X["signal_a"] + 3.0 * X["signal_b"] + rng.normal(0, 0.05, n))
    groups = pd.Series(np.repeat(np.arange(n_clusters), obs_per_cluster).astype(str))
    return X, y, groups


def test_lorenz_tier_from_importances_50_80_cutoffs():
    importances = {"a": 0.50, "b": 0.20, "c": 0.15, "d": 0.10, "e": 0.05}
    res = lorenz_tier_from_importances(importances, workload_label="synth")
    assert res.optimal_k == 4
    assert res.silhouette_scores == {}
    assert res.jenks_breaks == list(DEFAULT_LORENZ_BREAKPOINTS)
    # 'a' hits the 50% cumulative cutoff
    assert res.tier_assignments["a"] == "minimal"
    # 'b' and 'c' fall in core (cum mass crosses 0.80)
    assert res.tier_assignments["b"] == "core"
    assert res.tier_assignments["c"] == "core"
    assert res.tier_assignments["d"] == "standard"
    assert res.tier_assignments["e"] == "standard"


def test_lorenz_tier_from_importances_empty_raises():
    with pytest.raises(ValueError):
        lorenz_tier_from_importances({}, workload_label="synth")


def test_lorenz_tier_from_importances_single_knob_returns_minimal():
    res = lorenz_tier_from_importances({"shared_buffers": 0.5}, workload_label="synth")
    assert res.optimal_k == 1
    assert res.tier_assignments == {"shared_buffers": "minimal"}


def test_scalpel_tier_preflight_returns_degraded_on_tiny_input():
    """Below ``min_samples`` rows, SCALPEL must return a degenerate result, not raise."""
    X, y, groups = _synthetic_loaded(n_clusters=4, obs_per_cluster=5)
    hp = SCALPELHyperparameters(min_samples=10_000, workload_label="synth", seed=1)
    result = scalpel_tier(X, y, sample_groups=groups, hp=hp)
    assert result.is_degenerate is True
    assert "too_few_samples" in (result.preflight_reason or "")
    assert result.tier_assignments == {}
    # Nuisance filter still ran
    assert "array_nulls" in result.nuisance_dropped
    assert "log_min_duration_statement" in result.nuisance_dropped


def test_scalpel_tier_preflight_returns_degraded_on_too_few_clusters():
    X, y, groups = _synthetic_loaded(n_clusters=2, obs_per_cluster=120)
    hp = SCALPELHyperparameters(
        min_samples=10, min_clusters=4, workload_label="synth", seed=1
    )
    result = scalpel_tier(X, y, sample_groups=groups, hp=hp)
    assert result.is_degenerate is True
    assert "too_few_clusters" in (result.preflight_reason or "")


def test_scalpel_tier_to_tier_result_preserves_schema():
    """A degenerate SCALPELResult adapts to a legacy ``TierResult`` shape."""
    X, y, groups = _synthetic_loaded(n_clusters=4, obs_per_cluster=5)
    hp = SCALPELHyperparameters(min_samples=10_000, workload_label="synth", seed=1)
    result = scalpel_tier(X, y, sample_groups=groups, hp=hp)
    tier = result.to_tier_result(workload_label="synth")
    assert tier.optimal_k == 1
    assert tier.silhouette_scores == {}
    assert tier.jenks_breaks == list(DEFAULT_LORENZ_BREAKPOINTS)
    assert tier.tier_assignments == {}


def test_scalpel_hyperparameters_seed_derivation_is_workload_stable(monkeypatch):
    """Seed must depend on the workload label (B13 in the design)."""

    class _Args:
        scalpel_base_seed = 42
        random_seed = 42
        scalpel_rf_trees = 50
        scalpel_rf_max_features = "sqrt"
        scalpel_rf_min_samples_leaf = 3
        scalpel_boruta_iter = 5
        scalpel_fdr_q = 0.10
        scalpel_coverage_minimal = 0.50
        scalpel_coverage_core = 0.80
        scalpel_stability_b = 5
        scalpel_stability_frac = 0.5
        scalpel_nuisance_overrides = ""

    hp_a = SCALPELHyperparameters.from_args(_Args(), workload_label="oltp_read_write")
    hp_b = SCALPELHyperparameters.from_args(_Args(), workload_label="olap")
    assert hp_a.seed != hp_b.seed
    assert hp_a.workload_label == "oltp_read_write"
    assert hp_b.workload_label == "olap"


def test_scalpel_algorithm_slug_constant():
    assert SCALPEL_ALGORITHM_SLUG == "scalpel-v1"


@pytest.mark.slow
def test_scalpel_tier_end_to_end_on_synthetic_signal():
    """Full pipeline smoke: SCALPEL confirms strong signals and filters nuisance."""
    X, y, groups = _synthetic_loaded(n_clusters=16, obs_per_cluster=20, seed=11)
    hp = SCALPELHyperparameters(
        rf_n_estimators=120,
        boruta_iter=20,
        n_stability_subsamples=5,
        min_samples=50,
        min_clusters=4,
        workload_label="synth",
        seed=123,
    )
    result = scalpel_tier(X, y, sample_groups=groups, hp=hp)
    # Nuisance knobs are dropped before any modeling
    assert "array_nulls" in result.nuisance_dropped
    assert "log_min_duration_statement" in result.nuisance_dropped
    # At least one signal knob is confirmed
    assert {"signal_a", "signal_b"} & set(result.confirmed)
    # Tier assignments only contain canonical labels
    assert set(result.tier_assignments.values()) <= {"minimal", "core", "standard"}
