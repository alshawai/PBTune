"""
Custom exception hierarchy for the visualization framework.
"""


class VisualizationError(Exception):
    """Base exception for all visualization framework errors."""


class DataLoadError(VisualizationError):
    """Raised when a data loader fails to read or parse a required file."""


class InvalidSchemaError(DataLoadError):
    """Raised when loaded data is missing expected keys or has an invalid structure."""


class FigureRegistryError(VisualizationError):
    """Raised for figure registry lookup failures."""
