"""Loader for SCALPEL tier-diagnostics visualization.

Reads the new ``tier_generation`` JSON block produced by
:func:`src.scripts.analyze_knob_importance._save_analysis_results` plus
the optional sibling ``scalpel_diagnostics.json`` (full per-knob payload
written by :func:`src.analysis.tier_generator.export_data_driven_tiers`
when ``write_diagnostics=True``).

Design constraints (see /home/eima40x4c/.claude/plans/distributed-toasting-sparrow.md):

* Legacy Jenks-era ``importance_results.json`` files (no ``metadata`` /
  ``diagnostics`` block) load with empty SCALPEL-specific fields rather
  than raise — the plot module then renders only the tier-summary panel.
* Missing ``scalpel_diagnostics.json`` is non-fatal; the loader falls
  back to the small ``metadata.diagnostics`` block embedded in
  ``importance_results.json``.
* Every consumer-facing field is JSON-friendly; no numpy arrays escape
  the loader so the plot module can stay backend-agnostic.
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Optional

from src.utils.logger import get_logger

LOGGER = get_logger("TierDiagnosticsLoader")


@dataclass
class TierDiagnostics:
    """Visualization-ready container for SCALPEL tier-diagnostics output."""

    workload_label: str
    algorithm: str
    scalpel_version: Optional[str]
    is_degenerate: bool
    preflight_reason: Optional[str]
    tier_assignments: dict[str, str]
    confirmed: list[str]
    tentative: list[str]
    rejected: list[str]
    nuisance_dropped: list[str]
    full_importances: dict[str, float]
    confirmed_importances: dict[str, float]
    cumulative_coverage: dict[str, float]
    lorenz_breakpoints: dict[str, float]
    boruta_hits: dict[str, int]
    boruta_p_values_bh: dict[str, float]
    stability_probabilities: dict[str, float]
    dba_prior_violations: list[dict[str, str]]
    summary_diagnostics: dict[str, Any] = field(default_factory=dict)

    @property
    def has_full_payload(self) -> bool:
        """True when the sibling diagnostics file was found."""
        return bool(self.confirmed) or bool(self.boruta_hits)


def _load_json(path: Path) -> Optional[dict[str, Any]]:
    if not path.is_file():
        return None
    try:
        with path.open("r", encoding="utf-8") as f:
            return json.load(f)
    except (OSError, json.JSONDecodeError) as exc:
        LOGGER.warning("Failed to read %s: %s", path, exc)
        return None


def load_tier_diagnostics(
    importance_results_path: str | Path,
) -> TierDiagnostics:
    """Load the SCALPEL diagnostics surrounding an ``importance_results.json``.

    Parameters
    ----------
    importance_results_path : str | Path
        Path to the ``importance_results.json`` file produced by
        :mod:`src.scripts.analyze_knob_importance`. The sibling
        ``scalpel_diagnostics.json`` is auto-discovered next to it
        AND next to the ``data_driven_tiers.json`` for the same workload.

    Returns
    -------
    TierDiagnostics
        Always non-None. Legacy / missing fields default to empty
        collections so plot modules can render whatever is available.
    """
    path = Path(importance_results_path)
    if not path.is_file():
        raise FileNotFoundError(f"importance_results.json not found: {path}")

    primary = _load_json(path) or {}
    tier_block = primary.get("tier_generation", {}) or {}
    metadata = tier_block.get("metadata", {}) or {}

    workload_label = (
        tier_block.get("workload_label")
        or primary.get("workload_type")
        or "unknown"
    )
    algorithm = metadata.get("algorithm", "legacy")
    scalpel_version = metadata.get("scalpel_version")
    summary_diagnostics = dict(metadata.get("diagnostics", {}) or {})

    # Look for the sibling scalpel_diagnostics.json. The analyze CLI
    # writes it next to the data_driven_tiers.json; the visualization
    # entry point may also be pointed at the analysis-results dir, so
    # we probe both candidates.
    candidates = [
        path.with_name("scalpel_diagnostics.json"),
        Path("data") / "data_driven_knobs" / workload_label / "scalpel_diagnostics.json",
    ]
    full_payload: Optional[dict[str, Any]] = None
    for candidate in candidates:
        full_payload = _load_json(candidate)
        if full_payload is not None:
            break

    if full_payload is None:
        # Best-effort fallback: use whatever's in the legacy block
        return TierDiagnostics(
            workload_label=str(workload_label),
            algorithm=str(algorithm),
            scalpel_version=scalpel_version,
            is_degenerate=bool(summary_diagnostics.get("is_degenerate", False)),
            preflight_reason=summary_diagnostics.get("preflight_reason"),
            tier_assignments=dict(tier_block.get("tier_assignments", {}) or {}),
            confirmed=[],
            tentative=[],
            rejected=[],
            nuisance_dropped=list(summary_diagnostics.get("nuisance_dropped", [])),
            full_importances=dict(primary.get("marginal_importances", {}) or {}),
            confirmed_importances={},
            cumulative_coverage={},
            lorenz_breakpoints={},
            boruta_hits={},
            boruta_p_values_bh={},
            stability_probabilities={},
            dba_prior_violations=[],
            summary_diagnostics=summary_diagnostics,
        )

    return TierDiagnostics(
        workload_label=str(full_payload.get("workload_label", workload_label)),
        algorithm=str(full_payload.get("algorithm", algorithm)),
        scalpel_version=full_payload.get("scalpel_version", scalpel_version),
        is_degenerate=bool(full_payload.get("is_degenerate", False)),
        preflight_reason=full_payload.get("preflight_reason"),
        tier_assignments=dict(full_payload.get("tier_assignments", {}) or {}),
        confirmed=list(full_payload.get("confirmed", []) or []),
        tentative=list(full_payload.get("tentative", []) or []),
        rejected=list(full_payload.get("rejected", []) or []),
        nuisance_dropped=list(full_payload.get("nuisance_dropped", []) or []),
        full_importances=dict(full_payload.get("full_importances", {}) or {}),
        confirmed_importances=dict(full_payload.get("confirmed_importances", {}) or {}),
        cumulative_coverage=dict(full_payload.get("cumulative_coverage", {}) or {}),
        lorenz_breakpoints=dict(full_payload.get("lorenz_breakpoints", {}) or {}),
        boruta_hits=dict(full_payload.get("boruta_hits", {}) or {}),
        boruta_p_values_bh=dict(full_payload.get("boruta_p_values_bh", {}) or {}),
        stability_probabilities=dict(
            full_payload.get("stability_probabilities", {}) or {}
        ),
        dba_prior_violations=list(full_payload.get("dba_prior_violations", []) or []),
        summary_diagnostics=dict(full_payload.get("summary", summary_diagnostics)),
    )
