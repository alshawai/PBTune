"""
Data loaders for the visualization framework.

This layer transforms raw JSON / result objects into numpy arrays ready for plotting,
isolating the plot implementations from evolving JSON schemas.
"""

from src.visualization.loaders.session import load_session, load_sessions, SessionTrace, RAW_METRIC_KEYS
from src.visualization.loaders.multi_seed import aggregate_seeds, MultiSeedAggregate
from src.visualization.loaders.comparison import (
    load_comparison,
    ComparisonData,
    load_multi_arm_comparison,
    MultiArmComparisonData,
)
from src.visualization.loaders.importance import (
    load_importance,
    load_importance_from_dir,
    ImportanceData,
)
from src.visualization.loaders.tier_diagnostics import (
    load_tier_diagnostics,
    TierDiagnostics,
)
from src.visualization.loaders.baseline import load_bo_trace, BOTrace
from src.visualization.loaders.discovery import (
    discover_session_traces,
    discover_bo_traces,
    SESSION_TRACE_GLOBS,
    BO_TRACE_GLOBS,
)

__all__ = [
    "load_session",
    "load_sessions",
    "SessionTrace",
    "RAW_METRIC_KEYS",
    "aggregate_seeds",
    "MultiSeedAggregate",
    "load_comparison",
    "ComparisonData",
    "load_multi_arm_comparison",
    "MultiArmComparisonData",
    "load_importance",
    "load_importance_from_dir",
    "ImportanceData",
    "load_tier_diagnostics",
    "TierDiagnostics",
    "load_bo_trace",
    "BOTrace",
    "discover_session_traces",
    "discover_bo_traces",
    "SESSION_TRACE_GLOBS",
    "BO_TRACE_GLOBS",
]
