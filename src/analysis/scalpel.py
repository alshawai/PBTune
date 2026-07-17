"""
SCALPEL: Significance-Coverage-stability Algorithm for Layered
PErformance-knob Labeling.

Public entry point. Replaces the silhouette+Jenks pipeline in
``src/analysis/tier_generator.py``.

Pipeline (see /home/eima40x4c/.claude/plans/distributed-toasting-sparrow.md
for full provenance):

    1. Validate inputs
    2. Nuisance filter (data/knob_policy.json :: IMPORTANCE_NUISANCE_*)
    3. Preflight (degraded result on tiny / all-nuisance / cluster-poor input)
    4. Outer RF fit ONCE on the full cleaned feature set
    5. Group-permutation BORUTA + BH-FDR @ q=0.10 (Layer 1)
    6. fANOVA marginals on the full cleaned feature set
    7. Lorenz cuts within the confirmed subset (Layer 2)
    8. Group-clustered stability — full BORUTA re-run per subsample (Layer 3)
    9. DBA-prior audit (report-only)
    10. Assemble diagnostics
    11. Return SCALPELResult; ``.to_tier_result()`` adapts to TierResult.
"""

from __future__ import annotations

import argparse
import time
from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Any, Mapping, Optional

import numpy as np
import pandas as pd

if TYPE_CHECKING:
    from src.analysis.tier_generator import TierResult

from src.analysis.scalpel_significance import (
    _fit_outer_rf,
    boruta_with_group_perm,
    partition_boruta_hits,
)
from src.analysis.scalpel_stability import (
    apply_nuisance_filter,
    assign_lorenz_tiers,
    audit_dba_prior,
    compute_fanova_importance,
    compute_fanova_marginals,
    group_clustered_stability,
)
from src.knobs.policy import (
    IMPORTANCE_NUISANCE_EXCLUSIONS,
    IMPORTANCE_NUISANCE_PREFIXES,
)
from src.utils.logger import get_logger

LOGGER = get_logger("SCALPEL")

#: Schema slug persisted in metadata.algorithm of data_driven_tiers.json.
SCALPEL_ALGORITHM_SLUG = "scalpel-v1"
SCALPEL_VERSION = "1.0"

#: Lorenz cutoffs that satisfy the existing ``jenks_breaks`` schema slot.
DEFAULT_LORENZ_BREAKPOINTS: list[float] = [0.50, 0.80]

#: BH-FDR thresholds explored in the q-sensitivity sweep. Reviewers see how
#: tier composition shifts as the FDR tolerance widens; hit counts are
#: q-independent so the sweep is essentially free (Lorenz partitions only).
Q_SWEEP: tuple[float, ...] = (0.05, 0.10, 0.20, 0.30)


# ---------------------------------------------------------------------------
# Hyperparameters
# ---------------------------------------------------------------------------

