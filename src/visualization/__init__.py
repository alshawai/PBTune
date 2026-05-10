"""
Public API for the visualization framework.

This framework handles rendering programmatic figures for the PBTune paper,
enforcing consistent styling, proper PVLDB sizing, and semantic color choices.
"""

from src.visualization.theme import PBTuneTheme
from src.visualization.colors import METHOD_COLORS, METRIC_COLORS, get_method_style
from src.visualization.registry import REGISTRY, register_figure
from src.visualization.types import FigureSpec, FigureSize, ExportFormat, VenuePreset
from src.visualization.export import export_figure
from src.visualization.exceptions import (
    VisualizationError,
    DataLoadError,
    InvalidSchemaError,
    FigureRegistryError,
)

__all__ = [
    "PBTuneTheme",
    "METHOD_COLORS",
    "METRIC_COLORS",
    "get_method_style",
    "REGISTRY",
    "register_figure",
    "FigureSpec",
    "FigureSize",
    "ExportFormat",
    "VenuePreset",
    "export_figure",
    "VisualizationError",
    "DataLoadError",
    "InvalidSchemaError",
    "FigureRegistryError",
]
