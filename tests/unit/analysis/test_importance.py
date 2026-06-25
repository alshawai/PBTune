"""
Tests for Knob Importance Analysis using fANOVA.
"""

import pytest
import numpy as np
import pandas as pd

import sys
import importlib
from unittest.mock import MagicMock

# Mock fanova and shap


class MockFANOVA:
    def __init__(self, *args, **kwargs):
        pass

    def quantify_importance(self, indices):
        # Return dummy values
        # The key is the tuple itself
        if len(indices) == 1:
            if indices[0] == 0:
                return {indices: {"individual importance": 0.6}}
            elif indices[0] == 1:
                return {indices: {"individual importance": 0.3}}
            else:
                return {indices: {"individual importance": 0.05}}
        else:
            return {indices: {"individual importance": 0.1}}


mock_fanova = MagicMock()
mock_fanova.fANOVA = MockFANOVA
sys.modules["fanova"] = mock_fanova


class MockTreeExplainer:
    def __init__(self, model):
        pass

    def shap_values(self, X):
        # Return dummy SHAP values matching X shape
        shap_vals = np.zeros_like(X, dtype=float)
        if X.shape[1] > 0:
            shap_vals[:, 0] = 5.0  # Feature 0 high SHAP
        if X.shape[1] > 1:
            shap_vals[:, 1] = 1.0  # Feature 1 lower SHAP
        return shap_vals


mock_shap = MagicMock()
mock_shap.TreeExplainer = MockTreeExplainer
sys.modules["shap"] = mock_shap

from src.analysis.data_loader import LoadedData
from src.utils.metrics import MetricConfig

import src.analysis.importance as importance

importlib.reload(importance)

analyze_knob_importance = importance.analyze_knob_importance
InsufficientDataError = importance.InsufficientDataError


def create_mock_loaded_data(
    n_samples: int = 100,
    n_features: int = 5,
    noise_level: float = 0.1,
    constant_col: bool = False,
) -> LoadedData:
    np.random.seed(42)

    # Create simple features
    data = {}
    for i in range(n_features):
        data[f"feature_{i}"] = np.random.uniform(0, 1, size=n_samples)

    if constant_col:
        data["zero_variance"] = np.ones(n_samples)

    df = pd.DataFrame(data)

    # Calculate scores with feature_0 being strictly dominant: 3.0 * f0 + 0.1 * f1
    scores = (
        3.0 * df["feature_0"]
        + 0.1 * df["feature_1"]
        + np.random.normal(0, noise_level, size=n_samples)
    )
    scores_series = pd.Series(scores, name="score")

    bounds = {}
    for col in df.columns:
        bounds[col] = (0.0, 1.0)  # explicit true bounds

    metadata = [{"workload_type": "test_oltp"}]

    # Construct metric config bypass
    metric_config = MetricConfig.for_oltp()

    return LoadedData(
        config_df=df,
        scores=scores_series,
        metadata=metadata,
        metric_config=metric_config,
        knob_bounds=bounds,
        n_observations=n_samples,
    )


def test_insufficient_data():
    loaded_data = create_mock_loaded_data(n_samples=29)
    with pytest.raises(InsufficientDataError) as exc_info:
        analyze_knob_importance(loaded_data)
    assert "Need at least 30 observations" in str(exc_info.value)


def test_zero_variance_dropped(caplog):
    loaded_data = create_mock_loaded_data(n_samples=50, constant_col=True)

    # Should complete without error because remaining variance > 0
    result = analyze_knob_importance(loaded_data)

    assert "zero_variance" not in result.marginal_importances
    assert (
        "zero-variance knobs before importance analysis: ['zero_variance']"
        in caplog.text
    )
    assert result.n_features == 5  # The 5 normal features


def test_dominant_knob_and_marginal_sum():
    loaded_data = create_mock_loaded_data(n_samples=100)

    result = analyze_knob_importance(loaded_data, top_k=2)

    # Check top feature is feature_0 (dominant in synthetic data Generation)
    top_feature = list(result.marginal_importances.keys())[0]
    assert top_feature == "feature_0"

    # Check R2 is > 0.8
    assert result.model_r2 > 0.8

    # Check marginal importances sum to approximately 1.0 or less
    # Note: fanova importances are fractions of total variance explained by main effects
    # They should not exceed 1.0, and usually sum to slightly less than 1
    # depending on interaction magnitude.
    total_marginal = sum(result.marginal_importances.values())
    assert 0.0 <= total_marginal <= 1.05


def test_pairwise_interaction_top_k():
    loaded_data = create_mock_loaded_data(n_samples=100, n_features=5)

    # Ask for interactions for top 2 features only.
    # Total pairwise pairs for 2 features is 1.
    result = analyze_knob_importance(loaded_data, top_k=2, interaction_order=2)

    assert len(result.pairwise_interactions) == 1
    # Check that highest subset was feature_0 and feature_1 theoretically, or just length
    assert len(result.marginal_importances) == 5


