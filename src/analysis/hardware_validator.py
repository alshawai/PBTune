"""Hardware-aware validation for knob importance rankings."""

from __future__ import annotations

from dataclasses import dataclass
from itertools import combinations
from typing import Any, Iterable, Optional, Sequence

import numpy as np
import pandas as pd
from scipy.stats import kendalltau

from src.analysis.data_loader import LoadedData
from src.analysis.importance import (
    DEFAULT_INTERACTION_ORDER,
    DEFAULT_RF_BOOTSTRAP,
    DEFAULT_RF_MAX_DEPTH,
    DEFAULT_RF_MAX_FEATURES,
    DEFAULT_RF_MAX_SAMPLES,
    DEFAULT_RF_MIN_SAMPLES_LEAF,
    DEFAULT_RF_MIN_SAMPLES_SPLIT,
    DEFAULT_RF_N_ESTIMATORS,
    DEFAULT_RF_RANDOM_STATE,
    DEFAULT_TOP_K,
    ImportanceResult,
    analyze_knob_importance,
)
from src.analysis.tier_generator import (
    DEFAULT_FALLBACK_K,
    DEFAULT_K_VALUES,
    generate_tiers,
    get_tier_names,
    get_tier_rank_map,
)
from src.utils.hardware_info import WorkerResources
from src.utils.logger import get_logger

LOGGER = get_logger("HardwareValidator")


@dataclass
class HardwareValidationResult:
    """Results for cross-hardware importance validation.

    Attributes:
        kendall_taus: Mapping of (profile_a, profile_b) to Kendall's tau.
        stable_knobs: Knobs that remain in the same tier across profiles.
        shifting_knobs: Knobs that change tiers across profiles.
        conservative_tiers: Conservative tier assignment for shifting knobs.
        combined_importances: Optional combined-model importance result.
    """

    kendall_taus: dict[tuple[str, str], float]
    stable_knobs: list[str]
    shifting_knobs: dict[str, dict[str, str]]
    conservative_tiers: dict[str, str]
    combined_importances: Optional[ImportanceResult]


def _normalize_worker_resources(
    worker_resources: WorkerResources | dict[str, Any],
) -> WorkerResources:
    """Normalize worker resources into a WorkerResources instance.

    Args:
        worker_resources: WorkerResources or serialized dict.

    Returns:
        Normalized WorkerResources.

    Raises:
        ValueError: When required fields are missing or invalid.
    """
    if isinstance(worker_resources, WorkerResources):
        return worker_resources

    if not isinstance(worker_resources, dict):
        raise ValueError("worker_resources must be a dict or WorkerResources instance.")

    required = {"ram_bytes", "cpu_cores", "disk_type"}
    missing = required - set(worker_resources.keys())
    if missing:
        raise ValueError(f"worker_resources missing fields: {sorted(missing)}")

    try:
        ram_bytes = int(worker_resources["ram_bytes"])
        cpu_cores = int(worker_resources["cpu_cores"])
        disk_type = str(worker_resources["disk_type"])
    except (TypeError, ValueError) as exc:
        raise ValueError("worker_resources contains invalid values.") from exc

    if ram_bytes <= 0 or cpu_cores <= 0:
        raise ValueError("worker_resources must have positive ram_bytes and cpu_cores.")

    return WorkerResources(
        ram_bytes=ram_bytes,
        cpu_cores=cpu_cores,
        disk_type=disk_type,
    )


def build_hardware_profile_key(
    worker_resources: WorkerResources | dict[str, Any],
) -> str:
    """Build a deterministic hardware profile key.

    Args:
        worker_resources: WorkerResources or serialized dict.

    Returns:
        Hardware profile key in the format:
        "{cpu_cores}cores_{ram_gb}GB_{disk_type}".
    """
    resources = _normalize_worker_resources(worker_resources)
    ram_gb = resources.ram_bytes // (1024**3)
    return f"{resources.cpu_cores}cores_{ram_gb}GB_{resources.disk_type}"


