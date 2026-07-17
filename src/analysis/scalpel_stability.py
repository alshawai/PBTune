"""
SCALPEL: Layer 2 (coverage) and Layer 3 (stability) helpers.

Provides the building blocks the orchestrator stitches together:

* :func:`apply_nuisance_filter` — drops display/observability/auth knobs
  before any importance modeling so they cannot land in the data-driven
  ``minimal``/``core`` tiers.
* :func:`compute_fanova_marginals` — runs fANOVA on the outer Random
  Forest surrogate over the FULL clean feature set (Lorenz cuts later
  renormalize within the BORUTA-confirmed subset, eliminating the v0
  circularity blocker).
* :func:`assign_lorenz_tiers` — sorts confirmed knobs by ``(importance
  desc, knob_name asc)`` and walks cumulative mass to assign canonical
  ``minimal``/``core``/``standard`` tiers at 50% / 80% cuts.
* :func:`group_clustered_stability` — repeats Layers 1+2 on cluster
  subsamples (NOT row subsamples) and reports per-knob tier-assignment
  selection probability.
* :func:`audit_dba_prior` — flags expert-minimal knobs that did not land
  in data-driven ``minimal`` (report-only).
"""

from __future__ import annotations

import os
import warnings
from concurrent.futures import ProcessPoolExecutor, as_completed
from dataclasses import dataclass
from typing import Any, Callable, Iterable, Mapping, Optional

import numpy as np
import pandas as pd
from ConfigSpace import ConfigurationSpace
from ConfigSpace.hyperparameters import (
    UniformFloatHyperparameter,
    UniformIntegerHyperparameter,
)

from src.utils.logger import get_logger

LOGGER = get_logger("SCALPEL.stability")


# ---------------------------------------------------------------------------
# Nuisance filter
# ---------------------------------------------------------------------------

@dataclass
class NuisanceFilterResult:
    """Container for :func:`apply_nuisance_filter` output."""

    filtered: pd.DataFrame
    dropped: list[str]
    reasons: dict[str, str]


def apply_nuisance_filter(
    X: pd.DataFrame,
    *,
    exclusions: Mapping[str, tuple[str, str]],
    prefixes: Mapping[str, tuple[str, str]],
    overrides: Optional[Iterable[str]] = None,
) -> NuisanceFilterResult:
    """Drop display/auth/observability knobs before any importance modeling.

    Parameters
    ----------
    X : pd.DataFrame
        Configuration matrix.
    exclusions : Mapping[str, (str, str)]
        Exact knob-name → (reason_code, message) pairs from
        ``IMPORTANCE_NUISANCE_EXCLUSIONS``.
    prefixes : Mapping[str, (str, str)]
        Knob-name prefix → (reason_code, message) pairs from
        ``IMPORTANCE_NUISANCE_PREFIXES``.
    overrides : Iterable[str], optional
        Knobs that should NOT be dropped even when they match the
        exclusion or prefix lists (operator escape hatch).

    Returns
    -------
    NuisanceFilterResult
        Filtered DataFrame plus the dropped names and the reason for each.
    """
    override_set = set(overrides or [])
    dropped: list[str] = []
    reasons: dict[str, str] = {}

    for col in X.columns:
        if col in override_set:
            continue
        if col in exclusions:
            dropped.append(col)
            reasons[col] = exclusions[col][0]
            continue
        for prefix, (code, _msg) in prefixes.items():
            if col.startswith(prefix):
                dropped.append(col)
                reasons[col] = code
                break

    if dropped:
        LOGGER.info(
            "Nuisance filter dropped %d knobs (%s)",
            len(dropped),
            ", ".join(sorted({c for c in reasons.values()})),
        )

    filtered = X.drop(columns=dropped) if dropped else X.copy()
    return NuisanceFilterResult(
        filtered=filtered,
        dropped=sorted(dropped),
        reasons=reasons,
    )


# ---------------------------------------------------------------------------
# fANOVA marginals on the outer RF (no surrogate refit)
# ---------------------------------------------------------------------------

