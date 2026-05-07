"""
Exceptions for the evaluate_tuning module.
==========================================

Domain-specific exception hierarchy. All exceptions derive from
`EvaluationError` so callers can catch the full domain with a
single clause while still distinguishing specific failure modes.
"""


class EvaluationError(Exception):
    """Base exception for all evaluation failures."""


class TuningSessionLoadError(EvaluationError):
    """
    Raised when a PBT tuning session results file cannot be loaded.

    Covers missing files, malformed JSON, and missing required fields
    (best_configuration, worker_resources, tuning_session).
    """


class ScoringMetadataSchemaError(TuningSessionLoadError):
    """
    Raised when scoring metadata in a tuning session has an invalid schema.

    Covers malformed payloads such as non-object workload features,
    non-object normalization metadata, or unsupported score breakdown types.
    """


class DockerEnvironmentError(EvaluationError):
    """
    Raised when the Docker evaluation environment cannot be set up.

    Covers Docker daemon not running, image build failures, and
    resource-limit configuration errors.
    """


class BenchmarkExecutionError(EvaluationError):
    """
    Raised when a benchmark run fails inside an evaluation container.

    Covers sysbench/TPC-H execution errors, PostgreSQL failures inside
    the container, and result-parsing failures.
    """


class KnobApplicationError(EvaluationError):
    """
    Raised when a tuned knob configuration cannot be applied.

    Covers ALTER SYSTEM failures, pg_ctl restart timeouts, and
    configuration verification mismatches inside a container.
    """
