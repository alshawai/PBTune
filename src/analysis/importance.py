"""
Knob Importance Analysis
========================

Computes marginal and pairwise importance of database knobs using fANOVA variance decomposition.
"""

from typing import Any, Optional
from dataclasses import dataclass

import pandas as pd
from ConfigSpace import ConfigurationSpace
from ConfigSpace.hyperparameters import (
    UniformFloatHyperparameter,
    UniformIntegerHyperparameter,
)
import numpy as np
from sklearn.ensemble import RandomForestRegressor
from fanova import fANOVA
import shap
from scipy.stats import spearmanr

from src.analysis.data_loader import LoadedData
from src.utils.logger import get_logger


def _ensure_fanova_numpy_aliases() -> None:
    """Patch NumPy aliases expected by older fanova versions."""
    np.__dict__.setdefault("float", float)
    np.__dict__.setdefault("int", int)
    np.__dict__.setdefault("bool", bool)


_ensure_fanova_numpy_aliases()

logger = get_logger(__name__)

CORRELATION_THRESHOLD = 0.7
RETRY_MIN_ESTIMATORS = 512
RETRY_MIN_DEPTH = 10
FANOVA_FALLBACK_MAX_DEPTH = 64


class InsufficientDataError(Exception):
    """Raised when there are fewer than 30 samples available for importance analysis."""

    pass


@dataclass
class ImportanceResult:
    """
    Container for fANOVA importance variance decomposition results.

    Attributes
    ----------
    marginal_importances : dict[str, float]
        Marginal importance scores for each knob (0-1 normalized)
    pairwise_interactions : dict[tuple[str, str], float]
        Pairwise interaction importance scores between knob pairs
    model_r2 : float
        R² score of the underlying Random Forest model
    n_samples : int
        Number of tuning samples used for analysis
    n_features : int
        Number of knobs analyzed
    workload_type : str
        Type of workload (OLTP, OLAP, MIXED)
    shap_importances : dict[str, float]
        SHAP-based importance scores for each knob
    shap_values : np.ndarray
        Raw SHAP values for all samples and features
    fanova_shap_correlation : float
        Correlation between fANOVA and SHAP importance rankings
    scoring_policy : str
        Scoring policy used during tuning (default: "fixed_v1")
    scoring_policy_version : str
        Version of the scoring policy (default: "1.0")
    metric_reference_version : str
        Version of metric reference used (default: "v1")
    """

    marginal_importances: dict[str, float]
    pairwise_interactions: dict[tuple[str, str], float]
    model_r2: float
    n_samples: int
    n_features: int
    workload_type: str
    shap_importances: dict[str, float]
    shap_values: np.ndarray
    fanova_shap_correlation: float
    scoring_policy: str = "fixed_v1"
    scoring_policy_version: str = "1.0"
    metric_reference_version: str = "v1"


@dataclass
class _ImportancePassResult:
    """Internal container for one importance decomposition pass."""

    marginal_importances: dict[str, float]
    shap_importances: dict[str, float]
    shap_values: np.ndarray
    fanova_shap_correlation: float
    model_r2: float
    fanova_model: Any
    column_names: list[str]
    n_estimators: int
    max_depth: Optional[int]


def _drop_zero_variance_columns(df: pd.DataFrame) -> pd.DataFrame:
    """Drop constant columns that cannot contribute to importance analysis."""
    zero_var_cols = df.nunique()[lambda s: s <= 1].index.tolist()
    if zero_var_cols:
        logger.warning(
            "Dropping zero-variance knobs before importance analysis: %s",
            zero_var_cols,
        )
        return df.drop(columns=zero_var_cols)
    return df


def _build_config_space(
    df: pd.DataFrame,
    knob_bounds: dict[str, tuple[float, float]],
) -> ConfigurationSpace:
    """Create ConfigSpace definitions matching encoded dataframe columns."""
    config_space = ConfigurationSpace()

    for col in df.columns:
        b_min, b_max = knob_bounds[col]
        if df[col].dtype.kind in "biu" or pd.api.types.is_integer_dtype(df[col]):
            config_space.add_hyperparameter(
                UniformIntegerHyperparameter(col, int(b_min), int(b_max))
            )
        else:
            config_space.add_hyperparameter(
                UniformFloatHyperparameter(col, float(b_min), float(b_max))
            )

    return config_space


