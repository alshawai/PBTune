"""
Type definitions for the evaluate_tuning module.
=================================================

All public dataclasses used across the module live here to avoid
circular imports. Import order: types → exceptions → everything else.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Optional

from src.utils.hardware_info import WorkerResources
from src.utils.metrics import PerformanceMetrics
from src.utils.scoring.contracts import ScoreBreakdown


@dataclass
class ComparisonConfig:
    """
    Full configuration for a comparative benchmark evaluation.

    Attributes:
        tuning_session_path: Path to the PBT results JSON file.
        benchmark: Benchmark type — `"sysbench"` or `"tpch"`.
            `None` (default) to auto-detect from the tuning session.
        repetitions: Number of independent runs per configuration.
            Default 5, consistent with OtterTune/CDBTune methodology.
        scale_factor: TPC-H scale factor.
            None means resolve from session metadata or benchmark default.
        sysbench_duration: Sysbench measurement duration in seconds.
            None means resolve from session metadata or benchmark default.
        sysbench_tables: Number of sysbench tables.
            None means resolve from session metadata or benchmark default.
        sysbench_table_size: Rows per sysbench table.
            None means resolve from session metadata or benchmark default.
        sysbench_warmup_seconds: Sysbench warmup duration in seconds.
            None means resolve from session metadata or benchmark default.
        tpch_warmup_passes: Number of warmup passes before TPC-H measurement.
            None means resolve from session metadata or benchmark default.
        pair_seed: Base deterministic seed used to build run-pair seeds.
        use_docker: If True (default), use Docker containers for isolation.
            Falls back to bare-metal when False.
        docker_image: Name/tag of the Docker image to build and run.
        output_dir: Override the default output directory. If None, saves
            comparison JSON to results/{workload}/comparisons/{tier} and
            HTML logs to results/{workload}/comparisons/{tier}/logs.
    """

    tuning_session_path: Path
    benchmark: Optional[str] = None
    repetitions: int = 5
    scale_factor: Optional[float] = None
    sysbench_duration: Optional[int] = None
    sysbench_tables: Optional[int] = None
    sysbench_table_size: Optional[int] = None
    sysbench_workload: Optional[str] = None
    sysbench_warmup_seconds: Optional[int] = None
    tpch_warmup_passes: Optional[int] = None
    pair_seed: int = 50_000
    use_docker: bool = True
    docker_image: str = "pbt-eval"
    output_dir: Optional[Path] = None
    scoring_policy: Optional[str] = None
    scoring_policy_version: Optional[str] = None
    metric_reference_version: Optional[str] = None
    bo_session_path: Optional[Path] = None
    data_dir: Optional[str] = None
    colocate_output: bool = False


@dataclass
class TuningSessionData:
    """
    Parsed content of a PBT tuning session results JSON file.

    Attributes:
        best_knobs: Best discovered knob configuration (name → value).
        best_score: Composite score of the best configuration.
        worker_resources: CPU/RAM/disk constraints from the tuning run.
        system_info: Original hardware information dict.
        tuning_config: PBT configuration metadata (tier, generations, etc.).
        benchmark: Benchmark used during tuning ("sysbench" or "tpch").
        workload_type: Workload type string ("OLTP", "OLAP", or "MIXED").
        session_id: Timestamp-based session identifier.
        scoring_policy: Scoring policy identifier used during tuning.
        scoring_policy_version: Version identifier for the scoring policy.
        metric_reference_version: Version of metric schema used for scoring.
        workload_features: Feature vector metadata persisted by the tuner.
        normalization_metadata: Normalization state metadata for rescoring.
        score_breakdown: Best-configuration score breakdown.
    """

    best_knobs: dict[str, Any]
    best_score: float
    worker_resources: WorkerResources
    system_info: dict[str, Any]
    tuning_config: dict[str, Any]
    benchmark: str
    workload_type: str
    session_id: str
    sysbench_workload: Optional[str] = None
    knob_source: str = "expert"
    scoring_policy: str = "fixed_v1"
    scoring_policy_version: str = "1.0"
    metric_reference_version: str = "v1"
    workload_features: dict[str, Any] = field(default_factory=dict)
    normalization_metadata: dict[str, Any] = field(default_factory=dict)
    score_breakdown: ScoreBreakdown = field(
        default_factory=lambda: ScoreBreakdown(final_score=0.0)
    )


@dataclass
class RunResult:
    """
    Result from a single benchmark run (one repetition, one config type).

    Attributes:
        config_type: "default" or "tuned".
        run_number: 1-based repetition index.
        pair_seed: Deterministic workload seed used by both pair members.
        order_in_pair: Position in pair execution order (1 or 2).
        metrics: Raw performance snapshot from this run.
        score: Composite performance score in [0, 100].
        duration_seconds: Total wall-clock time for this run including
            container setup, benchmark, and teardown.
        container_id: Docker container ID (or "bare-metal").
    """

    config_type: str
    run_number: int
    pair_seed: int
    order_in_pair: int
    metrics: PerformanceMetrics
    score: float
    duration_seconds: float
    container_id: str = "bare-metal"


@dataclass
class StatSummary:
    """
    Statistical summary for a collection of scalar measurements.

    Attributes:
        mean: Arithmetic mean.
        std: Sample standard deviation.
        median: Median.
        iqr_lower: 25th percentile.
        iqr_upper: 75th percentile.
        values: Raw values (needed for bootstrap resampling).
    """

    mean: float
    std: float
    median: float
    iqr_lower: float
    iqr_upper: float
    values: list[float] = field(default_factory=list)


@dataclass
class MetricComparison:
    """
    Statistical comparison for a single performance metric.

    Attributes:
        metric_name: Human-readable name (e.g. "score", "latency_p95_ms").
        default: Summary statistics for the default configuration.
        tuned: Summary statistics for the tuned configuration.
        improvement_pct: Median-based percent improvement.
            Positive = tuned is better; negative = tuned is worse.
            For latency, improvement = (default - tuned) / default × 100.
            For throughput/score, improvement = (tuned - default) / default × 100.
        improvement_ci: Bootstrap 95% CI on the paired median difference (lower, upper).
        p_value: Wilcoxon signed-rank test p-value (two-sided, paired).
        p_value_corrected: Corrected p-value for endpoint family control.
        cohens_d: Paired Cohen's d effect size.
        significant: True if the endpoint is statistically significant.
        higher_is_better: True for throughput/score; False for latency/error_rate.
        endpoint_role: "primary" or "secondary".
        correction_method: Name of correction used for this metric (if any).
    """

    metric_name: str
    default: StatSummary
    tuned: StatSummary
    improvement_pct: float
    improvement_ci: tuple[float, float]
    p_value: float
    p_value_corrected: float
    cohens_d: float
    significant: bool
    higher_is_better: bool
    endpoint_role: str = "secondary"
    correction_method: Optional[str] = None


@dataclass
class ComparisonStatistics:
    """
    Full statistical comparison between default and tuned configurations.

    Attributes:
        metrics: Per-metric statistical comparisons.
        significant_metrics: Names of metrics that pass significance thresholds.
        overall_improvement_pct: Score-based overall improvement percentage.
        overall_improvement_ci: Bootstrap 95% CI on the score improvement.
        n_pairs: Number of paired runs used in statistical tests.
        correction_method: Summary name of correction strategy.
        power_warning: Optional low-power warning for small sample sizes.
        alpha: Significance level used for primary and secondary tests.
        primary_endpoint: Primary endpoint name.
        secondary_endpoints: Secondary endpoint names subject to correction.
        primary_significant: Whether the primary endpoint is significant.
        secondary_correction_method: Secondary family correction method.
    """

    metrics: list[MetricComparison]
    significant_metrics: list[str]
    overall_improvement_pct: float
    overall_improvement_ci: tuple[float, float]
    n_pairs: int = 0
    correction_method: str = "bonferroni"
    power_warning: Optional[str] = None
    alpha: float = 0.05
    primary_endpoint: str = "score"
    secondary_endpoints: list[str] = field(default_factory=list)
    primary_significant: bool = False
    secondary_correction_method: str = "holm"


@dataclass
class ComparisonResult:
    """
    Complete output of one comparative benchmark evaluation.

    This is the object serialized to the comparison_{timestamp}.json file.

    Attributes:
        default_runs: All N repetitions with the default configuration.
        tuned_runs: All N repetitions with the tuned configuration.
        tuned_knobs: The knob configuration that was evaluated.
        statistics: Full non-parametric statistical comparison.
        config: The ComparisonConfig used to produce this result.
        session_data: Parsed metadata from the tuning session.
        timestamp: ISO-8601 timestamp when the comparison was run.
        output_path: Path where the JSON result was saved.
        log_path: Path where the HTML session log was saved.
        scoring_metadata: Rescoring provenance and normalization ranges.
        session_scoring_metadata: Scoring metadata loaded from tuning session.
    """

    default_runs: list[RunResult]
    tuned_runs: list[RunResult]
    tuned_knobs: dict[str, Any]
    statistics: ComparisonStatistics
    config: ComparisonConfig
    session_data: TuningSessionData
    timestamp: str
    output_path: Optional[Path] = None
    log_path: Optional[Path] = None
    scoring_metadata: Optional[dict[str, Any]] = None
    session_scoring_metadata: dict[str, Any] = field(default_factory=dict)


@dataclass
class PairwiseResult:
    """
    Statistical comparison between two named arms.

    Attributes:
        arm_a: Baseline arm name (e.g. "default").
        arm_b: Comparator arm name (e.g. "pbt").
        statistics: Full Wilcoxon / bootstrap / Holm statistics for this pair.
    """

    arm_a: str
    arm_b: str
    statistics: ComparisonStatistics


@dataclass
class MultiArmComparisonResult:
    """
    Complete output of a multi-arm comparative benchmark evaluation.

    Supports 2+ configuration arms evaluated under identical conditions
    (same seeds, same environment lifecycle per repetition).

    Attributes:
        runs_by_arm: Mapping from arm name to its list of RunResults.
        knobs_by_arm: Mapping from arm name to its knob configuration dict.
        pairwise_statistics: C(k,2) pairwise statistical comparisons.
        config: The ComparisonConfig used to produce this result.
        session_data: Parsed metadata from the primary (PBT) tuning session.
        bo_session_data: Parsed metadata from the BO session (None for 2-way).
        timestamp: ISO-8601 timestamp when the comparison was run.
        output_path: Path where the JSON result was saved.
        log_path: Path where the HTML session log was saved.
        scoring_metadata: Rescoring provenance and normalization ranges.
        session_scoring_metadata: Scoring metadata loaded from tuning sessions.
    """

    runs_by_arm: dict[str, list[RunResult]]
    knobs_by_arm: dict[str, dict[str, Any]]
    pairwise_statistics: list[PairwiseResult]
    config: ComparisonConfig
    session_data: TuningSessionData
    bo_session_data: Optional[TuningSessionData] = None
    timestamp: str = ""
    output_path: Optional[Path] = None
    log_path: Optional[Path] = None
    scoring_metadata: Optional[dict[str, Any]] = None
    session_scoring_metadata: dict[str, Any] = field(default_factory=dict)