def group_importances_by_hardware(
    profile_results: Iterable[
        tuple[ImportanceResult, WorkerResources | dict[str, Any]]
    ],
) -> dict[str, ImportanceResult]:
    """Group importance results by hardware profile key.

    Args:
        profile_results: Iterable of (ImportanceResult, worker_resources).

    Returns:
        Mapping of hardware profile key to ImportanceResult.

    Raises:
        ValueError: When no results are provided or duplicate profiles exist.
    """
    profile_map: dict[str, ImportanceResult] = {}
    for importance, resources in profile_results:
        profile_key = build_hardware_profile_key(resources)
        if profile_key in profile_map:
            raise ValueError(
                f"Duplicate importance result for hardware profile: {profile_key}"
            )
        profile_map[profile_key] = importance

    if not profile_map:
        raise ValueError("No importance results provided for validation.")

    return profile_map


def _kendall_tau(
    importances_a: dict[str, float],
    importances_b: dict[str, float],
) -> float:
    """Compute Kendall's tau between two importance dictionaries.

    Args:
        importances_a: Marginal importance mapping for profile A.
        importances_b: Marginal importance mapping for profile B.

    Returns:
        Kendall's tau value, or 0.0 when undefined.
    """
    shared_knobs = sorted(set(importances_a.keys()) & set(importances_b.keys()))
    if len(shared_knobs) < 2:
        return 0.0

    values_a = [importances_a[knob] for knob in shared_knobs]
    values_b = [importances_b[knob] for knob in shared_knobs]
    tau, _ = kendalltau(values_a, values_b)
    if np.isnan(tau):
        return 0.0
    return float(tau)


def _build_tier_rank_map(tier_count: int) -> dict[str, int]:
    """Build tier rank mapping for a given tier count.

    Args:
        tier_count: Number of tiers.

    Returns:
        Mapping of tier name to rank (1 = most important).
    """
    return get_tier_rank_map(get_tier_names(tier_count))


def validate_hardware_importance(
    profile_results: Iterable[
        tuple[ImportanceResult, WorkerResources | dict[str, Any]]
    ],
    *,
    tier_k_values: Sequence[int] = DEFAULT_K_VALUES,
    tier_fallback_k: int = DEFAULT_FALLBACK_K,
    combined_data: Optional[
        Sequence[tuple[LoadedData, WorkerResources | dict[str, Any]]]
    ] = None,
) -> HardwareValidationResult:
    """Validate importance stability across hardware profiles.

    Args:
        profile_results: Iterable of (ImportanceResult, worker_resources).
        tier_k_values: Candidate k values for tier generation.
        tier_fallback_k: Fallback k if silhouette is undefined.
        combined_data: Optional (LoadedData, worker_resources) for combined modeling.

    Returns:
        HardwareValidationResult describing stability and conservative tiers.
    """
    profile_map = group_importances_by_hardware(profile_results)
    profile_keys = sorted(profile_map.keys())

    kendall_taus: dict[tuple[str, str], float] = {}
    for key_a, key_b in combinations(profile_keys, 2):
        tau = _kendall_tau(
            profile_map[key_a].marginal_importances,
            profile_map[key_b].marginal_importances,
        )
        kendall_taus[(key_a, key_b)] = tau

    tier_results = {
        key: generate_tiers(
            profile_map[key].marginal_importances,
            workload_label=key,
            k_values=tier_k_values,
            fallback_k=tier_fallback_k,
        )
        for key in profile_keys
    }
    tier_assignments = {key: tier_results[key].tier_assignments for key in profile_keys}

    stable_knobs: list[str] = []
    shifting_knobs: dict[str, dict[str, str]] = {}
    conservative_tiers: dict[str, str] = {}

    if profile_keys:
        # Under SCALPEL, ``tier_assignments`` only contains BORUTA-confirmed
        # knobs (non-confirmed are absent), so this intersection becomes the
        # set of knobs confirmed across every hardware profile rather than
        # the set of all labelled knobs. The result is exposed downstream
        # via ``hardware_validation.stable_knobs_semantics`` so reviewers
        # can interpret the value correctly. Under the legacy Lorenz
        # fallback every knob is present, preserving original semantics.
        common_knobs = set(tier_assignments[profile_keys[0]].keys())
        for key in profile_keys[1:]:
            common_knobs &= set(tier_assignments[key].keys())

        for knob in sorted(common_knobs):
            tiers_by_profile = {
                key: tier_assignments[key][knob] for key in profile_keys
            }
            if len(set(tiers_by_profile.values())) == 1:
                stable_knobs.append(knob)
            else:
                shifting_knobs[knob] = tiers_by_profile

        for knob, tiers_by_profile in shifting_knobs.items():
            best_tier = None
            best_rank = None
            for key, tier in tiers_by_profile.items():
                rank_map = _build_tier_rank_map(tier_results[key].optimal_k)
                rank = rank_map[tier]
                if best_rank is None or rank < best_rank:
                    best_rank = rank
                    best_tier = tier
            if best_tier is not None:
                conservative_tiers[knob] = best_tier

    if not profile_keys:
        LOGGER.warning("No hardware profiles found for validation.")

    if len(profile_keys) == 1 and not stable_knobs:
        stable_knobs = sorted(tier_assignments[profile_keys[0]].keys())

    combined_importances = None
    if combined_data is not None:
        if len(profile_keys) < 2:
            LOGGER.info("Skipping combined model: need at least two hardware profiles.")
        else:
            combined_importances = train_combined_importance(combined_data)

    return HardwareValidationResult(
        kendall_taus=kendall_taus,
        stable_knobs=stable_knobs,
        shifting_knobs=shifting_knobs,
        conservative_tiers=conservative_tiers,
        combined_importances=combined_importances,
    )


