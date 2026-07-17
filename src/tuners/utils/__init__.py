"""
Shared utilities for the unified tuners package.

These helpers hold the common lifecycle plumbing shared by every strategy
(PBT and LHS-design today, BO next). PBT has been migrated onto this framework
and the legacy ``src/tuner`` package removed; BO (``src/scripts/bo_baseline``)
is not yet migrated. See ADR-006 and its 2026-07-17 addendum.
"""

from src.tuners.utils.exceptions import (
    GenerationEvaluationError,
    KnobSpaceEmptyError,
    TunerConfigError,
    TunerError,
    TunerSetupError,
)
from src.tuners.utils.executors import WorkloadBundle, build_workload_bundle
from src.tuners.utils.knob_filter import (
    apply_tuning_mode_filter,
    compute_unsupported_knobs,
    log_pruning_summary,
    query_runtime_supported_knobs,
)
from src.tuners.utils.metrics_table import build_worker_metric_row
from src.tuners.utils.output_paths import resolve_tuner_output_root
from src.tuners.utils.resources import resolve_worker_resources
from src.tuners.utils.session_writer import (
    TIMING_SCHEMA_VERSION,
    build_scoring_block,
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
    WorkerEvalResult,
)

__all__ = [
    "WorkloadBundle",
    "build_workload_bundle",
    "TunerError",
    "TunerConfigError",
    "TunerSetupError",
    "KnobSpaceEmptyError",
    "GenerationEvaluationError",
    "apply_tuning_mode_filter",
    "compute_unsupported_knobs",
    "log_pruning_summary",
    "query_runtime_supported_knobs",
    "build_worker_metric_row",
    "resolve_tuner_output_root",
    "resolve_worker_resources",
    "TIMING_SCHEMA_VERSION",
    "build_scoring_block",
    "build_session_header",
    "convert_numpy_types",
    "worker_resources_to_dict",
    "write_best_config_json",
    "write_session_json",
    "GenerationOutcome",
    "TunerLifecycleConfig",
    "TuningStrategy",
    "WorkerEvalResult",
]
