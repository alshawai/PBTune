"""
Knob Preprocessing for Tuner
============================

This module processes raw PostgreSQL knobs (from pg_settings) into
tuner-ready format by:

1. Loading raw knobs from database or CSV
2. Overlaying tuning metadata (ranges, scales, tiers)
3. Filtering to tunable knobs only
4. Saving preprocessed CSVs for different tiers

Usage:
------
# From command line
python -m src.knobs.preprocess_knobs

# From code
from src.knobs.preprocess_knobs import preprocess_and_save_knobs
preprocess_and_save_knobs()
"""

import os
import ast
from pathlib import Path
from typing import Optional, Dict
import pandas as pd

from src.knobs.policy import (
    SUPPORTED_AUTOTUNING_VARTYPES,
    annotate_autotuning_policy,
    apply_bounds_safety_gate,
)
from src.knobs.retrieval import PostgreSQLKnobRetriever
from src.knobs.knob_metadata import KNOB_TUNING_METADATA, get_knobs_by_tier
from src.utils.logger import setup_logging, get_logger

setup_logging()

logger = get_logger(__name__)


def _log_source_policy_exclusions(df: pd.DataFrame) -> None:
    """Emit aggregated audit summary for source-stage policy exclusions."""
    excluded_source = df[~df["eligible_for_autotuning"]]
    if excluded_source.empty:
        return

    reason_counts = (
        excluded_source["autotuning_exclusion_reason_code"]
        .fillna("unspecified")
        .value_counts()
        .sort_index()
    )
    logger.warning(
        "source_policy_exclusions total=%d reasons=%s",
        len(excluded_source),
        ", ".join(f"{reason}:{count}" for reason, count in reason_counts.items()),
    )


def _clean_enumvals(df: pd.DataFrame) -> pd.DataFrame:
    """Remove environment-specific aliases and unsafe OS constraints from enums.

    This ensures that the tuner doesn't blindly sample values that are
    essentially aliases (like 'on' -> 'pglz' for wal_compression) or
    values known to crash most baseline UNIX systems (like 'io_uring').
    """
    df = df.copy()

    exclusions = {"wal_compression": {"on"}, "io_method": {"io_uring", "posix"}}

    for knob, ex_set in exclusions.items():
        if knob in df["name"].values:
            idx = df.index[df["name"] == knob].tolist()[0]
            val = df.at[idx, "enumvals"]
            if isinstance(val, str) and val.startswith("["):
                try:
                    lst = ast.literal_eval(val)
                    lst = [x for x in lst if x not in ex_set]
                    df.at[idx, "enumvals"] = str(lst)
                except Exception:
                    pass
    return df


def load_raw_knobs(csv_path: Optional[str] = None) -> pd.DataFrame:
    """
    Load raw knobs from CSV or database.

    Parameters
    ----------
    csv_path : Optional[str]
        Path to CSV file. If None, retrieves from database.

    Returns
    -------
    pd.DataFrame
        Raw knobs from pg_settings
    """
    if csv_path and os.path.exists(csv_path):
        print(f"Loading raw knobs from {csv_path}")
        df = pd.read_csv(csv_path)
    else:
        print("Retrieving knobs from PostgreSQL...")
        retriever = PostgreSQLKnobRetriever()
        df = retriever.get_all_knobs_with_metadata()

    return _clean_enumvals(annotate_autotuning_policy(df))