def build_combined_loaded_data(
    profile_data: Sequence[tuple[LoadedData, WorkerResources | dict[str, Any]]],
) -> LoadedData:
    """Build a combined LoadedData with hardware features added.

    Args:
        profile_data: Sequence of (LoadedData, worker_resources).

    Returns:
        Combined LoadedData with ram_bytes, cpu_cores, and disk_type features.

    Raises:
        ValueError: If profile_data is empty or knob columns differ.
    """
    if not profile_data:
        raise ValueError("profile_data is required to build combined dataset.")

    normalized_profiles: list[tuple[LoadedData, WorkerResources]] = []
    disk_types: list[str] = []
    expected_columns: Optional[set[str]] = None

    for loaded_data, resources in profile_data:
        normalized = _normalize_worker_resources(resources)
        normalized_profiles.append((loaded_data, normalized))
        disk_types.append(normalized.disk_type)

        columns = set(loaded_data.config_df.columns)
        if expected_columns is None:
            expected_columns = columns
        elif columns != expected_columns:
            raise ValueError("All profiles must use the same knob columns.")

        if len(loaded_data.config_df) != len(loaded_data.scores):
            raise ValueError("Config rows and score rows must match.")

    disk_type_mapping = {
        disk_type: idx for idx, disk_type in enumerate(sorted(set(disk_types)))
    }

    combined_frames: list[pd.DataFrame] = []
    combined_scores: list[pd.Series] = []
    combined_metadata: list[dict[str, Any]] = []
    combined_session_indices: list[pd.Series] = []
    combined_generation_indices: list[pd.Series] = []
    session_offset = 0

    for loaded_data, resources in normalized_profiles:
        profile_key = build_hardware_profile_key(resources)
        df = loaded_data.config_df.copy()
        df["ram_bytes"] = int(resources.ram_bytes)
        df["cpu_cores"] = int(resources.cpu_cores)
        df["disk_type"] = int(disk_type_mapping[resources.disk_type])

        combined_frames.append(df)
        combined_scores.append(loaded_data.scores.reset_index(drop=True))

        # Session indices need a per-profile offset so cluster ids stay
        # unique across the combined dataset. Generation indices do not
        # need offsetting because SCALPEL builds clusters from the
        # composite ``session_index:generation_index`` tuple downstream.
        if not loaded_data.session_index.empty:
            shifted = loaded_data.session_index.reset_index(drop=True) + session_offset
            combined_session_indices.append(shifted)
            session_offset += int(loaded_data.session_index.max() or 0) + 1
        else:
            combined_session_indices.append(
                pd.Series(dtype="int64", name="session_index")
            )
        combined_generation_indices.append(
            loaded_data.generation_index.reset_index(drop=True)
            if not loaded_data.generation_index.empty
            else pd.Series(dtype="int64", name="generation_index")
        )

        for entry in loaded_data.metadata:
            entry_copy = dict(entry)
            entry_copy["hardware_profile"] = profile_key
            combined_metadata.append(entry_copy)

    combined_df = pd.concat(combined_frames, ignore_index=True)
    combined_score_series = pd.concat(combined_scores, ignore_index=True)
    combined_session_series = (
        pd.concat(combined_session_indices, ignore_index=True)
        if any(not s.empty for s in combined_session_indices)
        else pd.Series(dtype="int64", name="session_index")
    )
    combined_generation_series = (
        pd.concat(combined_generation_indices, ignore_index=True)
        if any(not s.empty for s in combined_generation_indices)
        else pd.Series(dtype="int64", name="generation_index")
    )

    # Merge per-profile knob_bounds (union of widest bounds) so the
    # domain-spec-aware bounds established by _extract_knob_bounds()
    # survive into the combined model.  For columns not present in
    # any profile (hardware descriptors like ram_bytes, cpu_cores,
    # disk_type) fall back to observed min/max.
    knob_bounds: dict[str, tuple[float, float]] = {}
    for column in combined_df.columns:
        series = combined_df[column]
        if series.empty:
            knob_bounds[column] = (0.0, 1.0)
            continue
        profile_bounds = [
            ld.knob_bounds[column]
            for ld, _ in normalized_profiles
            if column in ld.knob_bounds
        ]
        if profile_bounds:
            b_min = min(b[0] for b in profile_bounds)
            b_max = max(b[1] for b in profile_bounds)
        else:
            b_min = float(series.min())
            b_max = float(series.max())
        # Also ensure every observed value in the combined set fits.
        b_min = min(b_min, float(series.min()))
        b_max = max(b_max, float(series.max()))
        knob_bounds[column] = (b_min, b_max)

    base_metric_config = normalized_profiles[0][0].metric_config

    return LoadedData(
        config_df=combined_df,
        scores=combined_score_series,
        metadata=combined_metadata,
        metric_config=base_metric_config,
        knob_bounds=knob_bounds,
        n_observations=len(combined_df),
        session_index=combined_session_series,
        generation_index=combined_generation_series,
    )


