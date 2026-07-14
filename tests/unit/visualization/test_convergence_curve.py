import pytest
import numpy as np
from pathlib import Path
from unittest.mock import patch, MagicMock

from src.visualization.loaders import SessionTrace, BOTrace, ComparisonData
from src.visualization.registry import REGISTRY
from src.visualization.plots.convergence_curve import generate

@pytest.fixture
def mock_pbt_session():
    trace = MagicMock(spec=SessionTrace)
    trace.generations = list(range(1, 11))
    trace.best_scores = np.linspace(0.1, 0.9, 10).tolist()
    trace.mean_scores = np.linspace(0.05, 0.85, 10).tolist()
    trace.wall_clock_seconds = np.linspace(10, 100, 10).tolist()
    trace.generation_elapsed_seconds = np.full(10, 9.0)
    trace.metadata = {"n_workers": 4}
    return trace

@pytest.fixture
def mock_bo_trace():
    trace = MagicMock(spec=BOTrace)
    trace.evaluations = list(range(1, 41))
    trace.best_scores = np.linspace(0.05, 0.85, 40).tolist()
    trace.wall_clock_seconds = np.linspace(5, 200, 40).tolist()
    return trace

@pytest.fixture
def mock_comparison_data():
    comp = MagicMock(spec=ComparisonData)
    comp.default_summaries = {"score": MagicMock(mean=0.3)}
    return comp

@pytest.fixture
def tmp_output_dir(tmp_path):
    return str(tmp_path)

@patch("src.visualization.plots.convergence_curve.load_session")
@patch("src.visualization.plots.convergence_curve.load_sessions")
@patch("src.visualization.plots.convergence_curve.load_bo_trace")
@patch("src.visualization.plots.convergence_curve.load_comparison")
def test_generate_returns_figure(mock_load_comp, mock_load_bo, mock_load_sessions, mock_load_session, mock_pbt_session, mock_bo_trace, mock_comparison_data, tmp_output_dir):
    mock_load_session.return_value = mock_pbt_session
    mock_load_bo.return_value = mock_bo_trace
    mock_load_comp.return_value = mock_comparison_data
    
    fig = generate(
        pbt_paths=["pbt1.json"],
        bo_paths=["bo1.json"],
        comparison_path="comp.json",
        output_dir=tmp_output_dir, venue="preview",
        annotation=True
    )
    import matplotlib.figure
    assert isinstance(fig, matplotlib.figure.Figure)
    assert len(fig.axes) == 2

@patch("src.visualization.plots.convergence_curve.load_session")
@patch("src.visualization.plots.convergence_curve.load_bo_trace")
@patch("src.visualization.plots.convergence_curve.load_comparison")
def test_left_panel_has_three_lines(mock_load_comp, mock_load_bo, mock_load_session, mock_pbt_session, mock_bo_trace, mock_comparison_data, tmp_output_dir):
    mock_load_session.return_value = mock_pbt_session
    mock_load_bo.return_value = mock_bo_trace
    mock_load_comp.return_value = mock_comparison_data
    
    fig = generate(
        pbt_paths=["pbt1.json"],
        bo_paths=["bo1.json"],
        comparison_path="comp.json",
        output_dir=tmp_output_dir, venue="preview"
    )
    from matplotlib.lines import Line2D
    ax_left = fig.axes[0]
    lines = [c for c in ax_left.get_children() if isinstance(c, Line2D)]
    assert len(lines) >= 3

@patch("src.visualization.plots.convergence_curve.load_session")
@patch("src.visualization.plots.convergence_curve.load_bo_trace")
@patch("src.visualization.plots.convergence_curve.load_comparison")
def test_right_panel_uses_wall_clock(mock_load_comp, mock_load_bo, mock_load_session, mock_pbt_session, mock_bo_trace, mock_comparison_data, tmp_output_dir):
    mock_load_session.return_value = mock_pbt_session
    mock_load_bo.return_value = mock_bo_trace
    mock_load_comp.return_value = mock_comparison_data
    
    fig = generate(
        pbt_paths=["pbt1.json"],
        bo_paths=["bo1.json"],
        comparison_path="comp.json",
        output_dir=tmp_output_dir, venue="preview"
    )
    ax_right = fig.axes[1]
    assert "Wall-Clock" in ax_right.get_xlabel()
    from matplotlib.lines import Line2D
    lines = [c for c in ax_right.get_children() if isinstance(c, Line2D)]
    for line in lines:
        if len(line.get_xdata()) > 0:
            assert isinstance(line.get_xdata()[0], (int, float, np.floating, np.integer))

@patch("src.visualization.plots.convergence_curve.load_session")
@patch("src.visualization.plots.convergence_curve.load_bo_trace")
@patch("src.visualization.plots.convergence_curve.load_comparison")
def test_no_annotation_when_disabled(mock_load_comp, mock_load_bo, mock_load_session, mock_pbt_session, mock_bo_trace, mock_comparison_data, tmp_output_dir):
    mock_load_session.return_value = mock_pbt_session
    mock_load_bo.return_value = mock_bo_trace
    mock_load_comp.return_value = mock_comparison_data
    
    fig = generate(
        pbt_paths=["pbt1.json"],
        bo_paths=["bo1.json"],
        comparison_path="comp.json",
        output_dir=tmp_output_dir, venue="preview",
        annotation=False
    )
    ax_right = fig.axes[1]
    from matplotlib.text import Annotation
    annotations = [c for c in ax_right.get_children() if isinstance(c, Annotation)]
    assert len(annotations) == 0