def test_config_space_uses_bounds():
    loaded_data = create_mock_loaded_data(n_samples=100, n_features=2)
    # Inject bounds wider than the actual data
    loaded_data.knob_bounds["feature_0"] = (-10.0, 10.0)
    loaded_data.knob_bounds["feature_1"] = (-5.0, 5.0)

    result = analyze_knob_importance(loaded_data)
    # The actual checks occur downstream successfully if fANOVA didn't crash.
    # If it was restricted incorrectly, fANOVA could raise bound warnings/errors.
    assert "feature_0" in result.marginal_importances


def test_shap_values_matrix_shape():
    loaded_data = create_mock_loaded_data(n_samples=50, n_features=3)
    result = analyze_knob_importance(loaded_data)

    assert result.shap_values is not None
    assert result.shap_values.shape == (50, 3)


def test_shap_global_importance():
    loaded_data = create_mock_loaded_data(n_samples=50, n_features=3)
    result = analyze_knob_importance(loaded_data)

    top_shap_feature = list(result.shap_importances.keys())[0]
    assert top_shap_feature == "feature_0"


def test_fanova_shap_correlation():
    loaded_data = create_mock_loaded_data(n_samples=50, n_features=3)
    result = analyze_knob_importance(loaded_data)

    # In our mocks, both fANOVA and SHAP rank feature_0 as #1.
    assert -1.0 <= result.fanova_shap_correlation <= 1.0
    assert result.fanova_shap_correlation > 0.8


def test_correlation_warning(caplog):
    loaded_data = create_mock_loaded_data(n_samples=50, n_features=3)

    # Temporarily modify the mock to produce a negative correlation
    original_shap_values = mock_shap.TreeExplainer.shap_values

    def bad_shap_values(self, X):
        shap_vals = np.zeros_like(X, dtype=float)
        if X.shape[1] > 0:
            shap_vals[:, 0] = 0.1  # Now feature_0 is least important
        if X.shape[1] > 1:
            shap_vals[:, 1] = 5.0  # feature_1 is most important
        return shap_vals

    mock_shap.TreeExplainer.shap_values = bad_shap_values
    try:
        analyze_knob_importance(loaded_data)
    finally:
        # Restore
        mock_shap.TreeExplainer.shap_values = original_shap_values

    assert "Low correlation between fANOVA and SHAP importance rankings" in caplog.text


# ── _build_config_space hardening ────────────────────────────────────

from src.analysis.importance import _build_config_space


def test_config_space_widens_with_epsilon():
    """Bounds set to exact min/max must be widened so fANOVA's strict
    ``X[i] > upper`` check doesn't reject boundary samples."""
    df = pd.DataFrame({"f": [0.0, 1.0]})
    bounds = {"f": (0.0, 1.0)}
    cs = _build_config_space(df, bounds)
    hp = cs.get_hyperparameter("f")
    assert hp.lower < 0.0, "lower bound must be widened below data min"
    assert hp.upper > 1.0, "upper bound must be widened above data max"


def test_config_space_encloses_observed_outside_bounds():
    """If data contains values outside the provided knob_bounds, the
    config space must still enclose them (union of bounds ∪ data)."""
    df = pd.DataFrame({"f": [-5.0, 10.0]})
    bounds = {"f": (0.0, 1.0)}
    cs = _build_config_space(df, bounds)
    hp = cs.get_hyperparameter("f")
    assert hp.lower < -5.0
    assert hp.upper > 10.0


def test_config_space_integer_uses_floor_ceil():
    """Integer bounds must use floor/ceil, not int() truncation, so the
    interval always widens rather than narrowing."""
    df = pd.DataFrame({"i": pd.array([5, 10], dtype="int64")})
    bounds = {"i": (5.0, 10.0)}
    cs = _build_config_space(df, bounds)
    hp = cs.get_hyperparameter("i")
    # After epsilon subtraction from 5.0 and addition to 10.0,
    # floor(4.999...) = 4, ceil(10.000...) = 11
    assert hp.lower <= 4, f"expected lower ≤ 4, got {hp.lower}"
    assert hp.upper >= 11, f"expected upper ≥ 11, got {hp.upper}"


def test_config_space_degenerate_interval():
    """When all values are identical (min == max), the config space must
    still produce a valid (non-zero-width) interval."""
    df = pd.DataFrame({"f": [42.0, 42.0, 42.0]})
    bounds = {"f": (42.0, 42.0)}
    cs = _build_config_space(df, bounds)
    hp = cs.get_hyperparameter("f")
    assert hp.lower < hp.upper


def test_config_space_large_integer_no_crash():
    """Large integer values (like ram_bytes) at exact boundaries must not
    crash fANOVA due to float-precision issues."""
    ram_vals = pd.array([8589934592, 17179869184], dtype="int64")
    df = pd.DataFrame({"ram_bytes": ram_vals})
    bounds = {"ram_bytes": (float(ram_vals[0]), float(ram_vals[1]))}
    cs = _build_config_space(df, bounds)
    hp = cs.get_hyperparameter("ram_bytes")
    assert hp.lower <= 8589934592
    assert hp.upper >= 17179869184
