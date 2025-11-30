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
from pathlib import Path
from typing import Optional, Dict
import pandas as pd

from src.knobs.retrieval import PostgreSQLKnobRetriever
from src.knobs.knob_metadata import KNOB_TUNING_METADATA, IMPACT_TIERS


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
        return pd.read_csv(csv_path)
    else:
        print("Retrieving knobs from PostgreSQL...")
        retriever = PostgreSQLKnobRetriever()
        return retriever.get_all_knobs_with_metadata()


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
    df["tuning_min"] = None
    df["tuning_max"] = None
    df["scale"] = "linear"
    df["impact_tier"] = "extensive"
    df["tuning_priority"] = 5
    df["tuning_notes"] = ""

    for knob_name, metadata in KNOB_TUNING_METADATA.items():
        if knob_name in df["name"].values:
            idx = df[df["name"] == knob_name].index[0]
            df.at[idx, "tuning_min"] = metadata.tuning_min
            df.at[idx, "tuning_max"] = metadata.tuning_max
            df.at[idx, "scale"] = metadata.scale
            df.at[idx, "impact_tier"] = metadata.impact_tier
            df.at[idx, "tuning_priority"] = metadata.tuning_priority
            df.at[idx, "tuning_notes"] = metadata.notes

    return df


def filter_tunable_knobs(df: pd.DataFrame) -> pd.DataFrame:
    """
    Filter to knobs that are actually tunable.
    
    Criteria:
    1. Not 'internal' context (cannot be changed)
    2. Numeric (integer/real) or boolean type (easier to tune than strings)
    3. Either has tuning metadata OR is runtime modifiable
    
    Parameters
    ----------
    df : pd.DataFrame
        Knobs dataframe
        
    Returns
    -------
    pd.DataFrame
        Filtered to tunable knobs only
    """
    tunable = df[df["context"] != "internal"].copy()

    has_metadata = tunable["name"].isin(KNOB_TUNING_METADATA.keys())
    is_numeric_or_bool = tunable["vartype"].isin(["integer", "real", "bool"])

    tunable = tunable[has_metadata | is_numeric_or_bool].copy()

    tunable["requires_restart"] = tunable["context"] == "postmaster"
    tunable["has_tuning_metadata"] = has_metadata

    return tunable


def create_tier_dataframes(df: pd.DataFrame) -> Dict[str, pd.DataFrame]:
    """
    Create separate dataframes for each impact tier.
    
    Parameters
    ----------
    df : pd.DataFrame
        Preprocessed knobs
        
    Returns
    -------
    Dict[str, pd.DataFrame]
        Dictionary mapping tier name to dataframe
    """
    tiers = {}

    minimal_knobs = IMPACT_TIERS["minimal"]
    tiers["minimal"] = df[df["name"].isin(minimal_knobs)].copy()

    core_knobs = IMPACT_TIERS["core"]
    tiers["core"] = df[df["name"].isin(core_knobs)].copy()

    tiers["standard"] = df[df["has_tuning_metadata"]].copy()

    tiers["extensive"] = df.copy()

    return tiers


def preprocess_and_save_knobs(
    raw_csv_path: Optional[str] = None,
    output_dir: str = "data/tuner_knobs"
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
    output_dir : str
        Directory to save preprocessed CSVs
        
    Returns
    -------
    Dict[str, str]
        Dictionary mapping tier name to saved CSV path
    """
    output_path = Path(output_dir)
    output_path.mkdir(parents=True, exist_ok=True)

    print("PostgreSQL Knob Preprocessing for PBT Tuner")
    print("=" * 43)

    print("\n[1/4] Loading raw knobs...")
    df_raw = load_raw_knobs(raw_csv_path)
    print(f"  Loaded {len(df_raw)} total knobs from PostgreSQL")

    print("\n[2/4] Adding tuning metadata...")
    df_with_metadata = add_tuning_metadata(df_raw)
    with_metadata_count = df_with_metadata["tuning_min"].notna().sum()
    print(f"  Added metadata for {with_metadata_count} knobs")

    print("\n[3/4] Filtering to tunable knobs...")
    df_tunable = filter_tunable_knobs(df_with_metadata)
    print(f"  Filtered to {len(df_tunable)} tunable knobs")
    print(f"    - Requires restart: {df_tunable['requires_restart'].sum()}")
    print(f"    - Runtime modifiable: {(~df_tunable['requires_restart']).sum()}")

    print("\n[4/4] Creating tier-specific datasets...")
    tiers = create_tier_dataframes(df_tunable)

    saved_paths = {}
    for tier_name, tier_df in tiers.items():
        # Sort by priority then name
        tier_df = tier_df.sort_values(["tuning_priority", "name"])

        csv_path = output_path / f"{tier_name}_knobs.csv"
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


def load_knobs_for_tier(tier: str, data_dir: str = "data/tuner_knobs") -> pd.DataFrame:
    """
    Load preprocessed knobs for a specific tier.
    
    Parameters
    ----------
    tier : str
        Tier name: 'minimal', 'core', 'standard', or 'extensive'
    data_dir : str
        Directory containing preprocessed knob CSVs
        
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
        raise ValueError(
            f"Unknown tier: {tier}. Must be one of {valid_tiers}"
        )

    csv_path = Path(data_dir) / f"{tier_lower}_knobs.csv"

    if not csv_path.exists():
        raise FileNotFoundError(
            f"Preprocessed knobs not found: {csv_path}\n"
            f"Run preprocessing first:\n"
            f"  python -m src.tuner.config.preprocess_knobs"
        )

    return pd.read_csv(csv_path)


if __name__ == "__main__":
    import sys

    raw_csv: Optional[str] = None
    if len(sys.argv) > 1:
        raw_csv = sys.argv[1]

    preprocess_and_save_knobs(raw_csv_path=raw_csv)
