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

import math
from typing import Callable

import numpy as np
import scipy.stats as stats

from src.utils.logger import get_logger
from src.utils.scoring.constants import METRIC_DIRECTIONALITY
from src.evaluation.types import (
    ComparisonStatistics,
    MetricComparison,
    RunResult,
    StatSummary,
)

LOGGER = get_logger(__name__)

_PRIMARY_ENDPOINT = "score"
_SECONDARY_ENDPOINT_BASE = (
    "throughput",
    "memory_utilization",
    "memory_pressure",
    "buffer_miss_rate",
    "tail_amplification",
    "scan_efficiency",
    "latency_variance",
)

# Number of bootstrap resamples for CI estimation
_N_BOOTSTRAP = 10_000

# Family-wise significance level before correction
_ALPHA = 0.05


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

    latency_endpoint = "latency_p99" if benchmark == "tpch" else "latency_p95"
    secondary_endpoints = (latency_endpoint, *_SECONDARY_ENDPOINT_BASE)

    metric_comparisons: list[MetricComparison] = []

    primary_extractor = _build_extractor(_PRIMARY_ENDPOINT)
    primary_default = [primary_extractor(r) for r in default_sorted]
    primary_tuned = [primary_extractor(r) for r in tuned_sorted]
    primary_metric = _compare_metric(
        metric_name=_PRIMARY_ENDPOINT,
        default_vals=primary_default,
        tuned_vals=primary_tuned,
        higher_is_better=True,
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

    secondary_metrics: list[MetricComparison] = []
    for metric_name in secondary_endpoints:
        # Determine directionality from METRIC_DIRECTIONALITY, default to lower_is_better
        directionality = METRIC_DIRECTIONALITY.get(metric_name, "lower_is_better")
        higher_is_better = directionality == "higher_is_better"
        extractor = _build_extractor(metric_name)
        default_vals = [extractor(r) for r in default_sorted]
        tuned_vals = [extractor(r) for r in tuned_sorted]

        mc = _compare_metric(
            metric_name=metric_name,
            default_vals=default_vals,
            tuned_vals=tuned_vals,
            higher_is_better=higher_is_better,
            endpoint_role="secondary",
            alpha=alpha,
        )
        secondary_metrics.append(mc)

    secondary_adjusted = _holm_adjusted_pvalues(
        [mc.p_value for mc in secondary_metrics]
    )
    _apply_significance(
        metrics=secondary_metrics,
        adjusted_p_values=secondary_adjusted,
        alpha=alpha,
        correction_method="holm",
    )
    metric_comparisons.extend(secondary_metrics)

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

    # Overall improvement uses the score metric
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
        secondary_endpoints=list(secondary_endpoints),
        primary_significant=score_mc.significant,
        secondary_correction_method="holm",
    )


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
        return 0.0 if mean_diff == 0.0 else math.copysign(float("inf"), mean_diff)
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


def _build_extractor(metric_name: str) -> Callable[[RunResult], float]:
    """Return a function that extracts the named metric from a RunResult."""
    extractors: dict[str, Callable[[RunResult], float]] = {
        "score": lambda r: r.score,
        "latency_p95": lambda r: r.metrics.latency_p95,
        "latency_p99": lambda r: r.metrics.latency_p99,
        "throughput": lambda r: r.metrics.throughput,
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
