"""Tests for the BaseTuner lifecycle ABC using a fake in-memory strategy."""

from typing import Any, Dict, List, Optional, Tuple

import pytest

from src.tuners.base import BaseTuner
from src.tuners.utils.types import (
    GenerationOutcome,
    TunerLifecycleConfig,
    TuningStrategy,
    WorkerEvalResult,
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
        self.proposed = False
        self.steps_run = 0

    @property
    def max_rounds(self) -> int:
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
        # DB-free stand-in for the concrete BaseTuner.setup(): skip the real
        # instance/orchestrator bring-up but honor the contract by seeding
        # worker resources and drawing the initial design via the hook.
        self.setup_called = True
        self.worker_resources = WorkerResources(
            ram_bytes=2048, cpu_cores=2, disk_type="SSD"
        )
        self.initial_configs = self.propose_initial_configs()

    def propose_initial_configs(self) -> List[Dict[str, Any]]:
        self.proposed = True
        return [{"work_mem": 0.1}]

    def step(self, generation: int) -> GenerationOutcome:
        self.steps_run += 1
        score = float(generation + 1)
        self._best_score_so_far = max(self._best_score_so_far, score)
        self.generation_history.append({"generation": generation, "score": score})
        return GenerationOutcome(
            index=generation,
            best_score_this_generation=score,
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
        assert tuner.proposed is True  # propose_initial_configs invoked by setup
        assert tuner.initial_configs == [{"work_mem": 0.1}]
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
            tmp_path / "traces" / "trace_20260619_1200.json"
        )
        best_file = tmp_path / "best_configs" / "best_20260619_1200.json"
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
        # worker_resources now lives inside the tuning_session block.
        wr = results["tuning_session"]["worker_resources"]
        assert wr["ram_bytes"] == 2048
        assert wr["cpu_cores"] == 2

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


class TestBaseTunerLogging:
    """Smoke tests for the PBT-grade banner + lifecycle logging parity."""

    def test_section_headers_and_system_info_fire(
        self, lifecycle, tmp_path, caplog
    ):
        tuner = _FakeTuner(lifecycle, timestamp="t", output_root=tmp_path)
        with caplog.at_level("INFO"):
            tuner.run()
        messages = "\n".join(rec.getMessage() for rec in caplog.records)

        # Lifecycle section headers (run + _log_optimization_header).
        assert "Tuner initialization" in messages
        assert "Setting up tuning environment" in messages
        assert "Starting Optimization" in messages
        assert "Optimization Loop" in messages
        # Final summary is emitted via the shared log_final_summary(), whose
        # title carries the uppercased strategy label ("LHS COMPLETE").
        assert "LHS COMPLETE" in messages
        # System-info block emitted by log_system_info().
        assert "System Information:" in messages

    def test_strategy_label_is_uppercased_in_headers(
        self, lifecycle, tmp_path, caplog
    ):
        tuner = _FakeTuner(lifecycle, timestamp="t", output_root=tmp_path)
        with caplog.at_level("INFO"):
            tuner.run()
        messages = "\n".join(rec.getMessage() for rec in caplog.records)
        # lifecycle.strategy is LHS -> headers carry the uppercased label.
        assert "LHS Tuner initialization" in messages
        assert "LHS Optimization Loop" in messages

    def test_key_info_surfaces_carry_ansi_styling(
        self, lifecycle, tmp_path, caplog
    ):
        tuner = _FakeTuner(lifecycle, timestamp="t", output_root=tmp_path)
        with caplog.at_level("INFO"):
            tuner.run()
        # The "Best Score" surface is rendered through the COLORS context.
        best_score_recs = [
            rec for rec in caplog.records if "Best Score" in rec.getMessage()
        ]
        assert best_score_recs, "expected a 'Best Score' summary line"
        rendered = best_score_recs[0].getMessage()
        assert "\x1b[" in rendered, "expected ANSI escape in the styled surface"

    def test_banner_picks_subtitle_per_strategy(self, capsys):
        from src.utils.logger.banners import print_startup_banner

        print_startup_banner(TuningStrategy.LHS)
        lhs_out = capsys.readouterr().out
        assert "SCALPEL" in lhs_out  # LHS subtitle mentions SCALPEL

        print_startup_banner(TuningStrategy.PBT)
        pbt_out = capsys.readouterr().out
        assert "Population-Based Training" in pbt_out

        print_startup_banner(TuningStrategy.BO)
        bo_out = capsys.readouterr().out
        assert "Bayesian Optimization" in bo_out

    def test_banner_defaults_to_pbt(self, capsys):
        from src.utils.logger.banners import print_startup_banner

        print_startup_banner()
        out = capsys.readouterr().out
        assert "Population-Based Training" in out


class _FakeTiming:
    """Stand-in for a TimingRecorder's per-eval dict output."""

    def __init__(self, records):
        self._records = records

    def to_dict(self, include_summary=True):
        return {"records": self._records}


class _MetricsWithDict:
    def __init__(self, payload):
        self._payload = payload

    def to_dict(self):
        return dict(self._payload)


class TestBuildGenerationRecord:
    """The shared uniform per-round record builder (was LHS-local projection)."""

    def _tuner(self, lifecycle, tmp_path):
        tuner = _FakeTuner(lifecycle, timestamp="t", output_root=tmp_path)
        # Give the wall-clock delta a stable origin without running the loop.
        tuner.tuning_start_time = 0.0
        tuner.start_time = 0.0
        return tuner

    def test_uniform_shared_fields_always_present(self, lifecycle, tmp_path):
        tuner = self._tuner(lifecycle, tmp_path)
        record = tuner._build_generation_record(
            generation=3,
            best_score_this_round=1.5,
            worker_results=[
                WorkerEvalResult(
                    worker_id=0,
                    knob_config={"work_mem": 4},
                    score=1.5,
                    metrics=_MetricsWithDict({"throughput": 42.0}),
                )
            ],
            generation_elapsed_seconds=2.0,
            restart_count=1,
        )
        # Shared axes present regardless of strategy. The round-index key speaks
        # the strategy's vocabulary (_FakeTuner uses the generic "Round" label).
        for key in (
            "round",
            "best_score",
            "restart_count",
            "timestamp",
            "wall_clock_seconds",
            "round_elapsed_seconds",
            "worker_scores",
            "worker_configs",
        ):
            assert key in record
        assert record["round"] == 3
        assert record["restart_count"] == 1
        # Per-record ``converged`` is dropped (top-level tuning_session.converged
        # is authoritative); no record-level ``timing`` block anymore.
        assert "converged" not in record
        assert "timing" not in record
        # Population-only stats omitted when not supplied.
        assert "mean_score" not in record
        assert "strategy_params" not in record

    def test_worker_scores_carry_breakdown_and_timing(self, lifecycle, tmp_path):
        tuner = self._tuner(lifecycle, tmp_path)
        timing = _FakeTiming([{"component": "apply", "seconds": 0.4}])
        breakdown = _MetricsWithDict({"final_score": 1.5, "components": {}})
        record = tuner._build_generation_record(
            generation=0,
            best_score_this_round=1.5,
            worker_results=[
                WorkerEvalResult(
                    worker_id=2,
                    knob_config={"work_mem": 8},
                    score=1.5,
                    metrics=_MetricsWithDict({"throughput": 10.0}),
                    score_breakdown=breakdown,
                    timing=timing,
                )
            ],
            generation_elapsed_seconds=1.0,
        )
        ws = record["worker_scores"][0]
        # The two fields LHS used to capture then discard are now serialized.
        assert ws["score_breakdown"] == {"final_score": 1.5, "components": {}}
        assert ws["timing"] == {"records": [{"component": "apply", "seconds": 0.4}]}
        assert record["worker_configs"][0] == {
            "worker_id": 2,
            "config": {"work_mem": 8},
        }

    def test_population_stats_and_extra_merged(self, lifecycle, tmp_path):
        tuner = self._tuner(lifecycle, tmp_path)
        record = tuner._build_generation_record(
            generation=1,
            best_score_this_round=2.0,
            worker_results=[],
            generation_elapsed_seconds=0.5,
            mean_score=1.2,
            std_score=0.3,
            num_exploited=2,
            extra={"evaluated": [0, 1]},
        )
        assert record["mean_score"] == 1.2
        assert record["std_score"] == 0.3
        # PBT-specific per-record data is emitted flat on the record.
        assert record["num_exploited"] == 2
        assert "strategy_params" not in record
        assert record["evaluated"] == [0, 1]

    def test_failed_worker_yields_null_score(self, lifecycle, tmp_path):
        tuner = self._tuner(lifecycle, tmp_path)
        record = tuner._build_generation_record(
            generation=0,
            best_score_this_round=0.0,
            worker_results=[
                WorkerEvalResult(worker_id=0, knob_config={"k": 1}, score=None)
            ],
            generation_elapsed_seconds=0.1,
        )
        ws = record["worker_scores"][0]
        assert ws["score"] is None
        assert ws["metrics"] is None
        assert ws["score_breakdown"] is None
        # The config is still recorded even for a failed evaluation.
        assert record["worker_configs"][0]["config"] == {"k": 1}


class TestAggregateSessionTiming:
    def test_merges_gen_and_worker_timing_records(self, lifecycle, tmp_path):
        tuner = _FakeTuner(lifecycle, timestamp="t", output_root=tmp_path)
        tuner.generation_history = [
            {
                "timing": {"records": [{"component": "restore", "seconds": 1.0}]},
                "worker_scores": [
                    {"timing": {"records": [{"component": "apply", "seconds": 0.5}]}},
                    {"timing": {"records": [{"component": "apply", "seconds": 0.7}]}},
                ],
            }
        ]
        agg = tuner._aggregate_session_timing()
        assert "restore" in agg
        assert "apply" in agg
        # Two apply records aggregated.
        assert agg["apply"]["n"] == 2

