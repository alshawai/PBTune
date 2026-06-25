"""Tests for hardware validation of knob importance."""

import sys
from unittest.mock import MagicMock

import numpy as np
import pandas as pd
import pytest

# Mock heavy dependencies before importing the analysis modules.
mock_fanova = MagicMock()
mock_fanova.fANOVA = MagicMock()
sys.modules.setdefault("fanova", mock_fanova)
mock_shap = MagicMock()
mock_shap.TreeExplainer = MagicMock()
sys.modules.setdefault("shap", mock_shap)

import src.analysis.hardware_validator as hardware_validator
from src.analysis.data_loader import LoadedData
from src.analysis.hardware_validator import (
    build_combined_loaded_data,
    build_hardware_profile_key,
    train_combined_importance,
    validate_hardware_importance,
)
from src.analysis.importance import ImportanceResult
from src.utils.hardware_info import WorkerResources
from src.utils.metrics import MetricConfig


def _make_importance(marginal_importances: dict[str, float]) -> ImportanceResult:
    n_features = len(marginal_importances)
    shap_values = np.zeros((10, n_features))
    return ImportanceResult(
        marginal_importances=marginal_importances,
        pairwise_interactions={},
        model_r2=0.9,
        n_samples=10,
        n_features=n_features,
        workload_type="test",
        shap_importances=dict(marginal_importances),
        shap_values=shap_values,
        fanova_shap_correlation=1.0,
    )


def _make_loaded_data(df: pd.DataFrame, scores: list[float]) -> LoadedData:
    knob_bounds = {
        col: (float(df[col].min()), float(df[col].max())) for col in df.columns
    }
    return LoadedData(
        config_df=df,
        scores=pd.Series(scores, name="score"),
        metadata=[{"workload_type": "oltp"}],
        metric_config=MetricConfig.for_oltp(),
        knob_bounds=knob_bounds,
        n_observations=len(df),
    )


def test_hardware_profile_key_deterministic():
    resources = {
        "ram_bytes": 8 * 1024**3,
        "cpu_cores": 4,
        "disk_type": "SSD",
    }

    key_from_dict = build_hardware_profile_key(resources)
    key_from_dataclass = build_hardware_profile_key(
        WorkerResources(
            ram_bytes=resources["ram_bytes"],
            cpu_cores=4,
            disk_type="SSD",
        )
    )

    assert key_from_dict == key_from_dataclass


def test_kendall_tau_identical_rankings():
    importance_a = _make_importance({"a": 0.9, "b": 0.5, "c": 0.1})
    importance_b = _make_importance({"a": 0.8, "b": 0.4, "c": 0.2})

    resources_a = {
        "ram_bytes": 8 * 1024**3,
        "cpu_cores": 4,
        "disk_type": "SSD",
    }
    resources_b = {
        "ram_bytes": 16 * 1024**3,
        "cpu_cores": 8,
        "disk_type": "HDD",
    }

    result = validate_hardware_importance(
        [(importance_a, resources_a), (importance_b, resources_b)],
        tier_k_values=[3],
    )

    key_pair = tuple(
        sorted(
            [
                build_hardware_profile_key(resources_a),
                build_hardware_profile_key(resources_b),
            ]
        )
    )
    assert result.kendall_taus[key_pair] == pytest.approx(1.0)


def test_kendall_tau_opposite_rankings():
    importance_a = _make_importance({"a": 0.9, "b": 0.5, "c": 0.1})
    importance_b = _make_importance({"a": 0.1, "b": 0.5, "c": 0.9})

    resources_a = {
        "ram_bytes": 8 * 1024**3,
        "cpu_cores": 4,
        "disk_type": "SSD",
    }
    resources_b = {
        "ram_bytes": 16 * 1024**3,
        "cpu_cores": 8,
        "disk_type": "HDD",
    }

    result = validate_hardware_importance(
        [(importance_a, resources_a), (importance_b, resources_b)],
        tier_k_values=[3],
    )

    key_pair = tuple(
        sorted(
            [
                build_hardware_profile_key(resources_a),
                build_hardware_profile_key(resources_b),
            ]
        )
    )
    assert result.kendall_taus[key_pair] == pytest.approx(-1.0)


