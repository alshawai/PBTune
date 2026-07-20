"""
Free functions for tuner lifecycle logging.

Extracted from ``BaseTuner`` so the display/formatting logic lives separately
from the class's control flow. These are pure logging helpers — they emit to a
logger and return nothing.
"""

from __future__ import annotations

from typing import Any, Dict, List, Optional, Sequence, Tuple

from src.tuners.utils.metrics_table import build_worker_metric_row
from src.tuners.utils.types import WorkerEvalResult
from src.utils.hardware_info import log_system_info
from src.utils.logger import (
    get_color_context,
    get_logger,
    log_section_header,
    log_worker_metrics_table as _log_worker_metrics_table,
)

LOGGER = get_logger("Tuner")
COLORS = get_color_context()


def log_optimization_header(
    *,
    strategy_label: str,
    system_info: Dict[str, Any],
    knob_tier: str,
    knob_count: int,
    config_summary_lines: List[Tuple[str, str]],
    workload_type_value: str,
    output_root: Any,
) -> None:
    """Emit the system-info + configuration summary block before the loop."""
    log_section_header(
        LOGGER,
        "%s%s Database Tuner - Starting Optimization%s",
        COLORS.bold,
        strategy_label,
        COLORS.reset,
    )
    log_system_info(LOGGER, system_info)
    LOGGER.info(
        "Knob Tier:       %s%s (%d knobs)%s",
        COLORS.cyan,
        knob_tier,
        knob_count,
        COLORS.reset,
    )
    for label, value in config_summary_lines:
        LOGGER.info("%-16s %s%s%s", label, COLORS.cyan, value, COLORS.reset)
    LOGGER.info(
        "Workload Type:   %s%s%s",
        COLORS.cyan,
        workload_type_value,
        COLORS.reset,
    )
    LOGGER.info(
        "Output Dir:      %s%s%s", COLORS.cyan, output_root, COLORS.reset
    )


def log_round_start(
    generation: int,
    *,
    round_label: str,
    scorer: Any = None,
) -> None:
    """Emit the per-round section header and the live scoring-weight table."""
    LOGGER.info("")
    log_section_header(
        LOGGER,
        "%s%s %d%s",
        COLORS.bold,
        round_label.upper(),
        generation,
        COLORS.reset,
        top_separator=False,
    )
    if scorer is not None:
        try:
            scorer.log_generation_weights(generation=generation)
        except (RuntimeError, ValueError, AttributeError) as exc:
            LOGGER.debug("Weight-table logging skipped: %s", exc)


def log_round_end(
    *,
    outcome_index: int,
    outcome_best_score: float,
    outcome_payload: Optional[Dict[str, Any]],
    prev_best: float,
    current_best: float,
    elapsed_seconds: float,
    emits_stop_status: bool,
    stopped: bool,
    stop_reason: Optional[str],
    round_label: str,
) -> None:
    """Announce a new best and log the generation summary."""
    if current_best > prev_best:
        LOGGER.info(
            "%s🔺 NEW BEST SCORE: %s%.4f%s",
            COLORS.bold,
            COLORS.teal,
            current_best,
            COLORS.reset,
        )
    payload = outcome_payload or {}
    mean_score = payload.get("mean_score")
    std_score = payload.get("std_score")
    num_exploited = payload.get("num_exploited")
    restart_count = payload.get("restart_count")

    status: Optional[str] = None
    if emits_stop_status:
        if stopped:
            reason = stop_reason or "criterion met"
            status = f"stopped - {reason}"
        else:
            status = "running"

    design_points = format_design_points(payload.get("evaluated"))

    from src.utils.logger import log_generation_summary

    log_generation_summary(
        LOGGER,
        elapsed_seconds,
        int(restart_count) if restart_count is not None else None,
        generation=outcome_index,
        best_score=float(outcome_best_score),
        mean_score=float(mean_score) if mean_score is not None else None,
        std_score=float(std_score) if std_score is not None else None,
        exploited=int(num_exploited) if num_exploited is not None else None,
        design_points=design_points,
        status=status,
        round_label=round_label,
    )


def format_design_points(evaluated: Optional[Sequence[int]]) -> Optional[str]:
    """Render a list of evaluated design indices as a compact range string.

    ``[6, 7]`` -> ``"6-7"``, ``[4]`` -> ``"4"``, ``[]``/``None`` -> ``None``.
    """
    if not evaluated:
        return None
    ordered = sorted(int(i) for i in evaluated)
    if ordered == list(range(ordered[0], ordered[-1] + 1)):
        if ordered[0] == ordered[-1]:
            return str(ordered[0])
        return f"{ordered[0]}-{ordered[-1]}"
    return ", ".join(str(i) for i in ordered)


def log_worker_metrics(
    worker_results: Sequence[WorkerEvalResult],
    *,
    title: Optional[str] = None,
    best_worker_metric: Optional[Dict[str, Any]] = None,
    best_worker_label: str = "Best Worker",
) -> None:
    """Render the end-of-round per-worker performance table.

    When ``best_worker_metric`` is supplied (the running incumbent's
    :func:`build_worker_metric_row`), it is rendered as a trailing green
    column so BO and LHS match PBT's end-of-generation table (which always
    shows the historical best alongside the current round).
    """
    payloads = [
        build_worker_metric_row(r.metrics, r.score)
        for r in worker_results
        if r.metrics is not None
    ]
    if not payloads:
        return
    labels = [
        f"Worker-{r.worker_id}"
        for r in worker_results
        if r.metrics is not None
    ]
    _log_worker_metrics_table(
        LOGGER,
        payloads,
        worker_labels=labels,
        best_worker_metric=best_worker_metric,
        best_worker_label=best_worker_label,
        title=title
        or f"\n{COLORS.bold}🔷 Round Worker Metrics 🔷{COLORS.reset}",
    )
