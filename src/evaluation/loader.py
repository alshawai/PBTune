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
from src.evaluation.exceptions import TuningSessionLoadError
from src.evaluation.types import TuningSessionData, WorkerResources
from src.benchmarks.sysbench.executor import (
    DEFAULT_SYSBENCH_WORKLOAD,
    validate_sysbench_workload,
)

logger = get_logger(__name__)

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
    logger.info("Loading tuning session from: %s", path)

    logger.debug("  Checking if correct tuning session file exists...")
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
    logger.debug("  ➤ Correct session file exist.")

    logger.debug("  Checking if required fields are present...")
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
    logger.debug("  ➤ Required fields are present.")

    logger.debug("  Checking if worker resources are valid...")
    worker_resources = WorkerResources(
        ram_bytes=int(wr_raw["ram_bytes"]),
        cpu_cores=int(wr_raw["cpu_cores"]),
        disk_type=str(wr_raw["disk_type"]),
    )

    if worker_resources.ram_bytes <= 0:
        raise TuningSessionLoadError(
            f"worker_resources.ram_bytes must be positive, got {worker_resources.ram_bytes}"
        )
    if worker_resources.cpu_cores <= 0:
        raise TuningSessionLoadError(
            f"worker_resources.cpu_cores must be positive, got {worker_resources.cpu_cores}"
        )
    logger.debug("  ➤ Worker resources are valid.")

    logger.debug("  Inferring tuning session metadata...")
    ts_meta: dict[str, Any] = data["tuning_session"]
    timestamp = ts_meta.get("timestamp")
    session_id = str(timestamp) if timestamp is not None else path.stem

    try:
        benchmark, workload_type = _infer_benchmark_and_workload(ts_meta, path)
    except TuningSessionLoadError as exc:
        raise TuningSessionLoadError(
            f"Failed to infer benchmark and workload type: {exc}"
        ) from exc
    logger.debug("  ➤ Benchmark and workload type inferred successfully.")

    sysbench_workload: Optional[str] = None
    if benchmark == "sysbench":
        raw_mode = ts_meta.get("sysbench_workload", DEFAULT_SYSBENCH_WORKLOAD)
        try:
            sysbench_workload = validate_sysbench_workload(str(raw_mode))
        except ValueError:
            logger.warning(
                "  ➤ Invalid sysbench_workload=%r in session metadata; "
                "falling back to %s",
                raw_mode,
                DEFAULT_SYSBENCH_WORKLOAD,
            )
            sysbench_workload = DEFAULT_SYSBENCH_WORKLOAD

    logger.debug("  Processing system info and tuning config...")
    system_info: dict[str, Any] = data.get("system_info", {})
    if not system_info:
        logger.warning(
            "  ➤ system_info missing from %s — hardware provenance will be incomplete",
            path.name,
        )

    tuning_config = _normalize_tuning_config(ts_meta)

    logger.debug("  ➤ System info and tuning config processed successfully.")

    logger.info(
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

    return TuningSessionData(
        best_knobs=best_knobs,
        best_score=best_score,
        worker_resources=worker_resources,
        system_info=system_info,
        tuning_config=tuning_config,
        benchmark=benchmark,
        workload_type=workload_type,
        sysbench_workload=sysbench_workload,
        session_id=session_id,
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
        logger.warning(
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

        logger.warning(
            "  ➤ benchmark name not found in session metadata, used workload"
            " type to infer benchmark: %s",
            benchmark,
        )

    return benchmark, workload_type


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
        if k not in {"benchmark_name", "workload_type", "timestamp"}
    }

    def _as_int(value: Any) -> Optional[int]:
        if value is None:
            return None
        try:
            return int(value)
        except (TypeError, ValueError):
            return None

    def _as_float(value: Any) -> Optional[float]:
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
            logger.warning(
                "Ignoring invalid sysbench_workload value in tuning metadata: %r",
                sysbench_workload,
            )

    return config
