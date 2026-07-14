"""
Knob Importance Analysis CLI
============================

CLI entry point for analyzing PBT knob importance using fANOVA and SHAP
and emitting data-driven tier assignments via SCALPEL.

See :mod:`src.analysis.scalpel` for the tier-generation algorithm and
``/home/eima40x4c/.claude/plans/distributed-toasting-sparrow.md`` for
the full design.
"""

import argparse
import json
import sys
from pathlib import Path
from typing import Any, Optional

import pandas as pd

from src.analysis.data_loader import load_pbt_results, LoadedData, find_result_files
from src.analysis.hardware_validator import (
    validate_hardware_importance,
    build_hardware_profile_key,
)
from src.analysis.importance import ImportanceResult, analyze_knob_importance
from src.analysis.scalpel import (
    SCALPELHyperparameters,
    SCALPELResult,
    SCALPEL_ALGORITHM_SLUG,
    scalpel_tier,
)
from src.analysis.tier_generator import (
    TierResult,
    generate_tiers,
    export_data_driven_tiers,
)
from src.knobs.knob_metadata import KNOB_TUNING_METADATA
from src.utils.logger import get_logger
from src.utils.logger.setup import setup_logging

LOGGER = get_logger("Analyzer")


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
        default=None,
        help="Where to save plots, reports, and logs. Default: results/analysis/{workload_type}/",
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
        "--export-tiers",
        type=Path,
        default=None,
        metavar="PATH",
        help=(
            "Export final tier assignments to a data_driven_tiers.json file "
            "consumable by preprocess_knobs.py and the tuner. "
            "Uses the canonical 4-tier system [minimal, core, standard, extensive]. "
            "Default path when flag is present without a value: "
            "data/data_driven_knobs/{workload_type}/data_driven_tiers.json"
        ),
        nargs="?",
        const=Path("auto"),
    )
    parser.add_argument(
        "--compare",
        type=Path,
        nargs=2,
        metavar=("RESULT_A", "RESULT_B"),
        help="Compare two previously generated importance_results.json files across workloads",
    )

    # SCALPEL pipeline knobs (see /home/eima40x4c/.claude/plans/distributed-toasting-sparrow.md)
    parser.add_argument(
        "--algorithm",
        choices=["scalpel", "legacy"],
        default="scalpel",
        help=(
            "Tier-generation algorithm. 'scalpel' (default) runs the full "
            "BORUTA + Lorenz + stability pipeline. 'legacy' uses the Lorenz "
            "fallback only — equivalent to the importance-dict-only path "
            "used by hardware_validator (no significance gate, no stability)."
        ),
    )
    parser.add_argument(
        "--scalpel-base-seed",
        type=int,
        default=42,
        help="Base seed; per-workload seeds are derived as hash((seed, workload)).",
    )
    parser.add_argument(
        "--scalpel-fdr-q",
        type=float,
        default=0.10,
        help="BH-FDR target for the BORUTA significance gate.",
    )
    parser.add_argument(
        "--scalpel-boruta-iter",
        type=int,
        default=100,
        help="Number of BORUTA shadow iterations.",
    )
    parser.add_argument(
        "--scalpel-stability-b",
        type=int,
        default=50,
        help=(
            "Number of group-clustered stability subsamples. Default 50 "
            "(Meinshausen and Buhlmann 2010 B in [50, 100], matches "
            "stabs::stabsel default)."
        ),
    )
    parser.add_argument(
        "--scalpel-stability-iter",
        type=int,
        default=None,
        help=(
            "BORUTA iteration count inside each stability subsample. "
            "When omitted, falls through to --scalpel-boruta-iter so "
            "the binomial null is calibrated against the same iteration "
            "count as the primary pass."
        ),
    )
    parser.add_argument(
        "--scalpel-stability-jobs",
        type=int,
        default=4,
        help=(
            "Worker processes for parallel stability subsamples. "
            "1 keeps the historical sequential path."
        ),
    )
    parser.add_argument(
        "--scalpel-coverage-minimal",
        type=float,
        default=0.50,
        help="Cumulative-mass cut for the data-driven 'minimal' tier.",
    )
    parser.add_argument(
        "--scalpel-coverage-core",
        type=float,
        default=0.80,
        help="Cumulative-mass cut for the data-driven 'core' tier.",
    )
    parser.add_argument(
        "--scalpel-interaction-alpha",
        type=float,
        default=0.5,
        help=(
            "Weight on the per-knob max pairwise interaction in the "
            "fused Lorenz input (marginal + alpha * max_interaction). "
            "Pass 0.0 to recover the marginal-only baseline."
        ),
    )
    parser.add_argument(
        "--scalpel-interaction-top-k",
        type=int,
        default=20,
        help=(
            "Search frontier for pairwise interactions: only the K knobs "
            "with the largest marginals get queried, capping the cost at "
            "O(K * p) instead of O(p^2). Pass 0 to skip interactions."
        ),
    )
    parser.add_argument(
        "--scalpel-rf-trees",
        type=int,
        default=500,
        help="Random Forest n_estimators for SCALPEL surrogate.",
    )
    parser.add_argument(
        "--scalpel-nuisance-overrides",
        type=str,
        default="",
        help=(
            "Comma-separated list of knobs that should NOT be filtered "
            "by IMPORTANCE_NUISANCE_EXCLUSIONS / PREFIXES."
        ),
    )

    # Multi-workload rollout — replaces a standalone regenerate script.
    parser.add_argument(
        "--all-workloads",
        type=Path,
        default=None,
        metavar="RESULTS_ROOT",
        help=(
            "Glob the given root for tuning-session directories under "
            "'<root>/sessions/*/pbt/extensive/traces' and run the "
            "analysis pipeline per discovered workload. Failures on one "
            "workload are logged and skipped, never fatal. Overrides "
            "--results-dir / --workload-label."
        ),
    )
    parser.add_argument(
        "--results-glob",
        type=str,
        default="sessions/*/pbt/extensive/traces",
        help=(
            "Glob (relative to --all-workloads) used to discover "
            "tuning-session directories. Default targets the canonical "
            "extensive subtree so SCALPEL operates on the broadest tunable space."
        ),
    )

    return parser.parse_args()


