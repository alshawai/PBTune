"""
Knob Importance Analysis CLI
============================

CLI entry point for analyzing PBT knob importance using fANOVA and SHAP.
"""

import argparse
import json
import sys
from pathlib import Path
from typing import Any

from src.analysis.data_loader import load_pbt_results, LoadedData
from src.analysis.hardware_validator import (
    validate_hardware_importance,
    build_hardware_profile_key,
)
from src.analysis.importance import ImportanceResult, analyze_knob_importance
from src.analysis.tier_generator import TierResult, generate_tiers
from src.utils.logger import get_logger
from src.utils.logger.setup import setup_logging

LOGGER = get_logger(__name__)


def parse_args() -> argparse.Namespace:
    """Parse command-line arguments."""
    parser = argparse.ArgumentParser(
        description="Analyze knob importance from PBT results."
    )
    parser.add_argument(
        "--results-dir",
        type=Path,
        help="Directory containing pbt_results_*.json",
    )
    parser.add_argument(
        "--output-dir",
        type=Path,
        help="Where to save plots, reports, and logs. Default: results/{workload}/analysis/",
    )
    parser.add_argument(
        "--workload-label",
        type=str,
        default="auto",
        help="Label for this run ('oltp', 'olap')",
    )
    parser.add_argument(
        "--top-k",
        type=int,
        default=20,
        help="Number of top knobs for detailed analysis",
    )
    parser.add_argument(
        "--interaction-order",
        type=int,
        default=2,
        help="Max interaction order (2 = pairwise, 3 = 3-way)",
    )
    parser.add_argument(
        "--n-estimators",
        type=int,
        default=64,
        help="Random Forest tree count",
    )
    parser.add_argument(
        "--random-seed",
        type=int,
        default=42,
        help="Reproducibility seed",
    )
    parser.add_argument(
        "--skip-shap",
        action="store_true",
        help="Skip TreeSHAP computation (faster for initial exploration)",
    )
    parser.add_argument(
        "--hardware-validation",
        action="store_true",
        help="Enable cross-hardware validation",
    )
    parser.add_argument(
        "--compare",
        type=Path,
        nargs=2,
        metavar=("RESULT_A", "RESULT_B"),
        help="Compare two previously generated importance_results.json files across workloads",
    )

    return parser.parse_args()


def _print_comparison_report(data_a: dict[str, Any], data_b: dict[str, Any]) -> None:
    """Print a cross-workload comparison report for two importance results."""
    imp_a = data_a.get("marginal_importances", {})
    imp_b = data_b.get("marginal_importances", {})
    tier_a = data_a.get("tier_assignments", {})
    tier_b = data_b.get("tier_assignments", {})

    all_knobs = sorted(set(imp_a.keys()) | set(imp_b.keys()))

    LOGGER.info("Cross-Workload Comparison Report:")
    print(
        f"{'Knob':<40} | {'Rank A':<8} | {'Rank B':<8} | "
        f"{'Shift':<6} | {'Tier A':<10} | {'Tier B':<10}"
    )
    print("-" * 95)

    sorted_a = sorted(imp_a.keys(), key=lambda k: imp_a[k], reverse=True)
    rank_a = {k: idx + 1 for idx, k in enumerate(sorted_a)}

    sorted_b = sorted(imp_b.keys(), key=lambda k: imp_b[k], reverse=True)
    rank_b = {k: idx + 1 for idx, k in enumerate(sorted_b)}

    for knob in all_knobs:
        ra = rank_a.get(knob, "N/A")
        rb = rank_b.get(knob, "N/A")

        shift_str = "N/A"
        if ra != "N/A" and rb != "N/A":
            shift = int(ra) - int(rb)  # positive means it moved up in B
            shift_str = f"+{shift}" if shift > 0 else str(shift)

        ta = tier_a.get(knob, "N/A")
        tb = tier_b.get(knob, "N/A")

        print(
            f"{knob:<40} | {str(ra):<8} | {str(rb):<8} | "
            f"{shift_str:<6} | {ta:<10} | {tb:<10}"
        )


def compare_results(result_a_path: Path, result_b_path: Path) -> None:
    """Load and compare two importance result JSON files."""
    LOGGER.info("Comparing %s and %s", result_a_path, result_b_path)
    try:
        with open(result_a_path, "r", encoding="utf-8") as f:
            data_a = json.load(f)
        with open(result_b_path, "r", encoding="utf-8") as f:
            data_b = json.load(f)
    except (json.JSONDecodeError, FileNotFoundError) as exc:
        LOGGER.error("Failed to load result files for comparison: %s", exc)
        sys.exit(1)

    _print_comparison_report(data_a, data_b)


