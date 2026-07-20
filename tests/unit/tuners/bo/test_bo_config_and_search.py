"""Unit tests for Bayesian Optimization baseline components."""

import pytest
import json
import argparse

from src.knobs import get_knob_space
from src.tuners.bo.search_space import build_configspace, configspace_to_knobs
from src.tuners.bo.config import BOConfig
from src.tuners.bo.objective import evaluate_config
from src.tuners.engine.worker import BaseWorker
from src.utils.hardware_info import WorkerResources, detect_worker_resources
from src.utils.types import BenchmarkConfig, TuningMode
from src.utils.metrics import PerformanceMetrics
from src.utils.scoring.contracts import ScoreBreakdown
from src.evaluation.loader import load_tuning_session


class TestPBTSessionParity:
    """Test extracting comparable BO settings from a PBT tuning session."""

    def test_bo_config_extracts_pbt_session_parameters(self, tmp_path):
        pbt_session = tmp_path / "pbt_results_test.json"
        pbt_session.write_text(
            json.dumps(
                {
                    "tuning_session": {
                        "knob_tier": "minimal",
                        "workload_type": "oltp",
                        "benchmark_name": "sysbench",
                        "sysbench_tables": 2,
                        "sysbench_table_size": 10000,
                        "sysbench_workload": "oltp_read_write",
                        "sysbench_duration_seconds": 15.0,
                        "sysbench_warmup_seconds": 10.0,
                        "tpch_scale_factor": 0.01,
                        "tpch_warmup_passes": 0,
                        "tuning_mode": "online",
                        "population_size": 4,
                        "num_parallel_workers": 4,
                        "total_generations": 10,
                    },
                    "best_configuration": {
                        "knobs": {
                            "effective_cache_size": 0.5,
                            "random_page_cost": 1.2,
                            "work_mem": 0.01,
                        }
                    },
                }
            ),
            encoding="utf-8",
        )

        args = argparse.Namespace(
            config="standard",
            benchmark_config=None,
            iterations=None,
            seed=123,
            tier=None,
            benchmark="tpch",
            workload="olap",
            duration=60.0,
            warmup=30.0,
            tuning_mode="offline",
            sysbench_tables=4,
            sysbench_table_size=100000,
            sysbench_workload="oltp_read_only",
            scale_factor=1.0,
            tpch_warmup_passes=1,
            no_docker=True,
            docker_image=None,
            force_recreate_instances=False,
            force_recreate_baseline=False,
            output_dir="results",
            verbose="INFO",
            range_update_interval=5,
            bo_surrogate="rf",
            batched_bo=True,
            resource_division=None,
            pbt_session=str(pbt_session),
        )

        config = BOConfig.from_args(args)
        benchmark_config = config.benchmark_config

        # pop_size(4) × actual_generations(10) = 40 equal-evaluation budget
        assert config.n_iterations == 40
        assert config.random_seed == 123
        assert config.knob_tier == "minimal"
        assert config.max_workers == 1
        assert benchmark_config.benchmark == "sysbench"
        assert benchmark_config.workload_type == "oltp"
        assert benchmark_config.evaluation_duration == 15.0
        assert benchmark_config.warmup_duration == 10.0
        assert benchmark_config.tuning_mode == TuningMode.ONLINE
        assert benchmark_config.sysbench_tables == 2
        assert benchmark_config.sysbench_table_size == 10000
        assert benchmark_config.sysbench_workload == "oltp_read_write"
        assert benchmark_config.scale_factor == 0.01
        assert benchmark_config.warmup_passes == 0
        assert config.pbt_session_path == pbt_session
        assert config.pbt_knob_names == (
            "effective_cache_size",
            "random_page_cost",
            "work_mem",
        )

    def test_bo_config_explicit_iterations_override_pbt_budget(self, tmp_path):
        pbt_session = tmp_path / "pbt_results_test.json"
        pbt_session.write_text(
            json.dumps(
                {
                    "tuning_session": {
                        "knob_tier": "minimal",
                        "workload_type": "oltp",
                        "benchmark_name": "sysbench",
                        "population_size": 4,
                        "num_parallel_workers": 2,
                        "total_generations": 10,
                    }
                }
            ),
            encoding="utf-8",
        )

        args = argparse.Namespace(
            config="standard",
            benchmark_config=None,
            iterations=7,
            seed=123,
            tier=None,
            benchmark="sysbench",
            workload="oltp",
            duration=15.0,
            warmup=10.0,
            tuning_mode="offline",
            sysbench_tables=2,
            sysbench_table_size=10000,
            sysbench_workload="oltp_read_write",
            scale_factor=0.01,
            tpch_warmup_passes=0,
            no_docker=True,
            docker_image=None,
            force_recreate_instances=False,
            force_recreate_baseline=False,
            output_dir="results",
            verbose="INFO",
            range_update_interval=5,
            bo_surrogate="rf",
            batched_bo=True,
            resource_division=None,
            pbt_session=str(pbt_session),
        )

        config = BOConfig.from_args(args)

        assert config.n_iterations == 7

    def test_bo_config_falls_back_when_pbt_parallel_workers_missing(self, tmp_path):
        pbt_session = tmp_path / "pbt_results_test.json"
        pbt_session.write_text(
            json.dumps(
                {
                    "tuning_session": {
                        "knob_tier": "minimal",
                        "workload_type": "oltp",
                        "benchmark_name": "sysbench",
                        "population_size": 4,
                        "total_generations": 10,
                    }
                }
            ),
            encoding="utf-8",
        )

        args = argparse.Namespace(
            config="standard",
            benchmark_config=None,
            iterations=None,
            seed=123,
            tier=None,
            benchmark="sysbench",
            workload="oltp",
            duration=15.0,
            warmup=10.0,
            tuning_mode="offline",
            sysbench_tables=2,
            sysbench_table_size=10000,
            sysbench_workload="oltp_read_write",
            scale_factor=0.01,
            tpch_warmup_passes=0,
            no_docker=True,
            docker_image=None,
            force_recreate_instances=False,
            force_recreate_baseline=False,
            output_dir="results",
            verbose="INFO",
            range_update_interval=5,
            bo_surrogate="rf",
            batched_bo=True,
            resource_division=None,
            pbt_session=str(pbt_session),
        )

        config = BOConfig.from_args(args)

        # pop_size(4) × actual_generations(10) = 40 (no parallel_workers needed)
        assert config.n_iterations == 40
        assert config.max_workers == 1

    def test_bo_config_falls_back_when_pbt_session_invalid(self, tmp_path):
        pbt_session = tmp_path / "pbt_results_test.json"
        pbt_session.write_text("{invalid json", encoding="utf-8")

        args = argparse.Namespace(
            config="standard",
            benchmark_config=None,
            iterations=None,
            seed=123,
            tier=None,
            benchmark="sysbench",
            workload="oltp",
            duration=15.0,
            warmup=10.0,
            tuning_mode="offline",
            sysbench_tables=2,
            sysbench_table_size=10000,
            sysbench_workload="oltp_read_write",
            scale_factor=0.01,
            tpch_warmup_passes=0,
            no_docker=True,
            docker_image=None,
            force_recreate_instances=False,
            force_recreate_baseline=False,
            output_dir="results",
            verbose="INFO",
            range_update_interval=5,
            bo_surrogate="rf",
            batched_bo=True,
            resource_division=None,
            pbt_session=str(pbt_session),
        )

        config = BOConfig.from_args(args)

        assert config.n_iterations == 80
        assert config.max_workers == 1

    def test_bo_config_requires_tier_without_pbt_session(self):
        args = argparse.Namespace(
            config="standard",
            benchmark_config=None,
            iterations=None,
            seed=123,
            tier=None,
            benchmark="sysbench",
            workload="oltp",
            duration=15.0,
            warmup=10.0,
            tuning_mode="offline",
            sysbench_tables=2,
            sysbench_table_size=10000,
            sysbench_workload="oltp_read_write",
            scale_factor=0.01,
            tpch_warmup_passes=0,
            no_docker=True,
            docker_image=None,
            force_recreate_instances=False,
            force_recreate_baseline=False,
            output_dir="results",
            verbose="INFO",
            range_update_interval=5,
            bo_surrogate="rf",
            pbt_session=None,
        )

        with pytest.raises(ValueError, match="Either --tier or --pbt-session"):
            BOConfig.from_args(args)


