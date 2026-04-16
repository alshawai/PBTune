import json
import pytest
import pandas as pd

from src.analysis.data_loader import load_pbt_results, _encode_dataframe_features

# We use known postgres knobs that exist in KNOB_TUNING_METADATA to test encoding
# enable_indexscan is a boolean
# wal_sync_method is an enum

@pytest.fixture
def mock_pbt_directory(tmp_path):
    """Creates a temporary directory with mock PBT result JSON files."""
    data_dir = tmp_path / "pbt_results"
    data_dir.mkdir()
    
    # File 1: Session A — two valid workers
    file1_data = {
        "tuning_session": {
            "workload_type": "oltp",
            "benchmark_name": "sysbench"
        },
        "system_info": {"cpu": 4},
        "generation_history": [
            {
                "worker_configs": [
                    {"worker_id": 0, "config": {"shared_buffers": 1024, "enable_indexscan": "on",  "wal_sync_method": "fdatasync"}},
                    {"worker_id": 1, "config": {"shared_buffers": 2048, "enable_indexscan": "off", "wal_sync_method": "open_datasync"}}
                ],
                "worker_scores": [
                    {"worker_id": 0, "score": 150.0, "metrics": {"latency_p95": 15.0, "throughput": 1000.0, "failure_type": None}},
                    {"worker_id": 1, "score": 200.0, "metrics": {"latency_p95": 10.0, "throughput": 1500.0, "failure_type": None}}
                ]
            }
        ]
    }
    
    # File 2: Session B — one valid worker, one crashed (failure_type set)
    file2_data = {
        "tuning_session": {
            "workload_type": "oltp",
            "benchmark_name": "sysbench"
        },
        "generation_history": [
            {
                "worker_configs": [
                    {"worker_id": 0, "config": {"shared_buffers": 4096, "enable_indexscan": "true",  "wal_sync_method": "fsync"}},
                    {"worker_id": 1, "config": {"shared_buffers": 8192, "enable_indexscan": "false", "wal_sync_method": "fdatasync"}}
                ],
                "worker_scores": [
                    {"worker_id": 0, "score": 120.0, "metrics": {"latency_p95": 25.0, "throughput": 800.0, "failure_type": None}},
                    {"worker_id": 1, "score": None,  "metrics": {"latency_p95": 0.0,  "throughput": 0.0,   "failure_type": "crashed"}}
                ]
            }
        ]
    }
    
    (data_dir / "pbt_results_1.json").write_text(json.dumps(file1_data))
    (data_dir / "pbt_results_2.json").write_text(json.dumps(file2_data))
    
    return data_dir

@pytest.fixture
def mock_mismatched_pbt_directory(tmp_path):
    """Creates JSON files with different configurations being tuned."""
    data_dir = tmp_path / "mismatched_pbt"
    data_dir.mkdir()
    
    file1 = {"generation_history": [{"worker_configs": [{"worker_id": 0, "config": {"a": 1}}],         "worker_scores": [{"worker_id": 0, "score": 1, "metrics": {"throughput": 1, "failure_type": None}}]}]}
    file2 = {"generation_history": [{"worker_configs": [{"worker_id": 0, "config": {"a": 1, "b": 2}}], "worker_scores": [{"worker_id": 0, "score": 1, "metrics": {"throughput": 1, "failure_type": None}}]}]}
    
    (data_dir / "pbt_results_1.json").write_text(json.dumps(file1))
    (data_dir / "pbt_results_2.json").write_text(json.dumps(file2))
    
    return data_dir

def test_encode_dataframe_features_maps_booleans_and_enums_correctly():
    # enable_indexscan is a predefined boolean in postgres
    # wal_sync_method is a predefined enum ('fsync', 'fdatasync', 'open_datasync', 'open_sync')
    raw_df = pd.DataFrame([
        {"shared_buffers": 1024, "enable_indexscan": "on", "wal_sync_method": "fsync"},
        {"shared_buffers": 2048, "enable_indexscan": "off", "wal_sync_method": "open_datasync"},
        {"shared_buffers": 4096, "enable_indexscan": "true", "wal_sync_method": "fdatasync"},
        {"shared_buffers": 8192, "enable_indexscan": False, "wal_sync_method": "fsync"},
    ])
    
    encoded = _encode_dataframe_features(raw_df.copy())
    
    # Bools: on=1, off=0, true=1, False=0
    assert encoded["enable_indexscan"].tolist() == [1, 0, 1, 0]
    
    # Enums are sorted alphabetically: 
    # ['fdatasync', 'fsync', 'open_datasync'] 
    # indices: fdatasync=0, fsync=1, open_datasync=2
    # So the order for ["fsync", "open_datasync", "fdatasync", "fsync"] should be [1, 2, 0, 1]
    assert encoded["wal_sync_method"].tolist() == [1, 2, 0, 1]

