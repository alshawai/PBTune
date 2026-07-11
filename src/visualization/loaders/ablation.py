"""
Loader for ablation studies.
"""

from dataclasses import dataclass
from pathlib import Path
from typing import Any

from src.utils.logger import get_logger
from src.utils.metrics import MetricConfig, PerformanceMetrics
from src.utils.calibration import rescore_metrics_globally
from src.visualization.exceptions import DataLoadError
from src.visualization.loaders.session import SessionTrace, load_sessions

LOGGER = get_logger("AblationLoader")


@dataclass
class AblationGroup:
    """Parsed ablation study results with a shared metric space."""

    variable_name: str
    groups: dict[str, list[SessionTrace]]  # value -> list of traces
    metric_config: MetricConfig  # The super-global normalizer


def load_ablation_study(ablation_dir: Path | str) -> AblationGroup:
    """
    Load an entire ablation study (multiple variations of a parameter).

    This function discovers all value directories under `ablation_dir`,
    loads all PBT sessions within them, and computes a single super-global
    MetricConfig across ALL variations to ensure perfectly fair comparison
    between e.g. pop_size=4 and pop_size=8.

    Args:
        ablation_dir: Path to the root ablation directory
            (e.g., `results/oltp/pbt_runs/minimal/ablations/population_size/`)

    Returns:
        AblationGroup containing grouped traces and the shared metric config.
    """
    dir_path = Path(ablation_dir)
    if not dir_path.exists() or not dir_path.is_dir():
        raise DataLoadError(f"Ablation directory not found: {ablation_dir}")

    variable_name = dir_path.name

    # Discover all value subdirectories
    value_dirs = [d for d in dir_path.iterdir() if d.is_dir()]
    if not value_dirs:
        raise DataLoadError(f"No variation directories found in {ablation_dir}")

    # First pass: load ALL sessions without rescoring to gather raw metrics
    raw_sessions = []

    # We use a temporary dictionary to hold the raw traces per group
    # before we apply the super-global rescoring
    raw_groups: dict[str, list[SessionTrace]] = {}

    for val_dir in value_dirs:
        # PBT sessions are saved in `tuning_sessions/` under the ablation value dir
        tuning_dir = val_dir / "tuning_sessions"
        if not tuning_dir.exists():
            LOGGER.warning("No tuning_sessions/ directory found in %s", val_dir)
            continue

        val_name = val_dir.name
        try:
            # We must load them individually so we can defer rescoring
            # load_sessions() groups everything in ONE directory, but here we
            # want to group across MULTIPLE directories
            traces = load_sessions(tuning_dir)
            raw_groups[val_name] = traces
            raw_sessions.extend(traces)
        except DataLoadError as e:
            LOGGER.warning("Failed to load sessions in %s: %s", tuning_dir, e)

    if not raw_sessions:
        raise DataLoadError(
            f"No valid PBT sessions found anywhere under {ablation_dir}"
        )

    # Second pass: Compute super-global MetricConfig
    # We need to extract the raw metrics from the traces. The traces store rescored
    # values by default (because load_sessions rescores).
    # Actually, load_sessions() computes a super-global config per directory.
    # To get a true super-global config across ALL directories, we must re-compute it.

    # Fortunately, the traces carry their raw JSON metadata and we can just use the
    # first one to get benchmark/workload, but we need raw metrics.
    # We don't store raw metrics in SessionTrace...

    # To do this correctly, we need to load each file manually and extract the raw metrics
    import json

    all_raw_metrics = []
    shared_metadata: dict[str, Any] = {}

    for val_dir in value_dirs:
        tuning_dir = val_dir / "tuning_sessions"
        if not tuning_dir.exists():
            continue

        json_files = sorted(tuning_dir.glob("pbt_results_*.json"))
        for f in json_files:
            try:
                with open(f, "r", encoding="utf-8") as file_obj:
                    data = json.load(file_obj)

                if not shared_metadata:
                    tuning_session = data.get("tuning_session", {})
                    scoring = tuning_session.get("scoring") or {}
                    shared_metadata = {
                        "workload": tuning_session.get("workload_type", "oltp"),
                        "benchmark": tuning_session.get("benchmark_name"),
                        "scoring_policy": scoring.get(
                            "scoring_policy",
                            data.get(
                                "scoring_policy",
                                tuning_session.get("scoring_policy"),
                            ),
                        ),
                        "scoring_policy_version": scoring.get(
                            "scoring_policy_version",
                            data.get(
                                "scoring_policy_version",
                                tuning_session.get("scoring_policy_version"),
                            ),
                        ),
                        "metric_reference_version": scoring.get(
                            "metric_reference_version",
                            data.get(
                                "metric_reference_version",
                                tuning_session.get("metric_reference_version"),
                            ),
                        ),
                        "workload_features": scoring.get(
                            "workload_features",
                            data.get("workload_features"),
                        ),
                    }

                history = data.get("generation_history", [])
                for gen in history:
                    for ws in gen.get("worker_scores", []):
                        m = ws.get("metrics")
                        if m:
                            valid_keys = PerformanceMetrics.__dataclass_fields__.keys()
                            filtered = {k: v for k, v in m.items() if k in valid_keys}
                            all_raw_metrics.append(PerformanceMetrics(**filtered))

            except Exception as e:
                LOGGER.warning("Failed to extract raw metrics from %s: %s", f, e)

    # Compute the super-global MetricConfig
    super_config, _, _ = rescore_metrics_globally(
        metrics=all_raw_metrics,
        benchmark=shared_metadata.get("benchmark"),
        workload=shared_metadata.get("workload"),
        scoring_policy=shared_metadata.get("scoring_policy"),
        scoring_policy_version=shared_metadata.get("scoring_policy_version"),
        metric_reference_version=shared_metadata.get("metric_reference_version"),
        workload_features=shared_metadata.get("workload_features"),
    )

    # Third pass: Now that we have the super-global MetricConfig, load the sessions AGAIN
    # but pass the super_config so they all use the exact same normalization scale.
    from src.visualization.loaders.session import load_session

    final_groups: dict[str, list[SessionTrace]] = {}
    for val_dir in value_dirs:
        tuning_dir = val_dir / "tuning_sessions"
        if not tuning_dir.exists():
            continue

        val_name = val_dir.name
        final_groups[val_name] = []

        for f in sorted(tuning_dir.glob("pbt_results_*.json")):
            try:
                trace = load_session(f, metric_config=super_config)
                final_groups[val_name].append(trace)
            except Exception as e:
                LOGGER.warning("Failed to reload %s with super-global config: %s", f, e)

    # Fourth pass: Validate invariant parameters
    # An ablation study must hold all parameters constant except the ablation variable.
    invariant_keys = [
        "knob_tier",
        "workload_type",
        "benchmark_name",
        "sysbench_tables",
        "sysbench_table_size",
        "sysbench_workload",
        "sysbench_duration_seconds",
        "tpch_scale_factor",
        "tuning_mode",
        "population_size",
        "exploit_quantile",
        "ready_interval",
    ]

    if variable_name in invariant_keys:
        invariant_keys.remove(variable_name)

    base_config = None
    base_file = None

    for _, traces in final_groups.items():
        for trace in traces:
            session_meta = trace.metadata.get("tuning_session", {})
            # Strategy-specific hyperparameters (population_size, exploit_quantile,
            # ready_interval, ...) move under ``tuning_session.strategy_params`` in
            # the unified schema (2a′+); shared identity keys stay flat. Read each
            # invariant from the flat header first, then strategy_params, so both
            # incumbent-flat and unified-nested PBT sessions validate.
            strategy_params = session_meta.get("strategy_params") or {}
            current_config = {
                k: session_meta.get(k, strategy_params.get(k))
                for k in invariant_keys
            }

            if base_config is None:
                base_config = current_config
                base_file = trace.metadata.get("file_name", "unknown")
            else:
                for k in invariant_keys:
                    if base_config[k] != current_config[k]:
                        raise DataLoadError(
                            f"Ablation study parameter mismatch!\n"
                            f"Invariant parameter '{k}' differs between runs.\n"
                            f"Base ({base_file}): {base_config[k]}\n"
                            f"Current ({trace.metadata.get('file_name', 'unknown')}): {current_config[k]}\n"
                            f"Ablation studies must hold all parameters constant except '{variable_name}'."
                        )

    return AblationGroup(
        variable_name=variable_name,
        groups={
            k: v for k, v in final_groups.items() if v
        },  # Only keep groups with >0 traces
        metric_config=super_config,
    )