class TestSearchSpaceTranslation:
    """Test ConfigSpace translation."""

    def test_build_configspace_minimal(self):
        """Test building ConfigSpace for minimal tier."""
        knob_space = get_knob_space("minimal")
        resources = detect_worker_resources()
        knob_space.resolve_hardware_ranges(resources)

        cs = build_configspace(knob_space, seed=42)

        assert cs is not None
        assert len(cs) == len(knob_space.knobs)

    def test_build_configspace_core(self):
        """Test building ConfigSpace for core tier."""
        knob_space = get_knob_space("core")
        resources = detect_worker_resources()
        knob_space.resolve_hardware_ranges(resources)

        cs = build_configspace(knob_space, seed=42)

        assert cs is not None
        assert len(cs) == len(knob_space.knobs)

    def test_configspace_to_knobs_conversion(self):
        """Test converting ConfigSpace config back to knob dict."""
        knob_space = get_knob_space("minimal")
        resources = detect_worker_resources()
        knob_space.resolve_hardware_ranges(resources)

        cs = build_configspace(knob_space, seed=42)

        # Sample a random configuration
        config = cs.sample_configuration()

        # Convert back to knob dict
        knob_dict = configspace_to_knobs(config, knob_space)

        assert isinstance(knob_dict, dict)
        assert len(knob_dict) > 0

        # Verify all values are valid Python types
        for _key, value in knob_dict.items():
            assert not isinstance(value, type(None)) or value is None
            assert isinstance(value, (int, float, str, bool, type(None)))

    def test_configspace_sampling_reproducibility(self):
        """Test that ConfigSpace sampling is reproducible with same seed."""
        knob_space = get_knob_space("minimal")
        resources = detect_worker_resources()
        knob_space.resolve_hardware_ranges(resources)

        cs1 = build_configspace(knob_space, seed=42)
        cs2 = build_configspace(knob_space, seed=42)

        config1 = cs1.sample_configuration()
        config2 = cs2.sample_configuration()

        # Configurations should be identical with same seed
        assert config1 == config2

    def test_configspace_validation(self):
        """Test that sampled configs pass knob space validation."""
        knob_space = get_knob_space("core")
        resources = detect_worker_resources()
        knob_space.resolve_hardware_ranges(resources)

        cs = build_configspace(knob_space, seed=42)

        for _ in range(10):
            config = cs.sample_configuration()
            knob_dict = configspace_to_knobs(config, knob_space)

            # Repair dependencies
            repaired = knob_space.repair_config_dependencies(knob_dict)

            # Validate
            assert knob_space.validate_config(repaired)


