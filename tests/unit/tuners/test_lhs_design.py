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
    def test_max_generations(self, design_size, workers, expected, tmp_path):
        tuner = _make_tuner(design_size, workers, tmp_path)
        assert tuner.max_generations == expected
        assert tuner.max_generations == max(1, math.ceil(design_size / workers))

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


class TestRecalibrationWiring:
    """DB-free coverage for the global post-hoc recalibration seam.

    ``collect_metric_history`` surfaces the live metric objects, and
    ``apply_recalibration`` folds globally rescored scores + breakdowns back
    into the design records and best-state so the serialized session is
    pre-rescored.
    """

    def _seed_records(self, tuner):
        """Hand-build three evaluated design points (no DB/orchestrator)."""
        from src.utils.metrics import PerformanceMetrics

        metrics = [
            PerformanceMetrics(latency_p95=10.0 + i, throughput=100.0 + i * 5.0)
            for i in range(3)
        ]
        for i, m in enumerate(metrics):
            tuner.design_records.append(
                {
                    "design_index": i,
                    "batch": 0,
                    "score": 0.10 * i,  # arbitrary local scores
                    "config": {"knob": float(i)},
                    "metrics": m.to_dict(),
                    "score_breakdown": None,
                }
            )
            tuner._eval_metrics.append(m)
            tuner._eval_configs.append({"shared_buffers": 128 * (i + 1)})
        return metrics

    def test_collect_metric_history_skips_failed(self, tmp_path):
        tuner = _make_tuner(design_size=4, tmp_path=tmp_path)
        self._seed_records(tuner)
        # Append a failed point (None metrics) — must be excluded.
        tuner.design_records.append({"design_index": 3, "metrics": None})
        tuner._eval_metrics.append(None)
        tuner._eval_configs.append(None)

        history = tuner.collect_metric_history()
        assert len(history) == 3
        assert all(h is not None for h in history)

    def test_apply_recalibration_rewrites_records_and_best(self, tmp_path):
        from src.tuners.utils.calibration import maybe_recalibrate_scores

        tuner = _make_tuner(design_size=3, workers=1, tmp_path=tmp_path)
        self._seed_records(tuner)

        history = tuner.collect_metric_history()
        result = maybe_recalibrate_scores(history, benchmark="tpch")
        assert result.applied is True

        tuner.recalibration = result
        tuner.apply_recalibration(result)

        # Every record now carries the rescored score + a breakdown dict.
        for rec, score, breakdown in zip(
            tuner.design_records, result.scores, result.breakdowns, strict=True
        ):
            assert rec["score"] == score
            assert rec["score_breakdown"] == breakdown.to_dict()

        # Best-state reflects the calibrated rubric.
        assert tuner._best_score_so_far == max(result.scores)
        assert tuner._best_config in tuner._eval_configs
        assert tuner._best_breakdown is not None
        # The calibrated config is now the live metric_config.
        assert tuner.metric_config is result.metric_config

    def test_apply_recalibration_noop_without_history(self, tmp_path):
        from src.tuners.utils.calibration import RecalibrationResult

        tuner = _make_tuner(design_size=3, tmp_path=tmp_path)
        # No records seeded → collect returns empty → unapplied result.
        assert tuner.collect_metric_history() == []
        before = tuner._best_score_so_far
        tuner.apply_recalibration(RecalibrationResult(applied=False))
        assert tuner._best_score_so_far == before


