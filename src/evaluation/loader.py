"""
Tuning Session Loader
=====================

Loads and validates PBT tuning session result JSON files, extracting
the best knob configuration, resource constraints, and benchmark metadata
needed to drive a comparative evaluation.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any, Optional

from src.utils.logger import get_logger
from src.evaluation.exceptions import (
    ScoringMetadataSchemaError,
    TuningSessionLoadError,
)
from src.evaluation.types import TuningSessionData, WorkerResources
from src.benchmarks.sysbench.executor import (
    DEFAULT_SYSBENCH_WORKLOAD,
    validate_sysbench_workload,
)
from src.utils.scoring.constants import (
    DEFAULT_METRIC_REFERENCE_VERSION,
    DEFAULT_SCORING_POLICY,
    DEFAULT_SCORING_POLICY_VERSION,
)
from src.utils.scoring.contracts import score_breakdown_from_dict

LOGGER = get_logger("Loader")

# Fields that MUST be present in the results JSON
_REQUIRED_TOP_LEVEL = {"best_configuration", "worker_resources", "tuning_session"}
_REQUIRED_BEST_CONFIG = {"knobs", "score"}
_REQUIRED_WORKER_RES = {"ram_bytes", "cpu_cores", "disk_type"}


def load_tuning_session(path: Path) -> TuningSessionData:
    """
    Load a PBT tuning session results JSON and extract evaluation inputs.

    Parses the following fields from the results file:

        best_configuration.knobs   → best knob config
        best_configuration.score   → best composite score
        worker_resources           → CPU / RAM / disk constraints
        system_info                → original hardware snapshot
        tuning_session             → PBT metadata (tier, benchmark, etc.)

    Args:
        path: Path to a `pbt_results_{timestamp}.json` file produced by
            `src/tuner/main.py`.

    Returns:
        TuningSessionData populated with all fields required to run an
        evaluation.

    Raises:
        TuningSessionLoadError: If the file does not exist, contains invalid
            JSON, or is missing any required field.
    """
    path = Path(path)
    LOGGER.info("Loading tuning session from: %s", path)

    LOGGER.debug("  Checking if correct tuning session file exists...")
    if not path.exists():
        raise TuningSessionLoadError(
            f"Tuning session file not found: {path}\n"
            "    Expected path: results/{{workload}}/pbt_runs/{{tier}}/tuning_sessions/"
            "pbt_results_{{timestamp}}.json"
        )

    try:
        with path.open("r", encoding="utf-8") as fh:
            data: dict[str, Any] = json.load(fh)
    except json.JSONDecodeError as exc:
        raise TuningSessionLoadError(
            f"Failed to parse JSON from {path}: {exc}"
        ) from exc
    except OSError as exc:
        raise TuningSessionLoadError(f"Cannot read file {path}: {exc}") from exc

    if not isinstance(data, dict):
        raise TuningSessionLoadError(
            f"Expected a JSON object at root, got {type(data).__name__}"
        )
    LOGGER.debug("  ➤ Correct session file exist.")

    LOGGER.debug("  Checking if required fields are present...")
    _assert_fields(data, _REQUIRED_TOP_LEVEL, context="root")

    best_config = data["best_configuration"]
    _assert_fields(best_config, _REQUIRED_BEST_CONFIG, context="best_configuration")

    best_knobs: dict[str, Any] = best_config["knobs"]
    best_score: float = float(best_config["score"])

    if not isinstance(best_knobs, dict) or not best_knobs:
        raise TuningSessionLoadError(
            "best_configuration.knobs must be a non-empty dict"
        )

    wr_raw = data["worker_resources"]
    _assert_fields(wr_raw, _REQUIRED_WORKER_RES, context="worker_resources")
    LOGGER.debug("  ➤ Required fields are present.")

    LOGGER.debug("  Checking if worker resources are valid...")
    worker_resources = WorkerResources(
        ram_bytes=int(wr_raw["ram_bytes"]),
        cpu_cores=int(wr_raw["cpu_cores"]),
        disk_type=str(wr_raw["disk_type"]),
        disk_read_bps=int(wr_raw.get("disk_read_bps", 0) or 0),
        disk_write_bps=int(wr_raw.get("disk_write_bps", 0) or 0),
        disk_read_iops=int(wr_raw.get("disk_read_iops", 0) or 0),
        disk_write_iops=int(wr_raw.get("disk_write_iops", 0) or 0),
        disk_class=str(wr_raw.get("disk_class", "unknown") or "unknown"),
    )

    if worker_resources.ram_bytes <= 0:
        raise TuningSessionLoadError(
            f"worker_resources.ram_bytes must be positive, got {worker_resources.ram_bytes}"
        )
    if worker_resources.cpu_cores <= 0:
        raise TuningSessionLoadError(
            f"worker_resources.cpu_cores must be positive, got {worker_resources.cpu_cores}"
        )
    LOGGER.debug("  ➤ Worker resources are valid.")

    LOGGER.debug("  Inferring tuning session metadata...")
    ts_meta: dict[str, Any] = data["tuning_session"]
    timestamp = ts_meta.get("timestamp")
    session_id = str(timestamp) if timestamp is not None else path.stem

    try:
        benchmark, workload_type = _infer_benchmark_and_workload(ts_meta, path)
    except TuningSessionLoadError as exc:
        raise TuningSessionLoadError(
            f"Failed to infer benchmark and workload type: {exc}"
        ) from exc
    LOGGER.debug("  ➤ Benchmark and workload type inferred successfully.")

    sysbench_workload: Optional[str] = None
    if benchmark == "sysbench":
        raw_mode = ts_meta.get("sysbench_workload", DEFAULT_SYSBENCH_WORKLOAD)
        try:
            sysbench_workload = validate_sysbench_workload(str(raw_mode))
        except ValueError:
            LOGGER.warning(
                "  ➤ Invalid sysbench_workload=%r in session metadata; "
                "falling back to %s",
                raw_mode,
                DEFAULT_SYSBENCH_WORKLOAD,
            )
            sysbench_workload = DEFAULT_SYSBENCH_WORKLOAD

    LOGGER.debug("  Processing system info and tuning config...")
    system_info: dict[str, Any] = data.get("system_info", {})
    if not system_info:
        LOGGER.warning(
            "  ➤ system_info missing from %s — hardware provenance will be incomplete",
            path.name,
        )

    tuning_config = _normalize_tuning_config(ts_meta)
    scoring_metadata = _extract_scoring_metadata(data, ts_meta, path)

    # Check for version compatibility and warn on mismatches
    _check_version_compatibility(
        scoring_metadata["scoring_policy_version"],
        scoring_metadata["metric_reference_version"],
        path,
    )

    LOGGER.debug("  ➤ System info and tuning config processed successfully.")

    LOGGER.info(
        "➤ Loaded tuning session: timestamp=%s benchmark=%s workload=%s "
        "best_score=%.2f knobs=%d resources=(cpu=%d ram=%.1fGB)",
        timestamp,
        benchmark,
        workload_type,
        best_score,
        len(best_knobs),
        worker_resources.cpu_cores,
        worker_resources.ram_bytes / (1024**3),
    )

    score_breakdown = score_breakdown_from_dict(scoring_metadata["score_breakdown"])
    knob_source = str(ts_meta.get("knob_source", "expert"))
    tuning_strategy = _infer_tuning_strategy(ts_meta, path)

    return TuningSessionData(
        best_knobs=best_knobs,
        best_score=best_score,
        worker_resources=worker_resources,
        system_info=system_info,
        tuning_config=tuning_config,
        benchmark=benchmark,
        workload_type=workload_type,
        sysbench_workload=sysbench_workload,
        knob_source=knob_source,
        tuning_strategy=tuning_strategy,
        session_id=session_id,
        scoring_policy=scoring_metadata["scoring_policy"],
        scoring_policy_version=scoring_metadata["scoring_policy_version"],
        metric_reference_version=scoring_metadata["metric_reference_version"],
        workload_features=scoring_metadata["workload_features"],
        normalization_metadata=scoring_metadata["normalization_metadata"],
        score_breakdown=score_breakdown,
    )


def _assert_fields(
    obj: dict[str, Any],
    required: set[str],
    context: str,
) -> None:
    """Raise TuningSessionLoadError if any required field is missing."""
    missing = required - set(obj.keys())
    if missing:
        raise TuningSessionLoadError(
            f"Missing required field(s) in '{context}': {sorted(missing)}"
        )


def _infer_benchmark_and_workload(
    ts_meta: dict[str, Any], path: Path
) -> tuple[str, str]:
    """
    Infer the benchmark type and workload type from tuning_session metadata or the file path.
    """
    benchmark = ts_meta.get("benchmark_name")
    workload_type = ts_meta.get("workload_type")
    if workload_type:
        workload_type = str(workload_type).lower()

    if not workload_type:
        LOGGER.warning(
            "  ➤ workload type not found in session metadata, falling back to "
            "path to infer workload type"
        )
        path_str = str(path).lower()
        if "olap" in path_str or "tpch" in path_str:
            workload_type = "olap"
        elif "oltp" in path_str or "sysbench" in path_str:
            workload_type = "oltp"
        elif "mixed" in path_str:
            workload_type = "mixed"
        else:
            raise TuningSessionLoadError(
                f"Couldn't infer workload type from path: {path}"
            )

    if not benchmark:
        if workload_type == "oltp" or workload_type == "mixed":
            benchmark = "sysbench"
        elif workload_type == "olap":
            benchmark = "tpch"
        else:
            raise TuningSessionLoadError(f"Unknown workload type: {workload_type}")

        LOGGER.warning(
            "  ➤ benchmark name not found in session metadata, used workload"
            " type to infer benchmark: %s",
            benchmark,
        )

    return benchmark, workload_type


def _infer_tuning_strategy(ts_meta: dict[str, Any], path: Path) -> str:
    """Resolve tuning_strategy from explicit field or path-based fallback.

    Mirrors ``src/analysis/data_loader.py::_infer_tuning_strategy`` so the two
    loader paths agree on the canonical label set: ``"pbt" | "bo" | "lhs" |
    "unknown"``.
    """
    explicit = ts_meta.get("tuning_strategy")
    if explicit:
        return str(explicit)
    path_str = str(path)
    if "/pbt_runs/" in path_str:
        return "pbt"
    if "/bo_runs/" in path_str:
        return "bo"
    if "/lhs_runs/" in path_str:
        return "lhs"
    return "unknown"


def _normalize_tuning_config(ts_meta: dict[str, Any]) -> dict[str, Any]:
    """
    Normalize runtime tuning metadata into canonical evaluation keys.

    This keeps evaluation backward-compatible with historical session files
    while allowing a stable precedence chain of:
    CLI override -> session metadata -> benchmark defaults.
    """
    config: dict[str, Any] = {
        k: v
        for k, v in ts_meta.items()
        if k not in {"benchmark_name", "workload_type", "timestamp", "tuning_strategy"}
    }

    def _as_int(value: Any) -> Optional[int]:
        """Convert value to int, returning None if conversion fails."""
        if value is None:
            return None
        try:
            return int(value)
        except (TypeError, ValueError):
            return None

    def _as_float(value: Any) -> Optional[float]:
        """Convert value to float, returning None if conversion fails."""
        if value is None:
            return None
        try:
            return float(value)
        except (TypeError, ValueError):
            return None

    # Canonical benchmark runtime parameter keys for evaluation.
    scale_factor = _as_float(config.get("scale_factor"))
    if scale_factor is not None:
        config["scale_factor"] = scale_factor

    sysbench_duration = _as_int(
        config.get("sysbench_duration_seconds")
        or config.get("sysbench_duration")
        or config.get("evaluation_duration")
        or config.get("measurement_duration")
    )
    if sysbench_duration is not None:
        config["sysbench_duration_seconds"] = sysbench_duration

    sysbench_warmup = _as_int(
        config.get("sysbench_warmup_seconds") or config.get("warmup_duration")
    )
    if sysbench_warmup is not None:
        config["sysbench_warmup_seconds"] = sysbench_warmup

    tpch_warmup_passes = _as_int(
        config.get("tpch_warmup_passes") or config.get("warmup_passes")
    )
    if tpch_warmup_passes is not None:
        config["tpch_warmup_passes"] = tpch_warmup_passes

    sysbench_tables = _as_int(config.get("sysbench_tables"))
    if sysbench_tables is not None:
        config["sysbench_tables"] = sysbench_tables

    sysbench_table_size = _as_int(config.get("sysbench_table_size"))
    if sysbench_table_size is not None:
        config["sysbench_table_size"] = sysbench_table_size

    sysbench_workload = config.get("sysbench_workload")
    if sysbench_workload is not None:
        try:
            config["sysbench_workload"] = validate_sysbench_workload(
                str(sysbench_workload)
            )
        except ValueError:
            LOGGER.warning(
                "Ignoring invalid sysbench_workload value in tuning metadata: %r",
                sysbench_workload,
            )

    return config


def _extract_scoring_metadata(
    raw_root: dict[str, Any],
    ts_meta: dict[str, Any],
    path: Path,
) -> dict[str, Any]:
    """Extract persisted scoring metadata with strict schema checks."""

    # Unified schema (2a′+) namespaces scoring provenance under
    # ``tuning_session.scoring``. Read it first, then fall back to the legacy
    # flat keys (which BO's writer and older sessions still emit), then the
    # raw document root — so every session shape parses uniformly.
    scoring = ts_meta.get("scoring") or {}

    scoring_policy_raw = scoring.get(
        "scoring_policy",
        ts_meta.get("scoring_policy", raw_root.get("scoring_policy")),
    )
    scoring_policy = (
        str(scoring_policy_raw).strip()
        if scoring_policy_raw is not None and str(scoring_policy_raw).strip()
        else DEFAULT_SCORING_POLICY
    )

    scoring_policy_version_raw = scoring.get(
        "scoring_policy_version",
        ts_meta.get(
            "scoring_policy_version", raw_root.get("scoring_policy_version")
        ),
    )
    scoring_policy_version = (
        str(scoring_policy_version_raw).strip()
        if scoring_policy_version_raw is not None
        and str(scoring_policy_version_raw).strip()
        else DEFAULT_SCORING_POLICY_VERSION
    )

    metric_reference_version_raw = scoring.get(
        "metric_reference_version",
        ts_meta.get(
            "metric_reference_version", raw_root.get("metric_reference_version")
        ),
    )
    metric_reference_version = (
        str(metric_reference_version_raw).strip()
        if metric_reference_version_raw is not None
        and str(metric_reference_version_raw).strip()
        else DEFAULT_METRIC_REFERENCE_VERSION
    )

    workload_features = _expect_object_or_empty(
        field_name="workload_features",
        value=scoring.get(
            "workload_features",
            raw_root.get("workload_features", ts_meta.get("workload_features")),
        ),
        path=path,
    )
    normalization_metadata = _expect_object_or_empty(
        field_name="normalization_metadata",
        value=scoring.get(
            "normalization_metadata",
            raw_root.get(
                "normalization_metadata",
                ts_meta.get("normalization_metadata"),
            ),
        ),
        path=path,
    )
    score_breakdown = _expect_object_or_empty(
        field_name="score_breakdown",
        value=scoring.get(
            "score_breakdown",
            raw_root.get("score_breakdown", ts_meta.get("score_breakdown")),
        ),
        path=path,
    )

    return {
        "scoring_policy": scoring_policy,
        "scoring_policy_version": scoring_policy_version,
        "metric_reference_version": metric_reference_version,
        "workload_features": workload_features,
        "normalization_metadata": normalization_metadata,
        "score_breakdown": score_breakdown,
    }


def _expect_object_or_empty(
    *,
    field_name: str,
    value: Any,
    path: Path,
) -> dict[str, Any]:
    """Validate persisted metadata object shape and return normalized dictionary."""
    if value is None:
        return {}

    if not isinstance(value, dict):
        raise ScoringMetadataSchemaError(
            f"Invalid scoring metadata in {path}: '{field_name}' must be a JSON "
            f"object, got {type(value).__name__}"
        )

    return dict(value)


def _check_version_compatibility(
    scoring_policy_version: str,
    metric_reference_version: str,
    path: Path,
) -> None:
    """
    Check for version mismatches and emit warnings for mixed-version data.

    This policy ensures that when loading tuning session data with different
    versions of scoring policies or metric references, appropriate warnings
    are emitted to alert users about potential compatibility issues.

    Parameters
    ----------
    scoring_policy_version : str
        Version of the scoring policy used during tuning
    metric_reference_version : str
        Version of the metric reference used during tuning
    path : Path
        Path to the tuning session file for logging context
    """
    # Check if versions differ from defaults
    if scoring_policy_version != DEFAULT_SCORING_POLICY_VERSION:
        LOGGER.warning(
            "  ➤ Tuning session %s used scoring_policy_version=%s "
            "(current default: %s) — results may not be directly comparable",
            path.name,
            scoring_policy_version,
            DEFAULT_SCORING_POLICY_VERSION,
        )

    if metric_reference_version != DEFAULT_METRIC_REFERENCE_VERSION:
        LOGGER.warning(
            "  ➤ Tuning session %s used metric_reference_version=%s "
            "(current default: %s) — metric interpretations may differ",
            path.name,
            metric_reference_version,
            DEFAULT_METRIC_REFERENCE_VERSION,
        )
