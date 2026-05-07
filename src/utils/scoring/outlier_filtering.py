"""IQR-based outlier filtering for normalization calibration."""

from typing import Tuple, Dict, Any
import numpy as np


def iqr_filter(
    values: np.ndarray,
    k: float = 2.5,
) -> Tuple[np.ndarray, Dict[str, Any]]:
    """
    Filter outliers using Interquartile Range (IQR) method.

    Removes values outside [Q1 - k*IQR, Q3 + k*IQR]. Uses k=2.5 as a
    compromise between classic 1.5× (too aggressive for noisy DB metrics)
    and 3.0× (too lenient).

    Parameters
    ----------
    values : np.ndarray
        Array of numeric values to filter.
    k : float
        IQR multiplier (default 2.5).

    Returns
    -------
    Tuple[np.ndarray, Dict[str, Any]]
        (filtered_array, metadata_dict) where metadata includes:
        - n_removed: number of outliers removed
        - original_size: size before filtering
        - lower_bound: lower outlier threshold
        - upper_bound: upper outlier threshold
        - fallback_used: whether fallback (unfiltered) was used
    """
    if len(values) < 4:
        return values, {
            "n_removed": 0,
            "original_size": len(values),
            "lower_bound": None,
            "upper_bound": None,
            "fallback_used": True,
            "reason": "too few values for IQR computation",
        }

    q1 = float(np.percentile(values, 25))
    q3 = float(np.percentile(values, 75))
    iqr = q3 - q1

    if iqr == 0:
        return values, {
            "n_removed": 0,
            "original_size": len(values),
            "lower_bound": q1,
            "upper_bound": q3,
            "fallback_used": True,
            "reason": "IQR is zero (all values identical)",
        }

    lower_bound = q1 - k * iqr
    upper_bound = q3 + k * iqr

    mask = (values >= lower_bound) & (values <= upper_bound)
    filtered = values[mask]

    n_removed = len(values) - len(filtered)

    if len(filtered) < 3:
        return values, {
            "n_removed": 0,
            "original_size": len(values),
            "lower_bound": lower_bound,
            "upper_bound": upper_bound,
            "fallback_used": True,
            "reason": f"filtering would remove {n_removed}/{len(values)} values (< 3 remain)",
        }

    return filtered, {
        "n_removed": n_removed,
        "original_size": len(values),
        "lower_bound": lower_bound,
        "upper_bound": upper_bound,
        "fallback_used": False,
    }
