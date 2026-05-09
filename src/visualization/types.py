"""
Types and domain models for the visualization framework.
"""

from dataclasses import dataclass
from enum import Enum
from typing import Callable


class ExportFormat(Enum):
    """Supported export formats for figures."""

    PDF = "pdf"  # Vector - paper
    PNG = "png"  # Raster - preview
    SVG = "svg"  # Vector - web/presentations


@dataclass(frozen=True)
class VenuePreset:
    """Target publication venue dimensions and typography."""

    name: str
    single_col_width_in: float
    double_col_width_in: float
    base_font_size_pt: int
    font_family: str
    use_latex: bool


@dataclass(frozen=True)
class FigureSize:
    """Resolved figure dimensions."""

    width_in: float
    height_in: float

    @classmethod
    def single_column(cls, venue: VenuePreset, aspect: float = 0.75) -> "FigureSize":
        """Create a single-column sized figure."""
        return cls(
            width_in=venue.single_col_width_in,
            height_in=venue.single_col_width_in * aspect,
        )

    @classmethod
    def double_column(cls, venue: VenuePreset, aspect: float = 0.42) -> "FigureSize":
        """Create a double-column sized figure."""
        return cls(
            width_in=venue.double_col_width_in,
            height_in=venue.double_col_width_in * aspect,
        )


@dataclass
class FigureSpec:
    """Metadata for one registered figure."""

    fig_id: str  # e.g. "convergence_curve"
    paper_label: str  # e.g. "fig:convergence"
    title: str  # Human-readable title
    section: str  # Paper section: "evaluation", "methodology"
    category: str  # "convergence", "performance", "importance"
    size_hint: str  # "single" or "double" column
    generator: Callable  # Function that produces the figure
    data_requirements: list[str]  # ["session_json", "baseline_json"]
    description: str  # One-line description for catalog