def _build_fanova_config_space(
    X: pd.DataFrame,
    knob_bounds: Optional[Mapping[str, tuple[float, float]]],
) -> ConfigurationSpace:
    """Translate the cleaned feature set into a fANOVA ConfigSpace.

    fANOVA enforces ``X[i, c] in [lower, upper]`` strictly, including
    the column-max equality. We widen by a small epsilon on both sides
    so samples that sit exactly on the observed min/max do not trip
    fANOVA's bound check after its internal float rounding.
    """
    epsilon = 1e-9
    config_space = ConfigurationSpace()
    for col in X.columns:
        if knob_bounds and col in knob_bounds:
            b_min, b_max = knob_bounds[col]
        else:
            b_min, b_max = (
                float(X[col].min()) if not X.empty else 0.0,
                float(X[col].max()) if not X.empty else 1.0,
            )
        if not X.empty:
            # Ensure observed samples are strictly inside the configured range.
            b_min = min(b_min, float(X[col].min())) - epsilon
            b_max = max(b_max, float(X[col].max())) + epsilon
        if b_min >= b_max:
            b_max = b_min + 1.0
        if pd.api.types.is_integer_dtype(X[col]):
            config_space.add_hyperparameter(
                UniformIntegerHyperparameter(col, int(b_min), int(b_max))
            )
        else:
            config_space.add_hyperparameter(
                UniformFloatHyperparameter(col, float(b_min), float(b_max))
            )
    return config_space


@dataclass
class FanovaImportance:
    """fANOVA pass output: marginals + pairwise interactions.

    Attributes
    ----------
    marginals : dict[str, float]
        Per-knob individual (marginal) importance, identical to the
        legacy ``compute_fanova_marginals`` payload.
    max_interactions : dict[str, float]
        Per-knob ``max_j fanova((i, j)).individual_importance`` over the
        top-K marginal knobs. Knobs outside the top-K (or knobs in the
        top-K whose best interaction is below 0) get 0.0.
    top_k_marginals : list[str]
        Knobs by descending marginal importance, the search frontier
        used to compute pairwise interactions.
    """

    marginals: dict[str, float]
    max_interactions: dict[str, float]
    top_k_marginals: list[str]


def compute_fanova_marginals(
    X: pd.DataFrame,
    y: pd.Series,
    *,
    knob_bounds: Optional[Mapping[str, tuple[float, float]]] = None,
    n_estimators: int = 500,
    min_samples_split: int = 5,
    min_samples_leaf: int = 3,
    max_features: float | int | str | None = "sqrt",
    bootstrap: bool = True,
    random_state: int = 42,
) -> dict[str, float]:
    """Backward-compatible thin wrapper returning only marginal importances.

    Used by the stability-layer closure and by callers that do not need
    pairwise interactions. New code should call
    :func:`compute_fanova_importance` directly to access the richer
    payload (max interactions, top-K marginals).
    """
    result = compute_fanova_importance(
        X,
        y,
        knob_bounds=knob_bounds,
        n_estimators=n_estimators,
        min_samples_split=min_samples_split,
        min_samples_leaf=min_samples_leaf,
        max_features=max_features,
        bootstrap=bootstrap,
        random_state=random_state,
        interaction_top_k=0,  # skip interaction work in the wrapper
    )
    return dict(result.marginals)


