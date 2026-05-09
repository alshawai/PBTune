"""
Loader/Bridge for knob importance analysis results.
"""

from dataclasses import dataclass
from pathlib import Path

import numpy as np
import pandas as pd

from src.analysis.importance import ImportanceResult
from src.analysis.data_loader import load_pbt_results
from src.analysis.importance import analyze_knob_importance
from src.utils.logger import get_logger

LOGGER = get_logger("ImportanceLoader")


@dataclass
class ImportanceData:
    """Visualization-ready format for importance results."""

    knob_names: list[str]  # Sorted by importance (descending)
    fanova_scores: np.ndarray  # Marginal importance values
    shap_scores: np.ndarray  # Mean |SHAP| values
    shap_values: np.ndarray  # Full SHAP matrix (n_samples × n_features)
    pairwise_matrix: np.ndarray  # Interaction heatmap matrix
    pairwise_labels: list[str]  # Row/col labels for heatmap
    correlation: float  # fANOVA–SHAP rank correlation
    config_df: pd.DataFrame  # Raw configs for dependence plots


def load_importance(
    result: ImportanceResult, config_df: pd.DataFrame
) -> ImportanceData:
    """
    Bridge an ImportanceResult from src.analysis.importance into the
    format needed by visualization plots.
    """
    # 1. Marginal importances (these are already sorted descending in ImportanceResult)
    knob_names = list(result.marginal_importances.keys())
    fanova_scores = np.array([result.marginal_importances[k] for k in knob_names])

    # 2. SHAP scores aligned to the sorted knob names
    shap_scores = np.array([result.shap_importances.get(k, 0.0) for k in knob_names])

    # 3. Pairwise interactions matrix
    # Extract the top interacting knobs to form a square matrix
    interacting_knobs = set()
    for k1, k2 in result.pairwise_interactions.keys():
        interacting_knobs.add(k1)
        interacting_knobs.add(k2)

    pairwise_labels = sorted(list(interacting_knobs))
    n_pairs = len(pairwise_labels)
    pairwise_matrix = np.zeros((n_pairs, n_pairs))

    for i, row_k in enumerate(pairwise_labels):
        for j, col_k in enumerate(pairwise_labels):
            if i == j:
                continue
            # Check both orderings
            val = result.pairwise_interactions.get((row_k, col_k))
            if val is None:
                val = result.pairwise_interactions.get((col_k, row_k), 0.0)
            pairwise_matrix[i, j] = val

    return ImportanceData(
        knob_names=knob_names,
        fanova_scores=fanova_scores,
        shap_scores=shap_scores,
        shap_values=result.shap_values,  # Keep raw SHAP matrix for beeswarm/dependence
        pairwise_matrix=pairwise_matrix,
        pairwise_labels=pairwise_labels,
        correlation=result.fanova_shap_correlation,
        config_df=config_df,
    )


def load_importance_from_dir(
    directory: Path | str, default_workload: str = "oltp"
) -> ImportanceData:
    """
    Convenience function that loads JSONs, runs the fANOVA analysis,
    and bridges to ImportanceData in one step.
    Note: This is computationally expensive!
    """
    path = Path(directory)
    if not path.exists():
        raise FileNotFoundError(f"Directory not found: {path}")

    LOGGER.info(
        "Loading data from %s and running importance analysis (this may take a moment)...",
        path,
    )

    loaded_data = load_pbt_results(path, default_workload_type=default_workload)
    result = analyze_knob_importance(loaded_data)

    return load_importance(result, loaded_data.config_df)
