"""Tier generation for knob importance using Jenks Natural Breaks."""

from __future__ import annotations

import argparse
import json
import math
from dataclasses import dataclass
from pathlib import Path
from typing import Iterable, Sequence

import numpy as np
import pandas as pd
from jenkspy import jenks_breaks
from sklearn.metrics import silhouette_score

from src.knobs.knob_metadata import KNOB_TUNING_METADATA
from src.utils.logger import get_logger

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
    """Result container for Jenks-based tiering.

    Attributes:
        optimal_k: Selected tier count.
        silhouette_scores: Silhouette scores per candidate k.
        tier_assignments: Mapping from knob name to tier name.
        jenks_breaks: Breakpoints returned by Jenks Natural Breaks.
        agreement_report: Expert vs data agreement report.
        workload_label: Label identifying the workload.
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
                str(k): _clean_score(score) for k, score in self.silhouette_scores.items()
            },
            "tier_assignments": self.tier_assignments,
            "jenks_breaks": self.jenks_breaks,
            "agreement_report": self.agreement_report.to_dict(),
            "workload_label": self.workload_label,
        }


def _clean_score(score: float) -> float | None:
    """Convert NaN scores to None for JSON output.

    Args:
        score: Silhouette score value.

    Returns:
        Score as a float, or None when the score is NaN.
    """
    if math.isnan(score):
        return None
    return float(score)


def get_tier_names(k: int) -> list[str]:
    """Return tier labels for the requested tier count.

    Args:
        k: Number of tiers.

    Returns:
        List of tier names ordered from most important to least important.
    """
    if k == 3:
        return ["minimal", "standard", "extensive"]
    if k == 4:
        return ["minimal", "core", "standard", "extensive"]

    logger.warning(
        "Non-standard tier count requested (k=%d); using tier_1..tier_%d.",
        k,
        k,
    )
    return [f"tier_{idx}" for idx in range(1, k + 1)]


def get_tier_rank_map(tier_names: Sequence[str]) -> dict[str, int]:
    """Build a ranking map for tier names.

    Args:
        tier_names: Ordered tier names from most important to least important.

    Returns:
        Mapping from tier name to rank (1 = most important).
    """
    return {tier: idx + 1 for idx, tier in enumerate(tier_names)}


def _assign_interval_index(value: float, breaks: Sequence[float]) -> int:
    """Assign a value to a Jenks interval index (ascending order).

    Args:
        value: Importance score value.
        breaks: Jenks breakpoints in ascending order.

    Returns:
        Zero-based interval index.
    """
    last_index = len(breaks) - 2
    for idx in range(last_index):
        if value <= breaks[idx + 1]:
            return idx
    return last_index


def _assign_labels(scores: np.ndarray, breaks: Sequence[float]) -> list[int]:
    """Assign Jenks cluster labels for each score.

    Args:
        scores: Array of importance scores.
        breaks: Jenks breakpoints in ascending order.

    Returns:
        List of cluster labels in ascending importance order.
    """
    return [_assign_interval_index(float(score), breaks) for score in scores]


def _safe_silhouette(scores: np.ndarray, labels: list[int]) -> float:
    """Compute silhouette score or return NaN when undefined.

    Args:
        scores: Array of importance scores.
        labels: Cluster labels from Jenks.

    Returns:
        Silhouette score or NaN if undefined.
    """
    unique_labels = set(labels)
    if len(unique_labels) < 2 or len(unique_labels) >= len(scores):
        return float("nan")

    try:
        return float(silhouette_score(scores.reshape(-1, 1), labels))
    except ValueError:
        return float("nan")


def _resolve_optimal_k(
    scores: np.ndarray,
    k_values: Sequence[int],
    fallback_k: int,
) -> tuple[int, dict[int, float]]:
    """Evaluate silhouette scores and select the best k.

    Args:
        scores: Array of importance scores.
        k_values: Candidate k values to evaluate.
        fallback_k: Fallback k when silhouettes are undefined.

    Returns:
        Tuple of optimal k and silhouette score mapping.
    """
    silhouette_scores: dict[int, float] = {}
    n_samples = len(scores)
    unique_count = len(np.unique(scores))

    for k in k_values:
        if k < 2 or k > n_samples or k > unique_count:
            silhouette_scores[k] = float("nan")
            continue

        breaks = jenks_breaks(scores.tolist(), k)
        labels = _assign_labels(scores, breaks)
        silhouette_scores[k] = _safe_silhouette(scores, labels)

    valid_scores = {
        k: score for k, score in silhouette_scores.items() if not math.isnan(score)
    }
    if not valid_scores:
        logger.warning(
            "Unable to compute silhouette scores; falling back to k=%d.",
            fallback_k,
        )
        return min(fallback_k, n_samples), silhouette_scores

    optimal_k = max(valid_scores.items(), key=lambda item: item[1])[0]
    return optimal_k, silhouette_scores


def compare_to_expert(
    tier_assignments: dict[str, str],
    data_rank_map: dict[str, int],
) -> AgreementReport:
    """Compare data-driven tiers against expert tiers.

    Args:
        tier_assignments: Mapping of knob name to data-driven tier.
        data_rank_map: Mapping of data-driven tier to rank.

    Returns:
        AgreementReport with agreements, promotions, and demotions.
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
    """Generate tier assignments from marginal importance scores.

    Args:
        marginal_importances: Mapping of knob name to fANOVA importance score.
        workload_label: Workload label to tag the output.
        k_values: Candidate k values for silhouette validation.
        fallback_k: Fallback tier count when silhouette is undefined.

    Returns:
        TierResult containing tier assignments and validation metadata.

    Raises:
        ValueError: If no importance scores are provided.
    """
    if not marginal_importances:
        raise ValueError("Marginal importance scores are required.")

    knobs = list(marginal_importances.keys())
    scores = np.array([float(marginal_importances[knob]) for knob in knobs])
    n_samples = len(scores)

    if n_samples == 1:
        tier_names = get_tier_names(1)
        tier_assignments = {knobs[0]: tier_names[0]}
        data_rank_map = get_tier_rank_map(tier_names)
        agreement_report = compare_to_expert(tier_assignments, data_rank_map)
        return TierResult(
            optimal_k=1,
            silhouette_scores={},
            tier_assignments=tier_assignments,
            jenks_breaks=[scores[0], scores[0]],
            agreement_report=agreement_report,
            workload_label=workload_label,
        )

    use_uniform_breaks = False
    if np.allclose(scores, scores[0]):
        logger.warning(
            "All importance scores are equal; silhouette is undefined. "
            "Falling back to k=%d.",
            fallback_k,
        )
        optimal_k = min(fallback_k, n_samples)
        silhouette_scores = {k: float("nan") for k in k_values}
        use_uniform_breaks = True
    else:
        optimal_k, silhouette_scores = _resolve_optimal_k(scores, k_values, fallback_k)

    if use_uniform_breaks:
        breaks = [float(scores[0])] * (optimal_k + 1)
        labels = [0] * n_samples
    else:
        breaks = jenks_breaks(scores.tolist(), optimal_k)
        labels = _assign_labels(scores, breaks)
    tier_names = get_tier_names(optimal_k)

    tier_assignments: dict[str, str] = {}
    for knob, label in zip(knobs, labels):
        tier_index = (optimal_k - 1) - label
        tier_assignments[knob] = tier_names[tier_index]

    data_rank_map = get_tier_rank_map(tier_names)
    agreement_report = compare_to_expert(tier_assignments, data_rank_map)

    return TierResult(
        optimal_k=optimal_k,
        silhouette_scores=silhouette_scores,
        tier_assignments=tier_assignments,
        jenks_breaks=[float(value) for value in breaks],
        agreement_report=agreement_report,
        workload_label=workload_label,
    )


def load_importances_csv(csv_path: Path) -> dict[str, float]:
    """Load marginal importances from a CSV file.

    Args:
        csv_path: Path to the CSV file.

    Returns:
        Mapping of knob name to importance score.
    """
    df = pd.read_csv(csv_path)
    if "knob" not in df.columns or "fanova_importance" not in df.columns:
        raise ValueError(
            "CSV must contain 'knob' and 'fanova_importance' columns."
        )

    return {
        str(row["knob"]): float(row["fanova_importance"])
        for _, row in df.iterrows()
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