@dataclass
class SCALPELHyperparameters:
    """Operator-tunable knobs for the SCALPEL pipeline.

    See the project plan for the rationale behind every default.
    """

    # Surrogate
    rf_n_estimators: int = 500
    rf_max_features: float | int | str | None = "sqrt"
    rf_min_samples_leaf: int = 3

    # Layer 1 — significance
    boruta_iter: int = 100
    fdr_q: float = 0.10

    # Layer 2 — coverage
    coverage_minimal: float = 0.50
    coverage_core: float = 0.80

    # Layer 2 — fused signal: marginal + alpha * max_interaction
    #
    # ``interaction_alpha`` mixes the fANOVA marginal mass with the
    # best pairwise-interaction contribution per knob. A knob whose
    # marginal mass is modest but whose strongest interaction lifts
    # the response surface should still land in the confirmed tier
    # system. The default 0.5 was chosen so a unit-mass interaction
    # only matters if the marginal is at least half its size. Set
    # ``--scalpel-interaction-alpha 0.0`` to recover the marginal-only
    # baseline.
    #
    # ``interaction_top_k`` caps the search frontier: only the K knobs
    # with the largest marginals get pairwise queries. The full O(p^2)
    # interaction matrix on ~180 knobs would dominate runtime; in
    # practice the fused signal only helps knobs whose marginal is
    # already competitive.
    interaction_alpha: float = 0.5
    interaction_top_k: int = 20

    # Layer 3 — stability
    #
    # ``n_stability_subsamples`` is 50 (Meinshausen & Bühlmann 2010 recommend
    # B ∈ [50, 100]; the R ``stabs::stabsel`` default is 50). Halved from the
    # v1.0 default of 100 to bring the wall-clock down without invalidating the
    # selection-probability estimator. Combined with ``stability_jobs`` > 1
    # the layer is the dominant runtime cost so parallelism is a hard
    # requirement, not an option.
    #
    # ``stability_boruta_iter`` is the BORUTA iteration count *inside* every
    # stability subsample. When None, falls through to ``boruta_iter`` so the
    # binomial null is calibrated against the same iteration count as the
    # primary pass — the v1.0 code path capped this at 50 against a primary
    # of 100, biasing ``stability_probability`` downward for borderline knobs.
    n_stability_subsamples: int = 50
    stability_subsample_frac: float = 0.5
    stability_boruta_iter: Optional[int] = None
    stability_jobs: int = 4

    # Preflight gates
    min_samples: int = 200
    min_features: int = 2
    min_clusters: int = 4
    min_obs_per_cluster: int = 1

    # Reproducibility
    seed: int = 42
    workload_label: str = "unknown"

    # Operator escape hatches
    nuisance_overrides: tuple[str, ...] = ()

    @classmethod
    def from_args(
        cls,
        args: argparse.Namespace,
        *,
        workload_label: str,
    ) -> "SCALPELHyperparameters":
        """Build an instance from ``analyze_knob_importance.py`` CLI args.

        Per-workload seed derivation (B13 in the plan) prevents the
        ``--all-workloads`` loop from sharing RF/BORUTA shadow draws
        across workloads, which would inflate apparent inter-workload
        agreement.
        """
        base_seed = int(getattr(args, "scalpel_base_seed", getattr(args, "random_seed", 42)))
        seed = (hash((base_seed, workload_label)) & 0xFFFFFFFF) or 42
        overrides_raw = getattr(args, "scalpel_nuisance_overrides", None) or ""
        if isinstance(overrides_raw, (list, tuple)):
            overrides = tuple(str(x) for x in overrides_raw if x)
        else:
            overrides = tuple(o.strip() for o in str(overrides_raw).split(",") if o.strip())

        return cls(
            rf_n_estimators=int(getattr(args, "scalpel_rf_trees", 500)),
            rf_max_features=getattr(args, "scalpel_rf_max_features", "sqrt"),
            rf_min_samples_leaf=int(getattr(args, "scalpel_rf_min_samples_leaf", 3)),
            boruta_iter=int(getattr(args, "scalpel_boruta_iter", 100)),
            fdr_q=float(getattr(args, "scalpel_fdr_q", 0.10)),
            coverage_minimal=float(getattr(args, "scalpel_coverage_minimal", 0.50)),
            coverage_core=float(getattr(args, "scalpel_coverage_core", 0.80)),
            interaction_alpha=float(getattr(args, "scalpel_interaction_alpha", 0.5)),
            interaction_top_k=int(getattr(args, "scalpel_interaction_top_k", 20)),
            n_stability_subsamples=int(getattr(args, "scalpel_stability_b", 50)),
            stability_subsample_frac=float(
                getattr(args, "scalpel_stability_frac", 0.5)
            ),
            stability_boruta_iter=getattr(args, "scalpel_stability_iter", None),
            stability_jobs=int(getattr(args, "scalpel_stability_jobs", 4)),
            seed=seed,
            workload_label=workload_label,
            nuisance_overrides=overrides,
        )


# ---------------------------------------------------------------------------
# Result container
# ---------------------------------------------------------------------------

