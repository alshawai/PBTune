"""
Shared utilities for the unified tuners package.

These helpers are extracted (by copy) from the PBT and BO tuners so that new
strategies — starting with LHS-design sampling — can reuse the common
lifecycle plumbing without importing from either incumbent. The original
``src/tuner`` and ``src/scripts/bo_baseline`` packages are intentionally left
unmodified; see ADR-006.
"""

from src.tuners.utils.calibration import (
    MIN_OBSERVATIONS_FOR_RECALIBRATION,
    RecalibrationResult,
    maybe_recalibrate_scores,
    rescore_metrics_globally,
    workload_for_benchmark,
)
from src.tuners.utils.exceptions import (
    GenerationEvaluationError,
    KnobSpaceEmptyError,
    TunerConfigError,
    TunerError,
    TunerSetupError,
)
from src.tuners.utils.executors import WorkloadBundle, build_workload_bundle
from src.tuners.utils.knob_filter import (
    compute_unsupported_knobs,
    log_pruning_summary,
    query_runtime_supported_knobs,
)
from src.tuners.utils.output_paths import resolve_tuner_output_root
from src.tuners.utils.resources import resolve_worker_resources
from src.tuners.utils.session_writer import (
    TIMING_SCHEMA_VERSION,
    build_session_header,
    convert_numpy_types,
    worker_resources_to_dict,
    write_best_config_json,
    write_session_json,
)
from src.tuners.utils.types import (
    GenerationOutcome,
    TunerLifecycleConfig,
    TuningStrategy,
)

__all__ = [
    "MIN_OBSERVATIONS_FOR_RECALIBRATION",
    "RecalibrationResult",
    "maybe_recalibrate_scores",
    "rescore_metrics_globally",
    "workload_for_benchmark",
    "WorkloadBundle",
    "build_workload_bundle",
    "TunerError",
    "TunerConfigError",
    "TunerSetupError",
    "KnobSpaceEmptyError",
    "GenerationEvaluationError",
    "compute_unsupported_knobs",
    "log_pruning_summary",
    "query_runtime_supported_knobs",
    "resolve_tuner_output_root",
    "resolve_worker_resources",
    "TIMING_SCHEMA_VERSION",
    "build_session_header",
    "convert_numpy_types",
    "worker_resources_to_dict",
    "write_best_config_json",
    "write_session_json",
    "GenerationOutcome",
    "TunerLifecycleConfig",
    "TuningStrategy",
]