def test_conservative_rule_promotes_highest_tier():
    importance_a = _make_importance({"knob_x": 0.9, "knob_y": 0.5, "knob_z": 0.1})
    importance_b = _make_importance({"knob_x": 0.5, "knob_y": 0.9, "knob_z": 0.1})

    resources_a = {
        "ram_bytes": 8 * 1024**3,
        "cpu_cores": 4,
        "disk_type": "SSD",
    }
    resources_b = {
        "ram_bytes": 16 * 1024**3,
        "cpu_cores": 8,
        "disk_type": "HDD",
    }

    result = validate_hardware_importance(
        [(importance_a, resources_a), (importance_b, resources_b)],
        tier_k_values=[3],
    )

    assert "knob_x" in result.shifting_knobs
    assert result.conservative_tiers["knob_x"] == "minimal"


def test_stable_knobs_when_profiles_agree():
    importance_a = _make_importance({"knob_a": 0.9, "knob_b": 0.5, "knob_c": 0.1})
    importance_b = _make_importance({"knob_a": 0.8, "knob_b": 0.4, "knob_c": 0.2})

    resources_a = {
        "ram_bytes": 8 * 1024**3,
        "cpu_cores": 4,
        "disk_type": "SSD",
    }
    resources_b = {
        "ram_bytes": 16 * 1024**3,
        "cpu_cores": 8,
        "disk_type": "HDD",
    }

    result = validate_hardware_importance(
        [(importance_a, resources_a), (importance_b, resources_b)],
        tier_k_values=[3],
    )

    assert set(result.stable_knobs) == {"knob_a", "knob_b", "knob_c"}
    assert result.shifting_knobs == {}


def test_single_profile_marks_all_stable():
    importance = _make_importance({"knob_a": 0.9, "knob_b": 0.5, "knob_c": 0.1})
    resources = {
        "ram_bytes": 8 * 1024**3,
        "cpu_cores": 4,
        "disk_type": "SSD",
    }

    result = validate_hardware_importance(
        [(importance, resources)],
        tier_k_values=[3],
    )

    assert result.kendall_taus == {}
    assert set(result.stable_knobs) == {"knob_a", "knob_b", "knob_c"}


def test_combined_loaded_data_adds_hardware_features():
    df_a = pd.DataFrame({"knob_a": [0.1, 0.2], "knob_b": [0.3, 0.4]})
    df_b = pd.DataFrame({"knob_a": [0.5, 0.6], "knob_b": [0.7, 0.8]})

    data_a = _make_loaded_data(df_a, [1.0, 2.0])
    data_b = _make_loaded_data(df_b, [1.5, 2.5])

    resources_a = {
        "ram_bytes": 8 * 1024**3,
        "cpu_cores": 4,
        "disk_type": "SSD",
    }
    resources_b = {
        "ram_bytes": 16 * 1024**3,
        "cpu_cores": 8,
        "disk_type": "HDD",
    }

    combined = build_combined_loaded_data(
        [(data_a, resources_a), (data_b, resources_b)]
    )

    assert {"ram_bytes", "cpu_cores", "disk_type"} <= set(combined.config_df.columns)
    assert combined.config_df["ram_bytes"].nunique() == 2
    assert combined.config_df["cpu_cores"].nunique() == 2
    assert combined.config_df["disk_type"].nunique() == 2


def test_train_combined_importance_includes_hardware_features(monkeypatch):
    df_a = pd.DataFrame({"knob_a": [0.1, 0.2], "knob_b": [0.3, 0.4]})
    df_b = pd.DataFrame({"knob_a": [0.5, 0.6], "knob_b": [0.7, 0.8]})

    data_a = _make_loaded_data(df_a, [1.0, 2.0])
    data_b = _make_loaded_data(df_b, [1.5, 2.5])

    resources_a = {
        "ram_bytes": 8 * 1024**3,
        "cpu_cores": 4,
        "disk_type": "SSD",
    }
    resources_b = {
        "ram_bytes": 16 * 1024**3,
        "cpu_cores": 8,
        "disk_type": "HDD",
    }

    captured_columns: list[str] = []

    def fake_analyze(loaded_data, **kwargs):
        captured_columns.extend(loaded_data.config_df.columns.tolist())
        return _make_importance({col: 1.0 for col in loaded_data.config_df.columns})

    monkeypatch.setattr(hardware_validator, "analyze_knob_importance", fake_analyze)

    result = train_combined_importance([(data_a, resources_a), (data_b, resources_b)])

    assert {"ram_bytes", "cpu_cores", "disk_type"} <= set(captured_columns)
    assert isinstance(result, ImportanceResult)


