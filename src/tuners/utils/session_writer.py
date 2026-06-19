"""Session-result serialization shared across tuning strategies.

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
    """Recursively convert numpy scalars/arrays to JSON-native Python types.

    Identical in behavior to the helpers embedded in the PBT and BO writers;
    centralized here so all three share one implementation.
    """
    if isinstance(obj, np.bool_):
        return bool(obj)
    if isinstance(obj, (np.integer, np.int64, np.int32, np.int16, np.int8)):
        return int(obj)
    if isinstance(obj, (np.floating, np.float64, np.float32, np.float16)):
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
    """Build the shared ``tuning_session`` header block.

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


def write_session_json(
    results: Dict[str, Any],
    *,
    output_dir: Path,
    filename: str,
) -> Path:
    """Write a session results dict to ``{output_dir}/tuning_sessions/{filename}``.

    Returns the path written. Numpy types are converted on the way out.
    """
    tuning_output_dir = Path(output_dir) / "tuning_sessions"
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
