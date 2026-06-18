"""Tier generation for knob importance.

The tiering algorithm is now :mod:`src.analysis.scalpel` (SCALPEL —
Significance-Coverage-stability Algorithm for Layered PErformance-knob
Labeling). The module-level helpers and :func:`generate_tiers` shim
below preserve the legacy ``(marginal_importances, workload_label)``
API used by callers that only retain a precomputed importance dict
(notably :func:`src.analysis.hardware_validator.validate_hardware_importance`);
they are wired through to a Lorenz cumulative-mass fallback in
:mod:`src.analysis.scalpel`. The full pipeline that runs on ``(X, y)``
lives in :func:`src.analysis.scalpel.scalpel_tier`.

See ``/home/eima40x4c/.claude/plans/distributed-toasting-sparrow.md``
for design rationale.
"""

from __future__ import annotations

import argparse
import json
import math
from dataclasses import dataclass
from pathlib import Path
from typing import TYPE_CHECKING, Iterable, Optional, Sequence

import pandas as pd

from src.knobs.knob_metadata import KNOB_TUNING_METADATA
from src.utils.logger import get_logger

if TYPE_CHECKING:  # pragma: no cover
    from src.analysis.scalpel import SCALPELResult

logger = get_logger(__name__)

DEFAULT_K_VALUES = (2, 3, 4, 5, 6)
DEFAULT_FALLBACK_K = 3
EXPERT_TIER_ORDER = {
    "minimal": 1,
    "core": 2,
    "standard": 3,
    "extensive": 4,
}


@dataclass
class AgreementReport:
    """Comparison between expert tiers and data-driven tiers.

    Attributes:
        agreements: Knobs where expert and data tiers match.
        promotions: Knobs promoted by data vs expert tiers.
        demotions: Knobs demoted by data vs expert tiers.
    """

    agreements: list[str]
    promotions: list[tuple[str, str, str]]
    demotions: list[tuple[str, str, str]]

    def to_dict(self) -> dict[str, object]:
        """Serialize report to a JSON-friendly dict."""
        return {
            "agreements": self.agreements,
            "promotions": [list(item) for item in self.promotions],
            "demotions": [list(item) for item in self.demotions],
        }


@dataclass
class TierResult:
    """Result container for legacy-shape tier output.

    Under SCALPEL the field semantics shift:

    * ``optimal_k`` = 4 on a successful tier assignment, 1 in the
      degenerate path. SCALPEL never emits other values.
    * ``silhouette_scores`` is always ``{}`` (silhouette is no longer
      part of the algorithm); the field is preserved for JSON
      compatibility with downstream readers.
    * ``tier_assignments`` only contains knobs that landed in
      ``minimal``/``core``/``standard`` — non-confirmed knobs are
      ABSENT (canonical ``extensive`` is ``null`` in the JSON contract,
      meaning "all tunable knobs"). This avoids
      ``hardware_validator._build_tier_rank_map`` KeyErrors.
    * ``jenks_breaks`` carries the Lorenz cumulative-mass cutoffs
      (default ``[0.50, 0.80]``) so the ``tier_generation`` JSON block
      remains schema-stable.
    * ``agreement_report`` is computed against ``EXPERT_TIER_ORDER``.

    For full per-knob diagnostics (BORUTA hits, BH p-values, stability
    probabilities, DBA-prior violations), use the SCALPELResult.
    """

    optimal_k: int
    silhouette_scores: dict[int, float]
    tier_assignments: dict[str, str]
    jenks_breaks: list[float]
    agreement_report: AgreementReport
    workload_label: str

    def to_dict(self) -> dict[str, object]:
        """Serialize result to a JSON-friendly dict."""
        return {
            "optimal_k": self.optimal_k,
            "silhouette_scores": {
                str(k): _clean_score(score)
                for k, score in self.silhouette_scores.items()
            },
            "tier_assignments": self.tier_assignments,
            "jenks_breaks": self.jenks_breaks,
            "agreement_report": self.agreement_report.to_dict(),
            "workload_label": self.workload_label,
        }


def _clean_score(score: float) -> float | None:
    """Convert NaN scores to None for JSON output.

    Retained for callers that still serialize legacy ``silhouette_scores``
    dicts; SCALPEL emits an empty silhouette mapping so this is rarely hit.
    """
    if math.isnan(score):
        return None
    return float(score)


