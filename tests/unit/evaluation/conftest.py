"""
Shared fixtures for evaluate_tuning unit tests.

Uses in-memory fakes so no Docker, PostgreSQL, or filesystem
access is needed during unit testing.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from src.utils.metrics import PerformanceMetrics
from src.evaluation.types import (
    RunResult,
    WorkerResources,
)


# ---------------------------------------------------------------------------
# Sample tuning session JSON (matches real file structure)
# ---------------------------------------------------------------------------

SAMPLE_SESSION: dict = {
    "tuning_session": {
        "timestamp": "20260326_2115",
        "benchmark_name": "tpch",
        "workload_type": "OLAP",
        "tier": "extensive",
        "generations": 20,
        "population_size": 8,
    },
    "best_configuration": {
        "score": 72.3,
        "knobs": {
            "shared_buffers": "400MB",
            "work_mem": "32MB",
            "effective_cache_size": "1GB",
            "checkpoint_completion_target": "0.9",
            "wal_buffers": "16MB",
        },
        "metrics": {
            "throughput": 45.2,
            "latency_p95": 210.0,
        },
    },
    "worker_resources": {
        "ram_bytes": 1_641_393_356,
        "cpu_cores": 1,
        "disk_type": "SSD",
    },
    "warm_start": None,
    "convergence": {"converged": False, "plateau_generations": 3},
    "system_info": {
        "cpu_model": "Intel(R) Core(TM) i5-6300U CPU @ 2.40GHz",
        "cpu_cores": {"physical": 2, "logical": 4},
        "ram": {"total_bytes": 8_206_966_784, "total_gb": 7.64},
        "disk_type": "SSD",
        "pg_version": "PostgreSQL 18.3",
        "os": {
            "system": "Linux",
            "release": "6.18.18-1-lts",
            "version": "#1 SMP PREEMPT_DYNAMIC",
            "machine": "x86_64",
        },
    },
}


@pytest.fixture()
def sample_session_file(tmp_path: Path) -> Path:
    """Write a valid PBT results JSON to a temp directory and return the path."""
    session_path = tmp_path / "pbt_results_20260326_2115.json"
    session_path.write_text(json.dumps(SAMPLE_SESSION), encoding="utf-8")
    return session_path


@pytest.fixture()
def sample_worker_resources() -> WorkerResources:
    """Return the WorkerResources matching the sample session."""
    return WorkerResources(
        ram_bytes=1_641_393_356,
        cpu_cores=1,
        disk_type="SSD",
    )


@pytest.fixture()
def make_run_result():
    """
    Factory fixture: create a RunResult with deterministic values.

    Usage::
        default_run = make_run_result("default", 1, score=42.0, tps=800.0)
    """

    def _factory(
        config_type: str,
        run_number: int,
        score: float = 50.0,
        tps: float = 1000.0,
        p95_ms: float = 200.0,
        memory_utilization: float = 0.5,
    ) -> RunResult:
        order_in_pair = 1 if config_type == "default" else 2
        return RunResult(
            config_type=config_type,
            run_number=run_number,
            pair_seed=50_000 + run_number - 1,
            order_in_pair=order_in_pair,
            metrics=PerformanceMetrics(
                latency_p50=p95_ms * 0.6,
                latency_p95=p95_ms,
                latency_p99=p95_ms * 1.3,
                throughput=tps,
                error_rate=0.0,
                memory_utilization=memory_utilization,
                total_queries=int(tps * 60),
                total_time=60.0,
            ),
            score=score,
            duration_seconds=90.0,
        )

    return _factory


@pytest.fixture()
def default_runs(make_run_result) -> list[RunResult]:
    """Five default-config runs with realistic variance."""
    scores = [41.2, 42.5, 40.8, 43.1, 41.9]
    tps_vals = [750.0, 780.0, 745.0, 800.0, 760.0]
    p95_vals = [220.0, 210.0, 225.0, 205.0, 215.0]
    return [
        make_run_result("default", i + 1, score=s, tps=t, p95_ms=p)
        for i, (s, t, p) in enumerate(zip(scores, tps_vals, p95_vals, strict=True))
    ]


@pytest.fixture()
def tuned_runs(make_run_result) -> list[RunResult]:
    """Five tuned-config runs showing clear improvement."""
    scores = [68.4, 70.1, 67.9, 71.2, 69.5]
    tps_vals = [1250.0, 1310.0, 1240.0, 1350.0, 1290.0]
    p95_vals = [140.0, 135.0, 145.0, 130.0, 138.0]
    return [
        make_run_result("tuned", i + 1, score=s, tps=t, p95_ms=p)
        for i, (s, t, p) in enumerate(zip(scores, tps_vals, p95_vals, strict=True))
    ]
