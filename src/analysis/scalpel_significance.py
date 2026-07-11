"""
SCALPEL: Layer 1 — significance gate.

Implements a BORUTA-style all-relevant feature selector with
group-aware shadow permutation and Benjamini–Hochberg FDR control.

Key deviations from the canonical Boruta R package recommended in the
SCALPEL design (see docs/architecture or /home/eima40x4c/.claude/plans/distributed-toasting-sparrow.md):

* Shadow features are permuted **within** ``sample_groups`` clusters
  (the ``session_index × generation_index`` tuple from PBT) so the null
  respects the non-i.i.d. structure of population-based-training samples.
* The per-knob hit-count test is corrected via Benjamini–Hochberg
  across all knobs (q=0.10 by default), not Bonferroni per iteration.
* The outer Random Forest is fit ONCE on the full cleaned feature set;
  hit counts are computed against per-iteration shadow draws but the
  RF parameters/seed are held constant to keep the comparison clean.
"""

from __future__ import annotations

from dataclasses import dataclass

import numpy as np
import pandas as pd
from scipy import stats
from sklearn.ensemble import RandomForestRegressor

from src.utils.logger import get_logger

LOGGER = get_logger("SCALPEL.significance")


@dataclass
class BorutaResult:
    """
    Container for a group-permutation BORUTA pass.

    Attributes
    ----------
    confirmed : list[str]
        Knobs whose hit count is significantly above the binomial null
        after BH-FDR adjustment.
    tentative : list[str]
        Knobs that did not reach the confirmation threshold but were
        not rejected (hit fraction is in the inconclusive band).
    rejected : list[str]
        Knobs whose hit count is significantly below chance.
    hit_counts : dict[str, int]
        Raw number of iterations each knob beat the shadow ceiling.
    p_values : dict[str, float]
        Two-sided binomial p-values per knob (un-adjusted).
    p_values_bh : dict[str, float]
        Benjamini–Hochberg-adjusted p-values per knob.
    n_iterations : int
        Number of completed iterations.
    """

    confirmed: list[str]
    tentative: list[str]
    rejected: list[str]
    hit_counts: dict[str, int]
    p_values: dict[str, float]
    p_values_bh: dict[str, float]
    n_iterations: int


def _bh_adjust(p_values: np.ndarray) -> np.ndarray:
    """Benjamini–Hochberg p-value adjustment.

    Returns an array of adjusted p-values matching the input order.
    """
    if p_values.size == 0:
        return p_values.copy()

    n = p_values.size
    order = np.argsort(p_values)
    ranked = p_values[order]
    adjusted = ranked * n / (np.arange(n) + 1)
    # Enforce monotonicity from the right (canonical BH procedure)
    adjusted = np.minimum.accumulate(adjusted[::-1])[::-1]
    adjusted = np.clip(adjusted, 0.0, 1.0)

    out = np.empty_like(adjusted)
    out[order] = adjusted
    return out


def _permute_within_groups(
    column: np.ndarray,
    group_codes: np.ndarray,
    rng: np.random.Generator,
) -> np.ndarray:
    """Permute a column independently within each group.

    Parameters
    ----------
    column : np.ndarray
        Raw column values (1D).
    group_codes : np.ndarray
        Integer-coded group ids of the same length as ``column``.
    rng : np.random.Generator
        Source of randomness.

    Returns
    -------
    np.ndarray
        Within-group shuffled copy of ``column``.

    Notes
    -----
    Singleton groups (size 1) cannot be permuted internally and contribute
    a row whose value is unchanged. The caller is responsible for warning
    about coverage; ``boruta_with_group_perm`` falls back to a global
    shuffle when ALL clusters are singletons (an i.i.d. degenerate case).
    """
    permuted = column.copy()
    # Build per-group index lists once
    unique, inverse = np.unique(group_codes, return_inverse=True)
    for gid in range(unique.size):
        idx = np.flatnonzero(inverse == gid)
        if idx.size <= 1:
            continue
        rng.shuffle(idx)
        # Reassign permuted slot positions
        permuted[np.flatnonzero(inverse == gid)] = column[idx]
    return permuted