def compute_fanova_importance(
    X: pd.DataFrame,
    y: pd.Series,
    *,
    knob_bounds: Optional[Mapping[str, tuple[float, float]]] = None,
    n_estimators: int = 500,
    min_samples_split: int = 5,
    min_samples_leaf: int = 3,
    max_features: float | int | str | None = "sqrt",
    bootstrap: bool = True,
    random_state: int = 42,
    interaction_top_k: int = 20,
) -> FanovaImportance:
    """Run an fANOVA pass and return marginals + pairwise interactions.

    Parameters
    ----------
    interaction_top_k : int, default 20
        Cap on the search frontier for pairwise interactions. The full
        ``O(p^2)`` interaction matrix is prohibitively expensive on
        ~180-knob inputs; we restrict to the top-K marginals because
        the fused signal ``marginal + alpha * max_interaction`` only
        helps knobs whose marginal is competitive. Set to 0 to skip
        interaction computation entirely (used by the
        :func:`compute_fanova_marginals` shim).

    Notes
    -----
    fANOVA's ``fANOVA`` constructor builds its own internal random
    forest. The SCALPEL outer RF (used by BORUTA) is NOT reused here —
    fANOVA insists on its own surrogate. We seed both with the same
    ``random_state`` so the comparison is reproducible. The Lorenz
    cuts in :func:`assign_lorenz_tiers` operate on these marginals
    over the full cleaned feature set, then renormalize within the
    BORUTA-confirmed subset to avoid the v0 circularity blocker.
    """
    if X.empty:
        return FanovaImportance(marginals={}, max_interactions={}, top_k_marginals=[])

    # Local import: fANOVA / pyrfr wheels are heavy and the import time
    # has historically caused test-collection slowdowns.
    from fanova import fANOVA  # type: ignore[import-not-found]

    fanova_max_features: int | None
    if isinstance(max_features, float) and 0.0 < max_features <= 1.0:
        fanova_max_features = max(1, int(max_features * X.shape[1]))
    elif isinstance(max_features, int):
        fanova_max_features = int(max_features)
    elif max_features == "sqrt":
        fanova_max_features = max(1, int(np.sqrt(X.shape[1])))
    elif max_features == "log2":
        fanova_max_features = max(1, int(np.log2(X.shape[1])))
    else:
        fanova_max_features = None

    config_space = _build_fanova_config_space(X, knob_bounds)
    # SCALPEL alphabetizes columns before this point; the ConfigSpace built
    # above iterates ``X.columns`` in the same order, so fANOVA's column
    # order matches ``config_space.get_hyperparameter_names()`` by
    # construction. The library emits the "data ordering" warning
    # unconditionally when the caller passes a NumPy array (no labels), so
    # we suppress the noise rather than fight the heuristic.
    with warnings.catch_warnings():
        warnings.filterwarnings(
            "ignore",
            message=r".*expects data to be ordered.*",
            module=r"fanova\.fanova",
        )
        fanova_model = fANOVA(
            X=X.to_numpy(),
            Y=y.to_numpy(),
            config_space=config_space,
            n_trees=n_estimators,
            seed=random_state,
            min_samples_split=min_samples_split,
            min_samples_leaf=min_samples_leaf,
            max_features=fanova_max_features,
            bootstrapping=bootstrap,
        )

        importances: dict[str, float] = {}
        for idx, col in enumerate(X.columns):
            res = fanova_model.quantify_importance((idx,))
            importances[col] = float(res[(idx,)]["individual importance"])

        max_interactions: dict[str, float] = {col: 0.0 for col in X.columns}
        top_k_marginals: list[str] = []
        if interaction_top_k > 0:
            ranked = sorted(
                importances.items(), key=lambda item: (-item[1], item[0])
            )
            top_cols = [col for col, _ in ranked[:interaction_top_k]]
            top_k_marginals = list(top_cols)
            col_to_idx = {col: i for i, col in enumerate(X.columns)}
            for col_a in top_cols:
                idx_a = col_to_idx[col_a]
                best = 0.0
                for col_b in top_cols:
                    if col_a == col_b:
                        continue
                    idx_b = col_to_idx[col_b]
                    pair = (min(idx_a, idx_b), max(idx_a, idx_b))
                    try:
                        res = fanova_model.quantify_importance(pair)
                        contrib = float(res[pair]["individual importance"])
                    except Exception:
                        continue
                    if contrib > best:
                        best = contrib
                max_interactions[col_a] = best

    return FanovaImportance(
        marginals=importances,
        max_interactions=max_interactions,
        top_k_marginals=top_k_marginals,
    )


# ---------------------------------------------------------------------------
# Lorenz tier assignment
# ---------------------------------------------------------------------------

