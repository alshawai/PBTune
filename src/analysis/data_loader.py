"""
PBT Analysis Data Loader
========================

This module provides loaders for parsing mult-session execution histories from Population 
Based Training (PBT). It handles global metric re-scoring and dataframe encoding to prepare 
data for downstream Machine Learning models and visualization.
"""

from __future__ import annotations
import json
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Dict, List, Tuple

import pandas as pd

from src.utils.metrics import (
    MetricConfig, 
    PerformanceMetrics, 
    create_metric_config
)
from src.tuner.config.knob_loader import get_knob_space
from src.tuner.config.knob_space import HARDWARE_RELATIVE_SPECS
from src.utils.logger import get_logger

logger = get_logger(__name__)

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
    metadata : List[Dict[str, Any]]
        System and setup metadata collected from each session.
    metric_config : MetricConfig
        The globally calibrated MetricConfig used for scoring.
    knob_bounds: Dict[str, Tuple[float, float]]
        Domain bounds for each variable used by fANOVA or HyperOpt algorithms.
    n_observations : int
        Total number of valid evaluations extracted.
    """
    config_df: pd.DataFrame
    scores: pd.Series
    metadata: List[Dict[str, Any]]
    metric_config: MetricConfig
    knob_bounds: Dict[str, Tuple[float, float]]
    n_observations: int


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
        if df[col].dtype == bool or pd.api.types.infer_dtype(df[col].dropna(), skipna=True) == 'boolean':
            df[col] = df[col].astype(bool).astype(int)
            continue

        # Analyze string/object columns to differentiate Bools vs Enums
        if df[col].dtype == object or pd.api.types.is_string_dtype(df[col]):
            unique_vals = set(df[col].dropna().astype(str).str.lower())

            # PostgreSQL represents booleans as "on" or "off" primarily
            if unique_vals.issubset({'on', 'off', 'true', 'false', '1', '0'}):
                df[col] = df[col].astype(str).str.lower().map({
                    'true': 1, 'on': 1, '1': 1, 'false': 0, 'off': 0, '0': 0
                }).fillna(0).astype(int)
            else:
                # Pure ENUM columns
                # Sort valid enumeration options alphabetically to construct stable mapping
                sorted_options = sorted(list(unique_vals))
                mapping = {val: idx for idx, val in enumerate(sorted_options)}
                
                # Apply mapping to dataframe column
                df[col] = df[col].astype(str).str.lower().map(mapping).fillna(-1).astype(int)

    return df


def _extract_knob_bounds(df: pd.DataFrame, worker_resources: Optional[Dict] = None, tier: str = "extensive") -> Dict[str, Tuple[float, float]]:
    """Determine continuous/discrete bounds for fANOVA ConfigSpace using KnobSpecs."""
    bounds = {}
    try:
        space = get_knob_space(tier)
        if worker_resources:
            space.resolve_hardware_ranges(worker_resources)
    except Exception as e:
        logger.warning(f"Knob space unavailable, using empirical fallback bounds: {e}")
        space = None

    for col in df.columns:
        b_min, b_max = 0.0, 1.0
        
        if space and col in space.knobs:
            kd = space.knobs[col]
            if kd.hardware_relative and col in HARDWARE_RELATIVE_SPECS:
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

        bounds[col] = (max(b_min, 0.0), b_max)

    return bounds

def load_pbt_results(
    directory_path: str | Path, 
    default_workload_type: str = "oltp"
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

    json_files = sorted(dir_path.glob("pbt_results_*.json"), key=lambda p: p.name)
    if not json_files:
        raise FileNotFoundError(f"No PBT result files found in {directory_path}")

    logger.info(f"Loading {len(json_files)} PBT result records from {directory_path}")

    raw_configs = []
    valid_metrics: List[PerformanceMetrics] = []
    metadata_list = []
    target_knob_set = None

    # 1. Parsing and Extraction
    for file_path in json_files:
        try:
            with open(file_path, 'r', encoding='utf-8') as f:
                data = json.load(f)
        except json.JSONDecodeError as e:
            logger.error(f"Failed to parse {file_path.name}: {e}")
            continue
            
        session_meta = data.get('tuning_session', {})
        metadata_list.append({
            'file_name': file_path.name,
            'workload_type': session_meta.get('workload_type', default_workload_type),
            'benchmark_name': session_meta.get('benchmark_name', 'unknown'),
            'system_info': data.get('system_info', {}),
            'worker_resources': data.get('worker_resources', {}),
            'knob_tier': session_meta.get('knob_tier', 'extensive')
        })

        for gen in data.get('generation_history', []):
            worker_configs = gen.get('worker_configs', [])
            worker_scores = gen.get('worker_scores', [])

            # Actual JSON format (written by main.py):
            #   worker_configs: [{worker_id, config}]
            #   worker_scores:  [{worker_id, score, metrics}]   ← metrics nested here
            # Join by worker_id so ordering differences don't corrupt alignment.
            score_by_id = {ws['worker_id']: ws for ws in worker_scores}

            for config_obj in worker_configs:
                worker_id = config_obj.get('worker_id')
                config = config_obj.get('config', {})
                score_obj = score_by_id.get(worker_id, {})
                old_score = score_obj.get('score')
                metrics_dict = score_obj.get('metrics') or {}
                
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
                if old_score is None or metrics_dict.get('failure_type') is not None:
                    continue

                try:
                    # Construct metrics object bridging older json exports and current class structure
                    valid_keys = PerformanceMetrics.__dataclass_fields__.keys()
                    filtered_metrics = {k: v for k, v in metrics_dict.items() if k in valid_keys}
                    pm = PerformanceMetrics(**filtered_metrics)
                except Exception as e:
                    logger.debug(f"Failed to parse metric dictionary in {file_path.name}: {e}")
                    continue

                raw_configs.append(config)
                valid_metrics.append(pm)

    n_valid = len(raw_configs)
    if n_valid == 0:
        logger.warning(f"No valid observations successfully loaded from {len(json_files)} files.")
        return LoadedData(
            config_df=pd.DataFrame(),
            scores=pd.Series(dtype=float),
            metadata=metadata_list,
            metric_config=create_metric_config(default_workload_type),
            knob_bounds={},
            n_observations=0
        )

    # 2. Global Rescoring
    workload = metadata_list[0].get('workload_type', default_workload_type)
    global_metric_config = create_metric_config(workload)
    
    # Scale ranges across ALL sessions (0 padding tightly bounds the range)
    logger.info("Computing global bounds across all tuning sessions...")
    global_metric_config.update_ranges(valid_metrics, padding_factor=0.0)

    global_scores = [global_metric_config.compute_score(m) for m in valid_metrics]

    # 3. DataFrame Post-Processing
    df = pd.DataFrame(raw_configs)
    df_encoded = _encode_dataframe_features(df)
    scores_series = pd.Series(global_scores, name="score")
    
    worker_resources = metadata_list[0].get("worker_resources", {}) if metadata_list else {}
    knob_tier = metadata_list[0].get("knob_tier", "extensive") if metadata_list else "extensive"
    
    knob_bounds = _extract_knob_bounds(df_encoded, worker_resources, knob_tier)

    logger.info(f"Loaded {n_valid} valid configurations with {len(df_encoded.columns)} variables.")

    return LoadedData(
        config_df=df_encoded,
        scores=scores_series,
        metadata=metadata_list,
        metric_config=global_metric_config,
        knob_bounds=knob_bounds,
        n_observations=n_valid
    )