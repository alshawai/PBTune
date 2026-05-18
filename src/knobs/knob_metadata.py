"""
Knob Tuning Metadata and Preprocessing
=======================================

This module defines tuning-specific metadata for PostgreSQL knobs that is
NOT available in pg_settings but is essential for optimization:

1. Tuning ranges (different from PostgreSQL min/max)
2. Scale type (linear vs logarithmic)
3. Impact tier (minimal, core, standard, extensive)
4. Recommended values and bounds

This metadata is overlaid onto knobs retrieved from pg_settings to create
a complete tuning specification.
"""

from pathlib import Path
import json
import logging
from typing import Dict, Any, Optional, List
from dataclasses import dataclass

logger = logging.getLogger(__name__)


@dataclass
class TuningMetadata:
    """
    Tuning-specific metadata for a knob.

    Attributes
    ----------
    tuning_min : Optional[Any]
        Minimum value for tuning (may differ from PostgreSQL min)
    tuning_max : Optional[Any]
        Maximum value for tuning (may differ from PostgreSQL max)
    scale : str
        'linear' or 'log' - how to sample/perturb this knob
    impact_tier : str
        Categorization for preset groups: 'minimal', 'core', 'standard', 'extensive'
        This determines which preset knob space includes this knob.
    tuning_priority : int
        Fine-grained priority within a tier (1-5, where 1 is highest)
        Used for sorting within tiers and for advanced selection strategies.
        Example: Two 'core' knobs may have different priorities (1 vs 2)
    notes : str
        Tuning-specific notes

    Distinction:
    -----------
    - impact_tier: Categorical grouping (which preset to include in)
    - tuning_priority: Numerical ranking (importance within and across tiers)

    Example:
    - shared_buffers: tier='minimal', priority=1 (most critical)
    - checkpoint_timeout: tier='core', priority=2 (important but secondary)
    - enable_nestloop: tier='standard', priority=4 (fine-tuning)
    """

    tuning_min: Optional[Any] = None
    tuning_max: Optional[Any] = None
    scale: str = "linear"
    impact_tier: str = "extensive"
    tuning_priority: int = 5
    notes: str = ""
    hardware_relative: bool = False
    resource_type: str = ""  # "ram", "cpu", "disk_type", or ""


def _load_metadata(path: str = "data/knob_metadata.json") -> Dict[str, TuningMetadata]:
    """Load knob tuning metadata from JSON and coerce values to TuningMetadata."""
    path = Path(path)
    try:
        with open(path, "r", encoding="utf-8") as f:
            data = json.load(f)

        return {k: TuningMetadata(**v) for k, v in data.items()}

    except FileNotFoundError as exc:
        raise FileNotFoundError(
            f"Knob metadata file not found at '{path}'. "
            "Generate it with the metadata export step or place knob metadata JSON at this path."
        ) from exc
    except TypeError as exc:
        # This handles cases where the JSON has missing or incorrect data for a specific Knob.
        raise TypeError(
            f"Malformed metadata in '{path}'. Check if all fields match TuningMetadata dataclass."
        ) from exc


KNOB_TUNING_METADATA: Dict[str, TuningMetadata] = _load_metadata()


# Tier definitions
IMPACT_TIERS = {
    "minimal": [
        k for k, v in KNOB_TUNING_METADATA.items() if v.impact_tier == "minimal"
    ],
    "core": [
        k
        for k, v in KNOB_TUNING_METADATA.items()
        if v.impact_tier in ("minimal", "core")
    ],
    "standard": [
        k
        for k, v in KNOB_TUNING_METADATA.items()
        if v.impact_tier in ("minimal", "core", "standard")
    ],
    "extensive": None,  # Will include all tunable knobs from pg_settings
}


DATA_DRIVEN_TIERS: Optional[Dict[str, Optional[List[str]]]] = None

def load_data_driven_tiers(json_path: str = "data/data_driven_tiers.json") -> None:
    """Load data-driven tiers from JSON and populate DATA_DRIVEN_TIERS."""
    global DATA_DRIVEN_TIERS
    path = Path(json_path)
    try:
        with open(path, "r", encoding="utf-8") as f:
            data = json.load(f)
    except FileNotFoundError as exc:
        raise FileNotFoundError(
            f"Data-driven tiers file not found at '{path}'. "
            "Please generate it using the analysis pipeline or provide a valid path."
        ) from exc

    if "tiers" not in data:
        raise ValueError(f"Malformed data-driven tiers in '{path}'. Missing 'tiers' key.")

    DATA_DRIVEN_TIERS = data["tiers"]


def get_knobs_by_tier(tier: str, source: str = "expert") -> list:
    """Return knob names for a tier, preserving the existing public API shape."""
    tier_lower = tier.lower()
    
    if source == "data_driven":
        if DATA_DRIVEN_TIERS is not None:
            if tier_lower in DATA_DRIVEN_TIERS:
                result = DATA_DRIVEN_TIERS[tier_lower]
                return result if result is not None else []
            elif tier_lower in IMPACT_TIERS:
                return []
            else:
                raise ValueError(
                    f"Unknown tier: {tier}. Must be one of {list(DATA_DRIVEN_TIERS.keys())} or {list(IMPACT_TIERS.keys())}"
                )
        else:
            logger.warning("Data-driven tiers requested but not loaded. Falling back to expert tiers.")

    if tier_lower not in IMPACT_TIERS:
        raise ValueError(
            f"Unknown tier: {tier}. Must be one of {list(IMPACT_TIERS.keys())}"
        )

    # Ensure a list is returned even if the tier value is None (e.g., 'extensive')
    result = IMPACT_TIERS[tier_lower]
    return result if result is not None else []
