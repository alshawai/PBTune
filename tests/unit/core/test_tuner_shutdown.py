"""Tests for PBTTuner shutdown behavior across normal and forced exits."""

from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace
from unittest.mock import MagicMock

import pytest

from src.config.database import DatabaseConfig
from src.tuner.main import PBTTuner


def _make_tuner(env: MagicMock, cleanup_instances: bool = False) -> PBTTuner:
    """Create a PBTTuner object with minimal state needed for run() tests."""
    tuner = PBTTuner.__new__(PBTTuner)

    tuner.logger = MagicMock()
    tuner.system_info = {}
    tuner.knob_tier = "minimal"
    tuner.knob_space = {}
    tuner.workload_type = SimpleNamespace(value="oltp")
    tuner.output_dir = Path("results")

    tuner.pbt_config = SimpleNamespace(
        population_size=1,
        num_generations=0,
        enable_snapshots=False,
    )
    tuner.force_recreate_instances = False
    tuner.cleanup_instances = cleanup_instances
    tuner.warm_start_path = None
    tuner.random_seed = 42

    tuner.db_config = DatabaseConfig(
        user="postgres",
        password="postgres",
        host="127.0.0.1",
        port=5432,
        dbname="test_dataset",
    )

    tuner.env = env
    tuner.population = SimpleNamespace(
        initialize=MagicMock(),
        setup_worker_instances=MagicMock(),
        workers=[],
        env=None,
        setup_snapshots=MagicMock(),
        should_stop=MagicMock(return_value=False),
        current_generation=0,
        history=[],
        generations_without_improvement=0,
        best_overall_metrics=None,
    )

    tuner._prune_unsupported_runtime_knobs = MagicMock()
    tuner.run_generation = MagicMock()
    tuner.save_intermediate_results = MagicMock()
    tuner._get_stop_reason = MagicMock(return_value="test-stop")

    tuner.save_final_results = MagicMock(return_value={"status": "ok"})
    tuner.print_final_summary = MagicMock()

    tuner.generation_history = []
    tuner.timestamp = "20260417_0000"
    tuner.warm_start_provenance = {"enabled": False}
    tuner.benchmark_name = "sysbench"

    return tuner


def test_run_stops_instances_on_normal_exit() -> None:
    """run() should stop instances after normal completion."""
    env = MagicMock()
    env.setup_instances.return_value = []
    env.verify_instances.return_value = {}

    tuner = _make_tuner(env)

    result = tuner.run()

    assert result == {"status": "ok"}
    env.stop_all.assert_called_once()
    env.cleanup.assert_not_called()


def test_run_stops_instances_when_setup_fails() -> None:
    """run() should still stop instances when setup raises runtime errors."""
    env = MagicMock()
    env.setup_instances.side_effect = RuntimeError("setup failed")

    tuner = _make_tuner(env)

    with pytest.raises(RuntimeError, match="setup failed"):
        tuner.run()

    env.stop_all.assert_called_once()
    tuner.save_final_results.assert_not_called()


def test_run_stops_instances_on_keyboard_interrupt_during_setup() -> None:
    """run() should stop instances when Ctrl+C interrupts setup."""
    env = MagicMock()
    env.setup_instances.side_effect = KeyboardInterrupt()

    tuner = _make_tuner(env)

    with pytest.raises(KeyboardInterrupt):
        tuner.run()

    env.stop_all.assert_called_once()
    tuner.save_final_results.assert_not_called()


def test_evaluate_worker_handles_recovery_exception_after_connection_failure() -> None:
    """Recovery failures after connection errors should not escape evaluate_worker."""
    tuner = PBTTuner.__new__(PBTTuner)
    tuner.current_generation = 0
    tuner._restart_logged_this_gen = False
    tuner.restart_count = 0
    tuner.metric_config = SimpleNamespace(latency_metric="p95")
    tuner.pbt_config = SimpleNamespace(dead_config_score=0.0, crash_score=0.0)

    tuner.orchestrator = MagicMock()
    tuner.orchestrator.evaluate_worker.side_effect = ConnectionError(
        "postgres unreachable"
    )

    tuner.env = MagicMock()
    tuner.env.recover_instance.side_effect = RuntimeError("docker read timeout")

    worker = SimpleNamespace(worker_id=0)

    metrics, score = tuner.evaluate_worker(worker)

    assert score == 0.0
    assert metrics.failure_type == "crash_dead"
    tuner.env.recover_instance.assert_called_once_with(0)
