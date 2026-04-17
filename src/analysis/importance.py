"""
Knob Importance Analysis
========================

Computes marginal and pairwise importance of database knobs using fANOVA variance decomposition.
"""

import logging
from typing import Dict, Tuple, Optional
from dataclasses import dataclass

import pandas as pd
from ConfigSpace import ConfigurationSpace
from ConfigSpace.hyperparameters import UniformFloatHyperparameter, UniformIntegerHyperparameter
import numpy as np
# Monkey-patch np.float for fanova backwards compatibility
if not hasattr(np, 'float'):
    np.float = float
if not hasattr(np, 'int'):
    np.int = int
if not hasattr(np, 'bool'):
    np.bool = bool

from sklearn.ensemble import RandomForestRegressor
from fanova import fANOVA

from src.analysis.data_loader import LoadedData

logger = logging.getLogger(__name__)

class InsufficientDataError(Exception):
    """Raised when there are fewer than 30 samples available for importance analysis."""
    pass

@dataclass
class ImportanceResult:
    """
    Container for fANOVA importance variance decomposition results.
    """
    marginal_importances: Dict[str, float]
    pairwise_interactions: Dict[Tuple[str, str], float]
    model_r2: float
    n_samples: int
    n_features: int
    workload_type: str


def analyze_knob_importance(
    loaded_data: LoadedData,
    n_estimators: int = 64,
    max_depth: Optional[int] = None,
    random_state: int = 42,
    top_k: int = 20,
    interaction_order: int = 2
) -> ImportanceResult:
    """
    Train a Random Forest and perform fANOVA decomposition to measure knob importance.
    
    Parameters
    ----------
    loaded_data : LoadedData
        Data loaded from PBT session containing scores and configuration constraints.
    n_estimators : int, optional
        Number of trees in the Random Forest, by default 64.
    max_depth : int, optional
        Maximum tree depth, by default None.
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
    df = loaded_data.config_df.copy()
    scores = loaded_data.scores
    
    n_samples = len(df)
    if n_samples < 30:
        raise InsufficientDataError(
            f"Insufficient data for importance analysis. Need at least 30 observations, but have {n_samples}. "
            f"Please run {(30 - n_samples)} more runs."
        )

    # 1. Handle edge cases (Zero variance)
    nunique = df.nunique()
    zero_var_cols = nunique[nunique <= 1].index.tolist()
    if zero_var_cols:
        logger.warning(f"Dropping zero-variance knobs before importance analysis: {zero_var_cols}")
        df = df.drop(columns=zero_var_cols)
        
    n_features = len(df.columns)
    if n_features == 0:
        raise ValueError("No features remaining after dropping zero variance columns.")

    # 2. Build ConfigSpace from boundaries, ignoring actual min/max inside DataFrame bounds
    cs = ConfigurationSpace()
    bounds = loaded_data.knob_bounds
    
    for col in df.columns:
        b_min, b_max = bounds[col]

        # Map to ConfigSpace using pure bounds parameter mapping, without squashing to empirical local data ranges
        if df[col].dtype.kind in 'biu' or pd.api.types.is_integer_dtype(df[col]):
            cs.add_hyperparameter(UniformIntegerHyperparameter(col, int(b_min), int(b_max)))
        else:
            cs.add_hyperparameter(UniformFloatHyperparameter(col, float(b_min), float(b_max)))

    X = df.values
    y = scores.values
    
    # 3. Train sklearn Random Forest Model for R2 sanity check
    rf = RandomForestRegressor(
        n_estimators=n_estimators, 
        max_depth=max_depth, 
        random_state=random_state
    )
    rf.fit(X, y)
    
    r2 = rf.score(X, y)
    if r2 < 0.5:
        logger.warning(
            f"model may not be capturing the response surface well - importance results should be interpreted with caution. R² = {r2:.3f}"
        )

    # 4. fANOVA Decomposition wrapper
    
    marginal_importances = {}
    pairwise_interactions = {}
    
    # Let fANOVA build its own native pyrfr internal random forest
    fANOVA_max_depth = max_depth if max_depth is not None else 64
    f = fANOVA(X=X, Y=y, config_space=cs, n_trees=n_estimators, seed=random_state, max_depth=fANOVA_max_depth)
    
    # 5. Calculate Marginals
    col_names = df.columns.tolist()
    
    for i, col in enumerate(col_names):
        res = f.quantify_importance((i,))
        val = res[(i,)]['individual importance']
        marginal_importances[col] = float(val)

    # Sort descending
    marginal_importances = dict(sorted(marginal_importances.items(), key=lambda item: item[1], reverse=True))
    
    # 6. Pairwise Interactions
    if interaction_order >= 2:
        top_k_features = list(marginal_importances.keys())[:top_k]
        top_k_indices = [col_names.index(feat) for feat in top_k_features]
        
        for i in range(len(top_k_indices)):
            for j in range(i + 1, len(top_k_indices)):
                idx1 = top_k_indices[i]
                idx2 = top_k_indices[j]
                
                res = f.quantify_importance((idx1, idx2))
                val = res[(idx1, idx2)]['individual importance']
                
                feat1 = col_names[idx1]
                feat2 = col_names[idx2]
                pairwise_interactions[(feat1, feat2)] = float(val)
                
        # Sort descending
        pairwise_interactions = dict(sorted(pairwise_interactions.items(), key=lambda item: item[1], reverse=True))
                
    workload_type = loaded_data.metadata[0].get('workload_type', 'unknown') if loaded_data.metadata else 'unknown'

    return ImportanceResult(
        marginal_importances=marginal_importances,
        pairwise_interactions=pairwise_interactions,
        model_r2=float(r2),
        n_samples=n_samples,
        n_features=n_features,
        workload_type=workload_type
    )