def _build_shadow_matrix(
    X: np.ndarray,
    group_codes: np.ndarray,
    rng: np.random.Generator,
    fall_back_to_iid: bool,
) -> np.ndarray:
    """Construct an in-group shadow matrix matching ``X``'s shape."""
    if fall_back_to_iid:
        # All-singleton clusters: use plain global shuffles per column.
        shadow = X.copy()
        for col_idx in range(shadow.shape[1]):
            rng.shuffle(shadow[:, col_idx])
        return shadow

    shadow = np.empty_like(X)
    for col_idx in range(X.shape[1]):
        shadow[:, col_idx] = _permute_within_groups(
            X[:, col_idx], group_codes, rng
        )
    return shadow


def _encode_groups(sample_groups: pd.Series) -> tuple[np.ndarray, bool]:
    """Encode ``sample_groups`` as int codes, return (codes, all_singletons)."""
    if sample_groups is None or len(sample_groups) == 0:
        return np.zeros(0, dtype=np.int64), True

    codes = pd.Categorical(sample_groups.astype(str)).codes.astype(np.int64)
    counts = np.bincount(codes)
    all_singletons = bool(np.all(counts <= 1))
    return codes, all_singletons


def _fit_outer_rf(
    X: pd.DataFrame,
    y: pd.Series,
    *,
    n_estimators: int,
    max_features: float | int | str | None,
    min_samples_leaf: int,
    random_state: int,
) -> tuple[RandomForestRegressor, float]:
    """Fit the SCALPEL outer Random Forest surrogate.

    Returns the fitted model and its OOB R² (NaN if OOB unavailable, e.g.
    bootstrap=False or too few samples).
    """
    rf = RandomForestRegressor(
        n_estimators=n_estimators,
        max_features=max_features,
        min_samples_leaf=min_samples_leaf,
        bootstrap=True,
        oob_score=True,
        random_state=random_state,
        n_jobs=-1,
    )
    rf.fit(X.to_numpy(), y.to_numpy())
    oob_r2 = float(getattr(rf, "oob_score_", float("nan")))
    return rf, oob_r2


def partition_boruta_hits(
    hit_counts: np.ndarray,
    knobs: list[str],
    n_iterations: int,
    *,
    fdr_q: float,
) -> BorutaResult:
    """Partition pre-computed BORUTA hit counts at a given FDR target.

    Pure function: takes already-computed hit counts and returns a
    confirmed/tentative/rejected verdict at ``fdr_q``. Used by the primary
    BORUTA pass and by the q-sensitivity sweep, which reuses the same
    hit counts at multiple thresholds without re-fitting RFs.
    """
    if len(knobs) == 0 or n_iterations == 0:
        return BorutaResult(
            confirmed=[],
            tentative=[],
            rejected=[],
            hit_counts={k: int(hit_counts[i]) for i, k in enumerate(knobs)},
            p_values={k: 1.0 for k in knobs},
            p_values_bh={k: 1.0 for k in knobs},
            n_iterations=n_iterations,
        )

    p_values = np.empty(len(knobs))
    for i, hits in enumerate(hit_counts):
        p_values[i] = float(
            stats.binomtest(int(hits), n=n_iterations, p=0.5, alternative="two-sided").pvalue
        )
    p_values_bh = _bh_adjust(p_values)
    half = n_iterations / 2.0

    confirmed: list[str] = []
    tentative: list[str] = []
    rejected: list[str] = []
    for i, knob in enumerate(knobs):
        if p_values_bh[i] <= fdr_q:
            if hit_counts[i] > half:
                confirmed.append(knob)
            else:
                rejected.append(knob)
        else:
            tentative.append(knob)

    return BorutaResult(
        confirmed=confirmed,
        tentative=tentative,
        rejected=rejected,
        hit_counts={k: int(hit_counts[i]) for i, k in enumerate(knobs)},
        p_values={k: float(p_values[i]) for i, k in enumerate(knobs)},
        p_values_bh={k: float(p_values_bh[i]) for i, k in enumerate(knobs)},
        n_iterations=n_iterations,
    )


