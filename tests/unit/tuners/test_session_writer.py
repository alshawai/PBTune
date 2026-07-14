"""Tests for src.tuners.utils.session_writer."""

import json

import numpy as np

from src.tuners.utils.session_writer import (
    TIMING_SCHEMA_VERSION,
    build_scoring_block,
    build_session_header,
    convert_numpy_types,
    worker_resources_to_dict,
    write_best_config_json,
    write_session_json,
)
from src.tuners.utils.types import TuningStrategy
from src.utils.hardware_info import WorkerResources


class TestConvertNumpyTypes:
    def test_scalars(self):
        assert convert_numpy_types(np.int64(5)) == 5
        assert isinstance(convert_numpy_types(np.int64(5)), int)
        assert convert_numpy_types(np.float32(1.5)) == 1.5
        assert convert_numpy_types(np.bool_(True)) is True

    def test_array(self):
        assert convert_numpy_types(np.array([1, 2, 3])) == [1, 2, 3]

    def test_nested(self):
        payload = {"a": [np.int64(1), {"b": np.float64(2.0)}]}
        assert convert_numpy_types(payload) == {"a": [1, {"b": 2.0}]}

    def test_passthrough(self):
        assert convert_numpy_types("x") == "x"
        assert convert_numpy_types(7) == 7


class TestBuildSessionHeader:
    def test_core_fields(self):
        header = build_session_header(
            strategy=TuningStrategy.LHS,
            knob_tier="core",
            knob_source="expert",
            num_knobs=12,
            workload_type="oltp",
            benchmark_name="sysbench",
            timestamp="20260619_1200",
            seed=42,
        )
        assert header["tuning_strategy"] == "lhs"
        assert header["timing_schema_version"] == TIMING_SCHEMA_VERSION
        assert header["num_knobs"] == 12
        assert header["seed"] == 42
        assert header["timestamp"] == "20260619_1200"

    def test_extra_merged(self):
        header = build_session_header(
            strategy="lhs",
            knob_tier="core",
            knob_source="expert",
            num_knobs=1,
            workload_type="oltp",
            benchmark_name="sysbench",
            timestamp="t",
            seed=None,
            extra={"design_size": 32},
        )
        assert header["design_size"] == 32


class TestBuildScoringBlock:
    def test_maps_metadata_and_breakdown(self):
        block = build_scoring_block(
            {
                "scoring_policy": "adaptive_v2",
                "scoring_policy_version": "2.1",
                "metric_reference_version": "v3",
                "workload_features": {"read_ratio": 0.8},
                "normalization_metadata": {"tps": [0, 100]},
            },
            {"final_score": 0.9},
        )
        assert block["scoring_policy"] == "adaptive_v2"
        assert block["scoring_policy_version"] == "2.1"
        assert block["metric_reference_version"] == "v3"
        assert block["workload_features"] == {"read_ratio": 0.8}
        assert block["normalization_metadata"] == {"tps": [0, 100]}
        assert block["score_breakdown"] == {"final_score": 0.9}

    def test_defaults_when_metadata_sparse(self):
        block = build_scoring_block({})
        assert block["scoring_policy"] == "fixed_v1"
        assert block["scoring_policy_version"] == "1.0"
        assert block["metric_reference_version"] == "v1"
        assert block["workload_features"] == {}
        assert block["normalization_metadata"] == {}
        assert block["score_breakdown"] == {}


class TestWorkerResourcesToDict:
    def test_fields(self):
        res = WorkerResources(
            ram_bytes=1024,
            cpu_cores=4,
            disk_type="SSD",
            disk_read_bps=100,
            disk_class="nvme_pcie4",
        )
        d = worker_resources_to_dict(res)
        assert d["ram_bytes"] == 1024
        assert d["cpu_cores"] == 4
        assert d["disk_type"] == "SSD"
        assert d["disk_read_bps"] == 100
        assert d["disk_class"] == "nvme_pcie4"


class TestWriteHelpers:
    def test_write_session_json(self, tmp_path):
        results = {"tuning_session": {"x": np.int64(3)}}
        out = write_session_json(
            results, output_dir=tmp_path, filename="lhs_results_t.json"
        )
        assert out.exists()
        assert out.parent.name == "traces"
        loaded = json.loads(out.read_text())
        assert loaded["tuning_session"]["x"] == 3

    def test_write_best_config_json(self, tmp_path):
        out = write_best_config_json(
            {"shared_buffers": 0.25},
            output_dir=tmp_path,
            filename="best_config_t.json",
        )
        assert out.exists()
        assert out.parent.name == "best_configs"
        assert json.loads(out.read_text())["shared_buffers"] == 0.25