def add_tuning_metadata(df: pd.DataFrame) -> pd.DataFrame:
    """
    Add tuning-specific metadata to knobs dataframe.

    Parameters
    ----------
    df : pd.DataFrame
        Raw knobs dataframe

    Returns
    -------
    pd.DataFrame
        Knobs with added tuning columns
    """
    df_with_defaults = df.copy()
    df_with_defaults["tuning_min"] = None
    df_with_defaults["tuning_max"] = None
    df_with_defaults["scale"] = "linear"
    df_with_defaults["impact_tier"] = "extensive"
    df_with_defaults["tuning_priority"] = 5
    df_with_defaults["tuning_notes"] = ""
    df_with_defaults["hardware_relative"] = False
    df_with_defaults["resource_type"] = ""

    metadata_rows = [
        {
            "name": knob_name,
            "tuning_min_meta": metadata.tuning_min,
            "tuning_max_meta": metadata.tuning_max,
            "scale_meta": metadata.scale,
            "impact_tier_meta": metadata.impact_tier,
            "tuning_priority_meta": metadata.tuning_priority,
            "tuning_notes_meta": metadata.notes,
            "hardware_relative_meta": metadata.hardware_relative,
            "resource_type_meta": metadata.resource_type,
        }
        for knob_name, metadata in KNOB_TUNING_METADATA.items()
    ]
    metadata_df = pd.DataFrame(metadata_rows)

    merged = df_with_defaults.merge(metadata_df, on="name", how="left")

    merged["tuning_min"] = merged["tuning_min_meta"].combine_first(merged["tuning_min"])
    merged["tuning_max"] = merged["tuning_max_meta"].combine_first(merged["tuning_max"])
    merged["scale"] = merged["scale_meta"].combine_first(merged["scale"])
    merged["impact_tier"] = merged["impact_tier_meta"].combine_first(
        merged["impact_tier"]
    )
    merged["tuning_priority"] = merged["tuning_priority_meta"].combine_first(
        merged["tuning_priority"]
    )
    merged["tuning_notes"] = merged["tuning_notes_meta"].combine_first(
        merged["tuning_notes"]
    )
    merged["hardware_relative"] = merged["hardware_relative_meta"].combine_first(
        merged["hardware_relative"]
    )
    merged["resource_type"] = merged["resource_type_meta"].combine_first(
        merged["resource_type"]
    )

    return merged.drop(
        columns=[
            "tuning_min_meta",
            "tuning_max_meta",
            "scale_meta",
            "impact_tier_meta",
            "tuning_priority_meta",
            "tuning_notes_meta",
            "hardware_relative_meta",
            "resource_type_meta",
        ]
    )


def filter_tunable_knobs(df: pd.DataFrame) -> pd.DataFrame:
    """
    Filter to knobs that are actually tunable.

    Criteria:
    1. Marked as eligible by source-stage autotuning policy classification
    2. Numeric (integer/real), boolean, or enum type (or explicitly curated via metadata)
    3. Passes bounds safety gate (curated metadata or bounded native max)

    Parameters
    ----------
    df : pd.DataFrame
        Knobs dataframe

    Returns
    -------
    pd.DataFrame
        Filtered to tunable knobs only
    """
    df = annotate_autotuning_policy(df)

    tunable = df[df["eligible_for_autotuning"]].copy()

    has_metadata = tunable["name"].isin(KNOB_TUNING_METADATA.keys())
    is_supported_vartype = tunable["vartype"].isin(SUPPORTED_AUTOTUNING_VARTYPES)

    tunable = tunable[has_metadata | is_supported_vartype].copy()

    tunable, excluded_details = apply_bounds_safety_gate(tunable)
    if not excluded_details.empty:
        logger.warning(
            "autotuning_bounds_exclusion reason_code=uncurated_intmax_sentinel count=%d",
            len(excluded_details),
        )
        for _, row in excluded_details.iterrows():
            logger.warning(
                "  > knob=%s max_val=%s vartype=%s context=%s",
                row["name"],
                row["max_val"],
                row["vartype"],
                row["context"],
            )

    tunable["requires_restart"] = tunable["context"] == "postmaster"
    tunable["has_tuning_metadata"] = tunable["name"].isin(KNOB_TUNING_METADATA.keys())

    return tunable


def create_tier_dataframes(
    df: pd.DataFrame, tier_source: str = "expert"
) -> Dict[str, pd.DataFrame]:
    """
    Create separate dataframes for each impact tier.

    Parameters
    ----------
    df : pd.DataFrame
        Preprocessed knobs
    tier_source : str
        Source of tiers ('expert' or 'data_driven')

    Returns
    -------
    Dict[str, pd.DataFrame]
        Dictionary mapping tier name to dataframe
    """
    tiers = {}

    if tier_source == "data_driven":
        from src.knobs.knob_metadata import DATA_DRIVEN_TIERS

        if DATA_DRIVEN_TIERS is not None:
            for tier_name in DATA_DRIVEN_TIERS.keys():
                if tier_name == "extensive":
                    continue
                knob_names = get_knobs_by_tier(tier_name, source=tier_source)
                tiers[tier_name] = df[df["name"].isin(knob_names)].copy()
        else:
            logger.warning(
                "Data-driven tiers not loaded. Falling back to expert tiers."
            )
            tier_source = "expert"

    if tier_source == "expert":
        minimal_knobs = get_knobs_by_tier("minimal", source=tier_source)
        tiers["minimal"] = df[df["name"].isin(minimal_knobs)].copy()

        core_knobs = get_knobs_by_tier("core", source=tier_source)
        tiers["core"] = df[df["name"].isin(core_knobs)].copy()

        standard_knobs = get_knobs_by_tier("standard", source=tier_source)
        tiers["standard"] = df[df["name"].isin(standard_knobs)].copy()

    tiers["extensive"] = df.copy()

    return tiers


