"""
PBT Analysis Data Loader
========================

This module provides loaders for parsing mult-session execution histories from Population
Based Training (PBT). It handles global metric re-scoring and dataframe encoding to prepare
data for downstream Machine Learning models and visualization.
"""

from __future__ import annotations
import json
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Optional

import pandas as pd

from src.tuners.utils.session_schema import get_history, get_iteration_index
from src.utils.metrics import MetricConfig, PerformanceMetrics, create_metric_config
from src.utils.calibration import rescore_metrics_globally
from src.knobs.knob_loader import get_knob_space
from src.knobs.knob_space import HARDWARE_RELATIVE_SPECS
from src.utils.hardware_info import WorkerResources
from src.utils.logger import get_logger

LOGGER = get_logger("Loader")

# Session-file discovery patterns. The unified tuners write a strategy-agnostic
# ``trace_*.json`` (the strategy is encoded in the ``sessions/<workload>/
# <strategy>/`` path, not the filename). The legacy per-strategy stems
# (``pbt_results_*.json`` / ``lhs_results_*.json`` / ``bo_results_*.json``) are
# still globbed so pre-rename traces on disk keep loading. All emit a PBT-shaped
# ``generation_history`` and are natively loadable here.
RESULT_FILE_GLOBS = (
    "trace_*.json",
    "pbt_results_*.json",
    "lhs_results_*.json",
    "bo_results_*.json",
)


def find_result_files(directory):
    """Return all known result-session files in ``directory``, name-sorted.

    Globs every pattern in :data:`RESULT_FILE_GLOBS` so PBT and LHS traces are
    discovered uniformly. The single source of truth for "what is a loadable
    session file" -- used by the loader and the SCALPEL grouping/walk gates.
    """
    found = []
    for pattern in RESULT_FILE_GLOBS:
        found.extend(directory.glob(pattern))
    return sorted(found, key=lambda p: p.name)


@dataclass
class LoadedData:
    """
    Container for processed PBT results.

    Attributes
    ----------
    config_df : pd.DataFrame
        DataFrame of all valid configurations from all sessions.
    scores : pd.Series
        Globally re-scored objective metrics for each configuration.
    metadata : list[dict[str, Any]]
        System and setup metadata collected from each session.
    metric_config : MetricConfig
        The globally calibrated MetricConfig used for scoring.
    knob_bounds: dict[str, tuple[float, float]]
        Domain bounds for each variable used by fANOVA or HyperOpt algorithms.
    n_observations : int
        Total number of valid evaluations extracted.
    session_index : pd.Series
        Per-row integer code indicating which input session JSON the row
        came from (index into ``metadata``). Aligned 1:1 with ``config_df``
        rows on the same ``RangeIndex(0, n_observations)``. Used by SCALPEL
        to build cluster ids for group-permutation BORUTA / stability so
        the null respects PBT's non-i.i.d. structure.
    generation_index : pd.Series
        Per-row PBT generation index. Aligned 1:1 with ``config_df``.
        Combined with ``session_index`` to form ``sample_groups``.
    """

    config_df: pd.DataFrame
    scores: pd.Series
    metadata: list[dict[str, Any]]
    metric_config: MetricConfig
    knob_bounds: dict[str, tuple[float, float]]
    n_observations: int
    session_index: pd.Series = field(
        default_factory=lambda: pd.Series(dtype="int64", name="session_index")
    )
    generation_index: pd.Series = field(
        default_factory=lambda: pd.Series(dtype="int64", name="generation_index")
    )