def get_tier_names(k: int) -> list[str]:
    """Return tier labels for the requested tier count.

    With SCALPEL, ``k`` is always 4 on success or 1 in the degenerate
    path; older callers passing arbitrary k still work via the
    ``tier_1..tier_k`` fallback.
    """
    if k == 3:
        return ["minimal", "standard", "extensive"]
    if k == 4:
        return ["minimal", "core", "standard", "extensive"]
    if k == 1:
        return ["minimal"]

    logger.warning(
        "Non-standard tier count requested (k=%d); using tier_1..tier_%d.",
        k,
        k,
    )
    return [f"tier_{idx}" for idx in range(1, k + 1)]


def get_tier_rank_map(tier_names: Sequence[str]) -> dict[str, int]:
    """Build a ranking map for tier names."""
    return {tier: idx + 1 for idx, tier in enumerate(tier_names)}


def compare_to_expert(
    tier_assignments: dict[str, str],
    data_rank_map: dict[str, int],
) -> AgreementReport:
    """Compare data-driven tiers against expert tiers.

    Knobs absent from ``tier_assignments`` (under SCALPEL: not
    BORUTA-confirmed) and knobs whose tier is not in ``data_rank_map``
    are silently skipped.
    """
    agreements: list[str] = []
    promotions: list[tuple[str, str, str]] = []
    demotions: list[tuple[str, str, str]] = []

    for knob, data_tier in tier_assignments.items():
        metadata = KNOB_TUNING_METADATA.get(knob)
        if metadata is None:
            continue

        expert_tier = metadata.impact_tier
        expert_rank = EXPERT_TIER_ORDER.get(expert_tier)
        data_rank = data_rank_map.get(data_tier)
        if expert_rank is None or data_rank is None:
            continue

        if data_rank == expert_rank:
            agreements.append(knob)
        elif data_rank < expert_rank:
            promotions.append((knob, expert_tier, data_tier))
        else:
            demotions.append((knob, expert_tier, data_tier))

    return AgreementReport(
        agreements=agreements,
        promotions=promotions,
        demotions=demotions,
    )


def generate_tiers(
    marginal_importances: dict[str, float],
    workload_label: str,
    k_values: Sequence[int] = DEFAULT_K_VALUES,
    fallback_k: int = DEFAULT_FALLBACK_K,
) -> TierResult:
    """Generate tier assignments from a precomputed importance dict.

    Wraps :func:`src.analysis.scalpel.lorenz_tier_from_importances` to
    preserve the legacy ``(marginal_importances, workload_label)`` API
    used by :mod:`src.analysis.hardware_validator`. ``k_values`` and
    ``fallback_k`` are accepted for signature compatibility but unused
    — SCALPEL does not perform silhouette-driven k selection.

    For the full ``(X, y)`` pipeline (BORUTA + Lorenz + stability),
    call :func:`src.analysis.scalpel.scalpel_tier` directly.

    Raises
    ------
    ValueError
        If no importance scores are provided.
    """
    from src.analysis.scalpel import lorenz_tier_from_importances  # local import to break cycle

    return lorenz_tier_from_importances(
        marginal_importances=marginal_importances,
        workload_label=workload_label,
    )