def boruta_with_group_perm(
    X: pd.DataFrame,
    y: pd.Series,
    sample_groups: pd.Series,
    *,
    n_iterations: int = 100,
    n_estimators: int = 500,
    max_features: float | int | str | None = "sqrt",
    min_samples_leaf: int = 3,
    fdr_q: float = 0.10,
    random_state: int = 42,
) -> BorutaResult:
    """Run a group-permutation BORUTA significance gate.

    Parameters
    ----------
    X : pd.DataFrame
        Encoded knob configurations (alphabetized columns recommended).
    y : pd.Series
        Globally rescored objective values.
    sample_groups : pd.Series
        Composite ``session_index:generation_index`` cluster id per row;
        used to permute shadow features within each cluster so the null
        respects PBT's non-i.i.d. structure.
    n_iterations : int, default 100
        Number of shadow regenerations (R in Kursa & Rudnicki 2010).
    n_estimators : int, default 500
        Trees in the importance RF used for hit detection.
    max_features : default "sqrt"
        ``RandomForestRegressor.max_features`` for the importance RF.
    min_samples_leaf : int, default 3
        ``RandomForestRegressor.min_samples_leaf`` for the importance RF.
    fdr_q : float, default 0.10
        BH-FDR target for confirmed/rejected decisions.
    random_state : int, default 42
        Base seed; per-iteration RFs reseed deterministically from this.

    Returns
    -------
    BorutaResult
        Aggregated significance verdicts.

    Notes
    -----
    Each iteration trains a fresh RF on the [X | shadow(X)] augmented
    matrix and counts a "hit" for every real-feature whose importance
    strictly exceeds ``max(shadow_importance)``. Two-sided binomial
    tests against ``p=0.5`` are then aggregated across knobs and
    BH-adjusted at level ``fdr_q`` (across the p knobs, not iterations).
    """
    if X.empty:
        return BorutaResult(
            confirmed=[],
            tentative=[],
            rejected=[],
            hit_counts={},
            p_values={},
            p_values_bh={},
            n_iterations=0,
        )

    knobs = list(X.columns)
    X_arr = X.to_numpy()
    y_arr = y.to_numpy()
    group_codes, all_singleton = _encode_groups(sample_groups)
    if all_singleton and len(group_codes) > 0:
        LOGGER.warning(
            "BORUTA: every cluster has a single observation; "
            "falling back to i.i.d. shuffles. The resulting null may be "
            "anti-conservative for PBT samples."
        )

    base_rng = np.random.default_rng(random_state)
    hit_counts = np.zeros(len(knobs), dtype=np.int64)

    for _it in range(n_iterations):
        rng = np.random.default_rng(base_rng.integers(0, 2**63 - 1))
        shadow = _build_shadow_matrix(X_arr, group_codes, rng, all_singleton)
        augmented = np.hstack([X_arr, shadow])

        rf = RandomForestRegressor(
            n_estimators=n_estimators,
            max_features=max_features,
            min_samples_leaf=min_samples_leaf,
            bootstrap=True,
            random_state=int(rng.integers(0, 2**31 - 1)),
            n_jobs=-1,
        )
        rf.fit(augmented, y_arr)
        importances = rf.feature_importances_
        real_imp = importances[: len(knobs)]
        shadow_imp = importances[len(knobs):]
        shadow_max = float(shadow_imp.max()) if shadow_imp.size else 0.0

        hit_counts += (real_imp > shadow_max).astype(np.int64)

    result = partition_boruta_hits(
        hit_counts, knobs, n_iterations, fdr_q=fdr_q
    )

    LOGGER.info(
        "BORUTA: confirmed=%d tentative=%d rejected=%d (iter=%d, fdr_q=%.2f)",
        len(result.confirmed),
        len(result.tentative),
        len(result.rejected),
        n_iterations,
        fdr_q,
    )

    return result