def preprocess_and_save_knobs(
    raw_csv_path: Optional[str] = None,
    output_dir: Optional[str] = None,
    tier_source: str = "expert",
    tiers_json: Optional[str] = None,
) -> Dict[str, str]:
    """
    Complete preprocessing pipeline.

    1. Load raw knobs
    2. Add tuning metadata
    3. Filter to tunable knobs
    4. Save tier-specific CSVs

    Parameters
    ----------
    raw_csv_path : Optional[str]
        Path to raw knobs CSV. If None, retrieves from database.
    output_dir : Optional[str]
        Directory to save preprocessed CSVs. If None, resolved based on tier_source.
    tier_source : str
        Source of tiers ('expert' or 'data_driven')
    tiers_json : Optional[str]
        Path to data-driven tiers JSON file.  When ``None`` (default) and
        ``tier_source`` is ``'data_driven'``, the path is resolved to
        ``data/data_driven_knobs/{workload_type}/data_driven_tiers.json``
        where ``workload_type`` must be provided via a CLI argument or the
        caller must pass an explicit path.

    Returns
    -------
    Dict[str, str]
        Dictionary mapping tier name to saved CSV path
    """
    import json

    if tier_source == "data_driven":
        if tiers_json is None:
            raise ValueError(
                "tiers_json must be specified when tier_source is 'data_driven'. "
                "Use the path: data/data_driven_knobs/{workload_type}/data_driven_tiers.json\n"
                "Generate it first with:\n"
                "  python -m src.scripts.analyze_knob_importance "
                "--results-dir <dir> --export-tiers"
            )
        from src.knobs.knob_metadata import load_data_driven_tiers

        load_data_driven_tiers(tiers_json)

    if output_dir is None:
        if tier_source == "expert":
            output_dir = "data/expert_defined_knobs"
        elif tier_source == "data_driven":
            with open(tiers_json, "r") as f:  # type: ignore[arg-type]
                metadata = json.load(f).get("metadata", {})
            workload_type = metadata.get("workload_type")
            if not workload_type:
                raise ValueError(f"workload_type not found in {tiers_json}")
            output_dir = f"data/data_driven_knobs/{workload_type}"
        else:
            raise ValueError(f"Unknown tier_source: {tier_source}")

    output_path = Path(output_dir)
    output_path.mkdir(parents=True, exist_ok=True)

    print("PostgreSQL Knob Preprocessing for PBT Tuner")
    print(f"Tier Source: {tier_source}")
    print("=" * 43)

    print("\n[1/4] Loading raw knobs...")
    df_raw = load_raw_knobs(raw_csv_path)
    print(f"  Loaded {len(df_raw)} total knobs from PostgreSQL")

    print("\n[2/4] Adding tuning metadata...")
    df_with_metadata = add_tuning_metadata(df_raw)
    with_metadata_count = df_with_metadata["tuning_min"].notna().sum()
    print(f"  Added metadata for {with_metadata_count} knobs")

    print("\n[3/4] Filtering to tunable knobs...")
    _log_source_policy_exclusions(df_with_metadata)

    df_tunable = filter_tunable_knobs(df_with_metadata)
    print(f"  Filtered to {len(df_tunable)} tunable knobs")
    print(f"    - Requires restart: {df_tunable['requires_restart'].sum()}")
    print(f"    - Runtime modifiable: {(~df_tunable['requires_restart']).sum()}")

    print("\n[4/4] Creating tier-specific datasets...")
    tiers = create_tier_dataframes(df_tunable, tier_source=tier_source)

    saved_paths = {}
    for tier_name, tier_df in tiers.items():
        # Sort by priority then name
        tier_df = tier_df.sort_values(["tuning_priority", "name"])

        csv_path = output_path / f"{tier_name}_knobs.csv"
        if tier_name != "extensive" and tier_df.empty:
            # SCALPEL produces empty 'core' or 'standard' lists when the
            # importance distribution is sharply concentrated. Writing a
            # header-only CSV would crash the tuner at LHS sample time, so
            # we skip the file and let `load_knob_space_for_tier` walk
            # down to the next-broader non-empty tier with a warning.
            if csv_path.exists():
                try:
                    csv_path.unlink()
                except OSError:
                    pass
            print(
                f"  ⚠ {tier_name.upper()}: SCALPEL produced 0 knobs — skipping CSV"
            )
            continue

        tier_df.to_csv(csv_path, index=False)
        saved_paths[tier_name] = str(csv_path)

        print(f"  ✓ {tier_name.upper()}: {len(tier_df)} knobs → {csv_path}")

    print("=" * 63)
    print("\nPreprocessing complete!")
    print("=" * 23)
    print("Saved files:")
    for tier, path in saved_paths.items():
        print(f"  - {tier}: {path}")

    return saved_paths


