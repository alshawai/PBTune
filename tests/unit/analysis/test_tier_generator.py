import json
import math

from src.analysis.tier_generator import (
    compare_tier_results,
    compare_to_expert,
    generate_tiers,
    get_tier_names,
    get_tier_rank_map,
)
from src.knobs.knob_metadata import TuningMetadata


def _build_importances(values):
    return {f"knob_{idx}": float(value) for idx, value in enumerate(values)}


def test_optimal_k_selected_for_three_clusters():
    # Arrange
    cluster_a = [0.01, 0.02, 0.03, 0.015, 0.018]
    cluster_b = [0.5, 0.52, 0.55, 0.58, 0.6]
    cluster_c = [0.9, 0.92, 0.94, 0.96, 0.98]
    importances = _build_importances(cluster_a + cluster_b + cluster_c)

    # Act
    result = generate_tiers(importances, workload_label="oltp", k_values=[2, 3, 4])

    # Assert
    assert result.optimal_k == 3
    assert set(result.silhouette_scores.keys()) == {2, 3, 4}
    assert not math.isnan(result.silhouette_scores[3])


def test_bimodal_scores_split_into_two_tiers():
    # Arrange
    cluster_low = [0.01, 0.02, 0.03, 0.04]
    cluster_high = [0.9, 0.92, 0.95, 0.99]
    importances = _build_importances(cluster_low + cluster_high)

    # Act
    result = generate_tiers(importances, workload_label="oltp", k_values=[2])

    # Assert
    assert result.optimal_k == 2
    assert len(set(result.tier_assignments.values())) == 2
    max_knob = max(importances, key=importances.get)
    assert result.tier_assignments[max_knob] == "tier_1"


def test_tier_names_for_k3_and_k4():
    # Arrange
    tier_names_k3 = get_tier_names(3)
    tier_names_k4 = get_tier_names(4)

    # Act
    # Assert
    assert tier_names_k3 == ["minimal", "standard", "extensive"]
    assert tier_names_k4 == ["minimal", "core", "standard", "extensive"]


def test_agreement_report_promotion(monkeypatch):
    # Arrange
    import src.analysis.tier_generator as tier_generator

    metadata = {"knob_a": TuningMetadata(impact_tier="extensive")}
    monkeypatch.setattr(tier_generator, "KNOB_TUNING_METADATA", metadata)

    tier_assignments = {"knob_a": "minimal"}
    data_rank_map = get_tier_rank_map(["minimal", "standard", "extensive"])

    # Act
    report = compare_to_expert(tier_assignments, data_rank_map)

    # Assert
    assert ("knob_a", "extensive", "minimal") in report.promotions
    assert "knob_a" not in report.demotions


def test_agreement_report_demotion(monkeypatch):
    # Arrange
    import src.analysis.tier_generator as tier_generator

    metadata = {"knob_b": TuningMetadata(impact_tier="minimal")}
    monkeypatch.setattr(tier_generator, "KNOB_TUNING_METADATA", metadata)

    tier_assignments = {"knob_b": "extensive"}
    data_rank_map = get_tier_rank_map(["minimal", "standard", "extensive"])

    # Act
    report = compare_to_expert(tier_assignments, data_rank_map)

    # Assert
    assert ("knob_b", "minimal", "extensive") in report.demotions
    assert "knob_b" not in report.promotions


def test_all_equal_importances_fallback(caplog):
    # Arrange
    importances = _build_importances([0.5, 0.5, 0.5, 0.5])

    # Act
    result = generate_tiers(importances, workload_label="oltp", k_values=[2, 3, 4])

    # Assert
    assert result.optimal_k == 3
    assert "All importance scores are equal" in caplog.text


def test_single_knob_single_tier():
    # Arrange
    importances = {"shared_buffers": 0.8}

    # Act
    result = generate_tiers(importances, workload_label="oltp")

    # Assert
    assert result.optimal_k == 1
    assert result.tier_assignments["shared_buffers"] == "tier_1"


def test_compare_tier_results_detects_shift(tmp_path):
    # Arrange
    data_a = {
        "workload_label": "oltp",
        "tier_assignments": {"knob_a": "minimal", "knob_b": "standard"},
    }
    data_b = {
        "workload_label": "olap",
        "tier_assignments": {"knob_a": "standard", "knob_b": "standard"},
    }

    path_a = tmp_path / "oltp.json"
    path_b = tmp_path / "olap.json"
    path_a.write_text(json.dumps(data_a))
    path_b.write_text(json.dumps(data_b))

    # Act
    report = compare_tier_results(path_a, path_b)

    # Assert
    assert report["n_shifted_knobs"] == 1
    assert report["shifted_knobs"][0]["knob"] == "knob_a"