@dataclass
class SCALPELResult:
    """End-to-end SCALPEL output."""

    workload_label: str
    tier_assignments: dict[str, str]
    confirmed: list[str]
    tentative: list[str]
    rejected: list[str]
    nuisance_dropped: list[str]
    full_importances: dict[str, float]
    confirmed_importances: dict[str, float]
    cumulative_coverage: dict[str, float]
    lorenz_breakpoints: dict[str, float]
    boruta_hits: dict[str, int]
    boruta_p_values: dict[str, float]
    boruta_p_values_bh: dict[str, float]
    stability_probabilities: dict[str, float]
    stability_tier_distribution: dict[str, dict[str, float]]
    dba_prior_violations: list[dict[str, str]]
    diagnostics: dict[str, Any]
    is_degenerate: bool = False
    preflight_reason: Optional[str] = None
    hyperparameters: dict[str, Any] = field(default_factory=dict)
    q_sensitivity: dict[str, dict[str, Any]] = field(default_factory=dict)
    marginal_importances: dict[str, float] = field(default_factory=dict)
    max_interactions: dict[str, float] = field(default_factory=dict)
    lorenz_input_importances: dict[str, float] = field(default_factory=dict)
    top_k_marginals: list[str] = field(default_factory=list)

    @property
    def is_successful(self) -> bool:
        """True when SCALPEL produced a usable tier assignment."""
        return not self.is_degenerate and bool(self.confirmed)

    def to_tier_result(self, *, workload_label: Optional[str] = None) -> "TierResult":
        """Adapt to the legacy :class:`TierResult` schema.

        ``optimal_k`` is set to ``4`` when SCALPEL produced confirmed
        knobs (the canonical 4-tier system was filled in) and ``1`` for
        the degenerate path. ``silhouette_scores`` is intentionally an
        empty mapping; ``jenks_breaks`` carries the Lorenz cutoffs so
        existing JSON readers stay happy.
        """
        from src.analysis.tier_generator import (  # local import to break cycle
            AgreementReport,
            EXPERT_TIER_ORDER,
            TierResult,
            compare_to_expert,
        )

        label = workload_label or self.workload_label
        if self.is_successful:
            optimal_k = 4
            agreement = compare_to_expert(self.tier_assignments, EXPERT_TIER_ORDER)
        else:
            optimal_k = 1
            agreement = AgreementReport(agreements=[], promotions=[], demotions=[])
        return TierResult(
            optimal_k=optimal_k,
            silhouette_scores={},
            tier_assignments=dict(self.tier_assignments),
            jenks_breaks=list(DEFAULT_LORENZ_BREAKPOINTS),
            agreement_report=agreement,
            workload_label=label,
        )

    def diagnostics_pruned(self) -> dict[str, Any]:
        """Return the small-payload diagnostics block embedded in JSON."""
        d = self.diagnostics
        return {
            "nuisance_dropped": list(self.nuisance_dropped),
            "oob_r2": d.get("oob_r2"),
            "n_confirmed": len(self.confirmed),
            "n_tentative": len(self.tentative),
            "n_rejected": len(self.rejected),
            "dba_prior_violations": [v["knob"] for v in self.dba_prior_violations],
            "lorenz_cutoffs": [
                self.hyperparameters.get("coverage_minimal", 0.50),
                self.hyperparameters.get("coverage_core", 0.80),
            ],
            "boruta_iter": self.hyperparameters.get("boruta_iter"),
            "fdr_q": self.hyperparameters.get("fdr_q"),
            "n_stability_subsamples": self.hyperparameters.get("n_stability_subsamples"),
            "stability_boruta_iter": self.hyperparameters.get("stability_boruta_iter"),
            "stability_jobs": self.hyperparameters.get("stability_jobs"),
            "wall_clock_s": d.get("wall_clock_s"),
            "preflight_reason": self.preflight_reason,
            "is_degenerate": self.is_degenerate,
            "stable_knobs_semantics": "intersection_of_confirmed_sets",
            "q_sensitivity_summary": {
                q: int(payload.get("n_confirmed", 0))
                for q, payload in self.q_sensitivity.items()
            },
        }

    def diagnostics_full(self) -> dict[str, Any]:
        """Return the full per-knob diagnostic block (for sibling JSON)."""
        return {
            "workload_label": self.workload_label,
            "algorithm": SCALPEL_ALGORITHM_SLUG,
            "scalpel_version": SCALPEL_VERSION,
            "is_degenerate": self.is_degenerate,
            "preflight_reason": self.preflight_reason,
            "hyperparameters": dict(self.hyperparameters),
            "summary": self.diagnostics_pruned(),
            "tier_assignments": dict(self.tier_assignments),
            "confirmed": list(self.confirmed),
            "tentative": list(self.tentative),
            "rejected": list(self.rejected),
            "nuisance_dropped": list(self.nuisance_dropped),
            "full_importances": dict(self.full_importances),
            "confirmed_importances": dict(self.confirmed_importances),
            "cumulative_coverage": dict(self.cumulative_coverage),
            "lorenz_breakpoints": dict(self.lorenz_breakpoints),
            "boruta_hits": dict(self.boruta_hits),
            "boruta_p_values": dict(self.boruta_p_values),
            "boruta_p_values_bh": dict(self.boruta_p_values_bh),
            "stability_probabilities": dict(self.stability_probabilities),
            "stability_tier_distribution": {
                k: dict(v) for k, v in self.stability_tier_distribution.items()
            },
            "dba_prior_violations": list(self.dba_prior_violations),
            "diagnostics": dict(self.diagnostics),
            "q_sensitivity": {q: dict(payload) for q, payload in self.q_sensitivity.items()},
            "marginal_importances": dict(self.marginal_importances),
            "max_interactions": dict(self.max_interactions),
            "lorenz_input_importances": dict(self.lorenz_input_importances),
            "top_k_marginals": list(self.top_k_marginals),
        }