def load_knobs_for_tier(
    tier: str,
    knob_source: str = "expert",
    workload_type: Optional[str] = None,
    data_dir: Optional[str] = None,
) -> pd.DataFrame:
    """
    Load preprocessed knobs for a specific tier.

    Parameters
    ----------
    tier : str
        Tier name: 'minimal', 'core', 'standard', or 'extensive'
    knob_source : str
        Knob source: 'expert' or 'data_driven'
    workload_type : str, optional
        Workload type (required if knob_source is 'data_driven')
    data_dir : str, optional
        Override directory containing preprocessed CSVs

    Returns
    -------
    pd.DataFrame
        Preprocessed knobs for the tier

    Raises
    ------
    FileNotFoundError
        If preprocessed CSV doesn't exist
    ValueError
        If tier is unknown
    """
    tier_lower = tier.lower()
    valid_tiers = ["minimal", "core", "standard", "extensive"]

    if tier_lower not in valid_tiers:
        raise ValueError(f"Unknown tier: {tier}. Must be one of {valid_tiers}")

    if data_dir is not None:
        csv_path = Path(data_dir) / f"{tier_lower}_knobs.csv"
    else:
        if knob_source == "expert":
            csv_path = Path("data/expert_defined_knobs") / f"{tier_lower}_knobs.csv"
        elif knob_source == "data_driven":
            if not workload_type:
                raise ValueError(
                    "workload_type must be specified when knob_source is 'data_driven'"
                )
            csv_path = (
                Path("data/data_driven_knobs")
                / workload_type
                / f"{tier_lower}_knobs.csv"
            )
        else:
            raise ValueError(f"Unknown knob_source: {knob_source}")

    if not csv_path.exists():
        raise FileNotFoundError(
            f"Preprocessed knobs not found: {csv_path}\n"
            f"Run preprocessing first:\n"
            f"  python -m src.knobs.preprocess_knobs"
        )

    return pd.read_csv(csv_path)


if __name__ == "__main__":
    import argparse

    parser = argparse.ArgumentParser(description="Preprocess PostgreSQL knobs")
    parser.add_argument(
        "--raw-csv",
        type=str,
        default=None,
        help="Path to raw CSV (pg_settings export)",
    )
    parser.add_argument(
        "--source",
        type=str,
        choices=["expert", "data_driven", "both"],
        default="expert",
        help="Knob tier source to generate ('expert', 'data_driven', or 'both')",
    )
    parser.add_argument(
        "--tiers-json",
        type=str,
        default=None,
        help=(
            "Path to data-driven tiers JSON file. "
            "Expected format: data/data_driven_knobs/{workload_type}/data_driven_tiers.json. "
            "Required when --source is 'data_driven' or 'both'."
        ),
    )
    parser.add_argument(
        "--output-dir",
        type=str,
        default=None,
        help="Override output directory",
    )

    args = parser.parse_args()

    if args.source == "both":
        print("Preprocessing expert-defined knobs...")
        preprocess_and_save_knobs(
            raw_csv_path=args.raw_csv,
            output_dir=args.output_dir,
            tier_source="expert",
        )
        print("\nPreprocessing data-driven knobs...")
        preprocess_and_save_knobs(
            raw_csv_path=args.raw_csv,
            output_dir=args.output_dir,
            tier_source="data_driven",
            tiers_json=args.tiers_json,
        )
    else:
        preprocess_and_save_knobs(
            raw_csv_path=args.raw_csv,
            output_dir=args.output_dir,
            tier_source=args.source,
            tiers_json=args.tiers_json,
        )
