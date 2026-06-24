"""Tests for the SCALPEL significance gate."""

from __future__ import annotations

import numpy as np
import pandas as pd

from src.analysis.scalpel_significance import (
    _bh_adjust,
    boruta_with_group_perm,
)


def test_bh_adjust_monotonic_and_clipped():
    pvals = np.array([0.04, 0.01, 0.50, 0.02])
    adj = _bh_adjust(pvals)
    # Adjusted p-values are between 0 and 1
    assert np.all(adj >= 0.0)
    assert np.all(adj <= 1.0)
    # BH ordering: the smallest raw p stays smallest after adjustment.
    smallest_raw_idx = int(np.argmin(pvals))
    smallest_adj_idx = int(np.argmin(adj))
    assert smallest_raw_idx == smallest_adj_idx


def test_boruta_with_group_perm_confirms_strong_signal():
    """When y is a clean function of two knobs, BORUTA should confirm them."""
    rng = np.random.default_rng(7)
    n = 300
    n_clusters = 30
    knobs = ["loud_a", "loud_b", "noise_c", "noise_d", "noise_e"]
    X = pd.DataFrame(rng.uniform(0.0, 1.0, size=(n, len(knobs))), columns=knobs)
    y = 5.0 * X["loud_a"] + 3.0 * X["loud_b"] + rng.normal(0, 0.05, n)
    groups = pd.Series(np.repeat(np.arange(n_clusters), n // n_clusters))

    result = boruta_with_group_perm(
        X,
        y,
        groups,
        n_iterations=20,
        n_estimators=120,
        max_features="sqrt",
        min_samples_leaf=2,
        fdr_q=0.10,
        random_state=42,
    )

    confirmed = set(result.confirmed)
    assert {"loud_a", "loud_b"} <= confirmed
    # At least one pure noise knob should NOT be confirmed.
    assert {"noise_c", "noise_d", "noise_e"} - confirmed


def test_boruta_with_group_perm_empty_input_returns_empty_result():
    X = pd.DataFrame()
    y = pd.Series(dtype=float)
    groups = pd.Series(dtype=str)
    result = boruta_with_group_perm(X, y, groups, n_iterations=5)
    assert result.confirmed == []
    assert result.tentative == []
    assert result.rejected == []
    assert result.n_iterations == 0


def test_boruta_with_group_perm_handles_singleton_clusters():
    """Singleton clusters fall back to i.i.d. shuffle with a warning."""
    rng = np.random.default_rng(0)
    n = 60
    X = pd.DataFrame(rng.uniform(size=(n, 3)), columns=["a", "b", "c"])
    y = pd.Series(2.0 * X["a"] + rng.normal(0, 0.1, n))
    # Every row in its own singleton cluster — degenerate case.
    groups = pd.Series([f"c{i}" for i in range(n)])
    result = boruta_with_group_perm(
        X, y, groups, n_iterations=5, n_estimators=60, random_state=42
    )
    # Should not raise; should still produce a hit-count dict.
    assert set(result.hit_counts.keys()) == {"a", "b", "c"}