def _print_comparison_report(data_a: dict[str, Any], data_b: dict[str, Any]) -> None:
    """Print a cross-workload comparison report for two importance results.

    Robust to SCALPEL outputs where ``tier_assignments`` contains only
    confirmed knobs — non-confirmed entries print as ``not_confirmed``
    rather than crashing on a missing key.
    """
    imp_a = data_a.get("marginal_importances", {})
    imp_b = data_b.get("marginal_importances", {})
    tier_block_a = data_a.get("tier_generation", {}).get("tier_assignments")
    tier_block_b = data_b.get("tier_generation", {}).get("tier_assignments")
    tier_a = tier_block_a if tier_block_a is not None else data_a.get(
        "tier_assignments", {}
    )
    tier_b = tier_block_b if tier_block_b is not None else data_b.get(
        "tier_assignments", {}
    )

    algo_a = data_a.get("tier_generation", {}).get("metadata", {}).get(
        "algorithm", "unknown"
    )
    algo_b = data_b.get("tier_generation", {}).get("metadata", {}).get(
        "algorithm", "unknown"
    )
    if algo_a != algo_b:
        LOGGER.warning(
            "Cross-algorithm comparison: A used '%s', B used '%s'. "
            "Tier-name semantics may differ.",
            algo_a,
            algo_b,
        )

    all_knobs = sorted(set(imp_a.keys()) | set(imp_b.keys()))

    LOGGER.info("Cross-Workload Comparison Report:")
    print(
        f"{'Knob':<40} | {'Rank A':<8} | {'Rank B':<8} | "
        f"{'Shift':<6} | {'Tier A':<14} | {'Tier B':<14}"
    )
    print("-" * 105)

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

        ta = tier_a.get(knob, "not_confirmed")
        tb = tier_b.get(knob, "not_confirmed")

        print(
            f"{knob:<40} | {str(ra):<8} | {str(rb):<8} | "
            f"{shift_str:<6} | {ta:<14} | {tb:<14}"
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
        out_dir = Path(f"results/analysis/{actual_workload}/")

    out_dir.mkdir(parents=True, exist_ok=True)
    return actual_workload, out_dir


def _save_analysis_results(
    out_dir: Path,
    actual_workload: str,
    importance_result: ImportanceResult,
    tier_result: TierResult,
    *,
    scalpel_result: Optional[SCALPELResult] = None,
) -> None:
    """Serialize the analysis outcomes to a JSON file.

    The ``tier_generation`` block carries legacy-shape fields plus a new
    ``metadata`` sub-block exposing SCALPEL provenance / diagnostics so
    downstream readers (e.g., the tier-diagnostics visualization) can
    surface BORUTA hits, BH-adjusted p-values, and stability scores.
    """
    out_json = out_dir / "importance_results.json"
    tier_metadata: dict[str, Any] = {
        "algorithm": SCALPEL_ALGORITHM_SLUG if scalpel_result else "lorenz_fallback",
    }
    if scalpel_result is not None:
        tier_metadata["scalpel_version"] = scalpel_result.diagnostics_full().get(
            "scalpel_version"
        )
        tier_metadata["diagnostics"] = scalpel_result.diagnostics_pruned()

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
            "metadata": tier_metadata,
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
    """Print a summary of the analyzed importance and tier assignments.

    Knobs absent from ``tier_assignments`` (under SCALPEL: not BORUTA-
    confirmed) are labeled ``not_confirmed`` rather than ``unknown`` to
    surface the algorithmic verdict explicitly.
    """
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
        tier = tier_result.tier_assignments.get(knob, "not_confirmed")
        print(f"  {idx:2d}. {knob:<35} {imp:.4f}  (Tier: {tier})")


def _group_files_by_hardware(
    results_dir: Path,
) -> tuple[dict[str, tuple[list[Path], dict[str, Any]]], str]:
    """Group JSON result files by hardware profile key and extract workload."""
    groups: dict[str, tuple[list[Path], dict[str, Any]]] = {}
    json_files = find_result_files(results_dir)

    first_workload = "unknown"

    for idx, file_path in enumerate(json_files):
        try:
            with open(file_path, "r", encoding="utf-8") as f:
                data = json.load(f)

            # Opportunistically extract the true workload from the first file
            if idx == 0:
                session_meta = data.get("tuning_session", {})
                first_workload = session_meta.get("workload_type", "unknown")
                # Promote to sysbench granular workload if available
                if session_meta.get(
                    "benchmark_name"
                ) == "sysbench" and session_meta.get("sysbench_workload"):
                    first_workload = session_meta.get("sysbench_workload")

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


def _run_scalpel_for_profile(
    args: argparse.Namespace,
    loaded_data: LoadedData,
    actual_workload: str,
) -> Optional[SCALPELResult]:
    """Run SCALPEL on a single hardware profile's loaded PBT data.

    Returns ``None`` when SCALPEL preflight rejects the input (too few
    samples, too few clusters, etc.) so the caller can fall back to the
    legacy Lorenz-from-importances path without crashing the pipeline.
    """
    if loaded_data.config_df.empty:
        LOGGER.warning(
            "SCALPEL: empty config_df for %s; skipping pipeline.", actual_workload
        )
        return None

    # Numpy-backed concatenation defends against pandas index-misalignment;
    # the loader builds session_index/generation_index on the same
    # RangeIndex(0, n_observations) as config_df/scores, but we never
    # rely on pandas alignment to derive sample_groups.
    sample_groups = pd.Series(
        (
            loaded_data.session_index.to_numpy().astype(str)
            + ":"
            + loaded_data.generation_index.to_numpy().astype(str)
        ),
        name="sample_groups",
    )
    hp = SCALPELHyperparameters.from_args(args, workload_label=actual_workload)
    LOGGER.info(
        "Running SCALPEL pipeline for %s (n=%d, p=%d, clusters=%d, seed=%d)",
        actual_workload,
        loaded_data.config_df.shape[0],
        loaded_data.config_df.shape[1],
        sample_groups.nunique(),
        hp.seed,
    )
    result = scalpel_tier(
        loaded_data.config_df,
        loaded_data.scores,
        sample_groups=sample_groups,
        hp=hp,
        knob_metadata=KNOB_TUNING_METADATA,
        knob_bounds=loaded_data.knob_bounds,
    )
    if result.is_degenerate:
        LOGGER.warning(
            "SCALPEL preflight rejected %s: %s",
            actual_workload,
            result.preflight_reason,
        )
    return result


def run_analysis_pipeline(
    args: argparse.Namespace,
    *,
    results_dir: Optional[Path] = None,
    workload_label_override: Optional[str] = None,
) -> None:
    """Execute the full end-to-end analysis pipeline for ONE workload.

    ``results_dir`` and ``workload_label_override`` are populated by the
    ``--all-workloads`` loop; when omitted, the function falls back to
    ``args.results_dir`` and ``args.workload_label`` (single-workload
    invocation).
    """
    if results_dir is None:
        results_dir = args.results_dir
    if not results_dir:
        LOGGER.error("--results-dir is required when not using --compare")
        sys.exit(1)

    workload_arg = workload_label_override or args.workload_label

    # First, group files and optionally auto-detect the workload type
    groups, auto_workload = _group_files_by_hardware(results_dir)

    initial_workload = workload_arg if workload_arg != "auto" else auto_workload

    temp_out_dir = args.output_dir or Path(f"results/analysis/{initial_workload}/")
    temp_out_dir.mkdir(parents=True, exist_ok=True)
    setup_logging(verbosity="INFO", output_file=temp_out_dir / "analysis_log.html")

    LOGGER.info("Starting Knob Importance Analysis")
    LOGGER.info("Loading and grouping data from %s", results_dir)

    if not groups:
        LOGGER.error("No valid JSON results found in %s", results_dir)
        sys.exit(1)

    LOGGER.info("Found %d distinct hardware profile(s).", len(groups))

    profile_results: list[tuple[ImportanceResult, dict[str, Any]]] = []
    combined_data_list: list[tuple[LoadedData, dict[str, Any]]] = []

    # Keep track of the final resolved paths for the combined model output
    final_actual_workload = initial_workload
    final_base_out_dir = temp_out_dir
    hw_val_res = None
    primary_scalpel_result: Optional[SCALPELResult] = None

    for hw_key, (file_paths, wr) in groups.items():
        LOGGER.info(
            "--- Processing hardware profile: %s (%d files) ---",
            hw_key,
            len(file_paths),
        )
        loaded_data = load_pbt_results(
            results_dir,
            default_workload_type=initial_workload,
            file_paths=file_paths,
        )

        actual_workload, base_out_dir = _resolve_workload_and_output_dir(
            loaded_data=loaded_data,
            workload_label=workload_arg,
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

        scalpel_result: Optional[SCALPELResult] = None
        if args.algorithm == "scalpel":
            scalpel_result = _run_scalpel_for_profile(args, loaded_data, actual_workload)

        if scalpel_result is not None and not scalpel_result.is_degenerate:
            tier_result = scalpel_result.to_tier_result(workload_label=actual_workload)
            LOGGER.info(
                "SCALPEL: confirmed=%d tentative=%d rejected=%d nuisance_dropped=%d",
                len(scalpel_result.confirmed),
                len(scalpel_result.tentative),
                len(scalpel_result.rejected),
                len(scalpel_result.nuisance_dropped),
            )
        else:
            LOGGER.info("Running tier generation (Lorenz fallback) for %s...", hw_key)
            tier_result = generate_tiers(
                marginal_importances=importance_result.marginal_importances,
                workload_label=actual_workload,
            )

        _save_analysis_results(
            out_dir,
            actual_workload,
            importance_result,
            tier_result,
            scalpel_result=scalpel_result,
        )

        # Only print summary for the first group to avoid extreme console spam
        if len(profile_results) == 0:
            _print_analysis_summary(actual_workload, importance_result, tier_result)
            primary_scalpel_result = scalpel_result

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

            # Save the combined model outputs to the root analysis dir.
            # Combined-hardware uses Lorenz fallback because hardware_validator
            # only retains a precomputed importance dict (no X/y available).
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
            "stable_knobs_semantics": "intersection_of_confirmed_sets"
            if args.algorithm == "scalpel"
            else "intersection_of_labelled_sets",
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

    if args.export_tiers:
        explicit_path: Path | None = (
            None if args.export_tiers == Path("auto") else args.export_tiers
        )
        if (
            args.algorithm == "scalpel"
            and primary_scalpel_result is not None
            and not primary_scalpel_result.is_degenerate
            and len(groups) == 1
        ):
            export_data_driven_tiers(
                workload_label=final_actual_workload,
                output_path=explicit_path,
                source_results=str(results_dir),
                scalpel_result=primary_scalpel_result,
                write_diagnostics=True,
            )
        else:
            if hw_val_res and hw_val_res.combined_importances:
                export_importances = (
                    hw_val_res.combined_importances.marginal_importances
                )
            elif profile_results:
                export_importances = profile_results[0][0].marginal_importances
            else:
                LOGGER.error("No importance results available to export.")
                return
            export_data_driven_tiers(
                marginal_importances=export_importances,
                workload_label=final_actual_workload,
                output_path=explicit_path,
                source_results=str(results_dir),
            )


def _discover_workloads(
    results_root: Path,
    glob_pattern: str,
) -> list[tuple[str, Path]]:
    """Discover ``(workload_label, traces_dir)`` pairs under ``results_root``.

    The workload label is derived from the second path segment under
    ``results_root`` — matching the layout
    ``results/sessions/<workload>/<strategy>/<tier>/traces``.
    Empty discovery results are signalled by returning ``[]``.
    """
    if not results_root.is_dir():
        return []
    discovered: list[tuple[str, Path]] = []
    for path in sorted(results_root.glob(glob_pattern)):
        if not path.is_dir():
            continue
        # Skip empty directories — refusing to overwrite a prior good
        # data_driven_tiers.json with empty SCALPEL output is by design.
        if not find_result_files(path):
            LOGGER.warning(
                "Skipping %s: no pbt_results_*.json / lhs_results_*.json files found.",
                path,
            )
            continue
        try:
            relative = path.relative_to(results_root)
            workload_label = relative.parts[1]
        except (ValueError, IndexError):
            LOGGER.warning(
                "Could not derive workload label from %s; skipping.", path
            )
            continue
        discovered.append((workload_label, path))
    return discovered


def main() -> None:
    """CLI entry point for knob importance logic."""
    args = parse_args()

    if args.compare:
        setup_logging(verbosity="INFO")
        compare_results(args.compare[0], args.compare[1])
        return

    if args.all_workloads:
        discovered = _discover_workloads(args.all_workloads, args.results_glob)
        if not discovered:
            LOGGER.error(
                "No workloads discovered under %s with glob '%s'.",
                args.all_workloads,
                args.results_glob,
            )
            sys.exit(1)
        LOGGER.info(
            "Discovered %d workload(s) under %s", len(discovered), args.all_workloads
        )
        failures: list[tuple[str, str]] = []
        for workload_label, results_dir in discovered:
            LOGGER.info(
                "=== Processing workload '%s' (%s) ===", workload_label, results_dir
            )
            try:
                run_analysis_pipeline(
                    args,
                    results_dir=results_dir,
                    workload_label_override=workload_label,
                )
            except Exception as exc:  # pragma: no cover - rollout helper
                LOGGER.exception(
                    "Workload '%s' failed; continuing rollout.", workload_label
                )
                failures.append((workload_label, str(exc)))
        if failures:
            LOGGER.warning(
                "Completed --all-workloads with %d failure(s): %s",
                len(failures),
                ", ".join(f"{w}({e[:60]})" for w, e in failures),
            )
        else:
            LOGGER.info("Completed --all-workloads with no failures.")
        return

    run_analysis_pipeline(args)


if __name__ == "__main__":
    main()