def test_load_pbt_results_global_rescoring(mock_pbt_directory):
    # Execute
    dataset = load_pbt_results(mock_pbt_directory)
    
    # Assertions
    assert dataset.n_observations == 3 # 4 total, 1 crashed
    assert len(dataset.config_df) == 3
    assert len(dataset.scores) == 3
    assert len(dataset.metadata) == 2 # 2 files
    
    # Verify bounds were updated across all 3 valid metrics
    # Latencies: 15.0, 10.0, 25.0 -> min ~10.0, max ~25.0 (without padding logic)
    # Throughput: 1000, 1500, 800 -> min ~820, max ~1450
    # Because update_ranges sets based on 5th/95th percentile, it might slightly differ
    # but the config should show awareness of the global bounds.
    assert dataset.metric_config.throughput_max == 1450.0
    assert dataset.metric_config.throughput_min == 820.0
    
    # We should have bounds for our mocked variables (shared_buffers, enable_indexscan, wal_sync_method)
    assert "shared_buffers" in dataset.knob_bounds
    assert "enable_indexscan" in dataset.knob_bounds
    assert "wal_sync_method" in dataset.knob_bounds
    
    # Booleans are strictly 0.0 to 1.0 bounds
    assert dataset.knob_bounds["enable_indexscan"] == (0.0, 1.0)
    
    # Enum max should match max indices dynamically determined (3 states -> max 2.0)
    assert dataset.knob_bounds["wal_sync_method"][1] == 2.0

def test_load_pbt_results_mismatched_knobs(mock_mismatched_pbt_directory):
    # Should raise ValueError due to inconsistent knob spaces in files
    with pytest.raises(ValueError, match="Knob set mismatch"):
        load_pbt_results(mock_mismatched_pbt_directory)

def test_load_pbt_results_empty_history(tmp_path):
    data_dir = tmp_path / "empty_hist"
    data_dir.mkdir()
    (data_dir / "pbt_results_empty.json").write_text(json.dumps({"tuning_session": {"workload_type": "oltp", "benchmark_name": "sysbench"}, "system_info": {"ram": "16GB"}, "generation_history": []}))
    dataset = load_pbt_results(data_dir)
    assert dataset.config_df.empty
    assert dataset.n_observations == 0

def test_metadata_and_rescoring_checks(mock_pbt_directory):
    dataset = load_pbt_results(mock_pbt_directory)
    
    # 1. Metadata extraction correctness
    assert len(dataset.metadata) == 2
    assert dataset.metadata[0]["benchmark_name"] == "sysbench"
    assert "cpu" in dataset.metadata[0]["system_info"]
    
    # 2. Rescored values differ from raw JSON values (proving re-scoring is active)
    # The raw scores were 150.0, 200.0, 120.0. The new mathematical scores computed against 5th/95th bounding should not equal the exact raw float inputs!
    assert dataset.scores.tolist() != [150.0, 200.0, 120.0]

from unittest.mock import patch, MagicMock

@patch("src.analysis.data_loader.get_knob_space")
def test_knob_bounds_hardware_relative(mock_get_knob_space, mock_pbt_directory):
    # Mock the KnobSpace and KnobDefinition to force hardware_relative = True for shared_buffers
    mock_space = MagicMock()
    mock_kd = MagicMock()
    mock_kd.hardware_relative = True
    mock_space.knobs = {"shared_buffers": mock_kd}
    mock_get_knob_space.return_value = mock_space
    
    dataset = load_pbt_results(mock_pbt_directory)
    
    # The loader must recognize it as hardware-relative and lookup HARDWARE_RELATIVE_SPECS
    assert "shared_buffers" in dataset.knob_bounds
    # HARDWARE_RELATIVE_SPECS["shared_buffers"] is (0.15, 0.40)
    assert dataset.knob_bounds["shared_buffers"] == (0.15, 0.40)