class TestObjectiveEvaluation:
    """Test the reusable BO evaluation helper."""

    def test_evaluate_config_returns_cost_and_metrics(self):
        knob_space = get_knob_space("minimal")
        resources = detect_worker_resources()
        knob_space.resolve_hardware_ranges(resources)

        configspace = build_configspace(knob_space, seed=42)
        config = configspace.sample_configuration()
        worker = BaseWorker(worker_id=0, knob_space=knob_space)

        expected_metrics = PerformanceMetrics(throughput=120.0, latency_p95=18.0)

        class DummyMetricConfig:
            scoring_policy = "default"
            scoring_policy_version = "1.0"
            metric_reference_version = "1.0"
            workload_features = {}

            def get_normalization_metadata(self):
                return {}

            def compute_score(self, metrics, worker_logger=None):
                return ScoreBreakdown(final_score=87.5)

        class DummyConfig:
            metric_config = DummyMetricConfig()

        class DummyOrchestrator:
            def __init__(self):
                self.received_worker = None
                self.config = DummyConfig()

            def evaluate_worker(self, worker, apply_config=True, random_seed=None,
                               restore_due=False, next_eval_will_restore=False,
                               barriers=None):
                self.received_worker = worker
                self.received_restore_due = restore_due
                worker.score_breakdown = ScoreBreakdown(final_score=87.5)
                from src.utils.timing import TimingRecorder
                return expected_metrics, 87.5, False, {}, TimingRecorder()

        orchestrator = DummyOrchestrator()

        cost, knob_config, metrics, score, score_breakdown, restarted, wall_time, eval_timing = (
            evaluate_config(
                config=config,
                worker=worker,
                orchestrator=orchestrator,
                knob_space=knob_space,
                previous_config=None,
            )
        )

        assert cost == 12.5
        assert score == 87.5
        assert restarted is False
        assert wall_time >= 0.0
        assert metrics == expected_metrics
        assert knob_config == worker.knob_config
        assert orchestrator.received_worker is worker
        assert worker.force_restart_next_eval is True
        # The recorder returned by the orchestrator is propagated unchanged.
        from src.utils.timing import TimingRecorder as _TR
        assert isinstance(eval_timing, _TR)


