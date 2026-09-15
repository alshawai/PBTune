"""
Evaluation Workload-Feature Policy
==================================

Resolves the ``workload_features`` vector used to score every arm of a
comparative evaluation, and carries the provenance of that choice into the
comparison output.

Why this module exists
----------------------
Under the ``feature_driven_v2`` scoring policy the composite metric weights are
a pure function of the workload-feature vector and the policy's base logits
(see :meth:`src.utils.scoring.scorer.CompositeScorer._resolve_weights`). The
normalizer stays workload-conditioned, but the *weights* are not: two different
feature vectors produce two different rubrics over the same raw metrics.

That makes the vector a first-class experimental parameter of the evaluation,
and the arms do not agree on one:

- **BO** persists the static vector extracted from its benchmark parameters.
- **PBT** persists a *moved* vector. Mid-session feature movement is deliberate
  PBT design — the population adapts its own objective as it learns, which is
  the database-tuning analogue of PBT's hyperparameter schedules — so the
  vector a PBT session ends with is a property of that run's search path.
- A **default** arm has no session and therefore no vector at all.

Grading a head-to-head with any one session's vector would hand that arm a
co-adapted rubric. Grading with no vector at all (the prior behaviour, passing
``workload_features=None``) silently falls through to an empty dict, because
``OLTP_METRIC_CONFIG``/``OLAP_METRIC_CONFIG``/``MIXED_METRIC_CONFIG`` never
define feature priors — which yields one feature-blind rubric shared by every
workload, under-weighting ``scan_efficiency`` roughly 11x on TPC-H.

The policy implemented here is ``eval_static_prior``: re-extract the static
prior from the evaluation's **own effective benchmark parameters**, the same way
:func:`src.tuners.utils.executors.build_workload_bundle` extracts it at the
start of a tuning run. The resulting vector is symmetric across arms,
workload-conditioned, reproducible from the comparison JSON alone, and
independent of any tuner's search path. It is also, by construction, the prior
that every arm's tuner started from.

See ``docs/architecture/decisions/ADR-007-evaluation-workload-feature-policy.md``.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

from src.evaluation.types import ComparisonConfig
from src.utils.scoring.workload_features import WorkloadFeatureExtractor

#: Identifier recorded in the comparison output under
#: ``scoring_metadata.workload_feature_policy``.
WORKLOAD_FEATURE_POLICY: str = "eval_static_prior"

#: Version of the policy itself. Bump when the derivation rule changes, so old
#: comparison JSONs stay interpretable.
WORKLOAD_FEATURE_POLICY_VERSION: str = "1.0"

#: Default sysbench client-thread count, mirroring ``SysbenchExecutor``.
_DEFAULT_SYSBENCH_THREADS: int = 8


@dataclass(frozen=True)
class ResolvedWorkloadFeatures:
    """
    The feature vector an evaluation scores with, plus how it was derived.

    Attributes:
        features: The resolved workload-feature vector. Applied identically to
            every arm.
        policy: Policy identifier (:data:`WORKLOAD_FEATURE_POLICY`).
        policy_version: Policy version (:data:`WORKLOAD_FEATURE_POLICY_VERSION`).
        source: Human-readable derivation, e.g.
            ``"extractor.extract_sysbench_features(eval_effective_params)"``.
        inputs: The effective benchmark parameters the vector was extracted
            from, so the vector can be recomputed from the comparison JSON.
    """

    features: dict[str, float]
    policy: str = WORKLOAD_FEATURE_POLICY
    policy_version: str = WORKLOAD_FEATURE_POLICY_VERSION
    source: str = ""
    inputs: dict[str, Any] = field(default_factory=dict)

    def as_metadata(self) -> dict[str, Any]:
        """Render the provenance fields for ``scoring_metadata``."""
        return {
            "workload_feature_policy": self.policy,
            "workload_feature_policy_version": self.policy_version,
            "workload_feature_source": self.source,
            "workload_feature_inputs": dict(self.inputs),
        }


def resolve_evaluation_workload_features(
    *,
    benchmark: str,
    executor: Any,
    config: ComparisonConfig,
    cpu_cores: int | None = None,
) -> ResolvedWorkloadFeatures:
    """
    Derive the evaluation's workload-feature prior from its own parameters.

    ``config`` must already carry the *effective* benchmark parameters (the
    CLI → session → default precedence resolved by
    ``ComparisonRunner._resolve_effective_benchmark_params``), because the
    rubric should describe the workload that is actually measured, not the one
    the source session happened to tune against.

    Args:
        benchmark: Benchmark driver — ``"sysbench"`` or ``"tpch"``.
        executor: The evaluation's benchmark executor. Supplies the sysbench
            client-thread count and the TPC-H query texts so the extraction
            matches what a tuning run would have produced.
        config: Comparison config holding the effective benchmark parameters.
        cpu_cores: Detected core count, forwarded for parity with the tuning
            path's extraction call.

    Returns:
        A :class:`ResolvedWorkloadFeatures` carrying the vector and provenance.

    Raises:
        ValueError: If ``benchmark`` is neither ``"sysbench"`` nor ``"tpch"``.
    """
    extractor = WorkloadFeatureExtractor()

    if benchmark == "sysbench":
        script = str(config.sysbench_workload or "oltp_read_write")
        tables = int(config.sysbench_tables or 10)
        table_size = int(config.sysbench_table_size or 100_000)
        threads = int(getattr(executor, "threads", _DEFAULT_SYSBENCH_THREADS))
        features = extractor.extract_sysbench_features(
            script=script,
            threads=threads,
            cpu_cores=int(cpu_cores or 1),
            table_size=table_size,
            tables=tables,
        )
        return ResolvedWorkloadFeatures(
            features=features,
            source="extractor.extract_sysbench_features(eval_effective_params)",
            inputs={
                "script": script,
                "threads": threads,
                "tables": tables,
                "table_size": table_size,
            },
        )

    if benchmark == "tpch":
        scale_factor = float(config.scale_factor or 1.0)
        warmup_passes = int(config.tpch_warmup_passes or 1)
        queries = getattr(executor, "queries", None)
        features = extractor.extract_tpch_features(
            scale_factor=scale_factor,
            warmup_passes=warmup_passes,
            queries=list(queries) if queries else None,
        )
        return ResolvedWorkloadFeatures(
            features=features,
            source="extractor.extract_tpch_features(eval_effective_params)",
            inputs={
                "scale_factor": scale_factor,
                "warmup_passes": warmup_passes,
                "query_count": len(queries) if queries else 0,
            },
        )

    raise ValueError(
        f"Cannot resolve workload features for benchmark '{benchmark}'. "
        "Expected 'sysbench' or 'tpch'."
    )


def describe_feature_divergence(
    eval_features: dict[str, float],
    session_features: dict[str, float],
    *,
    tolerance: float = 1e-6,
) -> dict[str, tuple[float, float]]:
    """
    Report features where a session's persisted vector differs from the
    evaluation prior.

    Used for reporting only — the divergence is expected for PBT sessions
    (mid-session feature movement is intended behaviour) and is surfaced so a
    reader can see which parts of the tuning rubric the evaluation does not
    reproduce.

    Args:
        eval_features: The evaluation prior actually used for scoring.
        session_features: A session's persisted ``scoring.workload_features``.
        tolerance: Absolute difference below which a feature counts as equal.

    Returns:
        Mapping of feature name → ``(session_value, eval_value)`` for every
        feature that differs or is present in only one of the two vectors.
    """
    if not session_features:
        return {}

    divergence: dict[str, tuple[float, float]] = {}
    for name in sorted(set(eval_features) | set(session_features)):
        session_value = float(session_features.get(name, 0.0) or 0.0)
        eval_value = float(eval_features.get(name, 0.0) or 0.0)
        if abs(session_value - eval_value) > tolerance:
            divergence[name] = (session_value, eval_value)
    return divergence
