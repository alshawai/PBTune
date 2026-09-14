"""
Statistical Analysis for Comparative Evaluation
================================================

Non-parametric statistical framework for comparing default vs tuned
PostgreSQL configurations across N repeated benchmark runs.

Methodology (justified for small N=5):
    - Wilcoxon signed-rank test (primary): non-parametric, paired,
      no normality assumption — appropriate for N ≥ 5.
    - Bootstrap CI (10,000 resamples): robust confidence intervals on
      paired median differences without distributional assumptions.
    - Holm correction for secondary endpoints only: controls family-wise
      error rate while preserving sensitivity for the primary endpoint.
    - Paired Cohen's d: standardized effect size so reviewers can judge
      practical significance, not just statistical significance.
    - Both mean ± std AND median ± IQR reported for comparability with
      literature that uses either convention.

References:
    Demšar, J. (2006). Statistical Comparisons of Classifiers over
        Multiple Data Sets. JMLR 7, 1–30.
    Wilcoxon, F. (1945). Individual Comparisons by Ranking Methods.
        Biometrics Bulletin, 1(6), 80–83.
"""

from __future__ import annotations

import itertools
import math
from typing import Callable

import numpy as np
import scipy.stats as stats

from src.utils.logger import get_logger
from src.utils.scoring.constants import METRIC_DIRECTIONALITY
from src.evaluation.types import (
    ComparisonStatistics,
    MetricComparison,
    PairwiseResult,
    RunResult,
    StatSummary,
)

LOGGER = get_logger("Statistics")

_PRIMARY_ENDPOINT = "score"

# Secondary endpoints subject to Holm family-wise correction. Deliberately
# curated to metrics that are (a) genuinely measured in the evaluation harness
# and (b) not a monotone restatement of another member.
_SECONDARY_ENDPOINTS = (
    "throughput",
    "latency_p99",
    "memory_pressure",
    "scan_efficiency",
)

# Reported for context (summary table + JSON) but never hypothesis-tested:
# near-constant on healthy runs (``error_rate``), highly correlated with a
# tested endpoint (``latency_p95`` / ``latency_p50`` vs ``latency_p99``), or
# niche restatements (``tail_amplification``, ``latency_variance``).
_REPORTED_ENDPOINTS = (
    "latency_p95",
    "latency_p50",
    "error_rate",
    "tail_amplification",
    "latency_variance",
)

# Number of bootstrap resamples for CI estimation
_N_BOOTSTRAP = 10_000

# Family-wise significance level before correction
_ALPHA = 0.05

# Finite sentinel for a perfectly-consistent paired effect (std == 0).
_MAX_COHENS_D = 1e6


