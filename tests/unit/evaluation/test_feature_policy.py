"""
Tests for the evaluation workload-feature policy (ADR-007).

Behavioural coverage of :mod:`src.evaluation.feature_policy`:

- the prior is derived from the evaluation's effective benchmark params;
- it is byte-for-byte the prior a tuning run starts from, so no arm is
  advantaged by the choice;
- it is workload-conditioned, unlike the empty vector it replaced;
- provenance is recorded so the vector can be recomputed from the output;
- divergence against a session's persisted vector is reported, not scored.
"""

from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace

import pytest

from src.evaluation.feature_policy import (
    WORKLOAD_FEATURE_POLICY,
    WORKLOAD_FEATURE_POLICY_VERSION,
    describe_feature_divergence,
    resolve_evaluation_workload_features,
)
from src.evaluation.runner import ComparisonRunner, _metrics_to_score
from src.evaluation.types import ComparisonConfig
from src.tuners.utils.executors import build_workload_bundle
from src.utils.metrics import PerformanceMetrics, WorkloadType
from src.utils.scoring.policies import V2_WEIGHT_MODEL
from src.utils.types import BenchmarkConfig


def _config(**overrides) -> ComparisonConfig:
    """Build a ComparisonConfig with effective benchmark params filled in."""
    base = {
        "tuning_session_path": Path("session.json"),
        "benchmark": "sysbench",
        "sysbench_workload": "oltp_read_write",
        "sysbench_tables": 4,
        "sysbench_table_size": 1_000_000,
        "scale_factor": 1.0,
        "tpch_warmup_passes": 1,
    }
    base.update(overrides)
    return ComparisonConfig(**base)  # type: ignore[arg-type]


# ---------------------------------------------------------------------------
# Derivation
# ---------------------------------------------------------------------------


def test_sysbench_prior_reads_effective_params_and_executor_threads() -> None:
    """The vector reflects the eval's own params, not any session's."""
    resolved = resolve_evaluation_workload_features(
        benchmark="sysbench",
        executor=SimpleNamespace(threads=8),
        config=_config(),
        cpu_cores=8,
    )

    # oltp_read_write is a 75/25 read/write split per the extractor's table.
    assert resolved.features["read_ratio"] == pytest.approx(0.75)
    assert resolved.features["write_ratio"] == pytest.approx(0.25)
    # concurrency_pressure = threads / 16, clamped to 1.0
    assert resolved.features["concurrency_pressure"] == pytest.approx(0.5)
    # working_set_millions = tables * table_size / 1e6
    assert resolved.features["working_set_millions"] == pytest.approx(4.0)

    assert resolved.policy == WORKLOAD_FEATURE_POLICY
    assert resolved.policy_version == WORKLOAD_FEATURE_POLICY_VERSION
    assert resolved.inputs == {
        "script": "oltp_read_write",
        "threads": 8,
        "tables": 4,
        "table_size": 1_000_000,
    }


def test_sysbench_prior_tracks_workload_mode() -> None:
    """A write-only evaluation gets a write-heavy prior."""
    resolved = resolve_evaluation_workload_features(
        benchmark="sysbench",
        executor=SimpleNamespace(threads=16),
        config=_config(sysbench_workload="oltp_write_only"),
        cpu_cores=8,
    )
    assert resolved.features["write_ratio"] == pytest.approx(0.90)
    assert resolved.features["concurrency_pressure"] == pytest.approx(1.0)


def test_tpch_prior_is_olap_shaped_and_records_query_count() -> None:
    """TPC-H gets high OLAP complexity and join intensity."""
    queries = ["select a, count(*) from t join u on t.id = u.id group by a"] * 22
    resolved = resolve_evaluation_workload_features(
        benchmark="tpch",
        executor=SimpleNamespace(queries=queries),
        config=_config(benchmark="tpch", scale_factor=2.0, tpch_warmup_passes=2),
        cpu_cores=4,
    )

    assert resolved.features["olap_complexity"] > 0.5
    assert resolved.features["join_intensity"] > 0.5
    assert resolved.features["working_set_millions"] == pytest.approx(
        (2.0 * 8_661_245) / 1_000_000.0
    )
    assert resolved.inputs["scale_factor"] == pytest.approx(2.0)
    assert resolved.inputs["warmup_passes"] == 2
    assert resolved.inputs["query_count"] == 22