@dataclass
class LorenzTierResult:
    """Container for a Lorenz-cut tier assignment.

    Attributes
    ----------
    tier_assignments : dict[str, str]
        Confirmed knobs only, mapped to one of ``minimal``, ``core``,
        ``standard``. Non-confirmed knobs are absent (extensive=null
        in the JSON contract).
    cumulative_coverage : dict[str, float]
        Cumulative renormalized mass through and including each knob.
    breakpoints : dict[str, float]
        Mass cumulated at the boundary between consecutive tiers.
    """

    tier_assignments: dict[str, str]
    cumulative_coverage: dict[str, float]
    breakpoints: dict[str, float]


def assign_lorenz_tiers(
    importances: Mapping[str, float],
    confirmed: Iterable[str],
    *,
    coverage_minimal: float = 0.50,
    coverage_core: float = 0.80,
) -> LorenzTierResult:
    """Assign canonical tiers via Lorenz cumulative-mass thresholds.

    Knobs are sorted by ``(importance desc, knob_name asc)``; cumulative
    mass is computed over ``sum_{k in confirmed} importances[k]`` (NOT
    the full feature set) so the cut points are interpretable on the
    confirmed subset only. Cuts are inclusive: the first knob whose
    cumulative mass meets or exceeds ``coverage_minimal`` is the last
    member of ``minimal``; same for ``core``. Anything beyond the core
    cut is ``standard``.
    """
    confirmed_set = set(confirmed)
    confirmed_only = {k: float(v) for k, v in importances.items() if k in confirmed_set}
    if not confirmed_only:
        return LorenzTierResult(
            tier_assignments={},
            cumulative_coverage={},
            breakpoints={"minimal": 0.0, "core": 0.0, "standard": 0.0},
        )

    total = float(sum(max(v, 0.0) for v in confirmed_only.values()))
    if total <= 0:
        # Degenerate: assign every confirmed knob to minimal.
        return LorenzTierResult(
            tier_assignments={k: "minimal" for k in confirmed_only},
            cumulative_coverage={k: 0.0 for k in confirmed_only},
            breakpoints={"minimal": 0.0, "core": 0.0, "standard": 0.0},
        )

    sorted_knobs = sorted(
        confirmed_only.items(),
        key=lambda item: (-item[1], item[0]),
    )

    tier_assignments: dict[str, str] = {}
    cumulative: dict[str, float] = {}
    minimal_cut = float("nan")
    core_cut = float("nan")
    cum = 0.0
    minimal_done = False
    core_done = False
    for knob, value in sorted_knobs:
        cum += max(value, 0.0)
        coverage = cum / total
        cumulative[knob] = coverage
        if not minimal_done:
            tier_assignments[knob] = "minimal"
            if coverage >= coverage_minimal:
                minimal_done = True
                minimal_cut = coverage
            continue
        if not core_done:
            tier_assignments[knob] = "core"
            if coverage >= coverage_core:
                core_done = True
                core_cut = coverage
            continue
        tier_assignments[knob] = "standard"

    if not minimal_done:
        # Total coverage never reached coverage_minimal — keep everything in minimal
        minimal_cut = cum / total
    if not core_done:
        core_cut = cum / total

    return LorenzTierResult(
        tier_assignments=tier_assignments,
        cumulative_coverage=cumulative,
        breakpoints={"minimal": minimal_cut, "core": core_cut, "standard": 1.0},
    )


# ---------------------------------------------------------------------------
# Group-clustered stability
# ---------------------------------------------------------------------------

@dataclass
class StabilityResult:
    """Container for :func:`group_clustered_stability` output."""

    selection_probability: dict[str, float]
    tier_distribution: dict[str, dict[str, float]]
    n_subsamples: int
    n_successful: int


SubsampleTierFn = Callable[
    [pd.DataFrame, pd.Series, pd.Series, int],
    Optional[Mapping[str, str]],
]