# ---------------------------------------------------------------------------
# Lorenz fallback (used by tier_generator.generate_tiers shim)
# ---------------------------------------------------------------------------

def lorenz_tier_from_importances(
    marginal_importances: Mapping[str, float],
    workload_label: str,
    *,
    coverage_minimal: float = 0.50,
    coverage_core: float = 0.80,
) -> "TierResult":
    """Coverage-only tiering used when only an importance dict is available.

    This is the lossy path consumed by ``tier_generator.generate_tiers``
    (which the ``hardware_validator`` cross-hardware export still calls).
    No significance gate, no stability — every knob is assigned to
    ``minimal`` / ``core`` / ``standard`` purely by Lorenz cumulative mass.
    """
    from src.analysis.tier_generator import (  # local import to break cycle
        EXPERT_TIER_ORDER,
        TierResult,
        compare_to_expert,
    )

    if not marginal_importances:
        raise ValueError("Marginal importance scores are required.")

    knobs = list(marginal_importances.keys())
    if len(knobs) == 1:
        only = knobs[0]
        agreement = compare_to_expert({only: "minimal"}, EXPERT_TIER_ORDER)
        return TierResult(
            optimal_k=1,
            silhouette_scores={},
            tier_assignments={only: "minimal"},
            jenks_breaks=list(DEFAULT_LORENZ_BREAKPOINTS),
            agreement_report=agreement,
            workload_label=workload_label,
        )

    lorenz = assign_lorenz_tiers(
        marginal_importances,
        confirmed=knobs,
        coverage_minimal=coverage_minimal,
        coverage_core=coverage_core,
    )
    agreement = compare_to_expert(lorenz.tier_assignments, EXPERT_TIER_ORDER)
    return TierResult(
        optimal_k=4,
        silhouette_scores={},
        tier_assignments=dict(lorenz.tier_assignments),
        jenks_breaks=list(DEFAULT_LORENZ_BREAKPOINTS),
        agreement_report=agreement,
        workload_label=workload_label,
    )


# ---------------------------------------------------------------------------
# Orchestrator
# ---------------------------------------------------------------------------

def _degenerate(
    workload_label: str,
    hp: SCALPELHyperparameters,
    *,
    reason: str,
    nuisance_dropped: Optional[list[str]] = None,
    full_importances: Optional[dict[str, float]] = None,
    extra: Optional[dict[str, Any]] = None,
) -> SCALPELResult:
    diagnostics: dict[str, Any] = {"oob_r2": None, "wall_clock_s": 0.0}
    if extra:
        diagnostics.update(extra)
    return SCALPELResult(
        workload_label=workload_label,
        tier_assignments={},
        confirmed=[],
        tentative=[],
        rejected=[],
        nuisance_dropped=nuisance_dropped or [],
        full_importances=full_importances or {},
        confirmed_importances={},
        cumulative_coverage={},
        lorenz_breakpoints={"minimal": 0.0, "core": 0.0, "standard": 0.0},
        boruta_hits={},
        boruta_p_values={},
        boruta_p_values_bh={},
        stability_probabilities={},
        stability_tier_distribution={},
        dba_prior_violations=[],
        diagnostics=diagnostics,
        is_degenerate=True,
        preflight_reason=reason,
        hyperparameters=_hp_dict(hp),
    )


