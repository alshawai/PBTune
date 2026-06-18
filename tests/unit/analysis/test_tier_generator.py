"""Tests for the SCALPEL-aware ``tier_generator`` shim.

Under SCALPEL, ``tier_generator.generate_tiers`` is a thin wrapper around
:func:`src.analysis.scalpel.lorenz_tier_from_importances` (the lossy
fallback used by callers that only retain a precomputed importance dict,
e.g. :mod:`src.analysis.hardware_validator`). The full pipeline lives in
:func:`src.analysis.scalpel.scalpel_tier`.
"""

import json

from src.analysis.scalpel import SCALPEL_ALGORITHM_SLUG
from src.analysis.tier_generator import (
    compare_tier_results,
    compare_to_expert,
    generate_tiers,
    get_tier_names,
    get_tier_rank_map,
    export_data_driven_tiers,
)
from src.knobs.knob_metadata import TuningMetadata


def _build_importances(values):
    return {f"knob_{idx}": float(value) for idx, value in enumerate(values)}


def test_generate_tiers_returns_canonical_lorenz_payload():
    # Arrange — heavy-tailed importances, ranking determined by knob index.
    cluster_low = [0.01, 0.02, 0.03, 0.04]
    cluster_high = [0.9, 0.92, 0.95, 0.99]
    importances = _build_importances(cluster_low + cluster_high)

    # Act
    result = generate_tiers(importances, workload_label="oltp")

    # Assert: SCALPEL emits canonical k=4 + the legacy fields stay schema-stable
    assert result.optimal_k == 4
    assert result.silhouette_scores == {}
    assert result.jenks_breaks == [0.50, 0.80]
    # Highest-importance knob is in 'minimal'
    max_knob = max(importances, key=importances.get)
    assert result.tier_assignments[max_knob] == "minimal"
    # Tier values are restricted to canonical {minimal, core, standard}
    assert set(result.tier_assignments.values()) <= {"minimal", "core", "standard"}


def test_get_tier_names_canonical():
    assert get_tier_names(3) == ["minimal", "standard", "extensive"]
    assert get_tier_names(4) == ["minimal", "core", "standard", "extensive"]
    assert get_tier_names(1) == ["minimal"]


def test_agreement_report_promotion(monkeypatch):
    # Arrange
    import src.analysis.tier_generator as tier_generator

    metadata = {"knob_a": TuningMetadata(impact_tier="extensive")}
    monkeypatch.setattr(tier_generator, "KNOB_TUNING_METADATA", metadata)

    tier_assignments = {"knob_a": "minimal"}
    data_rank_map = get_tier_rank_map(["minimal", "core", "standard", "extensive"])

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

    # SCALPEL puts a non-confirmed knob in NO tier, but compare_to_expert
    # is also called on Lorenz-fallback paths where every knob has a tier.
    tier_assignments = {"knob_b": "standard"}
    data_rank_map = get_tier_rank_map(["minimal", "core", "standard", "extensive"])

    # Act
    report = compare_to_expert(tier_assignments, data_rank_map)

    # Assert
    assert ("knob_b", "minimal", "standard") in report.demotions
    assert "knob_b" not in report.promotions


def test_compare_to_expert_skips_knobs_absent_from_assignments():
    """SCALPEL omits non-confirmed knobs; compare_to_expert must skip them."""
    # An expert-tagged knob that SCALPEL does NOT report should not appear
    # in any of the agreement / promotion / demotion buckets.
    tier_assignments = {}  # SCALPEL's degenerate / all-rejected case
    data_rank_map = get_tier_rank_map(["minimal", "core", "standard", "extensive"])
    report = compare_to_expert(tier_assignments, data_rank_map)
    assert report.agreements == []
    assert report.promotions == []
    assert report.demotions == []


def test_single_knob_single_tier():
    # Arrange
    importances = {"shared_buffers": 0.8}

    # Act
    result = generate_tiers(importances, workload_label="oltp")

    # Assert: degenerate single-knob path collapses to minimal
    assert result.optimal_k == 1
    assert result.tier_assignments["shared_buffers"] == "minimal"


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


def test_export_data_driven_tiers_writes_scalpel_v1_metadata(tmp_path):
    # Arrange
    importances = {
        "shared_buffers": 0.9,
        "work_mem": 0.5,
        "max_connections": 0.1,
        "maintenance_work_mem": 0.01,
    }
    output_file = tmp_path / "data_driven_tiers.json"

    # Act
    result = export_data_driven_tiers(
        marginal_importances=importances,
        workload_label="oltp_read_write",
        output_path=output_file,
        source_results="results/pbt_results_1",
    )

    # Assert: schema is preserved; new metadata.algorithm signals SCALPEL.
    assert result.optimal_k == 4
    assert output_file.exists()
    with open(output_file, "r") as f:
        data = json.load(f)

    assert "metadata" in data
    assert data["metadata"]["workload_type"] == "oltp_read_write"
    assert data["metadata"]["source_results"] == "results/pbt_results_1"
    assert data["metadata"]["algorithm"] == SCALPEL_ALGORITHM_SLUG
    assert "scalpel_version" in data["metadata"]
    assert "diagnostics" in data["metadata"]
    assert data["metadata"]["diagnostics"]["lorenz_cutoffs"] == [0.5, 0.8]

    assert "tiers" in data
    tiers = data["tiers"]
    assert set(tiers.keys()) == {"minimal", "core", "standard", "extensive"}
    assert tiers["extensive"] is None
    assert isinstance(tiers["minimal"], list)
    assert isinstance(tiers["core"], list)
    assert isinstance(tiers["standard"], list)


def test_export_data_driven_tiers_atomic_write(tmp_path):
    """The exporter must write through a .tmp file via os.replace."""
    importances = {"a": 0.5, "b": 0.3, "c": 0.2}
    output = tmp_path / "data_driven_tiers.json"
    output.write_text("stale")

    export_data_driven_tiers(
        marginal_importances=importances,
        workload_label="synth",
        output_path=output,
    )

    # No leftover .tmp file
    leftover = list(tmp_path.glob("*.tmp"))
    assert leftover == []
    payload = json.loads(output.read_text())
    assert payload["metadata"]["algorithm"] == SCALPEL_ALGORITHM_SLUG