def _run_one_subsample(
    payload: tuple[Any, Any, Any, list[str], int, Any],
) -> Optional[dict[str, str]]:
    """Worker entry point invoked inside each stability subsample process.

    The closure produced by :func:`scalpel._stability_tier_fn` captures
    ``SCALPELHyperparameters`` and is not picklable. To dispatch work to
    a ``ProcessPoolExecutor`` we send raw NumPy arrays + column names +
    seed + hyperparameters across the process boundary and reconstruct
    the closure inside the worker.
    """
    from src.analysis.scalpel import _stability_tier_fn  # local import — breaks cycle

    X_arr, y_arr, groups_arr, columns, seed, hp = payload
    X_sub = pd.DataFrame(X_arr, columns=columns)
    y_sub = pd.Series(y_arr)
    groups_sub = pd.Series(groups_arr)
    tier_fn = _stability_tier_fn(hp)
    try:
        result = tier_fn(X_sub, y_sub, groups_sub, seed)
    except Exception as exc:  # pragma: no cover - defensive
        LOGGER.debug("Stability subsample failed in worker: %s", exc)
        return None
    if not result:
        return None
    return dict(result)


def group_clustered_stability(
    X: pd.DataFrame,
    y: pd.Series,
    sample_groups: pd.Series,
    *,
    n_subsamples: int = 50,
    subsample_frac: float = 0.5,
    random_state: int = 42,
    tier_fn: SubsampleTierFn,
    n_jobs: int = 1,
    hp: Optional[Any] = None,
) -> StabilityResult:
    """Repeat the SCALPEL pipeline on cluster subsamples.

    Parameters
    ----------
    X, y, sample_groups : pd.DataFrame / pd.Series
        Same shape as the inputs to the orchestrator.
    n_subsamples : int, default 50
        Number of cluster subsamples (B in Meinshausen & Bühlmann).
        Halved from the v1.0 default of 100 to bring SCALPEL wall-clock
        from ~3 h to ~20 min on the 2000-sample dataset; the lower end
        of the canonical B ∈ [50, 100] range and matches the R
        ``stabs::stabsel`` default.
    subsample_frac : float, default 0.5
        Fraction of unique clusters to retain per subsample.
    random_state : int, default 42
        Reproducibility seed.
    tier_fn : callable
        Function taking ``(X_sub, y_sub, groups_sub, seed)`` and returning
        a tier-assignment mapping for the subsample (or None on failure).
        Used in the ``n_jobs == 1`` path; the parallel path reconstructs
        the closure inside each worker from ``hp`` to avoid pickling it.
    n_jobs : int, default 1
        Number of worker processes. ``1`` preserves historical sequential
        behavior; values > 1 dispatch subsamples to a
        :class:`ProcessPoolExecutor`. When ``hp`` is ``None``, the parallel
        path falls back to sequential (the worker cannot rebuild the
        closure without ``hp``).
    hp : SCALPELHyperparameters, optional
        Required when ``n_jobs > 1``. Passed to each worker so it can
        rebuild ``_stability_tier_fn(hp)`` locally.

    Returns
    -------
    StabilityResult
        Per-knob selection probability (fraction of subsamples that
        assigned the same tier as the primary run) and full tier
        distribution per knob.
    """
    if X.empty or sample_groups.empty:
        return StabilityResult(
            selection_probability={},
            tier_distribution={},
            n_subsamples=0,
            n_successful=0,
        )

    rng = np.random.default_rng(random_state)
    groups_arr = sample_groups.to_numpy()
    unique_groups = np.unique(groups_arr)
    if unique_groups.size == 0:
        return StabilityResult(
            selection_probability={},
            tier_distribution={},
            n_subsamples=0,
            n_successful=0,
        )

    target_size = max(1, int(round(subsample_frac * unique_groups.size)))

    # Pre-materialize every subsample slice + seed so the parallel path
    # can dispatch them without holding the RNG across processes. Stays
    # in the same order as the sequential path for reproducibility.
    columns = list(X.columns)
    subsamples: list[tuple[np.ndarray, np.ndarray, np.ndarray, list[str], int]] = []
    for _ in range(n_subsamples):
        sample = rng.choice(unique_groups, size=target_size, replace=False)
        mask = np.isin(groups_arr, sample)
        if not mask.any():
            continue
        seed = int(rng.integers(0, 2**31 - 1))
        subsamples.append(
            (
                X.loc[mask].to_numpy(),
                y.loc[mask].to_numpy(),
                sample_groups.loc[mask].to_numpy(),
                columns,
                seed,
            )
        )

    use_parallel = n_jobs > 1 and hp is not None and len(subsamples) > 1
    counts: dict[str, dict[str, int]] = {}
    assignment: Optional[Mapping[str, str]]
    successful = 0

    if use_parallel:
        max_workers = max(1, min(n_jobs, len(subsamples), os.cpu_count() or 1))
        LOGGER.info(
            "Stability: dispatching %d subsamples across %d worker processes",
            len(subsamples),
            max_workers,
        )
        payloads = [
            (X_arr, y_arr, groups_arr_sub, cols, seed, hp)
            for (X_arr, y_arr, groups_arr_sub, cols, seed) in subsamples
        ]
        with ProcessPoolExecutor(max_workers=max_workers) as executor:
            futures = {
                executor.submit(_run_one_subsample, payload): idx
                for idx, payload in enumerate(payloads)
            }
            for future in as_completed(futures):
                idx = futures[future]
                try:
                    assignment = future.result()
                except Exception as exc:  # pragma: no cover - defensive
                    LOGGER.debug("Stability subsample %d failed: %s", idx, exc)
                    continue
                if not assignment:
                    continue
                successful += 1
                for knob, tier in assignment.items():
                    counts.setdefault(knob, {}).setdefault(tier, 0)
                    counts[knob][tier] += 1
    else:
        for it, (X_arr, y_arr, groups_arr_sub, cols, seed) in enumerate(subsamples):
            X_sub = pd.DataFrame(X_arr, columns=cols)
            y_sub = pd.Series(y_arr)
            groups_sub = pd.Series(groups_arr_sub)
            try:
                assignment = tier_fn(X_sub, y_sub, groups_sub, seed)
            except Exception as exc:  # pragma: no cover - defensive
                LOGGER.debug("Stability subsample %d failed: %s", it, exc)
                continue
            if not assignment:
                continue
            successful += 1
            for knob, tier in assignment.items():
                counts.setdefault(knob, {}).setdefault(tier, 0)
                counts[knob][tier] += 1

    if successful == 0:
        return StabilityResult(
            selection_probability={knob: 0.0 for knob in counts},
            tier_distribution={knob: {} for knob in counts},
            n_subsamples=n_subsamples,
            n_successful=0,
        )

    distribution: dict[str, dict[str, float]] = {}
    for knob, tier_counts in counts.items():
        total = float(sum(tier_counts.values()))
        if total <= 0:
            distribution[knob] = {}
            continue
        distribution[knob] = {t: c / total for t, c in tier_counts.items()}

    selection_probability = {
        knob: max(dist.values()) if dist else 0.0
        for knob, dist in distribution.items()
    }

    return StabilityResult(
        selection_probability=selection_probability,
        tier_distribution=distribution,
        n_subsamples=n_subsamples,
        n_successful=successful,
    )


# ---------------------------------------------------------------------------
# DBA prior audit (report-only)
# ---------------------------------------------------------------------------

def audit_dba_prior(
    tier_assignments: Mapping[str, str],
    knob_metadata: Optional[Mapping[str, Any]],
) -> list[dict[str, str]]:
    """Flag expert-minimal knobs that did NOT land in data-driven minimal.

    The audit is purely descriptive — SCALPEL never enforces the prior.
    Each violation contains ``{knob, expert_tier, data_tier}`` so the
    reviewer can investigate whether the demotion is empirically
    supported (e.g., a knob that PBT held constant near its tuned
    optimum will look low-importance regardless of its causal effect).
    """
    if not knob_metadata:
        return []
    violations: list[dict[str, str]] = []
    for knob, meta in knob_metadata.items():
        expert_tier = getattr(meta, "impact_tier", None)
        if expert_tier != "minimal":
            continue
        data_tier = tier_assignments.get(knob, "not_confirmed")
        if data_tier == "minimal":
            continue
        violations.append(
            {
                "knob": knob,
                "expert_tier": "minimal",
                "data_tier": data_tier,
            }
        )
    return violations