def _hp_dict(hp: SCALPELHyperparameters) -> dict[str, Any]:
    return {
        "rf_n_estimators": hp.rf_n_estimators,
        "rf_max_features": hp.rf_max_features,
        "rf_min_samples_leaf": hp.rf_min_samples_leaf,
        "boruta_iter": hp.boruta_iter,
        "fdr_q": hp.fdr_q,
        "coverage_minimal": hp.coverage_minimal,
        "coverage_core": hp.coverage_core,
        "interaction_alpha": hp.interaction_alpha,
        "interaction_top_k": hp.interaction_top_k,
        "n_stability_subsamples": hp.n_stability_subsamples,
        "stability_subsample_frac": hp.stability_subsample_frac,
        "stability_boruta_iter": (
            hp.stability_boruta_iter
            if hp.stability_boruta_iter is not None
            else hp.boruta_iter
        ),
        "stability_jobs": hp.stability_jobs,
        "min_samples": hp.min_samples,
        "min_features": hp.min_features,
        "min_clusters": hp.min_clusters,
        "min_obs_per_cluster": hp.min_obs_per_cluster,
        "seed": hp.seed,
        "workload_label": hp.workload_label,
        "nuisance_overrides": list(hp.nuisance_overrides),
    }


def _stability_tier_fn(hp: SCALPELHyperparameters):
    """Closure that re-runs Layers 1+2 on a cluster subsample.

    Each subsample regenerates its own shadow features (no cached null);
    the BORUTA RF parameters are inherited from ``hp``. The iteration
    count defaults to ``hp.boruta_iter`` so the BH-FDR binomial null is
    calibrated against the same iteration count as the primary pass —
    a separate ``--scalpel-stability-iter`` flag lets operators trade
    statistical precision for runtime when they deliberately want a
    looser stability audit.
    """
    sub_iter = hp.stability_boruta_iter if hp.stability_boruta_iter is not None else hp.boruta_iter

    def _inner(
        X_sub: pd.DataFrame,
        y_sub: pd.Series,
        groups_sub: pd.Series,
        seed: int,
    ) -> Optional[Mapping[str, str]]:
        boruta = boruta_with_group_perm(
            X_sub,
            y_sub,
            groups_sub,
            n_iterations=sub_iter,
            n_estimators=max(100, hp.rf_n_estimators // 2),
            max_features=hp.rf_max_features,
            min_samples_leaf=hp.rf_min_samples_leaf,
            fdr_q=hp.fdr_q,
            random_state=seed,
        )
        if not boruta.confirmed:
            return None
        try:
            importances = compute_fanova_marginals(
                X_sub,
                y_sub,
                n_estimators=max(100, hp.rf_n_estimators // 2),
                min_samples_leaf=hp.rf_min_samples_leaf,
                max_features=hp.rf_max_features,
                random_state=seed,
            )
        except Exception:
            return None
        if not importances:
            return None
        lorenz = assign_lorenz_tiers(
            importances,
            confirmed=boruta.confirmed,
            coverage_minimal=hp.coverage_minimal,
            coverage_core=hp.coverage_core,
        )
        return dict(lorenz.tier_assignments)

    return _inner


def scalpel_tier(
    X: pd.DataFrame,
    y: pd.Series,
    *,
    sample_groups: pd.Series,
    hp: Optional[SCALPELHyperparameters] = None,
    knob_metadata: Optional[Mapping[str, Any]] = None,
    knob_bounds: Optional[Mapping[str, tuple[float, float]]] = None,
) -> SCALPELResult:
    """End-to-end SCALPEL tier generation.

    See module docstring for the full pipeline. ``X.columns`` MUST be
    alphabetized (the existing ``LoadedData.config_df`` already does
    this). ``sample_groups`` MUST be row-aligned with ``X`` and ``y``;
    the orchestrator does NOT rely on pandas index alignment (passing
    NumPy-built series is fine).
    """
    hp = hp or SCALPELHyperparameters()
    workload_label = hp.workload_label
    started = time.perf_counter()

    if X is None or y is None or sample_groups is None:
        return _degenerate(workload_label, hp, reason="missing_inputs")
    if not (len(X) == len(y) == len(sample_groups)):
        return _degenerate(
            workload_label,
            hp,
            reason="length_mismatch",
            extra={
                "n_X": int(len(X)),
                "n_y": int(len(y)),
                "n_groups": int(len(sample_groups)),
            },
        )

    # Step 2: nuisance filter (drop columns)
    nuisance = apply_nuisance_filter(
        X,
        exclusions=IMPORTANCE_NUISANCE_EXCLUSIONS,
        prefixes=IMPORTANCE_NUISANCE_PREFIXES,
        overrides=hp.nuisance_overrides,
    )
    X_clean = nuisance.filtered.reset_index(drop=True)
    y_clean = y.reset_index(drop=True)
    groups_clean = sample_groups.reset_index(drop=True)

    # Step 3: preflight
    n_samples, n_features = X_clean.shape
    if n_samples < hp.min_samples:
        return _degenerate(
            workload_label,
            hp,
            reason=f"too_few_samples(n={n_samples}<{hp.min_samples})",
            nuisance_dropped=nuisance.dropped,
        )
    if n_features < hp.min_features:
        return _degenerate(
            workload_label,
            hp,
            reason=f"too_few_features(p={n_features}<{hp.min_features})",
            nuisance_dropped=nuisance.dropped,
        )
    unique_clusters = pd.Series(groups_clean).nunique()
    if unique_clusters < hp.min_clusters:
        return _degenerate(
            workload_label,
            hp,
            reason=f"too_few_clusters({unique_clusters}<{hp.min_clusters})",
            nuisance_dropped=nuisance.dropped,
        )

    # Sort columns alphabetically for determinism (defensive — should already be)
    if list(X_clean.columns) != sorted(X_clean.columns):
        X_clean = X_clean.reindex(sorted(X_clean.columns), axis=1)

    # Step 4: outer RF — recorded for diagnostics; the BORUTA layer
    # trains its own per-iteration RFs against shadow features.
    _outer_rf, oob_r2 = _fit_outer_rf(
        X_clean,
        y_clean,
        n_estimators=hp.rf_n_estimators,
        max_features=hp.rf_max_features,
        min_samples_leaf=hp.rf_min_samples_leaf,
        random_state=hp.seed,
    )

    # Step 5: BORUTA + BH-FDR
    boruta = boruta_with_group_perm(
        X_clean,
        y_clean,
        groups_clean,
        n_iterations=hp.boruta_iter,
        n_estimators=hp.rf_n_estimators,
        max_features=hp.rf_max_features,
        min_samples_leaf=hp.rf_min_samples_leaf,
        fdr_q=hp.fdr_q,
        random_state=hp.seed,
    )

    # Step 6: fANOVA on the FULL cleaned feature set (marginals + pairwise interactions)
    fanova_result = compute_fanova_importance(
        X_clean,
        y_clean,
        knob_bounds=knob_bounds,
        n_estimators=hp.rf_n_estimators,
        min_samples_leaf=hp.rf_min_samples_leaf,
        max_features=hp.rf_max_features,
        random_state=hp.seed,
        interaction_top_k=hp.interaction_top_k,
    )
    full_importances = dict(fanova_result.marginals)
    fused_importances: dict[str, float] = {
        knob: float(full_importances.get(knob, 0.0))
        + hp.interaction_alpha * float(fanova_result.max_interactions.get(knob, 0.0))
        for knob in full_importances
    }

    if not boruta.confirmed:
        wall = time.perf_counter() - started
        result = _degenerate(
            workload_label,
            hp,
            reason="no_confirmed_knobs",
            nuisance_dropped=nuisance.dropped,
            full_importances=full_importances,
            extra={"oob_r2": oob_r2, "wall_clock_s": wall},
        )
        result.boruta_hits = dict(boruta.hit_counts)
        result.boruta_p_values = dict(boruta.p_values)
        result.boruta_p_values_bh = dict(boruta.p_values_bh)
        result.tentative = list(boruta.tentative)
        result.rejected = list(boruta.rejected)
        return result

    # Step 7: Lorenz cuts on confirmed subset
    lorenz = assign_lorenz_tiers(
        fused_importances,
        confirmed=boruta.confirmed,
        coverage_minimal=hp.coverage_minimal,
        coverage_core=hp.coverage_core,
    )

    # Step 7b: q-sensitivity sweep — partition the same hit counts at
    # alternative FDR targets and re-tier within each. Hit counts are
    # q-independent so this adds only a handful of millisecond-scale
    # Lorenz partitions; the sweep gives reviewers a tier-stability
    # diagnostic ("how does the boundary move at q=0.05 vs q=0.30?").
    knobs_in_X = list(X_clean.columns)
    hit_counts_arr = np.array(
        [boruta.hit_counts.get(k, 0) for k in knobs_in_X], dtype=np.int64
    )
    q_sensitivity: dict[str, dict[str, Any]] = {}
    for q in Q_SWEEP:
        q_result = partition_boruta_hits(
            hit_counts_arr, knobs_in_X, hp.boruta_iter, fdr_q=q
        )
        if q_result.confirmed:
            q_lorenz = assign_lorenz_tiers(
                fused_importances,
                confirmed=q_result.confirmed,
                coverage_minimal=hp.coverage_minimal,
                coverage_core=hp.coverage_core,
            )
            q_assignments = dict(q_lorenz.tier_assignments)
        else:
            q_assignments = {}
        q_sensitivity[f"{q:.2f}"] = {
            "confirmed": list(q_result.confirmed),
            "tentative": list(q_result.tentative),
            "rejected": list(q_result.rejected),
            "tier_assignments": q_assignments,
            "n_confirmed": len(q_result.confirmed),
        }

    # Step 8: stability — every subsample re-runs Layers 1+2 from scratch
    stability = group_clustered_stability(
        X_clean,
        y_clean,
        groups_clean,
        n_subsamples=hp.n_stability_subsamples,
        subsample_frac=hp.stability_subsample_frac,
        random_state=hp.seed,
        tier_fn=_stability_tier_fn(hp),
        n_jobs=hp.stability_jobs,
        hp=hp,
    )

    # Step 9: DBA-prior audit
    violations = audit_dba_prior(lorenz.tier_assignments, knob_metadata)

    confirmed_importances = {
        knob: float(fused_importances.get(knob, 0.0)) for knob in boruta.confirmed
    }

    wall = time.perf_counter() - started
    diagnostics: dict[str, Any] = {
        "oob_r2": oob_r2,
        "wall_clock_s": wall,
        "n_samples": int(n_samples),
        "n_features_after_nuisance": int(n_features),
        "n_unique_clusters": int(unique_clusters),
        "n_stability_successful": int(stability.n_successful),
    }

    return SCALPELResult(
        workload_label=workload_label,
        tier_assignments=dict(lorenz.tier_assignments),
        confirmed=list(boruta.confirmed),
        tentative=list(boruta.tentative),
        rejected=list(boruta.rejected),
        nuisance_dropped=list(nuisance.dropped),
        full_importances=dict(full_importances),
        confirmed_importances=confirmed_importances,
        cumulative_coverage=dict(lorenz.cumulative_coverage),
        lorenz_breakpoints=dict(lorenz.breakpoints),
        boruta_hits=dict(boruta.hit_counts),
        boruta_p_values=dict(boruta.p_values),
        boruta_p_values_bh=dict(boruta.p_values_bh),
        stability_probabilities=dict(stability.selection_probability),
        stability_tier_distribution=dict(stability.tier_distribution),
        dba_prior_violations=violations,
        diagnostics=diagnostics,
        is_degenerate=False,
        preflight_reason=None,
        hyperparameters=_hp_dict(hp),
        q_sensitivity=q_sensitivity,
        marginal_importances=dict(fanova_result.marginals),
        max_interactions=dict(fanova_result.max_interactions),
        lorenz_input_importances=dict(fused_importances),
        top_k_marginals=list(fanova_result.top_k_marginals),
    )