def test_unsupported_benchmark_is_rejected() -> None:
    """No silent empty-vector fallback for an unknown benchmark."""
    with pytest.raises(ValueError, match="sysbench"):
        resolve_evaluation_workload_features(
            benchmark="mongo",
            executor=SimpleNamespace(),
            config=_config(benchmark="mongo"),
            cpu_cores=2,
        )


# ---------------------------------------------------------------------------
# Fairness: the prior is what every tuner starts from
# ---------------------------------------------------------------------------


def test_sysbench_prior_matches_tuning_bundle_prior() -> None:
    """The eval prior equals the prior a tuning run extracts.

    This is the fairness argument for ADR-007: the shared rubric is not a
    third vector invented by the evaluation, it is the same static prior both
    PBT and BO began their search from.
    """
    bundle = build_workload_bundle(
        benchmark="sysbench",
        benchmark_config=BenchmarkConfig(
            benchmark="sysbench",
            workload_type="oltp",
            sysbench_workload="oltp_read_write",
            sysbench_tables=4,
            sysbench_table_size=1_000_000,
        ),
        workload_type=WorkloadType.OLTP,
        cpu_cores=8,
    )
    resolved = resolve_evaluation_workload_features(
        benchmark="sysbench",
        executor=SimpleNamespace(threads=int(getattr(bundle.executor, "threads", 8))),
        config=_config(),
        cpu_cores=8,
    )
    assert resolved.features == bundle.workload_features


def test_tpch_prior_matches_tuning_bundle_prior() -> None:
    """Same parity claim for the OLAP path."""
    bundle = build_workload_bundle(
        benchmark="tpch",
        benchmark_config=BenchmarkConfig(
            benchmark="tpch",
            workload_type="olap",
            scale_factor=1.0,
            warmup_passes=1,
        ),
        workload_type=WorkloadType.OLAP,
        cpu_cores=8,
    )
    resolved = resolve_evaluation_workload_features(
        benchmark="tpch",
        executor=bundle.executor,
        config=_config(benchmark="tpch", scale_factor=1.0, tpch_warmup_passes=1),
        cpu_cores=8,
    )
    assert resolved.features == bundle.workload_features


# ---------------------------------------------------------------------------
# Why the empty vector was wrong
# ---------------------------------------------------------------------------


def test_resolved_prior_restores_workload_conditioning_of_weights() -> None:
    """An empty vector weights OLTP and OLAP identically; the prior does not.

    Under ``feature_driven_v2`` the weights are a function of the feature
    vector alone, so with no features TPC-H and sysbench share one rubric
    and ``scan_efficiency`` — the metric an OLAP run most depends on — is
    left near its suppressed base logit.
    """
    empty_weights = V2_WEIGHT_MODEL.compute_weights({}, log_weights=False)

    oltp = resolve_evaluation_workload_features(
        benchmark="sysbench",
        executor=SimpleNamespace(threads=8),
        config=_config(),
        cpu_cores=8,
    )
    olap = resolve_evaluation_workload_features(
        benchmark="tpch",
        executor=SimpleNamespace(queries=None),
        config=_config(benchmark="tpch"),
        cpu_cores=8,
    )
    oltp_weights = V2_WEIGHT_MODEL.compute_weights(oltp.features, log_weights=False)
    olap_weights = V2_WEIGHT_MODEL.compute_weights(olap.features, log_weights=False)

    # The two workloads no longer share a rubric.
    assert oltp_weights != olap_weights

    # scan_efficiency is the clearest case: suppressed without features,
    # dominant-tier for OLAP once the prior is supplied.
    assert olap_weights["scan_efficiency"] > 5 * empty_weights["scan_efficiency"]
    assert olap_weights["scan_efficiency"] > oltp_weights["scan_efficiency"]


# ---------------------------------------------------------------------------
# Provenance and divergence reporting
# ---------------------------------------------------------------------------


def test_as_metadata_records_policy_and_inputs() -> None:
    """Acceptance criterion: the output states which vector/policy was used."""
    resolved = resolve_evaluation_workload_features(
        benchmark="sysbench",
        executor=SimpleNamespace(threads=8),
        config=_config(),
        cpu_cores=8,
    )
    metadata = resolved.as_metadata()

    assert metadata["workload_feature_policy"] == WORKLOAD_FEATURE_POLICY
    assert metadata["workload_feature_policy_version"] == (
        WORKLOAD_FEATURE_POLICY_VERSION
    )
    assert "extract_sysbench_features" in metadata["workload_feature_source"]
    assert metadata["workload_feature_inputs"]["tables"] == 4