def train_combined_importance(
    profile_data: Sequence[tuple[LoadedData, WorkerResources | dict[str, Any]]],
    *,
    n_estimators: int = DEFAULT_RF_N_ESTIMATORS,
    max_depth: Optional[int] = DEFAULT_RF_MAX_DEPTH,
    random_state: int = DEFAULT_RF_RANDOM_STATE,
    min_samples_split: int = DEFAULT_RF_MIN_SAMPLES_SPLIT,
    min_samples_leaf: int = DEFAULT_RF_MIN_SAMPLES_LEAF,
    max_features: Optional[float | int | str] = DEFAULT_RF_MAX_FEATURES,
    bootstrap: bool = DEFAULT_RF_BOOTSTRAP,
    max_samples: Optional[int | float] = DEFAULT_RF_MAX_SAMPLES,
    top_k: int = DEFAULT_TOP_K,
    interaction_order: int = DEFAULT_INTERACTION_ORDER,
) -> ImportanceResult:
    """Train a combined model with hardware features and run importance analysis.

    Args:
        profile_data: Sequence of (LoadedData, worker_resources).
        n_estimators: RandomForest tree count.
        max_depth: RandomForest max depth.
        random_state: Random seed.
        min_samples_split: Minimum samples to split an internal node.
        min_samples_leaf: Minimum samples per leaf.
        max_features: Features considered at each split.
        bootstrap: Whether to use bootstrap samples.
        max_samples: Bootstrap sample size.
        top_k: Top-k features for interaction analysis.
        interaction_order: Maximum order of fANOVA interactions.

    Returns:
        ImportanceResult for the combined model.
    """
    combined_data = build_combined_loaded_data(profile_data)
    return analyze_knob_importance(
        combined_data,
        n_estimators=n_estimators,
        max_depth=max_depth,
        random_state=random_state,
        min_samples_split=min_samples_split,
        min_samples_leaf=min_samples_leaf,
        max_features=max_features,
        bootstrap=bootstrap,
        max_samples=max_samples,
        top_k=top_k,
        interaction_order=interaction_order,
    )