class TestGenerationHistoryProjection:
    """``_build_generation_history`` emits a PBT-canonical view of the design.

    The shared analysis loader (``load_pbt_results``) reads observations only
    from ``generation_history[].worker_configs``/``worker_scores``. This
    projection makes an LHS trace natively loadable by that path without a
    ``design_records`` branch in the loader.
    """

    def _seed(self, tuner, records, configs):
        for rec, cfg in zip(records, configs, strict=True):
            tuner.design_records.append(rec)
            tuner._eval_configs.append(cfg)

    def test_groups_by_batch_with_local_worker_ids(self, tmp_path):
        tuner = _make_tuner(design_size=8, workers=4, tmp_path=tmp_path)
        # Two batches of 4; design_index is global, worker_id is the local offset.
        records = [
            {"design_index": i, "batch": i // 4, "score": float(i),
             "metrics": {"throughput": 100.0 + i}}
            for i in range(8)
        ]
        configs = [{"shared_buffers": 128 * (i + 1)} for i in range(8)]
        self._seed(tuner, records, configs)

        history = tuner._build_generation_history()
        assert [g["generation_index"] for g in history] == [0, 1]
        # worker_id resets to 0..3 within each batch.
        assert [c["worker_id"] for c in history[0]["worker_configs"]] == [0, 1, 2, 3]
        assert [c["worker_id"] for c in history[1]["worker_configs"]] == [0, 1, 2, 3]
        # Config is the raw decoded knob map (from _eval_configs), not fractions.
        assert history[1]["worker_configs"][0]["config"] == {"shared_buffers": 128 * 5}

    def test_scores_paired_by_worker_id_with_metrics(self, tmp_path):
        tuner = _make_tuner(design_size=4, workers=4, tmp_path=tmp_path)
        records = [
            {"design_index": i, "batch": 0, "score": float(i) + 0.5,
             "metrics": {"throughput": 10.0 * i}}
            for i in range(4)
        ]
        self._seed(tuner, records, [{"k": i} for i in range(4)])

        history = tuner._build_generation_history()
        scores = history[0]["worker_scores"]
        assert {s["worker_id"] for s in scores} == {0, 1, 2, 3}
        by_id = {s["worker_id"]: s for s in scores}
        assert by_id[2]["score"] == 2.5
        assert by_id[2]["metrics"] == {"throughput": 20.0}

    def test_failed_designs_dropped(self, tmp_path):
        tuner = _make_tuner(design_size=4, workers=4, tmp_path=tmp_path)
        records = [
            {"design_index": 0, "batch": 0, "score": 1.0, "metrics": {"x": 1}},
            {"design_index": 1, "batch": 0, "score": None, "metrics": None},
            {"design_index": 2, "batch": 0, "score": 2.0, "metrics": {"x": 2}},
        ]
        # The failed point has a None config (orchestrator failure path).
        self._seed(tuner, records, [{"k": 0}, None, {"k": 2}])

        history = tuner._build_generation_history()
        ids = [c["worker_id"] for c in history[0]["worker_configs"]]
        # worker_id 1 (the failed design) is absent; 0 and 2 survive.
        assert ids == [0, 2]

    def test_payload_includes_generation_history(self, tmp_path):
        """build_session_payload exposes the projection as a top-level key."""
        from src.utils.metrics import create_metric_config

        tuner = _make_tuner(design_size=2, workers=2, tmp_path=tmp_path)
        # build_session_payload reads scoring metadata off metric_config; in the
        # DB-free path no setup() ran, so provide a minimal one.
        tuner.metric_config = create_metric_config("oltp")
        self._seed(
            tuner,
            [{"design_index": 0, "batch": 0, "score": 1.0, "metrics": {"x": 1}}],
            [{"k": 0}],
        )
        payload = tuner.build_session_payload()
        assert "generation_history" in payload
        assert payload["generation_history"][0]["generation_index"] == 0
        # design_records stays intact alongside the projection.
        assert payload["design_records"] is tuner.design_records



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

        from src.tuner.core.barriers import GenerationBarrier

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

        from src.tuner.core.barriers import GenerationBarrier

        barriers = GenerationBarrier(num_workers=1, enabled=False)
        tuner._evaluate_batch_parallel(
            tuner._build_batch_workers(tuner.design), barriers, generation
        )

        assert captured[0]["restore_due"] is False
        assert captured[0]["next_eval_will_restore"] is False