def export_data_driven_tiers(
    marginal_importances: Optional[dict[str, float]] = None,
    workload_label: str = "unknown",
    output_path: Path | None = None,
    source_results: str = "",
    *,
    scalpel_result: Optional["SCALPELResult"] = None,
    write_diagnostics: bool = False,
) -> TierResult:
    """Export tier assignments as a ``data_driven_tiers.json`` file.

    Two call modes:

    * **SCALPEL primary** — pass a populated :class:`SCALPELResult`. The
      tier assignments + diagnostics come from the full pipeline.
    * **Lorenz fallback** — pass ``marginal_importances`` only. Used by
      :mod:`src.analysis.hardware_validator` for cross-hardware combined
      models, which only retain a precomputed importance dict.

    Tier shape on disk is unchanged from the legacy schema (the canonical
    keys ``minimal``, ``core``, ``standard`` carry NON-cumulative knob
    lists; ``extensive`` is ``null``). New keys appear under ``metadata``:
    ``algorithm = "scalpel-v1"``, ``scalpel_version``, and a pruned
    ``diagnostics`` block. Optional ``scalpel_diagnostics.json`` sibling
    holds the full per-knob payload when ``write_diagnostics=True``.

    Returns
    -------
    TierResult
        Adapted view of the SCALPEL run (or the Lorenz fallback) for
        callers that still consume legacy fields.
    """
    from datetime import datetime, timezone
    import os

    from src.analysis.scalpel import (  # local import to break cycle
        DEFAULT_LORENZ_BREAKPOINTS,
        SCALPEL_ALGORITHM_SLUG,
        SCALPEL_VERSION,
        lorenz_tier_from_importances,
    )

    if scalpel_result is None and not marginal_importances:
        raise ValueError(
            "export_data_driven_tiers requires either scalpel_result or "
            "non-empty marginal_importances."
        )

    if output_path is None:
        output_path = (
            Path("data")
            / "data_driven_knobs"
            / workload_label
            / "data_driven_tiers.json"
        )

    if scalpel_result is not None:
        tier_assignments = dict(scalpel_result.tier_assignments)
        diagnostics_block = scalpel_result.diagnostics_pruned()
        tier_result = scalpel_result.to_tier_result(workload_label=workload_label)
    else:
        tier_result = lorenz_tier_from_importances(
            marginal_importances=marginal_importances,  # type: ignore[arg-type]
            workload_label=workload_label,
        )
        tier_assignments = dict(tier_result.tier_assignments)
        diagnostics_block = {
            "lorenz_cutoffs": list(DEFAULT_LORENZ_BREAKPOINTS),
            "source": "lorenz_fallback",
        }

    canonical_assignments: dict[str, list[str]] = {
        "minimal": [],
        "core": [],
        "standard": [],
    }
    for knob, tier_name in tier_assignments.items():
        if tier_name in canonical_assignments:
            canonical_assignments[tier_name].append(knob)

    tiers: dict[str, list[str] | None] = {
        "minimal": sorted(canonical_assignments["minimal"]),
        "core": sorted(canonical_assignments["core"]),
        "standard": sorted(canonical_assignments["standard"]),
        "extensive": None,
    }

    payload = {
        "metadata": {
            "workload_type": workload_label,
            "generated_at": datetime.now(timezone.utc).isoformat(),
            "algorithm": SCALPEL_ALGORITHM_SLUG,
            "scalpel_version": SCALPEL_VERSION,
            "source_results": source_results,
            "diagnostics": diagnostics_block,
        },
        "tiers": tiers,
    }

    output_path.parent.mkdir(parents=True, exist_ok=True)
    tmp_path = output_path.with_suffix(output_path.suffix + ".tmp")
    with tmp_path.open("w", encoding="utf-8") as f:
        json.dump(payload, f, indent=2)
        f.write("\n")
    os.replace(tmp_path, output_path)

    if write_diagnostics and scalpel_result is not None:
        diag_path = output_path.parent / "scalpel_diagnostics.json"
        diag_tmp = diag_path.with_suffix(diag_path.suffix + ".tmp")
        with diag_tmp.open("w", encoding="utf-8") as f:
            json.dump(scalpel_result.diagnostics_full(), f, indent=2)
            f.write("\n")
        os.replace(diag_tmp, diag_path)
        logger.info("Wrote SCALPEL diagnostics to %s", diag_path)

    logger.info("Exported data-driven tiers to %s", output_path)
    return tier_result


def load_importances_csv(csv_path: Path) -> dict[str, float]:
    """Load marginal importances from a CSV file.

    Args:
        csv_path: Path to the CSV file.

    Returns:
        Mapping of knob name to importance score.
    """
    df = pd.read_csv(csv_path)
    if "knob" not in df.columns or "fanova_importance" not in df.columns:
        raise ValueError("CSV must contain 'knob' and 'fanova_importance' columns.")

    return {
        str(row["knob"]): float(row["fanova_importance"]) for _, row in df.iterrows()
    }