def compute_comparison_statistics(
    default_runs: list[RunResult],
    tuned_runs: list[RunResult],
    benchmark: str,
    alpha: float = _ALPHA,
) -> ComparisonStatistics:
    """
    Compute full statistical comparison between default and tuned runs.

    Pairs runs strictly by run_number/pair_seed so each repetition shares
    identical workload seed and execution protocol. Uses a primary endpoint
    (score) at alpha, and Holm-corrected secondary endpoints.

    Args:
        default_runs: RunResult list for the default (untuned) configuration.
        tuned_runs: RunResult list for the tuned configuration.

    Returns:
        ComparisonStatistics with per-metric Wilcoxon tests, bootstrap CIs,
        Cohen's d, a primary endpoint at alpha, and Holm-corrected
        secondary endpoint significance flags.

    Raises:
        ValueError: If run lists are empty or have inconsistent run_numbers.
    """
    if not default_runs or not tuned_runs:
        raise ValueError("Both default_runs and tuned_runs must be non-empty.")

    default_sorted = sorted(default_runs, key=lambda r: r.run_number)
    tuned_sorted = sorted(tuned_runs, key=lambda r: r.run_number)

    if len(default_sorted) != len(tuned_sorted):
        raise ValueError(
            "Paired run count mismatch: default and tuned runs must have equal length."
        )

    default_pair_keys = [(r.run_number, r.pair_seed) for r in default_sorted]
    tuned_pair_keys = [(r.run_number, r.pair_seed) for r in tuned_sorted]
    if default_pair_keys != tuned_pair_keys:
        raise ValueError(
            "Paired run mismatch: default and tuned run pairs are not aligned."
        )

    n = len(default_sorted)

    metric_comparisons: list[MetricComparison] = []

    def _vals(metric_name: str, runs: list[RunResult]) -> list[float]:
        extractor = _build_extractor(metric_name)
        return [extractor(r) for r in runs]

    # Primary endpoint: score (tested at alpha, no multiplicity correction) ──
    primary_metric = _compare_metric(
        metric_name=_PRIMARY_ENDPOINT,
        default_vals=_vals(_PRIMARY_ENDPOINT, default_sorted),
        tuned_vals=_vals(_PRIMARY_ENDPOINT, tuned_sorted),
        higher_is_better=_higher_is_better(_PRIMARY_ENDPOINT),
        endpoint_role="primary",
        alpha=alpha,
    )
    _apply_significance(
        metrics=[primary_metric],
        adjusted_p_values=[primary_metric.p_value],
        alpha=alpha,
        correction_method=None,
    )
    metric_comparisons.append(primary_metric)

    # Secondary endpoints: Holm-corrected over the NON-degenerate members
    secondary_metrics = [
        _compare_metric(
            metric_name=name,
            default_vals=_vals(name, default_sorted),
            tuned_vals=_vals(name, tuned_sorted),
            higher_is_better=_higher_is_better(name),
            endpoint_role="secondary",
            alpha=alpha,
        )
        for name in _SECONDARY_ENDPOINTS
    ]
    active = [mc for mc in secondary_metrics if not _is_degenerate(mc)]
    degenerate = [mc for mc in secondary_metrics if _is_degenerate(mc)]
    _apply_significance(
        metrics=active,
        adjusted_p_values=_holm_adjusted_pvalues([mc.p_value for mc in active]),
        alpha=alpha,
        correction_method="holm",
    )
    for mc in degenerate:
        mc.p_value_corrected = 1.0
        mc.significant = False
        mc.correction_method = "holm"
    if degenerate:
        LOGGER.info(
            "Excluded %d degenerate (all-zero-difference) endpoint(s) from the "
            "Holm family so they do not dilute the correction: %s",
            len(degenerate),
            ", ".join(mc.metric_name for mc in degenerate),
        )
    metric_comparisons.extend(secondary_metrics)

    # Reported endpoints: descriptive context only, never hypothesis-tested
    for name in _REPORTED_ENDPOINTS:
        reported = _compare_metric(
            metric_name=name,
            default_vals=_vals(name, default_sorted),
            tuned_vals=_vals(name, tuned_sorted),
            higher_is_better=_higher_is_better(name),
            endpoint_role="reported",
            alpha=alpha,
        )
        reported.significant = False
        reported.correction_method = None
        metric_comparisons.append(reported)

    for mc in metric_comparisons:
        LOGGER.debug(
            "Metric '%s' (%s): improvement=%.1f%% p=%.4f p_adj=%.4f cohen_d=%.2f significant=%s",
            mc.metric_name,
            mc.endpoint_role,
            mc.improvement_pct,
            mc.p_value,
            mc.p_value_corrected,
            mc.cohens_d,
            mc.significant,
        )

    significant_metrics = [
        mc.metric_name for mc in metric_comparisons if mc.significant
    ]

    power_warning = _build_power_warning(n)

    # Overall improvement uses the score (primary) endpoint.
    score_mc = next(
        mc for mc in metric_comparisons if mc.metric_name == _PRIMARY_ENDPOINT
    )

    return ComparisonStatistics(
        metrics=metric_comparisons,
        significant_metrics=significant_metrics,
        overall_improvement_pct=score_mc.improvement_pct,
        overall_improvement_ci=score_mc.improvement_ci,
        n_pairs=n,
        correction_method="holm_secondary",
        power_warning=power_warning,
        alpha=alpha,
        primary_endpoint=_PRIMARY_ENDPOINT,
        secondary_endpoints=list(_SECONDARY_ENDPOINTS),
        primary_significant=score_mc.significant,
        secondary_correction_method="holm",
    )


def compute_pairwise_statistics(
    runs_by_arm: dict[str, list[RunResult]],
    benchmark: str,
    alpha: float = _ALPHA,
) -> list[PairwiseResult]:
    """
    Compute Wilcoxon signed-rank statistics for all pairwise arm combinations.

    Uses ``itertools.combinations`` to generate C(k, 2) pairs from the arm
    dictionary. The "default" arm is always placed as arm_a (baseline) when
    present; otherwise pairs are ordered alphabetically so improvement
    direction is consistent (positive = arm_b outperforms arm_a).

    Args:
        runs_by_arm: Mapping of arm name to its list of RunResults.
            All arms must have the same number of runs with matching
            (run_number, pair_seed) tuples.
        benchmark: Benchmark type ("sysbench" or "tpch").
        alpha: Family-wise significance level.

    Returns:
        List of PairwiseResult, one per arm pair.
    """
    arm_names = sorted(runs_by_arm.keys())
    results: list[PairwiseResult] = []

    for raw_a, raw_b in itertools.combinations(arm_names, 2):
        if raw_b == "default":
            arm_a, arm_b = raw_b, raw_a
        elif raw_a == "default":
            arm_a, arm_b = raw_a, raw_b
        else:
            arm_a, arm_b = raw_a, raw_b

        stats_for_pair = compute_comparison_statistics(
            default_runs=runs_by_arm[arm_a],
            tuned_runs=runs_by_arm[arm_b],
            benchmark=benchmark,
            alpha=alpha,
        )
        results.append(
            PairwiseResult(
                arm_a=arm_a,
                arm_b=arm_b,
                statistics=stats_for_pair,
            )
        )

    LOGGER.info(
        "Computed pairwise statistics for %d pairs across %d arms.",
        len(results),
        len(arm_names),
    )
    return results


