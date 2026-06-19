"""Tests for SCALPEL pairwise-interaction fANOVA importance.

The fused Lorenz input is ``marginal + alpha * max_interaction``, where
``max_interaction[k]`` is ``max_j fanova((k, j)).individual_importance``
over the top-K marginal knobs. These tests verify the dataclass shape,
the top-K cap, and the fused-signal arithmetic that drives the Lorenz
partition in ``scalpel_tier``.
"""

from __future__ import annotations

import numpy as np
import pandas as pd
import pytest

from src.analysis.scalpel_stability import (
    FanovaImportance,
    compute_fanova_importance,
    compute_fanova_marginals,
)

fanova = pytest.importorskip("fanova")


def _synthetic_xy(n: int = 600, seed: int = 0) -> tuple[pd.DataFrame, pd.Series]:
    """4-knob design where ``a`` and ``b`` only matter through their interaction.

    The response is ``y = 4 * (a - 0.5) * (b - 0.5) + 0.1 * c + noise``.
    ``a`` and ``b`` have small marginal mass by construction (centered
    contribution averages out under uniform marginals) but a large
    pairwise interaction; ``c`` carries a small linear effect; ``d`` is
    pure noise.
    """
    rng = np.random.default_rng(seed)
    a = rng.uniform(size=n)
    b = rng.uniform(size=n)
    c = rng.uniform(size=n)
    d = rng.uniform(size=n)
    y_arr = 4.0 * (a - 0.5) * (b - 0.5) + 0.1 * c + 0.05 * rng.normal(size=n)
    X = pd.DataFrame({"a": a, "b": b, "c": c, "d": d})
    return X, pd.Series(y_arr)


def test_compute_fanova_importance_returns_dataclass_with_marginals_and_interactions():
    X, y = _synthetic_xy()
    result = compute_fanova_importance(
        X, y, n_estimators=64, random_state=0, interaction_top_k=4
    )
    assert isinstance(result, FanovaImportance)
    assert set(result.marginals.keys()) == {"a", "b", "c", "d"}
    assert set(result.max_interactions.keys()) == {"a", "b", "c", "d"}
    for v in result.marginals.values():
        assert np.isfinite(v) and v >= 0.0
    for v in result.max_interactions.values():
        assert np.isfinite(v) and v >= 0.0
    assert len(result.top_k_marginals) <= 4
    assert set(result.top_k_marginals) <= set(X.columns)


def test_compute_fanova_marginals_shim_returns_dict_only():
    X, y = _synthetic_xy()
    marginals = compute_fanova_marginals(
        X, y, n_estimators=64, random_state=0
    )
    assert isinstance(marginals, dict)
    assert set(marginals.keys()) == {"a", "b", "c", "d"}


def test_interaction_top_k_zero_skips_interaction_work():
    X, y = _synthetic_xy()
    result = compute_fanova_importance(
        X, y, n_estimators=64, random_state=0, interaction_top_k=0
    )
    assert all(v == 0.0 for v in result.max_interactions.values())
    assert result.top_k_marginals == []


def test_interaction_top_k_limits_search_frontier():
    X, y = _synthetic_xy()
    result = compute_fanova_importance(
        X, y, n_estimators=64, random_state=0, interaction_top_k=2
    )
    nonzero = {k for k, v in result.max_interactions.items() if v > 0.0}
    assert len(nonzero) <= 2
    assert len(result.top_k_marginals) == 2


def test_fused_signal_promotes_interaction_only_knobs():
    """On a 4-knob design where ``a`` and ``b`` only matter through their
    interaction, the fused signal ``marginal + alpha * max_interaction``
    lifts at least one of them above ``d`` (pure noise).
    """
    X, y = _synthetic_xy()
    result = compute_fanova_importance(
        X, y, n_estimators=128, random_state=0, interaction_top_k=4
    )
    alpha = 0.5
    fused = {
        k: result.marginals[k] + alpha * result.max_interactions[k]
        for k in X.columns
    }
    fused_max_ab = max(fused["a"], fused["b"])
    assert fused_max_ab > fused["d"], (
        f"interaction-only knob did not beat noise: "
        f"a={fused['a']:.4f} b={fused['b']:.4f} d={fused['d']:.4f}"
    )
