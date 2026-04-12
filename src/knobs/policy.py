"""Shared policy engine for PostgreSQL autotuning knob admission and exclusion."""

import json
import os
from typing import Dict

import pandas as pd

from src.knobs.knob_metadata import KNOB_TUNING_METADATA


def _load_policy(path: str = "data/knob_policy.json") -> Dict[str, tuple[str, str]]:
    """Load source exclusion policy from JSON while preserving tuple-based API."""
    if not os.path.isabs(path):
        # Resolve relative paths against repository root for deterministic imports/tests.
        project_root = os.path.abspath(os.path.join(os.path.dirname(__file__), "../.."))
        path = os.path.join(project_root, path)
    try:
        with open(path, "r", encoding="utf-8") as f:
            data = json.load(f)
    except FileNotFoundError as exc:
        raise FileNotFoundError(
            "Knob policy file not found at resolved path "
            f"'{path}'. Ensure data/knob_policy.json exists relative to the "
            "repository root or pass the correct policy path to _load_policy()."
        ) from exc
    # Accept either raw dict shape or wrapped export shape for backward compatibility.
    raw_policy = data.get("AUTOTUNING_SOURCE_EXCLUSIONS", data)
    return {k: tuple(v) for k, v in raw_policy.items()}


AUTOTUNING_SOURCE_EXCLUSIONS: Dict[str, tuple[str, str]] = _load_policy()

SOURCE_POLICY_COLUMNS = (
    "eligible_for_autotuning",
    "autotuning_exclusion_reason_code",
    "autotuning_exclusion_reason_detail",
)
SUPPORTED_AUTOTUNING_VARTYPES = frozenset({"integer", "real", "bool", "enum"})
INT_MAX_SENTINEL = 2_000_000_000


def annotate_autotuning_policy(df: pd.DataFrame) -> pd.DataFrame:
    """Annotate source-stage autotuning eligibility and exclusion reasons."""
    annotated = df.copy()

    annotated["eligible_for_autotuning"] = True
    annotated["autotuning_exclusion_reason_code"] = ""
    annotated["autotuning_exclusion_reason_detail"] = ""

    internal_mask = annotated["context"] == "internal"
    annotated.loc[internal_mask, "eligible_for_autotuning"] = False
    annotated.loc[internal_mask, "autotuning_exclusion_reason_code"] = "internal_context"
    annotated.loc[
        internal_mask,
        "autotuning_exclusion_reason_detail",
    ] = "Internal parameters cannot be modified via PostgreSQL runtime/config interfaces."

    # Apply explicit policy overrides after internal-context exclusion defaults.
    for knob_name, (reason_code, reason_detail) in AUTOTUNING_SOURCE_EXCLUSIONS.items():
        knob_mask = annotated["name"] == knob_name
        if knob_mask.any():  # type: ignore
            annotated.loc[knob_mask, "eligible_for_autotuning"] = False
            annotated.loc[knob_mask, "autotuning_exclusion_reason_code"] = reason_code
            annotated.loc[knob_mask, "autotuning_exclusion_reason_detail"] = reason_detail

    return annotated


def ensure_autotuning_policy_annotations(df: pd.DataFrame) -> pd.DataFrame:
    """Ensure policy columns are present without duplicating annotation passes."""
    if set(SOURCE_POLICY_COLUMNS).issubset(df.columns):
        return df
    return annotate_autotuning_policy(df)


def apply_bounds_safety_gate(df: pd.DataFrame) -> tuple[pd.DataFrame, pd.DataFrame]:
    """
    Exclude uncurated knobs with INT_MAX-style max bounds.

    Returns
    -------
    tuple[pd.DataFrame, pd.DataFrame]
        Filtered dataframe and dataframe of excluded knobs for audit/logging.
    """
    if "max_val" not in df.columns:
        return df, df.iloc[0:0].copy()

    max_vals = pd.to_numeric(df["max_val"], errors="coerce")
    safe_bounds_mask = (
        df["name"].isin(KNOB_TUNING_METADATA.keys())
        | (max_vals < INT_MAX_SENTINEL)
        | max_vals.isna()
    )

    excluded_details = df.loc[
        ~safe_bounds_mask, ["name", "max_val", "vartype", "context"]
    ].sort_values("name")

    return df[safe_bounds_mask].copy(), excluded_details