def _encode_dataframe_features(df: pd.DataFrame) -> pd.DataFrame:
    """
    Encode DataFrame configuration parameters inplace for ML compatibility.

    Converts:
    1. Booleans (and PostgreSQL "on"/"off" strings) to integers (0, 1)
    2. Enums directly to label encoded integers based on alphabetical sorting.

    Parameters
    ----------
    df : pd.DataFrame
        Raw dataframe of decoded PostgreSQL configurations.

    Returns
    -------
    pd.DataFrame
        Encoded dataframe ready for regression/classification.
    """
    if df.empty:
        return df

    # Alphabetize columns for determinism
    df = df.reindex(sorted(df.columns), axis=1)

    for col in df.columns:
        # Map explicit python booleans directly to 0/1.
        # json.load() produces Python bool objects, but pandas infers columns
        # of bools as object dtype, so we must also check infer_dtype.
        if (
            df[col].dtype == bool
            or pd.api.types.infer_dtype(df[col].dropna(), skipna=True) == "boolean"
        ):
            df[col] = df[col].astype(bool).astype(int)
            continue

        # Analyze string/object columns to differentiate Bools vs Enums
        if df[col].dtype == object or pd.api.types.is_string_dtype(df[col]):
            unique_vals = set(df[col].dropna().astype(str).str.lower())

            # PostgreSQL represents booleans as "on" or "off" primarily
            if unique_vals.issubset({"on", "off", "true", "false", "1", "0"}):
                df[col] = (
                    df[col]
                    .astype(str)
                    .str.lower()
                    .map({"true": 1, "on": 1, "1": 1, "false": 0, "off": 0, "0": 0})
                    .fillna(0)
                    .astype(int)
                )
            else:
                # Pure ENUM columns
                # Sort valid enumeration options alphabetically to construct stable mapping
                sorted_options = sorted(list(unique_vals))
                mapping = {val: idx for idx, val in enumerate(sorted_options)}

                # Apply mapping to dataframe column
                df[col] = (
                    df[col].astype(str).str.lower().map(mapping).fillna(-1).astype(int)
                )

    return df


def _coerce_worker_resources(
    worker_resources: Optional[WorkerResources | dict[str, Any]],
) -> Optional[WorkerResources]:
    """Normalize serialized worker resources into WorkerResources dataclass."""
    if worker_resources is None:
        return None

    if isinstance(worker_resources, WorkerResources):
        return worker_resources

    if not isinstance(worker_resources, dict) or not worker_resources:
        return None

    required_fields = {"ram_bytes", "cpu_cores", "disk_type"}
    missing_fields = required_fields - set(worker_resources.keys())
    if missing_fields:
        LOGGER.warning(
            "worker_resources missing required fields (%s); "
            "skipping hardware-aware bound resolution.",
            ", ".join(sorted(missing_fields)),
        )
        return None

    try:
        resources = WorkerResources(
            ram_bytes=int(worker_resources["ram_bytes"]),
            cpu_cores=int(worker_resources["cpu_cores"]),
            disk_type=str(worker_resources["disk_type"]),
        )
    except (TypeError, ValueError) as exc:
        LOGGER.warning(
            "Invalid worker_resources values; "
            "skipping hardware-aware bound resolution: %s",
            exc,
        )
        return None

    if resources.ram_bytes <= 0 or resources.cpu_cores <= 0:
        LOGGER.warning(
            "worker_resources must be positive (ram_bytes=%s, cpu_cores=%s); "
            "skipping hardware-aware bound resolution.",
            resources.ram_bytes,
            resources.cpu_cores,
        )
        return None

    return resources


def _extract_knob_bounds(
    df: pd.DataFrame,
    worker_resources: Optional[WorkerResources | dict[str, Any]] = None,
    tier: str = "extensive",
) -> dict[str, tuple[float, float]]:
    """Determine continuous/discrete bounds for fANOVA ConfigSpace using KnobSpecs."""
    bounds: dict[str, tuple[float, float]] = {}
    parsed_resources = _coerce_worker_resources(worker_resources)
    try:
        space = get_knob_space(tier, knob_source="expert")
        if parsed_resources is not None:
            space.resolve_hardware_ranges(parsed_resources)
    except Exception as e:
        LOGGER.warning(f"Knob space unavailable, using empirical fallback bounds: {e}")
        space = None

    for col in df.columns:
        b_min, b_max = 0.0, 1.0

        if space and col in space.knobs:
            kd = space.knobs[col]
            if (
                kd.hardware_relative
                and col in HARDWARE_RELATIVE_SPECS
                and parsed_resources is None
            ):
                specs = HARDWARE_RELATIVE_SPECS[col]
                b_min, b_max = float(specs[0]), float(specs[1])
            elif kd.knob_type.name == "BOOLEAN":
                b_min, b_max = 0.0, 1.0
            elif kd.knob_type.name == "ENUM":
                b_max = float(df[col].max()) if not df.empty else 1.0
            else:
                b_min = float(kd.min_value) if kd.min_value is not None else 0.0
                b_max = float(kd.max_value) if kd.max_value is not None else 1.0
        else:
            if df[col].dtype == int or df[col].dtype == bool:
                b_min, b_max = 0.0, float(df[col].max()) if not df.empty else 1.0
            else:
                b_min = float(df[col].min()) if not df.empty else 0.0
                b_max = float(df[col].max()) if not df.empty else 1.0

        # Bounds must enclose every observed value or fANOVA's ConfigSpace check
        # rejects the sample. Widen with observed extrema to cover:
        #   - sessions whose hardware-relative resolution differed from the
        #     first session's `worker_resources`
        #   - PostgreSQL sentinel values (e.g. -1 = "inherit") on int/real knobs
        #   - enum codes mapped to -1 by `_encode_dataframe_features` when the
        #     value is missing from the column's unique set
        if not df.empty:
            b_min = min(b_min, float(df[col].min()))
            b_max = max(b_max, float(df[col].max()))

        bounds[col] = (b_min, b_max)

    return bounds