class TestSessionPayload:
    """BO session assembly through the shared ``BaseTuner`` seam.

    Replaces the legacy ``write_bo_results`` schema tests. The flat writer is
    gone: assembly is now ``BaseTuner._assemble_results`` (shared envelope:
    header, best config, worker resources, generation history, bootstrap
    breakdown, timing summary) merged with ``BOTuner.build_session_payload``
    (nested ``scoring`` / ``strategy_params`` / ``convergence``). Disk writing
    is the separate ``write_session_json`` seam.

    These drive the real methods on a bare ``BOTuner`` built via ``__new__``
    with exactly the attribute surface the seam reads — no constructor, no
    setup, no database.
    """

    @staticmethod
    def _make_tuner(tmp_path, *, iteration_log, n_iterations, cotenancy_degree=1):
        from types import SimpleNamespace

        from src.tuners.bo.tuner import BOTuner
        from src.tuners.utils.types import TuningStrategy
        from src.utils.timing import TimingRecorder

        output_root = tmp_path / "results"
        output_root.mkdir(parents=True, exist_ok=True)

        knob_space = get_knob_space("minimal")
        resources = detect_worker_resources()
        knob_space.resolve_hardware_ranges(resources)

        tuner = BOTuner.__new__(BOTuner)

        # Identity / header surface.
        tuner.strategy = TuningStrategy.BO
        tuner.timestamp = "20260613_0900"
        tuner.output_root = output_root
        tuner._rounds_completed = 1
        tuner.start_time = 0.0
        tuner.tuning_start_time = 0.0

        tuner.lifecycle = SimpleNamespace(
            strategy=TuningStrategy.BO,
            knob_tier="minimal",
            knob_source="expert",
            random_seed=123,
            num_parallel_workers=cotenancy_degree,
            tuning_mode=SimpleNamespace(value="offline"),
            snapshot_restore_interval=1,
        )

        # Bundle-resolved fields (normally set in BaseTuner.setup()).
        tuner._workload_type = SimpleNamespace(value="oltp")
        tuner._benchmark_name = "sysbench"
        tuner.enable_snapshots = False

        tuner.full_knob_space = knob_space
        tuner.metric_config = SimpleNamespace(
            get_scoring_metadata=lambda: {
                "scoring_policy": "feature_driven_v2",
                "scoring_policy_version": "1.0",
                "metric_reference_version": "v1",
                "workload_features": {},
                "normalization_metadata": {},
            }
        )

        tuner.benchmark_config = BenchmarkConfig(
            benchmark="sysbench",
            workload_type="oltp",
        )

        tuner.bo_config = BOConfig(
            n_iterations=n_iterations,
            random_seed=123,
            knob_tier="minimal",
            benchmark_config=tuner.benchmark_config,
            bo_surrogate="gp",
            cotenancy_degree=cotenancy_degree,
        )
        tuner.bo_surrogate = "gp"
        tuner.requested_pilot_size = 0
        tuner.actual_pilot_size = 0

        tuner.worker_resources = WorkerResources(
            ram_bytes=16 * 1024 * 1024 * 1024,
            cpu_cores=8,
            disk_type="SSD",
        )

        tuner.iteration_log = iteration_log
        tuner.generation_history = tuner._build_generation_history()
        tuner._early_stopped = False
        tuner._early_stopping_enabled = True
        tuner._early_stopping_patience = 50
        tuner._stale_counter = 0
        tuner.bo_timing = TimingRecorder()
        tuner.bootstrap_timing = TimingRecorder()
        tuner.system_info = {"hostname": "test-host", "cpu_count": 8}
        tuner.session_environment = None
        tuner.cotenant = None
        return tuner

    @staticmethod
    def _iteration_log():
        return [
            {
                "iteration": 0,
                "config": {"shared_buffers": 0.4},
                "metrics": PerformanceMetrics(throughput=100.0, latency_p95=50.0),
                "score": 0.5,
                "cost": 50.0,
                "wall_clock_seconds": 30.0,
                "bo_overhead_seconds": 1.0,
                "restarted": False,
                "timestamp": 1234567890.0,
                "score_breakdown": ScoreBreakdown(final_score=0.5),
            },
            {
                "iteration": 1,
                "config": {"shared_buffers": 0.6},
                "metrics": PerformanceMetrics(throughput=120.0, latency_p95=45.0),
                "score": 0.6,
                "cost": 40.0,
                "wall_clock_seconds": 30.0,
                "bo_overhead_seconds": 1.0,
                "restarted": False,
                "timestamp": 1234567920.0,
                "score_breakdown": ScoreBreakdown(final_score=0.6),
            },
        ]

    def _assemble(self, tuner):
        return tuner._assemble_results(
            total_time=62.0, tuning_time=60.0, bootstrap_seconds=2.0
        )

    def test_nested_schema_and_best_config(self, tmp_path):
        tuner = self._make_tuner(
            tmp_path, iteration_log=self._iteration_log(), n_iterations=2
        )
        results = self._assemble(tuner)

        ts = results["tuning_session"]
        assert ts["tuning_strategy"] == "bo"
        assert ts["seed"] == 123
        assert ts["num_rounds"] == 1
        assert ts["total_evaluations"] == 2

        # Nested schema: scoring + strategy_params present, no flat/PBT keys.
        assert "scoring" in ts
        assert "strategy_params" in ts
        sp = ts["strategy_params"]
        # BO identity metadata folded into strategy_params.
        assert sp["optimizer"] == "bayesian_optimization"
        assert sp["bo_library"] == "smac3"
        assert sp["bo_acquisition"] == "expected_improvement"
        assert sp["n_iterations"] == 2
        assert sp["bo_surrogate"] == "gp"
        assert "population_size" not in ts
        assert "population_size" not in sp
        assert "n_workers" not in ts
        # Dead flat header keys are gone.
        assert "optimizer" not in ts
        assert "iterations" not in ts
        assert "requested_iterations" not in ts

        assert results["best_configuration"]["score"] == 0.6
        assert "history" in results
        # worker_resources now nested under tuning_session (was top-level sibling).
        assert "worker_resources" in ts

    def test_strategy_params_resource_equalization_and_cotenancy(self, tmp_path):
        tuner = self._make_tuner(
            tmp_path,
            iteration_log=self._iteration_log(),
            n_iterations=2,
            cotenancy_degree=4,
        )
        results = self._assemble(tuner)

        sp = results["tuning_session"]["strategy_params"]
        assert sp["cotenancy_degree"] == 4
        # No PBT worker-resources injected → equalization off.
        assert sp["resource_equalization"] is False
        assert sp["pbt_session_sync"] is None

    def test_generation_history_cumulative_best(self, tmp_path):
        tuner = self._make_tuner(
            tmp_path, iteration_log=self._iteration_log(), n_iterations=2
        )
        results = self._assemble(tuner)

        gen_hist = results["history"]
        assert len(gen_hist) == 2
        assert gen_hist[0]["iteration"] == 0
        assert gen_hist[0]["best_score"] == 0.5
        assert gen_hist[0]["worker_scores"][0]["score"] == 0.5
        # Cumulative best carries forward.
        assert gen_hist[1]["iteration"] == 1
        assert gen_hist[1]["best_score"] == 0.6

    def test_roundtrip_through_writer_and_loader(self, tmp_path):
        from src.tuners.utils.session_writer import write_session_json

        tuner = self._make_tuner(
            tmp_path, iteration_log=self._iteration_log(), n_iterations=2
        )
        results = self._assemble(tuner)

        result_file = write_session_json(
            results,
            output_dir=tuner.output_root,
            filename=f"trace_{tuner.timestamp}.json",
        )
        assert result_file.exists()

        written = json.loads(result_file.read_text(encoding="utf-8"))
        assert written["tuning_session"]["seed"] == 123
        assert written["tuning_session"]["tuning_strategy"] == "bo"

        loaded = load_tuning_session(result_file)
        assert loaded is not None
        assert loaded.best_score == 0.6


