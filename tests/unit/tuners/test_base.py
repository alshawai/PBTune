"""Tests for the BaseTuner lifecycle ABC using a fake in-memory strategy."""

from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

import pytest

from src.tuners.base import BaseTuner
from src.tuners.utils.types import (
    GenerationOutcome,
    TunerLifecycleConfig,
    TuningStrategy,
)
from src.utils.hardware_info import WorkerResources


class _FakeMetrics:
    def to_dict(self) -> Dict[str, Any]:
        return {"throughput": 100.0}


class _FakeTuner(BaseTuner):
    """Minimal concrete tuner that runs entirely in memory (no DB/instances)."""

    def __init__(self, lifecycle, *, timestamp, output_root, stop_after=2):
        super().__init__(lifecycle, timestamp=timestamp, output_root=output_root)
        self.stop_after = stop_after
        self.setup_called = False
        self.teardown_called = False
        self.steps_run = 0

    @property
    def max_generations(self) -> int:
        return 10

    @property
    def num_knobs(self) -> int:
        return 5

    @property
    def workload_type_value(self) -> str:
        return "oltp"

    @property
    def benchmark_name(self) -> str:
        return "sysbench"

    def setup(self) -> None:
        self.setup_called = True
        self.worker_resources = WorkerResources(
            ram_bytes=2048, cpu_cores=2, disk_type="SSD"
        )

    def propose_initial_configs(self) -> List[Dict[str, Any]]:
        return [{"work_mem": 0.1}]

    def step(self, generation: int) -> GenerationOutcome:
        self.steps_run += 1
        score = float(generation + 1)
        self.generation_history.append({"generation": generation, "score": score})
        return GenerationOutcome(
            index=generation,
            best_score_so_far=score,
            best_score_this_generation=score,
            num_evaluations=1,
        )

    def should_stop(self, outcome: GenerationOutcome) -> bool:
        return outcome.index + 1 >= self.stop_after

    def collect_best(self) -> Tuple[Dict[str, Any], float, Optional[Any]]:
        return {"work_mem": 0.25}, float(self.steps_run), _FakeMetrics()

    def build_session_payload(self) -> Dict[str, Any]:
        return {
            "convergence": {"converged": True},
            "tuning_session": {"design_size": 8},
        }

    def teardown(self) -> None:
        self.teardown_called = True

    def best_config_fractions(self, best_config):
        return best_config


@pytest.fixture
def lifecycle():
    return TunerLifecycleConfig(
        strategy=TuningStrategy.LHS,
        knob_tier="core",
        knob_source="expert",
        num_parallel_workers=2,
        random_seed=7,
    )


class TestBaseTunerLifecycle:
    def test_run_drives_full_lifecycle(self, lifecycle, tmp_path):
        tuner = _FakeTuner(
            lifecycle, timestamp="20260619_1200", output_root=tmp_path, stop_after=2
        )
        results = tuner.run()

        assert tuner.setup_called is True
        assert tuner.teardown_called is True
        assert tuner.steps_run == 2  # stopped after 2 generations

        session = results["tuning_session"]
        assert session["tuning_strategy"] == "lhs"
        assert session["knob_tier"] == "core"
        assert session["num_knobs"] == 5
        assert session["seed"] == 7
        assert session["num_parallel_workers"] == 2
        assert session["design_size"] == 8  # merged from payload's tuning_session
        assert "total_time_seconds" in session

    def test_run_writes_session_and_best_config(self, lifecycle, tmp_path):
        tuner = _FakeTuner(
            lifecycle, timestamp="20260619_1200", output_root=tmp_path
        )
        tuner.run()

        session_file = (
            tmp_path / "tuning_sessions" / "lhs_results_20260619_1200.json"
        )
        best_file = tmp_path / "best_configs" / "best_config_20260619_1200.json"
        assert session_file.exists()
        assert best_file.exists()

    def test_best_configuration_block(self, lifecycle, tmp_path):
        tuner = _FakeTuner(lifecycle, timestamp="t", output_root=tmp_path)
        results = tuner.run()
        best = results["best_configuration"]
        assert best["knobs"] == {"work_mem": 0.25}
        assert best["metrics"] == {"throughput": 100.0}
        assert best["score"] == 2.0

    def test_worker_resources_serialized(self, lifecycle, tmp_path):
        tuner = _FakeTuner(lifecycle, timestamp="t", output_root=tmp_path)
        results = tuner.run()
        assert results["worker_resources"]["ram_bytes"] == 2048
        assert results["worker_resources"]["cpu_cores"] == 2

    def test_teardown_runs_even_if_step_raises(self, lifecycle, tmp_path):
        class _Boom(_FakeTuner):
            def step(self, generation):
                raise RuntimeError("boom")

        tuner = _Boom(lifecycle, timestamp="t", output_root=tmp_path)
        with pytest.raises(RuntimeError, match="boom"):
            tuner.run()
        assert tuner.teardown_called is True

    def test_cannot_instantiate_abstract_base(self, lifecycle, tmp_path):
        with pytest.raises(TypeError):
            BaseTuner(lifecycle, timestamp="t", output_root=tmp_path)