def _infer_tuning_strategy(
    session_meta: dict[str, Any],
    file_path: Path,
) -> str:
    """Resolve the tuning_strategy label for a session file.

    Prefers the explicit ``tuning_session.tuning_strategy`` field. Falls back
    to a path heuristic for legacy and current session paths:
    ``/pbt/`` or ``/pbt_runs/`` -> ``"pbt"``, ``/bo/`` or ``/bo_runs/`` ->
    ``"bo"``, ``/lhs/`` or ``/lhs_runs/`` -> ``"lhs"``.
    Returns ``"unknown"`` when neither path nor field resolves.
    """
    explicit = session_meta.get("tuning_strategy")
    if explicit:
        return str(explicit)
    path_str = str(file_path)
    for segment, label in [
        ("/pbt/", "pbt"), ("/bo/", "bo"), ("/lhs/", "lhs"),
        ("/pbt_runs/", "pbt"), ("/bo_runs/", "bo"), ("/lhs_runs/", "lhs"),
    ]:
        if segment in path_str:
            return label
    return "unknown"


def _build_session_metadata(
    file_path: Path,
    session_meta: dict[str, Any],
    data: dict[str, Any],
    default_workload_type: str,
) -> dict[str, Any]:
    """Build normalized metadata payload for one tuning session file."""
    # New schema nests the raw snapshot under tuning_session.environment
    # .system_info; fall back to the legacy top-level system_info for older traces.
    raw_env = session_meta.get("environment")
    env_block: dict[str, Any] = raw_env if isinstance(raw_env, dict) else {}
    # New schema nests benchmark runtime params under tuning_session.benchmark.
    raw_bench = session_meta.get("benchmark")
    bench_block: dict[str, Any] = raw_bench if isinstance(raw_bench, dict) else {}
    metadata = {
        "file_name": file_path.name,
        "workload_type": session_meta.get("workload_type", default_workload_type),
        "benchmark_name": session_meta.get("benchmark_name", "unknown"),
        "tuning_strategy": _infer_tuning_strategy(session_meta, file_path),
        "system_info": env_block.get("system_info") or data.get("system_info", {}),
        # Unified schema nests worker_resources under tuning_session; older
        # traces carry it at the top level. Prefer nested, fall back to root.
        "worker_resources": session_meta.get("worker_resources")
        or data.get("worker_resources", {}),
    }
    # Promote to granular sysbench workload when available.
    # The tuner writes the coarse WorkloadType enum ("oltp") as workload_type,
    # but the granular sysbench mode (e.g. "oltp_read_write") is stored separately
    # under "sysbench_workload" (new schema: under tuning_session.benchmark).
    granular_workload = bench_block.get("sysbench_workload") or session_meta.get(
        "sysbench_workload"
    )
    if metadata["benchmark_name"] == "sysbench" and granular_workload:
        metadata["workload_type"] = granular_workload

    # Preserve all additional session_meta fields (e.g., sysbench_workload, scale_factor)
    for key, value in session_meta.items():
        if key not in {"workload_type", "benchmark_name", "tuning_strategy"}:
            metadata[key] = value

    # Also grab scoring overrides. The unified schema (2a′+) namespaces these
    # under ``tuning_session.scoring``; promote them flat (nested-first) so the
    # downstream global-rescoring reads see a uniform metadata dict regardless
    # of session shape. Older/BO-flat sessions fall back to the root object.
    scoring_block = session_meta.get("scoring") or {}
    for scoring_key in [
        "scoring_policy",
        "scoring_policy_version",
        "metric_reference_version",
    ]:
        if scoring_key in scoring_block:
            metadata[scoring_key] = scoring_block[scoring_key]
        elif scoring_key not in metadata and scoring_key in data:
            metadata[scoring_key] = data[scoring_key]

    return metadata


