"""
Unit tests for the Bayesian Optimization comparison runner.

Tests cover:
- ConfigSpace translation from KnobSpace
- Objective function wrapper
- Result serialization format
- CLI argument parsing
- Error handling for evaluation failures
- BO optimizer suggest/report cycle

All tests mock benchmark execution — no live PostgreSQL required.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any, Dict
from unittest.mock import MagicMock, patch, PropertyMock

import pytest
import numpy as np

from src.tuner.config.knob_space import (
    KnobSpace,
    KnobDefinition,
    KnobType,
    KnobScale,
)
from src.utils.metrics import PerformanceMetrics, WorkloadType


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture
def sample_knob_space() -> KnobSpace:
    """Create a minimal KnobSpace for testing ConfigSpace translation."""
    knobs = {
        "shared_buffers": KnobDefinition(
            name="shared_buffers",
            knob_type=KnobType.INTEGER,
            min_value=16384,
            max_value=2097152,
            scale=KnobScale.LOG,
            default=131072,
            unit="8kB",
            description="Sets shared memory buffers",
            category="memory",
            restart_required=True,
        ),
        "work_mem": KnobDefinition(
            name="work_mem",
            knob_type=KnobType.INTEGER,
            min_value=64,
            max_value=524288,
            scale=KnobScale.LOG,
            default=4096,
            unit="kB",
            description="Sets work memory",
            category="memory",
            restart_required=False,
        ),
        "random_page_cost": KnobDefinition(
            name="random_page_cost",
            knob_type=KnobType.REAL,
            min_value=0.1,
            max_value=10.0,
            scale=KnobScale.LINEAR,
            default=4.0,
            description="Planner cost for random page fetch",
            category="planner",
            restart_required=False,
        ),
        "enable_hashjoin": KnobDefinition(
            name="enable_hashjoin",
            knob_type=KnobType.BOOLEAN,
            default=True,
            description="Enable hash join plans",
            category="planner",
            restart_required=False,
        ),
        "wal_level": KnobDefinition(
            name="wal_level",
            knob_type=KnobType.ENUM,
            enum_values=["minimal", "replica", "logical"],
            default="replica",
            description="WAL level",
            category="wal",
            restart_required=True,
        ),
    }
    space = KnobSpace.__new__(KnobSpace)
    space.knobs = knobs
    space.worker_resources = MagicMock(ram_bytes=8_000_000_000, cpu_cores=4, disk_type="SSD")
    return space


@pytest.fixture
def sample_metrics() -> PerformanceMetrics:
    """Create sample performance metrics for testing."""
    return PerformanceMetrics(
        latency_p50=5.0,
        latency_p95=12.0,
        latency_p99=25.0,
        throughput=1500.0,
        memory_utilization=0.45,
        io_read_mb=10.0,
        io_write_mb=5.0,
        cache_hit_ratio=0.95,
        error_rate=0.01,
        total_queries=10000,
        total_time=30.0,
    )


# ---------------------------------------------------------------------------
# Test: ConfigSpace Translation
# ---------------------------------------------------------------------------


class TestConfigSpaceTranslation:
    """Tests for build_configspace_from_knob_space and related utilities."""

    def test_integer_knob_translated_correctly(self, sample_knob_space: KnobSpace) -> None:
        """INTEGER knobs should become Integer hyperparameters with correct bounds."""
        from src.scripts.bo_optimizer import build_configspace_from_knob_space

        cs = build_configspace_from_knob_space(sample_knob_space)

        hp = cs["shared_buffers"]
        assert hp.lower == 16384
        assert hp.upper == 2097152
        assert hp.log is True  # LOG scale preserved

    def test_real_knob_translated_correctly(self, sample_knob_space: KnobSpace) -> None:
        """REAL knobs should become Float hyperparameters."""
        from src.scripts.bo_optimizer import build_configspace_from_knob_space

        cs = build_configspace_from_knob_space(sample_knob_space)

        hp = cs["random_page_cost"]
        assert hp.lower == pytest.approx(0.1)
        assert hp.upper == pytest.approx(10.0)
        assert hp.log is False  # LINEAR scale

    def test_boolean_knob_translated_as_categorical(
        self, sample_knob_space: KnobSpace
    ) -> None:
        """BOOLEAN knobs should become Categorical with ['true', 'false']."""
        from src.scripts.bo_optimizer import build_configspace_from_knob_space

        cs = build_configspace_from_knob_space(sample_knob_space)

        hp = cs["enable_hashjoin"]
        # Categorical items
        assert set(hp.choices) == {"true", "false"}

    def test_enum_knob_translated_as_categorical(
        self, sample_knob_space: KnobSpace
    ) -> None:
        """ENUM knobs should become Categorical with enum_values."""
        from src.scripts.bo_optimizer import build_configspace_from_knob_space

        cs = build_configspace_from_knob_space(sample_knob_space)

        hp = cs["wal_level"]
        assert set(hp.choices) == {"minimal", "replica", "logical"}

    def test_total_hyperparameter_count(self, sample_knob_space: KnobSpace) -> None:
        """All 5 knobs should be translated into 5 hyperparameters."""
        from src.scripts.bo_optimizer import build_configspace_from_knob_space

        cs = build_configspace_from_knob_space(sample_knob_space)
        assert len(cs) == 5

    def test_log_scale_integer_with_positive_lower_bound(self) -> None:
        """Log-scale INTEGER with lower > 0 should have log=True."""
        from src.scripts.bo_optimizer import build_configspace_from_knob_space

        knobs = {
            "log_knob": KnobDefinition(
                name="log_knob",
                knob_type=KnobType.INTEGER,
                min_value=1,
                max_value=1000000,
                scale=KnobScale.LOG,
                default=1000,
                description="Log-scale test",
            ),
        }
        space = KnobSpace.__new__(KnobSpace)
        space.knobs = knobs
        cs = build_configspace_from_knob_space(space)
        assert cs["log_knob"].log is True

    def test_skips_knob_with_equal_bounds(self) -> None:
        """Knobs where lower == upper should be skipped."""
        from src.scripts.bo_optimizer import build_configspace_from_knob_space

        knobs = {
            "degenerate": KnobDefinition(
                name="degenerate",
                knob_type=KnobType.INTEGER,
                min_value=42,
                max_value=42,
                scale=KnobScale.LINEAR,
                default=42,
                description="Degenerate bounds",
            ),
        }
        space = KnobSpace.__new__(KnobSpace)
        space.knobs = knobs
        cs = build_configspace_from_knob_space(space)
        assert len(cs) == 0


# ---------------------------------------------------------------------------
# Test: ConfigSpace → Knob Config Conversion
# ---------------------------------------------------------------------------


class TestConfigSpaceToKnobConfig:
    """Tests for configspace_sample_to_knob_config."""

    def test_boolean_string_to_bool(self, sample_knob_space: KnobSpace) -> None:
        """Boolean categorical 'true'/'false' should convert to Python bool."""
        from src.scripts.bo_optimizer import (
            build_configspace_from_knob_space,
            configspace_sample_to_knob_config,
        )

        cs = build_configspace_from_knob_space(sample_knob_space)
        cs_config = cs.sample_configuration()

        knob_config = configspace_sample_to_knob_config(cs_config, sample_knob_space)

        assert isinstance(knob_config["enable_hashjoin"], bool)

    def test_integer_values_are_ints(self, sample_knob_space: KnobSpace) -> None:
        """Integer knob values should be Python ints after conversion."""
        from src.scripts.bo_optimizer import (
            build_configspace_from_knob_space,
            configspace_sample_to_knob_config,
        )

        cs = build_configspace_from_knob_space(sample_knob_space)
        cs_config = cs.sample_configuration()

        knob_config = configspace_sample_to_knob_config(cs_config, sample_knob_space)

        assert isinstance(knob_config["shared_buffers"], int)
        assert isinstance(knob_config["work_mem"], int)

    def test_real_values_are_floats(self, sample_knob_space: KnobSpace) -> None:
        """Real knob values should be Python floats."""
        from src.scripts.bo_optimizer import (
            build_configspace_from_knob_space,
            configspace_sample_to_knob_config,
        )

        cs = build_configspace_from_knob_space(sample_knob_space)
        cs_config = cs.sample_configuration()

        knob_config = configspace_sample_to_knob_config(cs_config, sample_knob_space)

        assert isinstance(knob_config["random_page_cost"], float)

    def test_enum_values_are_strings(self, sample_knob_space: KnobSpace) -> None:
        """Enum knob values should be Python strings."""
        from src.scripts.bo_optimizer import (
            build_configspace_from_knob_space,
            configspace_sample_to_knob_config,
        )

        cs = build_configspace_from_knob_space(sample_knob_space)
        cs_config = cs.sample_configuration()

        knob_config = configspace_sample_to_knob_config(cs_config, sample_knob_space)

        assert isinstance(knob_config["wal_level"], str)
        assert knob_config["wal_level"] in {"minimal", "replica", "logical"}

    def test_values_within_bounds(self, sample_knob_space: KnobSpace) -> None:
        """All converted values should respect knob bounds."""
        from src.scripts.bo_optimizer import (
            build_configspace_from_knob_space,
            configspace_sample_to_knob_config,
        )

        cs = build_configspace_from_knob_space(sample_knob_space)

        for _ in range(10):
            cs_config = cs.sample_configuration()
            knob_config = configspace_sample_to_knob_config(cs_config, sample_knob_space)

            assert 16384 <= knob_config["shared_buffers"] <= 2097152
            assert 64 <= knob_config["work_mem"] <= 524288
            assert 0.1 <= knob_config["random_page_cost"] <= 10.0


# ---------------------------------------------------------------------------
# Test: BOConfig Dataclass
# ---------------------------------------------------------------------------


class TestBOConfig:
    """Tests for BOConfig defaults and validation."""

    def test_default_values(self) -> None:
        """BOConfig should have sensible defaults."""
        from src.scripts.bo_optimizer import BOConfig

        config = BOConfig()
        assert config.optimizer_backend == "smac"
        assert config.max_evaluations == 30
        assert config.initial_design_size is None
        assert config.acquisition_function == "EI"

    def test_custom_values(self) -> None:
        """BOConfig should accept custom values."""
        from src.scripts.bo_optimizer import BOConfig

        config = BOConfig(
            optimizer_backend="smac",
            max_evaluations=100,
            initial_design_size=10,
            acquisition_function="LCB",
        )
        assert config.max_evaluations == 100
        assert config.initial_design_size == 10
        assert config.acquisition_function == "LCB"


# ---------------------------------------------------------------------------
# Test: BOOptimizer suggest/report cycle
# ---------------------------------------------------------------------------


class TestBOOptimizer:
    """Tests for BOOptimizer suggest/report interface."""

    def test_suggest_returns_configuration(self, sample_knob_space: KnobSpace) -> None:
        """suggest() should return a valid ConfigSpace Configuration."""
        from src.scripts.bo_optimizer import (
            BOConfig,
            BOOptimizer,
            build_configspace_from_knob_space,
        )
        from ConfigSpace import Configuration

        cs = build_configspace_from_knob_space(sample_knob_space)
        optimizer = BOOptimizer(
            config_space=cs,
            bo_config=BOConfig(max_evaluations=5, initial_design_size=2),
            seed=42,
        )

        config = optimizer.suggest()
        assert isinstance(config, Configuration)

    def test_suggest_report_cycle(self, sample_knob_space: KnobSpace) -> None:
        """A full suggest → report cycle should work without errors."""
        from src.scripts.bo_optimizer import (
            BOConfig,
            BOOptimizer,
            build_configspace_from_knob_space,
        )

        cs = build_configspace_from_knob_space(sample_knob_space)
        optimizer = BOOptimizer(
            config_space=cs,
            bo_config=BOConfig(max_evaluations=5, initial_design_size=2),
            seed=42,
        )

        for i in range(3):
            config = optimizer.suggest()
            optimizer.report(config, cost=-50.0 + i)

        assert optimizer._eval_count == 3

    def test_report_without_suggest_raises(self, sample_knob_space: KnobSpace) -> None:
        """report() without a preceding suggest() should raise RuntimeError."""
        from src.scripts.bo_optimizer import (
            BOConfig,
            BOOptimizer,
            build_configspace_from_knob_space,
        )

        cs = build_configspace_from_knob_space(sample_knob_space)
        optimizer = BOOptimizer(
            config_space=cs,
            bo_config=BOConfig(max_evaluations=5, initial_design_size=2),
            seed=42,
        )

        # Consume one suggest/report to clear initial state
        config = optimizer.suggest()
        optimizer.report(config, cost=-50.0)

        # Now report without suggest should raise
        with pytest.raises(RuntimeError, match="report.*without.*suggest"):
            optimizer.report(config, cost=-40.0)


# ---------------------------------------------------------------------------
# Test: Result Serialization
# ---------------------------------------------------------------------------


class TestResultSerialization:
    """Tests for BO result JSON schema compatibility."""

    def test_result_contains_required_fields(self, tmp_path: Path) -> None:
        """BO results JSON must contain the same fields needed for comparison plots."""
        from src.scripts.run_bo_comparison import _convert_numpy_types

        results = {
            "optimizer": "bayesian_optimization",
            "optimizer_backend": "smac",
            "tuning_session": {
                "knob_tier": "minimal",
                "num_knobs": 5,
                "workload_type": "oltp",
                "benchmark_name": "sysbench",
                "max_evaluations": 30,
                "total_evaluations": 25,
                "total_time_seconds": 600.0,
                "timestamp": "20260423_1200",
            },
            "best_configuration": {
                "score": 75.5,
                "knobs": {"shared_buffers": 0.25, "work_mem": 0.01},
                "metrics": {"throughput": 1500.0, "latency_p95": 12.0},
            },
            "evaluation_history": [
                {"evaluation": 1, "score": 50.0, "best_score_so_far": 50.0},
                {"evaluation": 2, "score": 75.5, "best_score_so_far": 75.5},
            ],
            "convergence": {
                "history": [50.0, 75.5],
                "final_best_score": 75.5,
                "total_evaluations": 2,
            },
            "system_info": {},
        }

        json_file = tmp_path / "bo_results.json"
        with open(json_file, "w") as f:
            json.dump(results, f, indent=2)

        with open(json_file, "r") as f:
            loaded = json.load(f)

        # Verify required top-level keys for PBT comparison
        assert "optimizer" in loaded
        assert "tuning_session" in loaded
        assert "best_configuration" in loaded
        assert "evaluation_history" in loaded
        assert "convergence" in loaded

        # Verify PBT-compatible fields in tuning_session
        session = loaded["tuning_session"]
        assert "knob_tier" in session
        assert "workload_type" in session
        assert "total_time_seconds" in session

        # Verify best_configuration format
        best = loaded["best_configuration"]
        assert "score" in best
        assert "knobs" in best
        assert "metrics" in best

    def test_numpy_types_converted(self) -> None:
        """numpy types should be converted to Python native types."""
        from src.scripts.run_bo_comparison import _convert_numpy_types

        data = {
            "score": np.float64(75.5),
            "count": np.int64(42),
            "flag": np.bool_(True),
            "array": np.array([1, 2, 3]),
            "nested": {"value": np.float32(3.14)},
        }

        result = _convert_numpy_types(data)

        assert type(result["score"]) is float
        assert type(result["count"]) is int
        assert type(result["flag"]) is bool
        assert type(result["array"]) is list
        assert type(result["nested"]["value"]) is float


# ---------------------------------------------------------------------------
# Test: Objective Function Wrapper
# ---------------------------------------------------------------------------


class TestObjectiveFunction:
    """Tests for the objective function that wraps Evaluator.evaluate_worker."""

    @patch("src.scripts.run_bo_comparison.Evaluator")
    @patch("src.scripts.run_bo_comparison.EnvironmentFactory")
    @patch("src.scripts.run_bo_comparison.get_db_config")
    @patch("src.scripts.run_bo_comparison.get_knob_space")
    @patch("src.scripts.run_bo_comparison.detect_worker_resources")
    def test_objective_returns_score_and_metrics(
        self,
        mock_resources,
        mock_get_knob_space,
        mock_get_db_config,
        mock_env_factory,
        mock_evaluator_cls,
        sample_knob_space,
        sample_metrics,
    ):
        """_objective_function should return (score, metrics) tuple."""
        # Setup mocks
        mock_resources.return_value = MagicMock(
            ram_bytes=8_000_000_000, cpu_cores=4, disk_type="SSD"
        )
        mock_get_knob_space.return_value = sample_knob_space
        mock_get_db_config.return_value = MagicMock()

        mock_env = MagicMock()
        mock_env.get_db_config.return_value = MagicMock(port=5460)
        mock_env_factory.create.return_value = mock_env

        mock_evaluator = MagicMock()
        mock_evaluator.evaluate_worker.return_value = (sample_metrics, 75.5, False)
        mock_evaluator_cls.return_value = mock_evaluator

        from src.scripts.run_bo_comparison import BOComparisonRunner
        from src.scripts.bo_optimizer import BOConfig

        runner = BOComparisonRunner(
            knob_tier="minimal",
            bo_config=BOConfig(max_evaluations=5),
            benchmark="sysbench",
            random_seed=42,
            no_docker=True,
        )

        knob_config = {"shared_buffers": 131072, "work_mem": 4096}
        score, metrics = runner._objective_function(knob_config, evaluation_number=1)

        assert score == 75.5
        assert metrics.throughput == 1500.0

    @patch("src.scripts.run_bo_comparison.Evaluator")
    @patch("src.scripts.run_bo_comparison.EnvironmentFactory")
    @patch("src.scripts.run_bo_comparison.get_db_config")
    @patch("src.scripts.run_bo_comparison.get_knob_space")
    @patch("src.scripts.run_bo_comparison.detect_worker_resources")
    def test_objective_handles_evaluation_failure(
        self,
        mock_resources,
        mock_get_knob_space,
        mock_get_db_config,
        mock_env_factory,
        mock_evaluator_cls,
        sample_knob_space,
    ):
        """_objective_function should return fallback (0.0, fallback_metrics) on failure."""
        mock_resources.return_value = MagicMock(
            ram_bytes=8_000_000_000, cpu_cores=4, disk_type="SSD"
        )
        mock_get_knob_space.return_value = sample_knob_space
        mock_get_db_config.return_value = MagicMock()

        mock_env = MagicMock()
        mock_env.get_db_config.return_value = MagicMock(port=5460)
        mock_env_factory.create.return_value = mock_env

        mock_evaluator = MagicMock()
        mock_evaluator.evaluate_worker.side_effect = RuntimeError("DB crashed")
        mock_evaluator_cls.return_value = mock_evaluator

        from src.scripts.run_bo_comparison import BOComparisonRunner
        from src.scripts.bo_optimizer import BOConfig

        runner = BOComparisonRunner(
            knob_tier="minimal",
            bo_config=BOConfig(max_evaluations=5),
            benchmark="sysbench",
            random_seed=42,
            no_docker=True,
        )

        score, metrics = runner._objective_function(
            {"shared_buffers": 131072}, evaluation_number=1
        )

        assert score == 0.0
        assert metrics.throughput == 0.0
        assert metrics.error_rate == 1.0
        assert metrics.failure_type == "crash_bo_eval"


# ---------------------------------------------------------------------------
# Test: CLI Entry Point
# ---------------------------------------------------------------------------


class TestCLIParsing:
    """Tests for command-line argument parsing."""

    def test_default_arguments(self) -> None:
        """parse_args with no arguments should use sensible defaults."""
        from src.scripts.run_bo_comparison import parse_args

        with patch("sys.argv", ["run_bo_comparison"]):
            args = parse_args()

        assert args.tier == "minimal"
        assert args.config == "standard"
        assert args.max_evaluations == 30
        assert args.seed == 42
        assert args.optimizer_backend == "smac"
        assert args.acquisition_function == "EI"

    def test_custom_bo_arguments(self) -> None:
        """BO-specific CLI arguments should be correctly parsed."""
        from src.scripts.run_bo_comparison import parse_args

        with patch(
            "sys.argv",
            [
                "run_bo_comparison",
                "--tier", "core",
                "--max-evaluations", "50",
                "--initial-design-size", "10",
                "--acquisition-function", "LCB",
                "--seed", "123",
            ],
        ):
            args = parse_args()

        assert args.tier == "core"
        assert args.max_evaluations == 50
        assert args.initial_design_size == 10
        assert args.acquisition_function == "LCB"
        assert args.seed == 123

    def test_benchmark_argument(self) -> None:
        """--benchmark flag should be parsed correctly."""
        from src.scripts.run_bo_comparison import parse_args

        with patch(
            "sys.argv",
            ["run_bo_comparison", "--benchmark", "tpch", "--tier", "standard"],
        ):
            args = parse_args()

        assert args.benchmark == "tpch"

    def test_workload_and_benchmark_mutually_exclusive(self) -> None:
        """--workload and --benchmark should be mutually exclusive."""
        from src.scripts.run_bo_comparison import parse_args

        with patch(
            "sys.argv",
            [
                "run_bo_comparison",
                "--workload", "olap",
                "--benchmark", "tpch",
            ],
        ):
            with pytest.raises(SystemExit):
                parse_args()


# ---------------------------------------------------------------------------
# Test: Error Handling
# ---------------------------------------------------------------------------


class TestErrorHandling:
    """Tests for error handling in the BO runner."""

    def test_smac_import_error_message(self) -> None:
        """BOOptimizer should raise helpful ImportError if SMAC3 is not installed."""
        from src.scripts.bo_optimizer import BOOptimizer, BOConfig, SMAC_AVAILABLE

        if not SMAC_AVAILABLE:
            from ConfigSpace import ConfigurationSpace, Float

            cs = ConfigurationSpace(seed=42)
            cs.add(Float("x", bounds=(0.0, 1.0)))

            with pytest.raises(ImportError, match="SMAC3"):
                BOOptimizer(
                    config_space=cs,
                    bo_config=BOConfig(max_evaluations=5),
                    seed=42,
                )
        else:
            pytest.skip("SMAC3 is installed; cannot test import error")
