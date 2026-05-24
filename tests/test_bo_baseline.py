"""Unit tests for Bayesian Optimization baseline components."""

import pytest
import json
import argparse

from src.tuner.config import get_knob_space
from src.scripts.bo_baseline.search_space import build_configspace, configspace_to_knobs
from src.scripts.bo_baseline.config import BOConfig
from src.scripts.bo_baseline.objective import evaluate_config
from src.scripts.bo_baseline.result_writer import write_bo_results
from src.tuner.core.worker import Worker
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

        assert config.n_iterations == 40
        assert config.random_seed == 123
        assert config.knob_tier == "minimal"
        assert config.max_workers == 4
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
            batched_bo=True, resource_division=None,
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
            batched_bo=True, resource_division=None,
            pbt_session=str(pbt_session),
        )

        config = BOConfig.from_args(args)

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
            batched_bo=True, resource_division=None,
            pbt_session=str(pbt_session),
        )

        config = BOConfig.from_args(args)

        assert config.n_iterations == 120
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
        worker = Worker(worker_id=0, knob_space=knob_space)

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

            def evaluate_worker(self, worker, apply_config=True):
                self.received_worker = worker
                worker.score_breakdown = ScoreBreakdown(final_score=87.5)
                return expected_metrics, 87.5, False

        orchestrator = DummyOrchestrator()

        cost, knob_config, metrics, score, score_breakdown, restarted, wall_time = (
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
        assert worker.force_restart_next_eval is False


class TestResultFormat:
    """Test result serialization format."""

    def test_result_format_compatibility(self, tmp_path):
        """Test that generated results are compatible with loader."""
        knob_space = get_knob_space("minimal")
        resources = detect_worker_resources()
        knob_space.resolve_hardware_ranges(resources)

        config = BOConfig(
            n_iterations=3,
            random_seed=123,
            knob_tier="minimal",
            benchmark_config=BenchmarkConfig(
                benchmark="sysbench",
                workload_type="oltp",
            ),
            max_workers=2,
        )

        worker_resources = WorkerResources(
            ram_bytes=16 * 1024 * 1024 * 1024,
            cpu_cores=8,
            disk_type="SSD",
        )

        system_info = {
            "hostname": "test-host",
            "platform": "linux",
            "cpu_count": 8,
        }

        # Create mock iteration log
        iteration_log = [
            {
                "iteration": 0,
                "config": {"shared_buffers": 1024},
                "metrics": {"throughput": 100.0, "latency_p95": 50.0},
                "score": 0.5,
                "cost": 50.0,
                "wall_clock_seconds": 30.0,
                "restarted": False,
                "timestamp": 1234567890.0,
                "score_breakdown": ScoreBreakdown(final_score=0.5),
            },
            {
                "iteration": 1,
                "config": {"shared_buffers": 2048},
                "metrics": {"throughput": 120.0, "latency_p95": 45.0},
                "score": 0.6,
                "cost": 40.0,
                "wall_clock_seconds": 30.0,
                "restarted": False,
                "timestamp": 1234567920.0,
                "score_breakdown": ScoreBreakdown(final_score=0.6),
            },
        ]

        class DummyMetricConfig:
            scoring_policy = "default"
            scoring_policy_version = "1.0"
            metric_reference_version = "1.0"
            workload_features = {}

            def get_normalization_metadata(self):
                return {}

        results = write_bo_results(
            knob_space=knob_space,
            config=config,
            worker_resources=worker_resources,
            system_info=system_info,
            iteration_log=iteration_log,
            total_time=60.0,
            output_dir=tmp_path,
            metric_config=DummyMetricConfig(),
            bo_surrogate="gp",
        )

        # Verify result structure
        assert "tuning_session" in results
        assert "best_configuration" in results
        assert "worker_resources" in results
        assert "generation_history" in results

        # Verify required fields
        assert results["tuning_session"]["optimizer"] == "bayesian_optimization"
        assert results["tuning_session"]["bo_library"] == "smac3"
        assert results["tuning_session"]["bo_surrogate"] == "gp"
        assert results["tuning_session"]["seed"] == 123
        assert results["best_configuration"]["score"] == 0.6

        # Verify parallel BO fields
        assert results["tuning_session"]["iterations"] == 2
        assert results["tuning_session"]["num_parallel_workers"] == 2
        assert "n_iterations" not in results["tuning_session"]
        assert "n_workers" not in results["tuning_session"]
        assert "population_size" not in results["tuning_session"]
        assert results["tuning_session"]["resource_equalization"] is False

        # Find the written file
        result_files = list(tmp_path.glob("**/bo_results_*.json"))
        assert len(result_files) == 1

        # Load and verify with loader
        result_file = result_files[0]
        loaded = load_tuning_session(result_file)

        assert loaded is not None
        assert loaded.best_score == 0.6
        written = json.loads(result_file.read_text(encoding="utf-8"))
        assert written["tuning_session"]["seed"] == 123
        assert isinstance(written["best_configuration"]["score_breakdown"], dict)
        assert written["best_configuration"]["score_breakdown"]["final_score"] == 0.6
        assert isinstance(
            written["generation_history"][0]["worker_scores"][0]["score_breakdown"],
            dict,
        )
        assert written["generation_history"][0]["worker_scores"][0]["score_breakdown"]["final_score"] == 0.5

    def test_result_generation_history(self, tmp_path):
        """Test that generation history is properly formatted."""
        knob_space = get_knob_space("minimal")
        resources = detect_worker_resources()
        knob_space.resolve_hardware_ranges(resources)

        config = BOConfig(
            n_iterations=2,
            knob_tier="minimal",
            benchmark_config=BenchmarkConfig(
                benchmark="sysbench",
                workload_type="oltp",
            ),
        )

        iteration_log = [
            {
                "iteration": 0,
                "config": {"shared_buffers": 1024},
                "metrics": {"throughput": 100.0},
                "score": 0.5,
                "cost": 50.0,
                "wall_clock_seconds": 30.0,
                "restarted": False,
                "timestamp": 1234567890.0,
                "score_breakdown": {"total": 0.5},
            },
            {
                "iteration": 1,
                "config": {"shared_buffers": 2048},
                "metrics": {"throughput": 120.0},
                "score": 0.6,
                "cost": 40.0,
                "wall_clock_seconds": 30.0,
                "restarted": False,
                "timestamp": 1234567920.0,
                "score_breakdown": {"total": 0.6},
            },
        ]

        class DummyMetricConfig:
            scoring_policy = "default"
            scoring_policy_version = "1.0"
            metric_reference_version = "1.0"
            workload_features = {}

            def get_normalization_metadata(self):
                return {}

        results = write_bo_results(
            knob_space=knob_space,
            config=config,
            worker_resources=WorkerResources(
                ram_bytes=16 * 1024 * 1024 * 1024,
                cpu_cores=8,
                disk_type="SSD",
            ),
            system_info={},
            iteration_log=iteration_log,
            total_time=60.0,
            output_dir=tmp_path,
            metric_config=DummyMetricConfig(),
        )

        # Verify generation history
        gen_hist = results["generation_history"]
        assert len(gen_hist) == 2

        # Check first generation
        assert gen_hist[0]["generation"] == 0
        assert gen_hist[0]["best_score"] == 0.5
        assert gen_hist[0]["mean_score"] == 0.5
        assert len(gen_hist[0]["worker_scores"]) == 1
        assert gen_hist[0]["worker_scores"][0]["score"] == 0.5

        # Check second generation (best_score should be cumulative)
        assert gen_hist[1]["generation"] == 1
        assert gen_hist[1]["best_score"] == 0.6
        assert gen_hist[1]["mean_score"] == 0.6


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
            batched_bo=True, resource_division=None,
            pbt_session=str(pbt_session),
        )

        config = BOConfig.from_args(args)

        assert config.max_workers == 4
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
            batched_bo=True, resource_division=2,
            pbt_session=str(pbt_session),
        )

        config = BOConfig.from_args(args)

        assert config.max_workers == 2

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
            batched_bo=True, resource_division=None,
            pbt_session=None,
        )

        config = BOConfig.from_args(args)

        assert config.max_workers == 1
        assert config.pbt_worker_resources is None