def _compare_metric(
    metric_name: str,
    default_vals: list[float],
    tuned_vals: list[float],
    higher_is_better: bool,
    endpoint_role: str,
    alpha: float,
) -> MetricComparison:
    """Build a MetricComparison for one metric."""
    d_arr = np.array(default_vals, dtype=float)
    t_arr = np.array(tuned_vals, dtype=float)

    if higher_is_better:
        differences = t_arr - d_arr  # throughput/score: tuned > default = good
    else:
        differences = d_arr - t_arr  # latency: default > tuned = good

    p_value = _wilcoxon_p(differences)

    ci_lower, ci_upper = _bootstrap_ci_median(differences)

    baseline_median = float(np.median(d_arr))
    if baseline_median == 0.0:
        improvement_pct = 0.0
    else:
        improvement_pct = (float(np.median(differences)) / abs(baseline_median)) * 100.0

    # Convert CI from absolute difference to percentage
    if baseline_median != 0.0:
        ci_pct = (
            ci_lower / abs(baseline_median) * 100.0,
            ci_upper / abs(baseline_median) * 100.0,
        )
    else:
        ci_pct = (0.0, 0.0)

    cohens_d = _paired_cohens_d(differences)

    return MetricComparison(
        metric_name=metric_name,
        default=_stat_summary(default_vals),
        tuned=_stat_summary(tuned_vals),
        improvement_pct=improvement_pct,
        improvement_ci=ci_pct,
        p_value=p_value,
        p_value_corrected=p_value,
        cohens_d=cohens_d,
        significant=p_value < alpha,
        higher_is_better=higher_is_better,
        endpoint_role=endpoint_role,
        correction_method=None,
    )


def _apply_significance(
    metrics: list[MetricComparison],
    adjusted_p_values: list[float],
    alpha: float,
    correction_method: str | None,
) -> None:
    """Apply corrected p-values and significance flags to metric comparisons."""
    for mc, p_adj in zip(metrics, adjusted_p_values, strict=True):
        mc.p_value_corrected = p_adj
        mc.significant = p_adj < alpha
        mc.correction_method = correction_method


def _wilcoxon_p(differences: np.ndarray) -> float:
    """
    Wilcoxon signed-rank test p-value (two-sided) on paired differences.

    Falls back to 1.0 if all differences are zero (no effect) or if
    scipy raises (e.g. n < 5 after zero-tie removal).
    """
    nonzero = differences[differences != 0.0]
    if len(nonzero) == 0:
        return 1.0  # No difference at all
    try:
        _, p = stats.wilcoxon(nonzero, alternative="two-sided", zero_method="wilcox")
        return float(p)  # type: ignore
    except ValueError:
        return (
            1.0  # scipy requires at least 1 nonzero difference; already checked above
        )


def _bootstrap_ci_median(
    differences: np.ndarray,
    n_bootstrap: int = _N_BOOTSTRAP,
    confidence: float = 0.95,
) -> tuple[float, float]:
    """
    Bootstrap 95% confidence interval on the median of paired differences.

    Resamples with replacement from the observed differences, computes
    the median of each resample, and returns the (alpha/2, 1-alpha/2)
    percentiles of the bootstrap distribution.

    Args:
        differences: 1-D array of paired differences (tuned - default or
            default - tuned for inverse metrics).
        n_bootstrap: Number of bootstrap resamples (default 10,000).
        confidence: Confidence level (default 0.95 → 95% CI).

    Returns:
        (lower_bound, upper_bound) of the CI in the same units as
        `differences`.
    """
    rng = np.random.default_rng(seed=42)  # Fixed seed for reproducibility
    n = len(differences)

    resamples = rng.choice(differences, size=(n_bootstrap, n), replace=True)
    bootstrap_medians = np.median(resamples, axis=1)

    alpha = 1.0 - confidence
    lower = float(np.percentile(bootstrap_medians, alpha / 2 * 100))
    upper = float(np.percentile(bootstrap_medians, (1 - alpha / 2) * 100))
    return lower, upper


