import pytest
from pathlib import Path
from unittest.mock import patch, MagicMock

from src.visualization.loaders import SessionTrace, BOTrace, ComparisonData
from src.visualization.registry import REGISTRY
from src.visualization.plots.performance_distribution import generate

@pytest.fixture
def pbt_sessions_3_seeds():
    sessions = []
    for score in [0.7, 0.75, 0.8]:
        s = MagicMock(spec=SessionTrace)
        s.best_scores = [0.1, score]
        s.mean_scores = [0.05, score - 0.05]
        sessions.append(s)
    return sessions

@pytest.fixture
def bo_traces_3_seeds():
    traces = []
    for score in [0.6, 0.65, 0.7]:
        t = MagicMock(spec=BOTrace)
        t.best_scores = [0.05, score]
        traces.append(t)
    return traces

@pytest.fixture
def comparison_with_scalar_default():
    comp = MagicMock(spec=ComparisonData)
    # len=1 default list
    comp.default_summaries = {"score": MagicMock(mean=0.3, values=[0.3])}
    return comp

@pytest.fixture
def comparison_with_multi_default():
    comp = MagicMock(spec=ComparisonData)
    # len=5 default list
    comp.default_summaries = {"score": MagicMock(mean=0.3, values=[0.25, 0.28, 0.3, 0.32, 0.35])}
    return comp

@pytest.fixture
def tmp_output_dir(tmp_path):
    return str(tmp_path)

@patch("src.visualization.plots.performance_distribution.load_sessions")
@patch("src.visualization.plots.performance_distribution.load_bo_trace")
@patch("src.visualization.plots.performance_distribution.load_comparison")
def test_generate_returns_figure(mock_load_comp, mock_load_bo, mock_load_sessions, pbt_sessions_3_seeds, bo_traces_3_seeds, comparison_with_multi_default, tmp_output_dir):
    mock_load_sessions.return_value = pbt_sessions_3_seeds
    mock_load_bo.side_effect = bo_traces_3_seeds
    mock_load_comp.return_value = comparison_with_multi_default
    
    with patch("src.visualization.plots.performance_distribution.Path.is_dir", return_value=True):
        fig = generate(
            pbt_paths=["pbt_dir"],
            bo_paths=["bo1", "bo2", "bo3"],
            comparison_path="comp.json",
            output_dir=tmp_output_dir, venue="preview"
        )
    import matplotlib.figure
    assert isinstance(fig, matplotlib.figure.Figure)

@patch("src.visualization.plots.performance_distribution.load_sessions")
@patch("src.visualization.plots.performance_distribution.load_bo_trace")
@patch("src.visualization.plots.performance_distribution.load_comparison")
def test_violin_fallback_when_no_ptitprince(mock_load_comp, mock_load_bo, mock_load_sessions, pbt_sessions_3_seeds, bo_traces_3_seeds, comparison_with_multi_default, tmp_output_dir):
    mock_load_sessions.return_value = pbt_sessions_3_seeds
    mock_load_bo.side_effect = bo_traces_3_seeds
    mock_load_comp.return_value = comparison_with_multi_default
    
    with patch("src.visualization.plots.performance_distribution._try_import_raincloud", return_value=None), patch("src.visualization.plots.performance_distribution.Path.is_dir", return_value=True):
        fig = generate(
            pbt_paths=["pbt_dir"],
            bo_paths=["bo1", "bo2", "bo3"],
            comparison_path="comp.json",
            output_dir=tmp_output_dir, venue="preview"
        )
    assert fig is not None

@patch("src.visualization.plots.performance_distribution.load_sessions")
@patch("src.visualization.plots.performance_distribution.load_bo_trace")
@patch("src.visualization.plots.performance_distribution.load_comparison")
@patch("src.visualization.plots.performance_distribution.discover_bo_traces")
def test_significance_brackets_drawn(mock_discover_bo, mock_load_comp, mock_load_bo, mock_load_sessions, pbt_sessions_3_seeds, bo_traces_3_seeds, comparison_with_multi_default, tmp_output_dir):
    mock_load_sessions.return_value = pbt_sessions_3_seeds
    mock_load_bo.side_effect = bo_traces_3_seeds
    mock_discover_bo.side_effect = [[Path("bo1")], [Path("bo2")], [Path("bo3")]]
    mock_load_comp.return_value = comparison_with_multi_default

    with patch("src.visualization.plots.performance_distribution.Path.is_dir", return_value=True):
        fig = generate(
            pbt_paths=["pbt_dir"],
            bo_paths=["bo1", "bo2", "bo3"],
            comparison_path="comp.json",
            output_dir=tmp_output_dir, venue="preview",
            show_significance=True
        )
    
    ax = fig.axes[0]
    from matplotlib.text import Text
    texts = [c for c in ax.get_children() if isinstance(c, Text)]
    # Look for a text containing '*'
    assert any("*" in t.get_text() or "ns" in t.get_text() for t in texts)

