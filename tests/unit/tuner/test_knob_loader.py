"""Tests for the SCALPEL-aware ``knob_loader`` walk-down fallback.

When SCALPEL confirms few knobs, ``preprocess_knobs.create_tier_dataframes``
skips writing CSV files for empty tiers. The loader must then walk DOWN
the canonical order ``[minimal, core, standard, extensive]`` to the next
broader tier that actually exists, logging a warning rather than crashing
the tuner at LHS-sample time.
"""

from __future__ import annotations

from pathlib import Path

import pandas as pd
import pytest

from src.knobs import knob_loader as knob_loader_module
from src.knobs.knob_loader import (
    CANONICAL_TIER_ORDER,
    load_knob_space_for_tier,
)


@pytest.fixture
def mock_data_driven_layout(tmp_path: Path, monkeypatch):
    """Stage a ``data/data_driven_knobs/<workload>/`` with only some tiers."""
    workload = "oltp_read_write"
    data_dir = tmp_path / "data_driven_knobs" / workload
    data_dir.mkdir(parents=True)
    monkeypatch.setattr(
        knob_loader_module,
        "DATA_DRIVEN_KNOBS_DIR",
        str(tmp_path / "data_driven_knobs"),
    )
    # Wipe the lru-style cache so tests don't bleed
    knob_loader_module._KNOB_SPACES.clear()
    return workload, data_dir


def _write_knobs_csv(path: Path, rows: list[dict]) -> None:
    df = pd.DataFrame(rows)
    df.to_csv(path, index=False)


def _minimal_csv_row(name: str) -> dict:
    """Build a minimal CSV row that ``load_knob_space_from_csv`` accepts."""
    return {
        "name": name,
        "vartype": "real",
        "scale": "linear",
        "min_val": 0.0,
        "max_val": 1.0,
        "tuning_min": 0.0,
        "tuning_max": 1.0,
        "enumvals": "",
        "boot_val": 0.5,
        "value": 0.5,
        "unit": "",
        "description": "",
        "custom_category": "other",
        "requires_restart": False,
        "hardware_relative": False,
        "resource_type": "",
        "context": "user",
    }


def test_walk_down_to_broader_tier_when_csv_missing(
    mock_data_driven_layout, caplog
):
    workload, data_dir = mock_data_driven_layout
    # SCALPEL produced only 'standard' + 'extensive' (no minimal / core CSV)
    _write_knobs_csv(
        data_dir / "standard_knobs.csv",
        [_minimal_csv_row("shared_buffers")],
    )
    _write_knobs_csv(
        data_dir / "extensive_knobs.csv",
        [_minimal_csv_row("shared_buffers"), _minimal_csv_row("work_mem")],
    )

    with caplog.at_level("WARNING"):
        space = load_knob_space_for_tier(
            "minimal", knob_source="data_driven", workload_type=workload
        )
    # Should have walked down minimal → core → standard and used standard
    assert len(space) == 1
    assert any("standard" in record.message for record in caplog.records)


def test_walk_down_to_extensive_when_all_intermediate_empty(
    mock_data_driven_layout, caplog
):
    workload, data_dir = mock_data_driven_layout
    # SCALPEL confirmed zero knobs → preprocess wrote ONLY extensive.
    _write_knobs_csv(
        data_dir / "extensive_knobs.csv",
        [_minimal_csv_row("shared_buffers"), _minimal_csv_row("work_mem")],
    )

    with caplog.at_level("WARNING"):
        space = load_knob_space_for_tier(
            "core", knob_source="data_driven", workload_type=workload
        )
    assert len(space) == 2


def test_raises_when_no_tier_exists(mock_data_driven_layout):
    workload, _ = mock_data_driven_layout
    with pytest.raises(FileNotFoundError):
        load_knob_space_for_tier(
            "minimal", knob_source="data_driven", workload_type=workload
        )


def test_canonical_tier_order_constant():
    assert CANONICAL_TIER_ORDER == ["minimal", "core", "standard", "extensive"]