def _resolve_workload_and_output_dir(
    loaded_data: LoadedData,
    workload_label: str,
    output_dir: Path | None,
) -> tuple[str, Path]:
    """Resolve the actual workload name and appropriate output directory."""
    actual_workload = workload_label if workload_label != "auto" else "unknown"

    if workload_label == "auto" and loaded_data.metadata:
        actual_workload = loaded_data.metadata[0].get("workload_type", "unknown")

    if output_dir:
        out_dir = output_dir
    else:
        out_dir = Path(f"results/{actual_workload}/analysis/")

    out_dir.mkdir(parents=True, exist_ok=True)
    return actual_workload, out_dir


def _save_analysis_results(
    out_dir: Path,
    actual_workload: str,
    importance_result: ImportanceResult,
    tier_result: TierResult,
) -> None:
    """Serialize the analysis outcomes to a JSON file."""
    out_json = out_dir / "importance_results.json"
    result_dict = {
        "workload_type": actual_workload,
        "model_r2": importance_result.model_r2,
        "n_samples": importance_result.n_samples,
        "n_features": importance_result.n_features,
        "fanova_shap_correlation": importance_result.fanova_shap_correlation,
        "marginal_importances": importance_result.marginal_importances,
        "pairwise_interactions": {
            f"{k1},{k2}": v
            for (k1, k2), v in importance_result.pairwise_interactions.items()
        },
        "shap_importances": importance_result.shap_importances,
        "tier_generation": {
            "optimal_k": tier_result.optimal_k,
            "silhouette_scores": tier_result.silhouette_scores,
            "tier_assignments": tier_result.tier_assignments,
            "jenks_breaks": tier_result.jenks_breaks,
            "agreement_report": tier_result.agreement_report.to_dict()
            if tier_result.agreement_report
            else None,
            "workload_label": tier_result.workload_label,
        },
    }

    with open(out_json, "w", encoding="utf-8") as f:
        json.dump(result_dict, f, indent=2)

    LOGGER.info("Saved results to %s", out_json)


def _print_analysis_summary(
    actual_workload: str,
    importance_result: ImportanceResult,
    tier_result: TierResult,
) -> None:
    """Print a summary of the analyzed importance and tier assignments."""
    print("\nKnob Importance Summary:")
    print("========================")
    print(f"Workload: {actual_workload}")
    print(f"R² Score: {importance_result.model_r2:.3f}")
    print(
        f"Analyzed {importance_result.n_features} knobs over "
        f"{importance_result.n_samples} samples"
    )
    print("\nTop 10 Knobs by fANOVA Marginal Importance:")

    top_knobs = list(importance_result.marginal_importances.items())[:10]
    for idx, (knob, imp) in enumerate(top_knobs, start=1):
        tier = tier_result.tier_assignments.get(knob, "unknown")
        print(f"  {idx:2d}. {knob:<35} {imp:.4f}  (Tier: {tier})")


def _group_files_by_hardware(
    results_dir: Path,
) -> tuple[dict[str, tuple[list[Path], dict[str, Any]]], str]:
    """Group JSON result files by hardware profile key and extract workload."""
    groups: dict[str, tuple[list[Path], dict[str, Any]]] = {}
    json_files = sorted(results_dir.glob("pbt_results_*.json"), key=lambda p: p.name)

    first_workload = "unknown"

    for idx, file_path in enumerate(json_files):
        try:
            with open(file_path, "r", encoding="utf-8") as f:
                data = json.load(f)

            # Opportunistically extract the true workload from the first file
            if idx == 0:
                session_meta = data.get("tuning_session", {})
                first_workload = session_meta.get("workload_type", "unknown")

            wr = data.get("worker_resources", {})
            if not wr:
                # Default empty resources if missing to avoid dropping files entirely
                wr = {"cpu_cores": 1, "ram_bytes": 1024**3, "disk_type": "unknown"}

            key = build_hardware_profile_key(wr)
            if key not in groups:
                groups[key] = ([], wr)
            groups[key][0].append(file_path)
        except Exception as exc:
            LOGGER.error("Failed to read %s for grouping: %s", file_path, exc)

    return groups, first_workload