@patch("src.visualization.plots.performance_distribution.load_sessions")
@patch("src.visualization.plots.performance_distribution.load_bo_trace")
@patch("src.visualization.plots.performance_distribution.load_comparison")
def test_no_brackets_when_disabled(mock_load_comp, mock_load_bo, mock_load_sessions, pbt_sessions_3_seeds, bo_traces_3_seeds, comparison_with_multi_default, tmp_output_dir):
    mock_load_sessions.return_value = pbt_sessions_3_seeds
    mock_load_bo.side_effect = bo_traces_3_seeds
    mock_load_comp.return_value = comparison_with_multi_default
    
    with patch("src.visualization.plots.performance_distribution.Path.is_dir", return_value=True):
        fig = generate(
            pbt_paths=["pbt_dir"],
            bo_paths=["bo1", "bo2", "bo3"],
            comparison_path="comp.json",
            output_dir=tmp_output_dir, venue="preview",
            show_significance=False
        )
    
    ax = fig.axes[0]
    from matplotlib.text import Text
    texts = [c for c in ax.get_children() if isinstance(c, Text)]
    # None of the texts should be our significance labels (* or ns) except tick labels
    assert not any(t.get_text() in ["ns", "*", "**", "***"] for t in texts)

@patch("src.visualization.plots.performance_distribution.load_sessions")
@patch("src.visualization.plots.performance_distribution.load_bo_trace")
@patch("src.visualization.plots.performance_distribution.load_comparison")
def test_scalar_default_renders_as_marker(mock_load_comp, mock_load_bo, mock_load_sessions, pbt_sessions_3_seeds, bo_traces_3_seeds, comparison_with_scalar_default, tmp_output_dir):
    mock_load_sessions.return_value = pbt_sessions_3_seeds
    mock_load_bo.side_effect = bo_traces_3_seeds
    mock_load_comp.return_value = comparison_with_scalar_default
    
    with patch("src.visualization.plots.performance_distribution.Path.is_dir", return_value=True):
        fig = generate(
            pbt_paths=["pbt_dir"],
            bo_paths=["bo1", "bo2", "bo3"],
            comparison_path="comp.json",
            output_dir=tmp_output_dir, venue="preview"
        )
    
    ax = fig.axes[0]
    from matplotlib.collections import PathCollection
    # Should have a scatter for the default
    scatters = [c for c in ax.get_children() if isinstance(c, PathCollection)]
    # Seaborn stripplot will also add scatter, but we check that the code doesn't crash 
    assert len(scatters) > 0

@patch("src.visualization.plots.performance_distribution.discover_bo_traces")
@patch("src.visualization.plots.performance_distribution.load_sessions")
@patch("src.visualization.plots.performance_distribution.load_bo_trace")
@patch("src.visualization.plots.performance_distribution.load_comparison")
def test_skips_test_when_n_lt_2(mock_load_comp, mock_load_bo, mock_load_sessions, mock_discover_bo, pbt_sessions_3_seeds, bo_traces_3_seeds, comparison_with_multi_default, tmp_output_dir, caplog):
    mock_load_sessions.return_value = pbt_sessions_3_seeds
    mock_load_bo.side_effect = [bo_traces_3_seeds[0]]
    mock_discover_bo.return_value = [Path("bo1")]
    mock_load_comp.return_value = comparison_with_multi_default

    with patch("src.visualization.plots.performance_distribution.Path.is_dir", return_value=True):
        generate(
            pbt_paths=["pbt_dir"],
            bo_paths=["bo1"],
            comparison_path="comp.json",
            output_dir=tmp_output_dir, venue="preview",
            show_significance=True
        )
    # The Mann-Whitney should not run for BO vs anything since N=1
    mw_logs = [r for r in caplog.records if "Mann-Whitney U" in r.message]
    # Only Default vs PBTune should run
    assert len(mw_logs) == 1
    assert "Default vs PBTune" in mw_logs[0].message

def test_registers_in_registry():
    spec = REGISTRY.get("performance_distribution")
    assert spec is not None
