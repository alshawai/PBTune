"""Unit tests for LHSDesignTuner batch math and header wiring (DB-free)."""

import math

import pytest

from src.tuners.lhs_design import LHSDesignTuner
from src.tuners.utils.exceptions import TunerConfigError
from src.tuners.utils.types import TunerLifecycleConfig, TuningStrategy
from src.utils.types import STANDARD_BENCHMARK_CONFIG, clone_benchmark_config


def _make_tuner(design_size=8, workers=2, tmp_path=None):
    lifecycle = TunerLifecycleConfig(
        strategy=TuningStrategy.LHS,
        knob_tier="minimal",
        num_parallel_workers=workers,
    )
    return LHSDesignTuner(
        lifecycle,
        benchmark="tpch",
        benchmark_config=clone_benchmark_config(STANDARD_BENCHMARK_CONFIG),
        design_size=design_size,
        timestamp="20260619_1200",
        output_root=tmp_path,
    )


class TestConstruction:
    def test_strategy_forced_to_lhs(self, tmp_path):
        lifecycle = TunerLifecycleConfig(strategy=TuningStrategy.PBT)
        tuner = LHSDesignTuner(
            lifecycle,
            benchmark="tpch",
            benchmark_config=clone_benchmark_config(STANDARD_BENCHMARK_CONFIG),
            design_size=4,
            timestamp="t",
            output_root=tmp_path,
        )
        assert tuner.strategy is TuningStrategy.LHS

    def test_rejects_zero_design(self, tmp_path):
        lifecycle = TunerLifecycleConfig(strategy=TuningStrategy.LHS)
        with pytest.raises(TunerConfigError, match="design_size"):
            LHSDesignTuner(
                lifecycle,
                benchmark="tpch",
                benchmark_config=clone_benchmark_config(STANDARD_BENCHMARK_CONFIG),
                design_size=0,
                timestamp="t",
                output_root=tmp_path,
            )


class TestBatchMath:
    @pytest.mark.parametrize(
        "design_size,workers,expected",
        [
            (8, 2, 4),
            (5, 2, 3),
            (1, 1, 1),
            (10, 4, 3),
            (4, 8, 1),  # more workers than design -> single batch
        ],
    )
    def test_max_rounds(self, design_size, workers, expected, tmp_path):
        tuner = _make_tuner(design_size, workers, tmp_path)
        assert tuner.max_rounds == expected
        assert tuner.max_rounds == max(1, math.ceil(design_size / workers))

    def test_should_stop_after_design_covered(self, tmp_path):
        from src.tuners.utils.types import GenerationOutcome

        tuner = _make_tuner(design_size=5, workers=2, tmp_path=tmp_path)
        tuner.design = list(range(5))  # pretend 5 design points
        # batch 0 covers 2, batch 1 covers 4, batch 2 covers 6 >= 5 -> stop
        assert tuner.should_stop(GenerationOutcome(index=0)) is False
        assert tuner.should_stop(GenerationOutcome(index=1)) is False
        assert tuner.should_stop(GenerationOutcome(index=2)) is True


class TestHeaderProperties:
    def test_benchmark_and_workload_default(self, tmp_path):
        tuner = _make_tuner(tmp_path=tmp_path)
        # Before setup, benchmark_name is unknown and num_knobs is 0.
        assert tuner.benchmark_name == "unknown"
        assert tuner.num_knobs == 0

    def test_best_config_fractions_empty_without_space(self, tmp_path):
        tuner = _make_tuner(tmp_path=tmp_path)
        assert tuner.best_config_fractions({"x": 1}) == {}


class TestSnapshotCadence:
    """``_evaluate_batch_parallel`` computes the per-batch restore predicate.

    PBT/BO restore the pristine baseline snapshot on a per-profile cadence;
    LHS mirrors that predicate per design batch. These tests capture the
    ``restore_due`` / ``next_eval_will_restore`` kwargs passed into a *mocked*
    ``orchestrator.evaluate_worker`` (no DB, no real instances) and assert the
    cadence math directly.
    """

    def _wire_single_worker_batch(self, tuner, captured):
        """Stub the seams so ``_evaluate_batch_parallel`` runs DB-free.

        Single-worker path → one ``evaluate_worker`` call whose kwargs we
        record. The orchestrator returns a 5-tuple
        ``(metrics, score, restart, cfg, timing)``.
        """
        from unittest import mock

        tuner.design = [{"knob": 0.0}]

        instance = mock.Mock()
        instance.port = 5440
        tuner._instances = [instance]

        env = mock.Mock()
        env.get_db_config.return_value = {"host": "localhost"}
        tuner.env = env

        tuner.knob_space = mock.Mock()

        orchestrator = mock.Mock()

        def _record(worker, **kwargs):
            captured.append(kwargs)
            return (None, None, False, None, None)

        orchestrator.evaluate_worker.side_effect = _record
        tuner.orchestrator = orchestrator

    @pytest.mark.parametrize(
        "generation,expect_due,expect_next",
        [
            (0, False, False),  # gen 0 never restores; next gen 1 (1%2!=0) not due
            (1, False, True),   # gen 1 % 2 != 0; next gen 2 (2%2==0) is due
            (2, True, False),   # gen 2 % 2 == 0 and > 0; next gen 3 not due
            (3, False, True),   # gen 3 % 2 != 0; next gen 4 is due
            (4, True, False),   # gen 4 % 2 == 0; next gen 5 not due
        ],
    )
    def test_restore_cadence_interval_two(
        self, generation, expect_due, expect_next, tmp_path
    ):
        tuner = _make_tuner(design_size=8, workers=1, tmp_path=tmp_path)
        tuner.enable_snapshots = True
        tuner.lifecycle.snapshot_restore_interval = 2

        captured = []
        self._wire_single_worker_batch(tuner, captured)

        from src.tuners.engine.barriers import GenerationBarrier

        barriers = GenerationBarrier(num_workers=1, enabled=False)
        tuner._evaluate_batch_parallel(
            tuner._build_batch_workers(tuner.design), barriers, generation
        )

        assert len(captured) == 1
        assert captured[0]["restore_due"] is expect_due
        assert captured[0]["next_eval_will_restore"] is expect_next

    @pytest.mark.parametrize("generation", [0, 1, 2, 3, 4])
    def test_no_restore_when_snapshots_disabled(self, generation, tmp_path):
        tuner = _make_tuner(design_size=8, workers=1, tmp_path=tmp_path)
        tuner.enable_snapshots = False
        tuner.lifecycle.snapshot_restore_interval = 2

        captured = []
        self._wire_single_worker_batch(tuner, captured)

        from src.tuners.engine.barriers import GenerationBarrier

        barriers = GenerationBarrier(num_workers=1, enabled=False)
        tuner._evaluate_batch_parallel(
            tuner._build_batch_workers(tuner.design), barriers, generation
        )

        assert captured[0]["restore_due"] is False
        assert captured[0]["next_eval_will_restore"] is False