# ── build_combined_loaded_data: knob_bounds merging ──────────────────


def test_combined_bounds_merge_widest_profile_bounds():
    """Combined knob_bounds must take the widest envelope from per-profile
    bounds, not just the tight empirical min/max of the concatenated data."""
    df_a = pd.DataFrame({"knob_a": [0.3, 0.4]})
    df_b = pd.DataFrame({"knob_a": [0.5, 0.6]})

    # Profile A has wide domain-spec bounds, profile B has narrow ones.
    data_a = _make_loaded_data(df_a, [1.0, 2.0])
    data_a.knob_bounds["knob_a"] = (0.0, 100.0)  # wide domain spec

    data_b = _make_loaded_data(df_b, [1.5, 2.5])
    data_b.knob_bounds["knob_a"] = (0.0, 50.0)

    resources_a = {"ram_bytes": 8 * 1024**3, "cpu_cores": 4, "disk_type": "SSD"}
    resources_b = {"ram_bytes": 16 * 1024**3, "cpu_cores": 8, "disk_type": "SSD"}

    combined = build_combined_loaded_data([(data_a, resources_a), (data_b, resources_b)])

    b_min, b_max = combined.knob_bounds["knob_a"]
    # Must preserve the widest profile bound (100.0), not just data max (0.6).
    assert b_max >= 100.0, f"expected max >= 100.0, got {b_max}"
    assert b_min <= 0.0, f"expected min <= 0.0, got {b_min}"


def test_combined_bounds_hardware_descriptors_from_data():
    """Hardware descriptor columns (ram_bytes, cpu_cores, disk_type) are
    not in per-profile knob_bounds, so they must get data-derived bounds."""
    df_a = pd.DataFrame({"knob_a": [0.1, 0.2]})
    df_b = pd.DataFrame({"knob_a": [0.3, 0.4]})

    data_a = _make_loaded_data(df_a, [1.0, 2.0])
    data_b = _make_loaded_data(df_b, [1.5, 2.5])

    resources_a = {"ram_bytes": 8 * 1024**3, "cpu_cores": 4, "disk_type": "SSD"}
    resources_b = {"ram_bytes": 16 * 1024**3, "cpu_cores": 8, "disk_type": "HDD"}

    combined = build_combined_loaded_data([(data_a, resources_a), (data_b, resources_b)])

    # ram_bytes and cpu_cores are hardware-only columns.
    ram_min, ram_max = combined.knob_bounds["ram_bytes"]
    assert ram_min <= 8 * 1024**3
    assert ram_max >= 16 * 1024**3

    cpu_min, cpu_max = combined.knob_bounds["cpu_cores"]
    assert cpu_min <= 4
    assert cpu_max >= 8


def test_combined_bounds_enclose_observed_outside_profile_bounds():
    """If a profile's observed data exceeds its own knob_bounds (sentinel
    values, rounding), the combined bounds must still enclose it."""
    df_a = pd.DataFrame({"knob_a": [-1.0, 0.5]})  # -1 is a sentinel
    df_b = pd.DataFrame({"knob_a": [0.3, 0.4]})

    data_a = _make_loaded_data(df_a, [1.0, 2.0])
    data_a.knob_bounds["knob_a"] = (0.0, 1.0)  # doesn't cover -1

    data_b = _make_loaded_data(df_b, [1.5, 2.5])
    data_b.knob_bounds["knob_a"] = (0.0, 1.0)

    resources_a = {"ram_bytes": 8 * 1024**3, "cpu_cores": 4, "disk_type": "SSD"}
    resources_b = {"ram_bytes": 8 * 1024**3, "cpu_cores": 4, "disk_type": "SSD"}

    combined = build_combined_loaded_data([(data_a, resources_a), (data_b, resources_b)])

    b_min, _ = combined.knob_bounds["knob_a"]
    assert b_min <= -1.0, f"expected min <= -1.0, got {b_min}"