def _compute_rank_correlation(
    col_names: list[str],
    fanova_importances: dict[str, float],
    shap_importances: dict[str, float],
) -> float:
    """Compute Spearman correlation between fANOVA and SHAP importance vectors."""
    fanova_vals = [fanova_importances[col] for col in col_names]
    shap_vals = [shap_importances[col] for col in col_names]
    correlation, _ = spearmanr(fanova_vals, shap_vals)
    if np.isnan(correlation):
        return 0.0
    return float(correlation)


def _run_importance_pass(
    X: np.ndarray,
    y: np.ndarray,
    col_names: list[str],
    config_space: ConfigurationSpace,
    n_estimators: int,
    max_depth: Optional[int],
    random_state: int,
) -> _ImportancePassResult:
    """Run one full SHAP + fANOVA decomposition pass."""
    rf = RandomForestRegressor(
        n_estimators=n_estimators,
        max_depth=max_depth,
        random_state=random_state,
    )
    rf.fit(X, y)
    pass_r2 = float(rf.score(X, y))

    explainer = shap.TreeExplainer(rf)
    shap_values = explainer.shap_values(X)
    mean_abs_shap = np.mean(np.abs(shap_values), axis=0)

    if len(col_names) != len(mean_abs_shap):
        raise ValueError(
            "SHAP vector length does not match feature count: "
            f"{len(mean_abs_shap)} vs {len(col_names)}"
        )

    shap_importances = {
        col: float(mean_abs_shap[idx]) for idx, col in enumerate(col_names)
    }
    shap_importances = dict(
        sorted(shap_importances.items(), key=lambda item: item[1], reverse=True)
    )

    fanova_max_depth = max_depth if max_depth is not None else FANOVA_FALLBACK_MAX_DEPTH
    fanova_model = fANOVA(
        X=X,
        Y=y,
        config_space=config_space,
        n_trees=n_estimators,
        seed=random_state,
        max_depth=fanova_max_depth,
    )

    marginal_importances: dict[str, float] = {}
    for i, col in enumerate(col_names):
        res = fanova_model.quantify_importance((i,))
        marginal_importances[col] = float(res[(i,)]["individual importance"])

    marginal_importances = dict(
        sorted(marginal_importances.items(), key=lambda item: item[1], reverse=True)
    )
    correlation = _compute_rank_correlation(
        col_names=col_names,
        fanova_importances=marginal_importances,
        shap_importances=shap_importances,
    )

    return _ImportancePassResult(
        marginal_importances=marginal_importances,
        shap_importances=shap_importances,
        shap_values=shap_values,
        fanova_shap_correlation=correlation,
        model_r2=pass_r2,
        fanova_model=fanova_model,
        column_names=col_names,
        n_estimators=n_estimators,
        max_depth=max_depth,
    )


