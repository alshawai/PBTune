"""Tests for the SCALPEL tier-diagnostics visualization loader."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from src.visualization.loaders.tier_diagnostics import load_tier_diagnostics


def _write_importance_results(
    path: Path,
    *,
    algorithm: str = "scalpel-v1",
    include_full_payload: bool = True,
) -> Path:
    """Write a synthetic ``importance_results.json`` + optional diagnostics sibling."""
    tier_block = {
        "metadata": {
            "algorithm": algorithm,
            "scalpel_version": "1.0",
            "diagnostics": {
                "nuisance_dropped": ["array_nulls"],
                "oob_r2": 0.61,
                "n_confirmed": 3,
                "n_tentative": 1,
                "n_rejected": 2,
                "dba_prior_violations": ["max_parallel_workers_per_gather"],
                "lorenz_cutoffs": [0.5, 0.8],
                "boruta_iter": 100,
                "fdr_q": 0.10,
                "n_stability_subsamples": 100,
                "wall_clock_s": 12.34,
                "preflight_reason": None,
                "is_degenerate": False,
                "stable_knobs_semantics": "intersection_of_confirmed_sets",
            },
        },
        "optimal_k": 4,
        "silhouette_scores": {},
        "tier_assignments": {
            "shared_buffers": "minimal",
            "work_mem": "core",
            "checkpoint_timeout": "standard",
        },
        "jenks_breaks": [0.5, 0.8],
        "agreement_report": {"agreements": [], "promotions": [], "demotions": []},
        "workload_label": "oltp_read_write",
    }
    importance_results = {
        "workload_type": "oltp_read_write",
        "model_r2": 0.83,
        "n_samples": 2067,
        "n_features": 179,
        "marginal_importances": {
            "shared_buffers": 0.30,
            "work_mem": 0.20,
            "checkpoint_timeout": 0.10,
        },
        "tier_generation": tier_block,
    }
    path.write_text(json.dumps(importance_results))

    if include_full_payload:
        diag_path = path.with_name("scalpel_diagnostics.json")
        full = {
            "workload_label": "oltp_read_write",
            "algorithm": algorithm,
            "scalpel_version": "1.0",
            "is_degenerate": False,
            "preflight_reason": None,
            "hyperparameters": {"seed": 42},
            "summary": tier_block["metadata"]["diagnostics"],
            "tier_assignments": tier_block["tier_assignments"],
            "confirmed": ["shared_buffers", "work_mem", "checkpoint_timeout"],
            "tentative": [],
            "rejected": [],
            "nuisance_dropped": ["array_nulls"],
            "full_importances": importance_results["marginal_importances"],
            "confirmed_importances": importance_results["marginal_importances"],
            "cumulative_coverage": {
                "shared_buffers": 0.5,
                "work_mem": 0.83,
                "checkpoint_timeout": 1.0,
            },
            "lorenz_breakpoints": {"minimal": 0.5, "core": 0.83, "standard": 1.0},
            "boruta_hits": {
                "shared_buffers": 100,
                "work_mem": 95,
                "checkpoint_timeout": 60,
            },
            "boruta_p_values": {"shared_buffers": 0.0, "work_mem": 0.0},
            "boruta_p_values_bh": {"shared_buffers": 0.0, "work_mem": 0.0},
            "stability_probabilities": {
                "shared_buffers": 0.96,
                "work_mem": 0.82,
                "checkpoint_timeout": 0.55,
            },
            "stability_tier_distribution": {
                "shared_buffers": {"minimal": 0.96, "core": 0.04},
            },
            "dba_prior_violations": [
                {
                    "knob": "max_parallel_workers_per_gather",
                    "expert_tier": "minimal",
                    "data_tier": "not_confirmed",
                }
            ],
            "diagnostics": {"oob_r2": 0.61, "wall_clock_s": 12.34},
        }
        diag_path.write_text(json.dumps(full))

    return path


def test_load_tier_diagnostics_reads_scalpel_block(tmp_path: Path):
    path = _write_importance_results(tmp_path / "importance_results.json")
    diag = load_tier_diagnostics(path)
    assert diag.algorithm == "scalpel-v1"
    assert diag.scalpel_version == "1.0"
    assert diag.workload_label == "oltp_read_write"
    assert "shared_buffers" in diag.confirmed
    assert diag.boruta_hits["shared_buffers"] == 100
    assert diag.stability_probabilities["shared_buffers"] == 0.96
    assert diag.tier_assignments["shared_buffers"] == "minimal"
    assert diag.dba_prior_violations[0]["knob"] == "max_parallel_workers_per_gather"
    assert diag.lorenz_breakpoints["minimal"] == 0.5
    assert diag.has_full_payload is True


def test_load_tier_diagnostics_handles_missing_diagnostics_sibling(tmp_path: Path):
    """When scalpel_diagnostics.json is absent, the loader returns minimal payload."""
    path = _write_importance_results(
        tmp_path / "importance_results.json", include_full_payload=False
    )
    diag = load_tier_diagnostics(path)
    assert diag.algorithm == "scalpel-v1"
    # Sibling absent → boruta_hits / stability fields empty, but tier assignments
    # still come from the inline tier_generation block.
    assert diag.boruta_hits == {}
    assert diag.stability_probabilities == {}
    assert diag.tier_assignments["shared_buffers"] == "minimal"
    assert diag.has_full_payload is False


def test_load_tier_diagnostics_legacy_jenks_file_returns_minimal_payload(tmp_path: Path):
    """A pre-SCALPEL ``importance_results.json`` (no metadata block) loads cleanly."""
    legacy = {
        "workload_type": "oltp_read_write",
        "model_r2": 0.83,
        "n_samples": 100,
        "n_features": 10,
        "marginal_importances": {"shared_buffers": 0.5, "work_mem": 0.3},
        "tier_generation": {
            "optimal_k": 2,
            "silhouette_scores": {"2": 0.91},
            "tier_assignments": {"shared_buffers": "tier_1"},
            "jenks_breaks": [0.0, 0.3, 0.5],
            "agreement_report": {"agreements": [], "promotions": [], "demotions": []},
            "workload_label": "oltp_read_write",
        },
    }
    path = tmp_path / "importance_results.json"
    path.write_text(json.dumps(legacy))

    diag = load_tier_diagnostics(path)
    assert diag.algorithm == "legacy"
    assert diag.scalpel_version is None
    assert diag.tier_assignments == {"shared_buffers": "tier_1"}
    assert diag.has_full_payload is False


def test_load_tier_diagnostics_raises_on_missing_file(tmp_path: Path):
    with pytest.raises(FileNotFoundError):
        load_tier_diagnostics(tmp_path / "no_such_file.json")


def test_tier_diagnostics_plot_registers_figure():
    """Importing the plot module registers the figure under 'importance' category."""
    # Re-import to trigger register_figure (idempotent in the FigureRegistry)
    import importlib

    import src.visualization.plots.tier_diagnostics as plot_mod

    importlib.reload(plot_mod)
    from src.visualization import REGISTRY

    fig_ids = {spec.fig_id for spec in REGISTRY.list_all()}
    assert "tier_diagnostics" in fig_ids
    spec = next(s for s in REGISTRY.list_all() if s.fig_id == "tier_diagnostics")
    assert spec.category == "importance"