def test_divergence_is_empty_for_a_matching_session_vector() -> None:
    """A never-refining session (BO, LHS) matches the eval prior exactly."""
    resolved = resolve_evaluation_workload_features(
        benchmark="sysbench",
        executor=SimpleNamespace(threads=8),
        config=_config(),
        cpu_cores=8,
    )
    assert describe_feature_divergence(resolved.features, resolved.features) == {}


def test_divergence_reports_moved_features_both_ways() -> None:
    """A PBT session that moved its features is reported, with both values."""
    eval_features = {"concurrency_pressure": 0.5, "tail_latency_sensitivity": 0.55}
    session_features = {
        "concurrency_pressure": 0.118,
        "tail_latency_sensitivity": 0.55,
    }

    divergence = describe_feature_divergence(eval_features, session_features)

    assert set(divergence) == {"concurrency_pressure"}
    session_value, eval_value = divergence["concurrency_pressure"]
    assert session_value == pytest.approx(0.118)
    assert eval_value == pytest.approx(0.5)


def test_divergence_handles_absent_session_vector() -> None:
    """A default arm (or a legacy session) has no vector to compare."""
    assert describe_feature_divergence({"read_ratio": 1.0}, {}) == {}


def test_divergence_flags_features_present_on_only_one_side() -> None:
    """A feature missing from one vector counts as divergence, not equality."""
    divergence = describe_feature_divergence(
        {"read_ratio": 1.0}, {"read_ratio": 1.0, "join_intensity": 0.9}
    )
    assert set(divergence) == {"join_intensity"}
    assert divergence["join_intensity"] == (pytest.approx(0.9), pytest.approx(0.0))


# ---------------------------------------------------------------------------
# Runner wiring
# ---------------------------------------------------------------------------


def test_runner_resolves_prior_and_logs_session_divergence(caplog) -> None:
    """``_resolve_scoring_features`` returns the prior and reports divergence.

    Mirrors the real PBT-vs-BO case: the BO arm's persisted vector matches the
    evaluation prior, the PBT arm's has moved, and the default arm has none.
    """
    runner = ComparisonRunner(_config())
    expected = resolve_evaluation_workload_features(
        benchmark="sysbench",
        executor=SimpleNamespace(threads=8),
        config=runner.config,
        cpu_cores=8,
    )
    moved = {**expected.features, "concurrency_pressure": 0.118}

    with caplog.at_level("INFO"):
        resolved = runner._resolve_scoring_features(
            benchmark="sysbench",
            executor=SimpleNamespace(threads=8),
            cpu_cores=8,
            session_vectors={
                "pbt": moved,
                "bo": dict(expected.features),
                "default": {},
            },
        )

    assert resolved.features == expected.features

    log_text = caplog.text
    assert "eval_static_prior" in log_text
    assert "pbt session vector diverges" in log_text
    assert "bo session vector matches" in log_text
    assert "default session recorded no workload features" in log_text


def test_supplied_vector_changes_the_intermediate_score() -> None:
    """The per-run score depends on the vector, so threading it matters.

    Identical raw metrics scored under the TPC-H prior and under the empty
    vector must differ — otherwise passing the prior through the run loops
    would be cosmetic.
    """
    metrics = PerformanceMetrics(
        throughput=100.0,
        latency_p50=50.0,
        latency_p95=120.0,
        latency_p99=400.0,
        error_rate=0.0,
        scan_efficiency=0.2,
        buffer_miss_rate=0.4,
        memory_pressure=0.3,
    )
    olap_prior = resolve_evaluation_workload_features(
        benchmark="tpch",
        executor=SimpleNamespace(queries=None),
        config=_config(benchmark="tpch"),
        cpu_cores=8,
    )

    with_prior = _metrics_to_score(
        metrics,
        "tpch",
        scoring_policy="feature_driven_v2",
        scoring_policy_version="2.0",
        metric_reference_version="v2",
        workload_features=olap_prior.features,
    )
    without_prior = _metrics_to_score(
        metrics,
        "tpch",
        scoring_policy="feature_driven_v2",
        scoring_policy_version="2.0",
        metric_reference_version="v2",
        workload_features=None,
    )

    assert with_prior != pytest.approx(without_prior)