def analyze_knob_importance(
    loaded_data: LoadedData,
    n_estimators: int = 100,
    max_depth: Optional[int] = 5,
    random_state: int = 42,
    top_k: int = 20,
    interaction_order: int = 2,
) -> ImportanceResult:
    """
    Train a Random Forest and perform fANOVA decomposition to measure knob importance.

    Parameters
    ----------
    loaded_data : LoadedData
        Data loaded from PBT session containing scores and configuration constraints.
    n_estimators : int, optional
        Number of trees in the Random Forest, by default 100.
    max_depth : int, optional
        Maximum tree depth, by default 5.
    random_state : int, optional
        Random seed for reproducibility, by default 42.
    top_k : int, optional
        Number of top features strictly evaluated for pairwise interactions, by default 20.
    interaction_order : int, optional
        Maximum order of fANOVA interaction calculated, by default 2. Note: Order 3+ is computationally expensive.

    Returns
    -------
    ImportanceResult
        Decomposition metrics mapping.
    """
    df = _drop_zero_variance_columns(loaded_data.config_df.copy())
    scores = loaded_data.scores

    n_samples = len(df)
    if n_samples < 30:
        raise InsufficientDataError(
            f"Insufficient data for importance analysis. Need at least 30 observations, but have {n_samples}. "
            f"Please run {(30 - n_samples)} more runs."
        )

    n_features = len(df.columns)
    if n_features == 0:
        raise ValueError("No features remaining after dropping zero variance columns.")

    config_space = _build_config_space(df=df, knob_bounds=loaded_data.knob_bounds)
    X = df.to_numpy()
    y = scores.to_numpy()
    col_names = df.columns.tolist()

    primary_pass = _run_importance_pass(
        X=X,
        y=y,
        col_names=col_names,
        config_space=config_space,
        n_estimators=n_estimators,
        max_depth=max_depth,
        random_state=random_state,
    )
    selected_pass = primary_pass

    if primary_pass.fanova_shap_correlation < CORRELATION_THRESHOLD:
        retry_n_estimators = max(RETRY_MIN_ESTIMATORS, n_estimators * 2)
        retry_max_depth = (
            RETRY_MIN_DEPTH if max_depth is None else max(RETRY_MIN_DEPTH, max_depth)
        )

        if retry_n_estimators != n_estimators or retry_max_depth != max_depth:
            logger.info(
                "Low fANOVA-SHAP correlation detected (ρ=%.3f). "
                "Retrying with n_estimators=%d, max_depth=%s.",
                primary_pass.fanova_shap_correlation,
                retry_n_estimators,
                str(retry_max_depth),
            )
            retry_pass = _run_importance_pass(
                X=X,
                y=y,
                col_names=col_names,
                config_space=config_space,
                n_estimators=retry_n_estimators,
                max_depth=retry_max_depth,
                random_state=random_state,
            )
            if (
                retry_pass.fanova_shap_correlation
                > primary_pass.fanova_shap_correlation
            ):
                selected_pass = retry_pass
                logger.info(
                    "Improved fANOVA-SHAP correlation from ρ=%.3f to ρ=%.3f.",
                    primary_pass.fanova_shap_correlation,
                    retry_pass.fanova_shap_correlation,
                )
            else:
                logger.info(
                    "Retry did not improve fANOVA-SHAP correlation (ρ=%.3f -> ρ=%.3f). "
                    "Keeping primary pass.",
                    primary_pass.fanova_shap_correlation,
                    retry_pass.fanova_shap_correlation,
                )

    if selected_pass.model_r2 < 0.5:
        logger.warning(
            "model may not be capturing the response surface well - "
            "importance results should be interpreted with caution. R² = %.3f",
            selected_pass.model_r2,
        )

    if selected_pass.fanova_shap_correlation < CORRELATION_THRESHOLD:
        logger.warning(
            "Low correlation between fANOVA and SHAP importance rankings: ρ = %.3f",
            selected_pass.fanova_shap_correlation,
        )

    pairwise_interactions: dict[tuple[str, str], float] = {}

    # 6. Pairwise Interactions
    if interaction_order >= 2:
        top_k_features = list(selected_pass.marginal_importances.keys())[:top_k]
        top_k_indices = [
            selected_pass.column_names.index(feat) for feat in top_k_features
        ]

        for i in range(len(top_k_indices)):
            for j in range(i + 1, len(top_k_indices)):
                idx1 = top_k_indices[i]
                idx2 = top_k_indices[j]

                res = selected_pass.fanova_model.quantify_importance((idx1, idx2))
                val = res[(idx1, idx2)]["individual importance"]

                feat1 = selected_pass.column_names[idx1]
                feat2 = selected_pass.column_names[idx2]
                pairwise_interactions[(feat1, feat2)] = float(val)

        # Sort descending
        pairwise_interactions = dict(
            sorted(
                pairwise_interactions.items(), key=lambda item: item[1], reverse=True
            )
        )

    workload_type = (
        loaded_data.metadata[0].get("workload_type", "unknown")
        if loaded_data.metadata
        else "unknown"
    )
    scoring_policy = (
        loaded_data.metadata[0].get("scoring_policy", "fixed_v1")
        if loaded_data.metadata
        else "fixed_v1"
    )
    scoring_policy_version = (
        loaded_data.metadata[0].get("scoring_policy_version", "1.0")
        if loaded_data.metadata
        else "1.0"
    )
    metric_reference_version = (
        loaded_data.metadata[0].get("metric_reference_version", "v1")
        if loaded_data.metadata
        else "v1"
    )

    return ImportanceResult(
        marginal_importances=selected_pass.marginal_importances,
        pairwise_interactions=pairwise_interactions,
        model_r2=selected_pass.model_r2,
        n_samples=n_samples,
        n_features=n_features,
        workload_type=workload_type,
        shap_importances=selected_pass.shap_importances,
        shap_values=selected_pass.shap_values,
        fanova_shap_correlation=selected_pass.fanova_shap_correlation,
        scoring_policy=scoring_policy,
        scoring_policy_version=scoring_policy_version,
        metric_reference_version=metric_reference_version,
    )