class TestParallelBOConfiguration:
    """Test parallel BO and resource equalization configuration."""

    def test_bo_config_extracts_worker_resources_from_pbt_session(self, tmp_path):
        """Test that worker_resources are extracted from PBT session."""
        pbt_session = tmp_path / "pbt_results_test.json"
        pbt_session.write_text(
            json.dumps(
                {
                    "tuning_session": {
                        "knob_tier": "minimal",
                        "workload_type": "oltp",
                        "benchmark_name": "sysbench",
                        "population_size": 4,
                        "num_parallel_workers": 4,
                        "total_generations": 10,
                    },
                    "worker_resources": {
                        "ram_bytes": 8589934592,
                        "cpu_cores": 4,
                        "disk_type": "SSD",
                    },
                }
            ),
            encoding="utf-8",
        )

        args = argparse.Namespace(
            config="standard",
            benchmark_config=None,
            iterations=None,
            seed=123,
            tier=None,
            benchmark="sysbench",
            workload="oltp",
            duration=15.0,
            warmup=10.0,
            tuning_mode="offline",
            sysbench_tables=2,
            sysbench_table_size=10000,
            sysbench_workload="oltp_read_write",
            scale_factor=0.01,
            tpch_warmup_passes=0,
            no_docker=True,
            docker_image=None,
            force_recreate_instances=False,
            force_recreate_baseline=False,
            output_dir="results",
            verbose="INFO",
            range_update_interval=5,
            bo_surrogate="rf",
            batched_bo=True,
            resource_division=None,
            pbt_session=str(pbt_session),
        )

        config = BOConfig.from_args(args)

        assert config.max_workers == 1
        assert config.pbt_worker_resources is not None
        assert config.pbt_worker_resources["ram_bytes"] == 8589934592
        assert config.pbt_worker_resources["cpu_cores"] == 4
        assert config.pbt_worker_resources["disk_type"] == "SSD"

    def test_bo_config_max_workers_cli_override(self, tmp_path):
        """Test that --batched-bo CLI arg overrides PBT-derived value."""
        pbt_session = tmp_path / "pbt_results_test.json"
        pbt_session.write_text(
            json.dumps(
                {
                    "tuning_session": {
                        "knob_tier": "minimal",
                        "workload_type": "oltp",
                        "benchmark_name": "sysbench",
                        "population_size": 4,
                        "num_parallel_workers": 2,
                        "total_generations": 10,
                    }
                }
            ),
            encoding="utf-8",
        )

        args = argparse.Namespace(
            config="standard",
            benchmark_config=None,
            iterations=None,
            seed=123,
            tier=None,
            benchmark="sysbench",
            workload="oltp",
            duration=15.0,
            warmup=10.0,
            tuning_mode="offline",
            sysbench_tables=2,
            sysbench_table_size=10000,
            sysbench_workload="oltp_read_write",
            scale_factor=0.01,
            tpch_warmup_passes=0,
            no_docker=True,
            docker_image=None,
            force_recreate_instances=False,
            force_recreate_baseline=False,
            output_dir="results",
            verbose="INFO",
            range_update_interval=5,
            bo_surrogate="rf",
            batched_bo=True,
            resource_division=2,
            pbt_session=str(pbt_session),
        )

        config = BOConfig.from_args(args)

        assert config.max_workers == 1

    def test_bo_config_default_max_workers(self):
        """Test that max_workers defaults to 1."""
        args = argparse.Namespace(
            config="standard",
            benchmark_config=None,
            iterations=None,
            seed=123,
            tier="minimal",
            benchmark="sysbench",
            workload="oltp",
            duration=15.0,
            warmup=10.0,
            tuning_mode="offline",
            sysbench_tables=2,
            sysbench_table_size=10000,
            sysbench_workload="oltp_read_write",
            scale_factor=0.01,
            tpch_warmup_passes=0,
            no_docker=True,
            docker_image=None,
            force_recreate_instances=False,
            force_recreate_baseline=False,
            output_dir="results",
            verbose="INFO",
            range_update_interval=5,
            bo_surrogate="rf",
            batched_bo=True,
            resource_division=None,
            pbt_session=None,
        )

        config = BOConfig.from_args(args)

        assert config.max_workers == 1
        assert config.pbt_worker_resources is None