def _to_coarse_workload(workload: str) -> str:
    """Normalize a granular workload type to a coarse workload type ('oltp', 'olap', or 'mixed')."""
    workload_lower = workload.lower()
    if "oltp" in workload_lower:
        return "oltp"
    if "olap" in workload_lower or "tpch" in workload_lower:
        return "olap"
    if "mixed" in workload_lower:
        return "mixed"
    return "oltp"  # Fallback default


def load_pbt_results(
    directory_path: str | Path,
    default_workload_type: str = "oltp",
    file_paths: Optional[list[Path]] = None,
) -> LoadedData:
    """
    Load, validate, and globally re-score PBT training results across multiple files.

    This loader implements global re-scoring. It extracts metrics from several
    independent PBT JSON result files and normalizes them uniformly so that scores are
    directly comparable downstream on an absolute scale.

    Parameters
    ----------
    directory_path : str | Path
        Directory containing `pbt_results_*.json` files.
    default_workload_type : str
        The default workload type to use for scoring if not specified in metadata.

    Returns
    -------
    LoadedData
        Processed configurations, global scores, and metadata.

    Raises
    ------
    FileNotFoundError
        If no JSON result files are found.
    ValueError
        If the knob parameters tuned differ between sessions.
    """
    dir_path = Path(directory_path)
    if not dir_path.exists() or not dir_path.is_dir():
        raise FileNotFoundError(f"Directory not found: {directory_path}")

    if file_paths is not None:
        json_files = file_paths
        if not json_files:
            raise FileNotFoundError("Provided file_paths list is empty")
    else:
        json_files = find_result_files(dir_path)
        if not json_files:
            raise FileNotFoundError(f"No PBT result files found in {directory_path}")

    LOGGER.info(f"Loading {len(json_files)} PBT result records from {directory_path}")

    raw_configs: list[dict[str, Any]] = []
    valid_metrics: list[PerformanceMetrics] = []
    metadata_list: list[dict[str, Any]] = []
    session_indices: list[int] = []
    generation_indices: list[int] = []
    target_knob_set = None

    # 1. Parsing and Extraction
    for file_path in json_files:
        try:
            with open(file_path, "r", encoding="utf-8") as f:
                data = json.load(f)
        except json.JSONDecodeError as e:
            LOGGER.error(f"Failed to parse {file_path.name}: {e}")
            continue

        session_meta = data.get("tuning_session", {})
        metadata_list.append(
            _build_session_metadata(
                file_path=file_path,
                session_meta=session_meta,
                data=data,
                default_workload_type=default_workload_type,
            )
        )
        session_id_int = len(metadata_list) - 1

        for gen_position, gen in enumerate(get_history(data)):
            worker_configs = gen.get("worker_configs", [])
            worker_scores = gen.get("worker_scores", [])
            generation_id = int(get_iteration_index(gen, gen_position))

            # Actual JSON format (written by main.py):
            #   worker_configs: [{worker_id, config}]
            #   worker_scores:  [{worker_id, score, metrics}]   ← metrics nested here
            # Join by worker_id so ordering differences don't corrupt alignment.
            score_by_id = {ws["worker_id"]: ws for ws in worker_scores}

            for config_obj in worker_configs:
                worker_id = config_obj.get("worker_id")
                config = config_obj.get("config", {})
                score_obj = score_by_id.get(worker_id, {})
                old_score = score_obj.get("score")
                metrics_dict = score_obj.get("metrics") or {}

                # Validation: Mismatched dimensions crashes clustering models
                current_knobs = frozenset(config.keys())
                if target_knob_set is None:
                    target_knob_set = current_knobs
                elif target_knob_set != current_knobs:
                    raise ValueError(
                        f"Knob set mismatch detected. File {file_path.name} tuned "
                        f"{len(current_knobs)} knobs, expected {len(target_knob_set)}. "
                        "All sessions must share identical tunable parameters."
                    )

                # Omit null scores and degraded evaluation failures
                if old_score is None or metrics_dict.get("failure_type") is not None:
                    continue

                try:
                    # Construct metrics object bridging older json exports and current class structure
                    valid_keys = PerformanceMetrics.__dataclass_fields__.keys()
                    filtered_metrics = {
                        k: v for k, v in metrics_dict.items() if k in valid_keys
                    }
                    pm = PerformanceMetrics(**filtered_metrics)
                except Exception as e:
                    LOGGER.debug(
                        f"Failed to parse metric dictionary in {file_path.name}: {e}"
                    )
                    continue

                raw_configs.append(config)
                valid_metrics.append(pm)
                session_indices.append(session_id_int)
                generation_indices.append(generation_id)

    n_valid = len(raw_configs)
    if n_valid == 0:
        LOGGER.warning(
            f"No valid observations successfully loaded from {len(json_files)} files."
        )
        return LoadedData(
            config_df=pd.DataFrame(),
            scores=pd.Series(dtype=float),
            metadata=metadata_list,
            metric_config=create_metric_config(
                _to_coarse_workload(default_workload_type)
            ),
            knob_bounds={},
            n_observations=0,
            session_index=pd.Series(dtype="int64", name="session_index"),
            generation_index=pd.Series(dtype="int64", name="generation_index"),
        )

    # 2. Global Rescoring
    workload = str(metadata_list[0].get("workload_type", default_workload_type))
    scoring_policy = (
        metadata_list[0].get("scoring_policy", None) if metadata_list else None
    )
    scoring_policy_version = (
        metadata_list[0].get("scoring_policy_version", None) if metadata_list else None
    )
    metric_ref_version = (
        metadata_list[0].get("metric_reference_version", None)
        if metadata_list
        else None
    )

    global_metric_config, global_scores, rescoring_metadata = rescore_metrics_globally(
        valid_metrics,
        workload=_to_coarse_workload(workload),
        padding_factor=0.0,
        scoring_policy=scoring_policy,
        scoring_policy_version=scoring_policy_version,
        metric_reference_version=metric_ref_version,
    )
    LOGGER.info(
        "Computed global ranges via shared rescoring utility: latency=%s calibrated=%s",
        rescoring_metadata["latency_metric"],
        rescoring_metadata["ranges_calibrated"],
    )

    global_metric_config.normalization_metadata = rescoring_metadata
    for md in metadata_list:
        md["rescoring_metadata"] = rescoring_metadata

    # 3. DataFrame Post-Processing
    df = pd.DataFrame(raw_configs)
    df_encoded = _encode_dataframe_features(df).reset_index(drop=True)
    scores_series = pd.Series(global_scores, name="score").reset_index(drop=True)
    session_index_series = pd.Series(
        session_indices, name="session_index", dtype="int64"
    ).reset_index(drop=True)
    generation_index_series = pd.Series(
        generation_indices, name="generation_index", dtype="int64"
    ).reset_index(drop=True)

    worker_resources = (
        metadata_list[0].get("worker_resources", {}) if metadata_list else {}
    )
    knob_tier = (
        metadata_list[0].get("knob_tier", "extensive") if metadata_list else "extensive"
    )

    knob_bounds = _extract_knob_bounds(df_encoded, worker_resources, knob_tier)

    LOGGER.info(
        f"Loaded {n_valid} valid configurations with {len(df_encoded.columns)} variables."
    )

    return LoadedData(
        config_df=df_encoded,
        scores=scores_series,
        metadata=metadata_list,
        metric_config=global_metric_config,
        knob_bounds=knob_bounds,
        n_observations=n_valid,
        session_index=session_index_series,
        generation_index=generation_index_series,
    )
