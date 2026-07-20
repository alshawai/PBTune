"""
Session-result serialization shared across tuning strategies.

PBT (``PBTTuner.save_final_results``) and BO (``write_bo_results``) each emit a
``tuning_session`` JSON envelope with a near-identical header block plus a
``best_configuration`` and ``worker_resources`` section. This module provides
the shared ``convert_numpy_types`` serializer and a builder for the common
envelope so a new strategy produces schema-compatible output without copying
the whole writer.

Strategy-specific sections (generation history, score breakdown, warm-start
provenance, ...) are merged in by the caller.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any, Dict, Optional

import numpy as np

from src.tuners.utils.types import TuningStrategy
from src.utils.hardware_info import WorkerResources
from src.utils.logger import get_logger

LOGGER = get_logger("TunerSessionWriter")

# The timing-instrumentation schema version this writer targets. Kept in lockstep
# with the incumbent writers so downstream loaders treat all strategies alike.
TIMING_SCHEMA_VERSION = "1.1"


def convert_numpy_types(obj: Any) -> Any:
    """
    Recursively convert numpy scalars/arrays to JSON-native Python types.

    Identical in behavior to the helpers embedded in the PBT and BO writers;
    centralized here so all three share one implementation.
    """
    if isinstance(obj, np.bool_):
        return bool(obj)
    if isinstance(obj, (np.integer, np.int64, np.int32, np.int16, np.int8)):  # type: ignore
        return int(obj)
    if isinstance(obj, (np.floating, np.float64, np.float32, np.float16)):  # type: ignore
        return float(obj)
    if isinstance(obj, np.ndarray):
        return obj.tolist()
    if isinstance(obj, dict):
        return {key: convert_numpy_types(value) for key, value in obj.items()}
    if isinstance(obj, (list, tuple)):
        return [convert_numpy_types(item) for item in obj]
    if hasattr(obj, "to_dict") and callable(obj.to_dict):
        return convert_numpy_types(obj.to_dict())
    return obj


def worker_resources_to_dict(resources: WorkerResources) -> Dict[str, Any]:
    """Serialize a ``WorkerResources`` into the canonical session sub-block."""
    return {
        "ram_bytes": resources.ram_bytes,
        "cpu_cores": resources.cpu_cores,
        "disk_type": resources.disk_type,
        "disk_read_bps": resources.disk_read_bps,
        "disk_write_bps": resources.disk_write_bps,
        "disk_read_iops": resources.disk_read_iops,
        "disk_write_iops": resources.disk_write_iops,
        "disk_class": resources.disk_class,
    }


def build_session_header(
    *,
    strategy: TuningStrategy | str,
    knob_tier: str,
    knob_source: str,
    num_knobs: int,
    workload_type: str,
    benchmark_name: str,
    timestamp: str,
    seed: Optional[int],
    extra: Optional[Dict[str, Any]] = None,
) -> Dict[str, Any]:
    """
    Build the shared ``tuning_session`` header block.

    The header carries the fields every loader expects regardless of
    strategy: schema version, the new ``tuning_strategy`` discriminator, the
    knob tier/source, the workload/benchmark identity, the seed, and the
    timestamp. ``extra`` is merged in for strategy-specific header fields
    (e.g. population size, design size, acquisition function).
    """
    header: Dict[str, Any] = {
        "timing_schema_version": TIMING_SCHEMA_VERSION,
        "tuning_strategy": TuningStrategy.from_value(strategy).value,
        "knob_tier": knob_tier,
        "knob_source": knob_source,
        "num_knobs": int(num_knobs),
        "workload_type": workload_type,
        "benchmark_name": benchmark_name,
        "seed": seed,
        "timestamp": timestamp,
    }
    if extra:
        header.update(extra)
    return header


def build_scoring_block(
    scoring_metadata: Dict[str, Any],
    score_breakdown: Optional[Dict[str, Any]] = None,
) -> Dict[str, Any]:
    """Build the unified ``tuning_session.scoring`` sub-block.

    Every strategy scores its configs through the same engine, so the scoring
    provenance (policy, versions, the static workload-feature prior, the
    normalization ranges) and the winning config's ``score_breakdown`` are
    serialized under one namespaced block regardless of strategy. Downstream
    loaders read ``tuning_session.scoring`` first and fall back to the legacy
    flat keys, so folding these six fields here keeps a single code path for
    PBT and LHS while remaining tolerant of older/BO-flat sessions.

    ``scoring_metadata`` is the dict returned by ``MetricConfig
    .get_scoring_metadata()``; ``score_breakdown`` is the best config's
    breakdown (``{}`` when unavailable).
    """
    return {
        "scoring_policy": scoring_metadata.get("scoring_policy", "fixed_v1"),
        "scoring_policy_version": scoring_metadata.get(
            "scoring_policy_version", "1.0"
        ),
        "metric_reference_version": scoring_metadata.get(
            "metric_reference_version", "v1"
        ),
        "workload_features": scoring_metadata.get("workload_features", {}),
        "normalization_metadata": scoring_metadata.get(
            "normalization_metadata", {}
        ),
        "score_breakdown": score_breakdown or {},
    }


def build_benchmark_block(
    benchmark_config: Any,
    benchmark_name: str,
) -> Dict[str, Any]:
    """Build the unified ``tuning_session.benchmark`` sub-block.

    Both PBT and BO previously scattered ``sysbench_*`` / ``tpch_*`` /
    duration keys flat across the header (with slightly different names per
    strategy). This folds the benchmark identity and runtime parameters into
    one namespaced block emitted by every tuner, so a loader has a single
    place to read benchmark provenance. Downstream loaders read
    ``tuning_session.benchmark`` first and fall back to the legacy flat keys.

    ``benchmark_config`` is a :class:`~src.utils.types.BenchmarkConfig`;
    ``benchmark_name`` is the driver name (``"sysbench"`` / ``"tpch"``).
    """
    return {
        "name": benchmark_name,
        "workload": getattr(benchmark_config, "workload_type", None),
        "sysbench_tables": getattr(benchmark_config, "sysbench_tables", None),
        "sysbench_table_size": getattr(
            benchmark_config, "sysbench_table_size", None
        ),
        "sysbench_workload": getattr(benchmark_config, "sysbench_workload", None),
        "warmup_seconds": getattr(benchmark_config, "warmup_duration", None),
        "measurement_seconds": getattr(
            benchmark_config, "evaluation_duration", None
        ),
        "warmup_passes": getattr(benchmark_config, "warmup_passes", None),
        "scale_factor": getattr(benchmark_config, "scale_factor", None),
    }


def build_environment_block(
    system_info: Optional[Dict[str, Any]],
    session_environment: Optional[Any],
) -> Dict[str, Any]:
    """Build the unified ``tuning_session.environment`` sub-block.

    ``system_info`` (raw hardware snapshot) and ``session_environment`` (the
    composed :class:`~src.utils.types.SessionEnvironment`) overlap heavily
    (cpu / ram / os / pg). This merges them into one block: the raw
    ``system_info`` is the single source of hardware truth, and only the
    genuinely *session-level* ``session_environment`` fields (pg server
    version, docker, topology, pinning) overlay it. The flat hardware
    duplicates that ``SessionEnvironment.to_dict`` also carries (``cpu_model``,
    ``cpu_cores_physical``, ``ram_bytes_total``, ``os_*``, ``pg_client_version``,
    …) are dropped — they restate ``system_info``. ``per_worker_resources`` is
    likewise dropped as redundant with the top-level ``worker_resources``
    section. Downstream loaders read ``tuning_session.environment`` first,
    falling back to the legacy top-level ``system_info`` / ``session_environment``
    keys.
    """
    # Session-level fields kept from the SessionEnvironment overlay. Everything
    # else it emits duplicates the raw hardware snapshot in ``system_info``.
    _SESSION_LEVEL_KEYS = (
        "data_disk_type",
        "kernel_version",
        "pg_server_version",
        "docker_version",
        "use_docker",
        "num_parallel_workers",
        "population_size",
        "cpu_pinning_scheme",
    )
    merged: Dict[str, Any] = {}
    if system_info:
        merged["system_info"] = system_info
    if session_environment is not None:
        env_dict = (
            session_environment.to_dict()
            if hasattr(session_environment, "to_dict")
            else dict(session_environment)
        )
        for key in _SESSION_LEVEL_KEYS:
            if key in env_dict:
                merged[key] = env_dict[key]
    return merged


def write_session_json(
    results: Dict[str, Any],
    *,
    output_dir: Path,
    filename: str,
) -> Path:
    """Write a session results dict to ``{output_dir}/traces/{filename}``.

    Returns the path written. Numpy types are converted on the way out.
    """
    tuning_output_dir = Path(output_dir) / "traces"
    tuning_output_dir.mkdir(parents=True, exist_ok=True)
    json_file = tuning_output_dir / filename

    with open(json_file, "w", encoding="utf-8") as f:
        json.dump(convert_numpy_types(results), f, indent=2)
    LOGGER.info("Saved session results to %s", json_file)
    return json_file


def write_best_config_json(
    best_config_fractions: Dict[str, Any],
    *,
    output_dir: Path,
    filename: str,
) -> Path:
    """Write the best-config fractions to ``{output_dir}/best_configs/{filename}``."""
    best_config_output_dir = Path(output_dir) / "best_configs"
    best_config_output_dir.mkdir(parents=True, exist_ok=True)
    best_config_file = best_config_output_dir / filename

    with open(best_config_file, "w", encoding="utf-8") as f:
        json.dump(convert_numpy_types(best_config_fractions), f, indent=2)
    LOGGER.info("Saved best config to %s", best_config_file)
    return best_config_file