def write_tier_result(output_path: Path, result: TierResult) -> None:
    """Write a TierResult to disk.

    Args:
        output_path: Output JSON path.
        result: TierResult to serialize.
    """
    output_path.parent.mkdir(parents=True, exist_ok=True)
    with output_path.open("w", encoding="utf-8") as handle:
        json.dump(result.to_dict(), handle, indent=2)


def compare_tier_results(result_a: Path, result_b: Path) -> dict[str, object]:
    """Compare two tier result JSON files.

    Args:
        result_a: Path to the first tier result JSON.
        result_b: Path to the second tier result JSON.

    Returns:
        Comparison report dictionary.
    """
    with result_a.open("r", encoding="utf-8") as handle:
        data_a = json.load(handle)
    with result_b.open("r", encoding="utf-8") as handle:
        data_b = json.load(handle)

    assignments_a = data_a.get("tier_assignments", {})
    assignments_b = data_b.get("tier_assignments", {})

    knobs_a = set(assignments_a.keys())
    knobs_b = set(assignments_b.keys())
    common_knobs = knobs_a & knobs_b

    shifted_knobs = [
        {
            "knob": knob,
            "tier_a": assignments_a[knob],
            "tier_b": assignments_b[knob],
        }
        for knob in sorted(common_knobs)
        if assignments_a[knob] != assignments_b[knob]
    ]

    return {
        "workload_a": data_a.get("workload_label", "unknown"),
        "workload_b": data_b.get("workload_label", "unknown"),
        "n_common_knobs": len(common_knobs),
        "n_shifted_knobs": len(shifted_knobs),
        "shifted_knobs": shifted_knobs,
        "only_in_a": sorted(knobs_a - knobs_b),
        "only_in_b": sorted(knobs_b - knobs_a),
    }


def _parse_k_values(values: Iterable[int]) -> list[int]:
    """Normalize and validate k-values.

    Args:
        values: Iterable of k values.

    Returns:
        Sorted list of unique k values.
    """
    parsed = sorted({int(value) for value in values})
    if not parsed:
        raise ValueError("At least one k value is required.")
    return parsed


def parse_args() -> argparse.Namespace:
    """Parse command-line arguments.

    Returns:
        Parsed CLI arguments.
    """
    parser = argparse.ArgumentParser(
        description="Generate knob tiers using Jenks Natural Breaks."
    )
    parser.add_argument(
        "--fanova-csv",
        type=Path,
        default=Path("results/analysis/importance/fanova_marginal_importance.csv"),
        help="Path to fanova_marginal_importance.csv",
    )
    parser.add_argument(
        "--workload-label",
        type=str,
        default="unknown",
        help="Workload label for the output JSON.",
    )
    parser.add_argument(
        "--output",
        type=Path,
        default=Path("results/analysis/importance/tier_result.json"),
        help="Output path for tier results or comparison report.",
    )
    parser.add_argument(
        "--k-values",
        type=int,
        nargs="+",
        default=list(DEFAULT_K_VALUES),
        help="Candidate k values for silhouette validation.",
    )
    parser.add_argument(
        "--fallback-k",
        type=int,
        default=DEFAULT_FALLBACK_K,
        help="Fallback k when silhouette is undefined.",
    )
    parser.add_argument(
        "--compare",
        type=Path,
        nargs=2,
        metavar=("RESULT_A", "RESULT_B"),
        help="Compare two tier result JSON files.",
    )
    return parser.parse_args()


def main() -> None:
    """CLI entrypoint."""
    args = parse_args()

    if args.compare:
        report = compare_tier_results(args.compare[0], args.compare[1])
        args.output.parent.mkdir(parents=True, exist_ok=True)
        with args.output.open("w", encoding="utf-8") as handle:
            json.dump(report, handle, indent=2)
        print(f"Comparison written to {args.output}")
        print(f"Shifted knobs: {report['n_shifted_knobs']}")
        return

    importances = load_importances_csv(args.fanova_csv)
    k_values = _parse_k_values(args.k_values)

    result = generate_tiers(
        marginal_importances=importances,
        workload_label=args.workload_label,
        k_values=k_values,
        fallback_k=args.fallback_k,
    )

    write_tier_result(args.output, result)
    print(f"Tier result written to {args.output}")
    print(f"Optimal k: {result.optimal_k}")
    print(f"Workload: {result.workload_label}")


if __name__ == "__main__":
    main()