def _paired_cohens_d(differences: np.ndarray) -> float:
    """
    Paired Cohen's d = mean(differences) / std(differences).

    Interpreted as: 0.2 = small, 0.5 = medium, 0.8 = large effect.
    Returns 0.0 when std is zero (perfectly consistent effect).
    """
    mean_diff = float(np.mean(differences))
    std_diff = float(np.std(differences, ddof=1))
    if std_diff == 0.0:
        if mean_diff == 0.0:
            return 0.0
        return math.copysign(_MAX_COHENS_D, mean_diff)
    return mean_diff / std_diff


def _stat_summary(values: list[float]) -> StatSummary:
    """Compute mean, std, median, and IQR for a list of values."""
    arr = np.array(values, dtype=float)
    return StatSummary(
        mean=float(np.mean(arr)),
        std=float(np.std(arr, ddof=1)) if len(arr) > 1 else 0.0,
        median=float(np.median(arr)),
        iqr_lower=float(np.percentile(arr, 25)),
        iqr_upper=float(np.percentile(arr, 75)),
        values=list(values),
    )


def _higher_is_better(metric_name: str) -> bool:
    """
    Return True when a larger value is better for this metric.

    The composite ``score`` is higher-is-better by construction; every other
    metric follows ``METRIC_DIRECTIONALITY`` (defaulting to lower-is-better).
    """
    if metric_name == _PRIMARY_ENDPOINT:
        return True
    return (
        METRIC_DIRECTIONALITY.get(metric_name, "lower_is_better")
        == "higher_is_better"
    )


def _is_degenerate(mc: MetricComparison) -> bool:
    """
    True when a metric has no paired signal (both arms identical per pair).

    Such an endpoint yields a Wilcoxon p of 1.0 and carries no information;
    keeping it in the Holm family would only inflate the multiplier and
    over-correct the endpoints that actually vary. It is still reported, but
    excluded from the correction family.
    """
    default_vals = np.asarray(mc.default.values, dtype=float)
    tuned_vals = np.asarray(mc.tuned.values, dtype=float)
    if default_vals.size == 0 or tuned_vals.size != default_vals.size:
        return True
    return bool(np.allclose(default_vals - tuned_vals, 0.0))


def _build_extractor(metric_name: str) -> Callable[[RunResult], float]:
    """Return a function that extracts the named metric from a RunResult."""
    extractors: dict[str, Callable[[RunResult], float]] = {
        "score": lambda r: r.score,
        "latency_p50": lambda r: r.metrics.latency_p50,
        "latency_p95": lambda r: r.metrics.latency_p95,
        "latency_p99": lambda r: r.metrics.latency_p99,
        "throughput": lambda r: r.metrics.throughput,
        "error_rate": lambda r: r.metrics.error_rate,
        "memory_utilization": lambda r: r.metrics.memory_utilization,
        "memory_pressure": lambda r: getattr(r.metrics, "memory_pressure", 0.0),
        "buffer_miss_rate": lambda r: getattr(r.metrics, "buffer_miss_rate", 0.0),
        "tail_amplification": lambda r: getattr(r.metrics, "tail_amplification", 0.0),
        "scan_efficiency": lambda r: getattr(r.metrics, "scan_efficiency", 0.0),
        "latency_variance": lambda r: getattr(r.metrics, "latency_variance", 0.0),
    }
    if metric_name not in extractors:
        raise ValueError(
            f"Unknown metric '{metric_name}'. Valid options: {sorted(extractors)}"
        )
    return extractors[metric_name]


def _holm_adjusted_pvalues(p_values: list[float]) -> list[float]:
    """
    Compute Holm-adjusted p-values and return them in original metric order.

    Holm step-down controls FWER while being less conservative than Bonferroni.
    """
    m = len(p_values)
    if m == 0:
        return []

    indexed = sorted(enumerate(p_values), key=lambda item: item[1])
    adjusted_sorted: list[float] = []

    running_max = 0.0
    for i, (_, p) in enumerate(indexed):
        factor = m - i
        adjusted = min(1.0, p * factor)
        running_max = max(running_max, adjusted)
        adjusted_sorted.append(running_max)

    adjusted_original = [1.0] * m
    for (original_idx, _), p_adj in zip(indexed, adjusted_sorted, strict=True):
        adjusted_original[original_idx] = p_adj

    return adjusted_original


def _build_power_warning(n_pairs: int) -> str | None:
    """Return a warning message when the paired sample size is low."""
    if n_pairs >= 8:
        return None
    if n_pairs == 5:
        return (
            "Low statistical power at N=5: minimum possible two-sided Wilcoxon "
            "p-value is 0.0625, so p<0.05 cannot be reached even before correction."
        )

    min_possible_p = min(1.0, 2.0 / (2.0**n_pairs))
    return (
        f"Low statistical power at N={n_pairs}: minimum possible two-sided "
        f"Wilcoxon p-value is {min_possible_p:.4f}."
    )