@patch("src.visualization.plots.convergence_curve.load_session")
@patch("src.visualization.plots.convergence_curve.load_bo_trace")
@patch("src.visualization.plots.convergence_curve.load_comparison")
def test_single_seed_no_fill(mock_load_comp, mock_load_bo, mock_load_session, mock_pbt_session, mock_bo_trace, mock_comparison_data, tmp_output_dir):
    mock_load_session.return_value = mock_pbt_session
    mock_load_bo.return_value = mock_bo_trace
    mock_load_comp.return_value = mock_comparison_data
    
    fig = generate(
        pbt_paths=["pbt1.json"],
        bo_paths=["bo1.json"],
        comparison_path="comp.json",
        output_dir=tmp_output_dir, venue="preview"
    )
    from matplotlib.collections import PolyCollection
    for ax in fig.axes:
        fills = [c for c in ax.get_children() if isinstance(c, PolyCollection)]
        assert len(fills) == 0

@patch("src.visualization.plots.convergence_curve.discover_bo_traces")
@patch("src.visualization.plots.convergence_curve.load_sessions")
@patch("src.visualization.plots.convergence_curve.load_bo_trace")
@patch("src.visualization.plots.convergence_curve.load_comparison")
def test_multi_seed_has_fill(mock_load_comp, mock_load_bo, mock_load_sessions, mock_discover_bo, mock_pbt_session, mock_bo_trace, mock_comparison_data, tmp_output_dir):
    # Setup mock_pbt_session to return different objects so std is not exactly 0
    p2 = MagicMock(spec=SessionTrace)
    p2.generations = list(range(1, 11))
    p2.best_scores = np.linspace(0.1, 0.8, 10).tolist()
    p2.mean_scores = np.linspace(0.05, 0.75, 10).tolist()
    p2.wall_clock_seconds = np.linspace(10, 100, 10).tolist()
    p2.generation_elapsed_seconds = np.full(10, 9.0)
    p2.metadata = {"n_workers": 4}

    p3 = MagicMock(spec=SessionTrace)
    p3.generations = list(range(1, 11))
    p3.best_scores = np.linspace(0.1, 1.0, 10).tolist()
    p3.mean_scores = np.linspace(0.05, 0.95, 10).tolist()
    p3.wall_clock_seconds = np.linspace(10, 100, 10).tolist()
    p3.generation_elapsed_seconds = np.full(10, 9.0)
    p3.metadata = {"n_workers": 4}

    mock_load_sessions.return_value = [mock_pbt_session, p2, p3]

    b2 = MagicMock(spec=BOTrace)
    b2.evaluations = list(range(1, 41))
    b2.best_scores = np.linspace(0.05, 0.75, 40).tolist()
    b2.wall_clock_seconds = np.linspace(5, 200, 40).tolist()

    mock_load_bo.side_effect = [mock_bo_trace, b2]
    mock_discover_bo.side_effect = [[Path("bo1.json")], [Path("bo2.json")]]
    mock_load_comp.return_value = mock_comparison_data

    with patch("src.visualization.plots.convergence_curve.Path.is_dir", return_value=True):
        fig = generate(
            pbt_paths=["pbt_dir"],
            bo_paths=["bo1.json", "bo2.json"],
            comparison_path="comp.json",
            output_dir=tmp_output_dir, venue="preview"
        )
    from matplotlib.collections import PolyCollection
    for ax in fig.axes:
        fills = [c for c in ax.get_children() if isinstance(c, PolyCollection)]
        assert len(fills) >= 1

@patch("src.visualization.plots.convergence_curve.load_session")
@patch("src.visualization.plots.convergence_curve.load_bo_trace")
@patch("src.visualization.plots.convergence_curve.load_comparison")
def test_export_creates_files(mock_load_comp, mock_load_bo, mock_load_session, mock_pbt_session, mock_bo_trace, mock_comparison_data, tmp_output_dir):
    mock_load_session.return_value = mock_pbt_session
    mock_load_bo.return_value = mock_bo_trace
    mock_load_comp.return_value = mock_comparison_data
    
    generate(
        pbt_paths=["pbt1.json"],
        bo_paths=["bo1.json"],
        comparison_path="comp.json",
        output_dir=tmp_output_dir, venue="preview",
        formats=["pdf", "png"]
    )
    assert (Path(tmp_output_dir) / "convergence_curve.pdf").exists()
    assert (Path(tmp_output_dir) / "convergence_curve.png").exists()

@patch("src.visualization.plots.convergence_curve.load_session")
@patch("src.visualization.plots.convergence_curve.load_bo_trace")
def test_default_score_warning_when_no_comparison(mock_load_bo, mock_load_session, mock_pbt_session, mock_bo_trace, tmp_output_dir, caplog):
    mock_load_session.return_value = mock_pbt_session
    mock_load_bo.return_value = mock_bo_trace
    
    generate(
        pbt_paths=["pbt1.json"],
        bo_paths=["bo1.json"],
        comparison_path=None,
        output_dir=tmp_output_dir, venue="preview"
    )
    assert any("inferred" in r.message for r in caplog.records if r.levelname == "WARNING")

def test_registers_in_registry():
    spec = REGISTRY.get("convergence_curve")
    assert spec is not None
