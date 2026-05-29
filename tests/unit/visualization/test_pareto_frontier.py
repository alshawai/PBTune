import pytest
from unittest.mock import patch, MagicMock
from pathlib import Path
from matplotlib.figure import Figure

from src.visualization.plots.pareto_frontier import generate, FIG_ID
from src.visualization.loaders.session import SessionTrace
from src.visualization.loaders.baseline import BOTrace
from src.visualization.registry import REGISTRY
from src.utils.metrics import PerformanceMetrics

@pytest.fixture
def mock_pbt_session():
    trace = MagicMock(spec=SessionTrace)
    trace.metadata = {
        "best_config_metrics": PerformanceMetrics(throughput=1000.0, latency_p95=5.0)
    }
    return trace

@pytest.fixture
def mock_bo_trace():
    trace = MagicMock(spec=BOTrace)
    trace.metadata = {
        "best_config_metrics": PerformanceMetrics(throughput=800.0, latency_p95=6.0)
    }
    return trace

def test_registers_in_registry():
    spec = REGISTRY.get(FIG_ID)
    assert spec is not None
    assert spec.fig_id == FIG_ID
    assert spec.paper_label == "pareto_frontier"

@patch("src.visualization.plots.pareto_frontier.load_session")
@patch("src.visualization.plots.pareto_frontier.load_bo_trace")
def test_generate_returns_figure(mock_load_bo, mock_load_session, mock_pbt_session, mock_bo_trace, tmp_path):
    mock_load_session.return_value = mock_pbt_session
    mock_load_bo.return_value = mock_bo_trace
    
    fig = generate(
        pbt_paths=["pbt1.json"],
        bo_paths=["bo1.json"],
        output_dir=str(tmp_path),
        venue="preview"
    )
    
    assert isinstance(fig, Figure)
    
@patch("src.visualization.plots.pareto_frontier.load_session")
@patch("src.visualization.plots.pareto_frontier.load_bo_trace")
def test_generate_empty_when_no_metrics(mock_load_bo, mock_load_session, mock_pbt_session, mock_bo_trace, tmp_path):
    # Mock no best metrics
    mock_pbt_session.metadata = {}
    mock_bo_trace.metadata = {}
    mock_load_session.return_value = mock_pbt_session
    mock_load_bo.return_value = mock_bo_trace
    
    fig = generate(
        pbt_paths=["pbt1.json"],
        bo_paths=["bo1.json"],
        output_dir=str(tmp_path),
        venue="preview"
    )
    
    assert isinstance(fig, Figure)
    # The figure will be empty
    assert len(fig.axes) == 1
    assert len(fig.axes[0].collections) == 0

@patch("src.visualization.plots.pareto_frontier.load_session")
@patch("src.visualization.plots.pareto_frontier.load_bo_trace")
def test_export_creates_files(mock_load_bo, mock_load_session, mock_pbt_session, mock_bo_trace, tmp_path):
    mock_load_session.return_value = mock_pbt_session
    mock_load_bo.return_value = mock_bo_trace
    
    generate(
        pbt_paths=["pbt1.json"],
        bo_paths=["bo1.json"],
        output_dir=str(tmp_path),
        venue="preview",
        formats=["pdf", "png"]
    )
    
    assert (tmp_path / f"{FIG_ID}.pdf").exists()
    assert (tmp_path / f"{FIG_ID}.png").exists()
