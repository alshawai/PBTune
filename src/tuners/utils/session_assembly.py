"""
Free functions for assembling generation records and session timing summaries.

Extracted from ``BaseTuner`` so the record-building logic is unit-testable
without instantiating a tuner. Each function takes explicit inputs rather than
reaching into ``self``.
"""

from __future__ import annotations

import time
from datetime import datetime
from typing import Any, Dict, List, Optional, Sequence

from src.tuners.utils.session_writer import convert_numpy_types
from src.tuners.utils.types import WorkerEvalResult
from src.utils.logger import get_logger
from src.utils.metrics import PerformanceMetrics
from src.utils.scoring.contracts import ScoreBreakdown
from src.utils.timing import TimingRecorder

LOGGER = get_logger("Tuner")


def safe_breakdown(
    metrics: Optional[PerformanceMetrics],
    scorer: Any,
) -> Optional[ScoreBreakdown]:
    """Compute a score breakdown, tolerating scorer failures."""
    if metrics is None or scorer is None:
        return None
    try:
        return scorer.compute_breakdown(metrics)
    except (RuntimeError, ValueError, AttributeError) as exc:
        LOGGER.debug("Failed to compute score breakdown: %s", exc)
        return None


def build_generation_record(
    *,
    generation: int,
    best_score_this_round: float,
    converged: bool,
    worker_results: Sequence[WorkerEvalResult],
    generation_elapsed_seconds: float,
    tuning_start_time: float,
    start_time: float,
    scorer: Any = None,
    restart_count: int = 0,
    generation_timing: Optional[Any] = None,
    mean_score: Optional[float] = None,
    std_score: Optional[float] = None,
    num_exploited: Optional[int] = None,
    extra: Optional[Dict[str, Any]] = None,
) -> Dict[str, Any]:
    """Assemble one uniform ``generation_history`` entry for any strategy.

    This is the single seam every tuner feeds so that per-round records are
    structurally identical across PBT, LHS, and BO — the concrete realization
    of "all tuners' session JSONs follow the same fields".
    """
    worker_scores: List[Dict[str, Any]] = []
    worker_configs: List[Dict[str, Any]] = []
    for result in worker_results:
        breakdown = result.score_breakdown
        if breakdown is None and result.metrics is not None:
            breakdown = safe_breakdown(result.metrics, scorer)
        worker_scores.append(
            {
                "worker_id": result.worker_id,
                "score": (
                    float(result.score) if result.score is not None else None
                ),
                "metrics": (
                    result.metrics.to_dict()
                    if result.metrics is not None
                    else None
                ),
                "score_breakdown": (
                    convert_numpy_types(breakdown.to_dict())
                    if breakdown is not None
                    else None
                ),
                "timing": (
                    result.timing.to_dict(include_summary=False)
                    if result.timing is not None
                    else None
                ),
            }
        )
        worker_configs.append(
            {
                "worker_id": result.worker_id,
                "config": convert_numpy_types(result.knob_config),
            }
        )

    record: Dict[str, Any] = {
        "generation": generation,
        "best_score": float(best_score_this_round),
        "converged": bool(converged),
        "restart_count": int(restart_count),
        "timestamp": datetime.now().isoformat(),
        "wall_clock_seconds": time.time() - (tuning_start_time or start_time),
        "generation_elapsed_seconds": float(generation_elapsed_seconds),
        "timing": (
            generation_timing.to_dict(include_summary=False)
            if generation_timing is not None
            else None
        ),
        "worker_scores": worker_scores,
        "worker_configs": worker_configs,
    }
    if mean_score is not None:
        record["mean_score"] = float(mean_score)
    if std_score is not None:
        record["std_score"] = float(std_score)
    if num_exploited is not None:
        record["num_exploited"] = int(num_exploited)
    if extra:
        record.update(extra)
    return record


def aggregate_session_timing(
    generation_history: List[Dict[str, Any]],
) -> Dict[str, Any]:
    """Aggregate per-component timing across every (round, worker) tuple.

    Walks generation_history and merges every per-worker and per-round
    ``timing.records`` block into a single recorder, then emits
    ``aggregate()`` for mean/std/n/min/max/total per component.
    """
    merged = TimingRecorder()
    for gen in generation_history:
        gen_timing = gen.get("timing")
        if gen_timing and isinstance(gen_timing, dict):
            for rec in gen_timing.get("records", []) or []:
                merged.add(
                    rec.get("component", "unknown"),
                    float(rec.get("seconds", 0.0)),
                    **(rec.get("metadata") or {}),
                )
        for ws in gen.get("worker_scores", []) or []:
            ws_timing = ws.get("timing")
            if not ws_timing or not isinstance(ws_timing, dict):
                continue
            for rec in ws_timing.get("records", []) or []:
                merged.add(
                    rec.get("component", "unknown"),
                    float(rec.get("seconds", 0.0)),
                    **(rec.get("metadata") or {}),
                )
    return merged.aggregate()