def run_analysis_pipeline(args: argparse.Namespace) -> None:
    """Execute the full end-to-end analysis pipeline."""
    if not args.results_dir:
        LOGGER.error("--results-dir is required when not using --compare")
        sys.exit(1)

    # First, group files and optionally auto-detect the workload type
    groups, auto_workload = _group_files_by_hardware(args.results_dir)

    initial_workload = (
        args.workload_label if args.workload_label != "auto" else auto_workload
    )

    temp_out_dir = args.output_dir or Path(f"results/{initial_workload}/analysis/")
    temp_out_dir.mkdir(parents=True, exist_ok=True)
    setup_logging(verbosity="INFO", output_file=temp_out_dir / "analysis_log.html")

    LOGGER.info("Starting Knob Importance Analysis")
    LOGGER.info("Loading and grouping data from %s", args.results_dir)

    if not groups:
        LOGGER.error("No valid JSON results found in %s", args.results_dir)
        sys.exit(1)

    LOGGER.info("Found %d distinct hardware profile(s).", len(groups))

    profile_results: list[tuple[ImportanceResult, dict[str, Any]]] = []
    combined_data_list: list[tuple[LoadedData, dict[str, Any]]] = []

    # Keep track of the final resolved paths for the combined model output
    final_actual_workload = initial_workload
    final_base_out_dir = temp_out_dir

    for hw_key, (file_paths, wr) in groups.items():
        LOGGER.info(
            "--- Processing hardware profile: %s (%d files) ---",
            hw_key,
            len(file_paths),
        )
        loaded_data = load_pbt_results(
            args.results_dir,
            default_workload_type=initial_workload,
            file_paths=file_paths,
        )

        actual_workload, base_out_dir = _resolve_workload_and_output_dir(
            loaded_data=loaded_data,
            workload_label=args.workload_label,
            output_dir=args.output_dir,
        )

        final_actual_workload = actual_workload
        final_base_out_dir = base_out_dir

        # If multiple groups exist, put them in subdirectories so they don't overwrite
        if len(groups) > 1:
            out_dir = base_out_dir / hw_key
            out_dir.mkdir(parents=True, exist_ok=True)
        else:
            out_dir = base_out_dir

        # Re-configure logging in case the directory path was resolved to a new location
        setup_logging(verbosity="INFO", output_file=out_dir / "analysis_log.html")

        LOGGER.info(
            "Running fANOVA%s for %s...",
            " (skipping SHAP)" if args.skip_shap else "/SHAP",
            hw_key,
        )
        importance_result = analyze_knob_importance(
            loaded_data,
            n_estimators=args.n_estimators,
            random_state=args.random_seed,
            top_k=args.top_k,
            interaction_order=args.interaction_order,
            skip_shap=args.skip_shap,
        )

        LOGGER.info("Running tier generation for %s...", hw_key)
        tier_result = generate_tiers(
            marginal_importances=importance_result.marginal_importances,
            workload_label=actual_workload,
        )

        _save_analysis_results(out_dir, actual_workload, importance_result, tier_result)

        # Only print summary for the first group to avoid extreme console spam
        if len(profile_results) == 0:
            _print_analysis_summary(actual_workload, importance_result, tier_result)

        profile_results.append((importance_result, wr))
        combined_data_list.append((loaded_data, wr))

    if args.hardware_validation or len(groups) > 1:
        LOGGER.info("Running cross-hardware validation and building combined model...")
        hw_val_res = validate_hardware_importance(
            profile_results, combined_data=combined_data_list
        )

        LOGGER.info(
            "Hardware validation completed! Stable universal knobs: %d",
            len(hw_val_res.stable_knobs),
        )
        if hw_val_res.combined_importances:
            LOGGER.info(
                "Successfully built combined fANOVA model across all hardware profiles."
            )

            # Save the combined model outputs to the root analysis dir
            combined_tier = generate_tiers(
                marginal_importances=hw_val_res.combined_importances.marginal_importances,
                workload_label=final_actual_workload,
            )
            _save_analysis_results(
                final_base_out_dir,
                final_actual_workload,
                hw_val_res.combined_importances,
                combined_tier,
            )
            LOGGER.info(
                "Combined model saved to %s",
                final_base_out_dir / "importance_results.json",
            )

        # Save the full HardwareValidationResult metrics (taus, stable knobs, shifts)
        # We append this to the main importance_results.json file.
        main_json_path = final_base_out_dir / "importance_results.json"

        # Convert tuple keys in kendall_taus to strings for JSON serialization
        json_taus = {f"{k[0]}_vs_{k[1]}": v for k, v in hw_val_res.kendall_taus.items()}

        report_dict = {
            "stable_knobs": hw_val_res.stable_knobs,
            "shifting_knobs": hw_val_res.shifting_knobs,
            "conservative_tiers": hw_val_res.conservative_tiers,
            "kendall_taus": json_taus,
        }

        try:
            with open(main_json_path, "r", encoding="utf-8") as f:
                existing_data = json.load(f)

            existing_data["hardware_validation"] = report_dict

            with open(main_json_path, "w", encoding="utf-8") as f:
                json.dump(existing_data, f, indent=2)

            LOGGER.info("Merged hardware validation report into %s", main_json_path)
        except Exception as exc:
            LOGGER.error(
                "Failed to merge hardware validation into main results: %s", exc
            )


def main() -> None:
    """CLI entry point for knob importance logic."""
    args = parse_args()

    if args.compare:
        setup_logging(verbosity="INFO")
        compare_results(args.compare[0], args.compare[1])
    else:
        run_analysis_pipeline(args)


if __name__ == "__main__":
    main()
