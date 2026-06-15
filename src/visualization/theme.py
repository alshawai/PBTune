"""
Publication theme engine for ensuring consistent, venue-specific styling.
"""

from contextlib import contextmanager
from typing import Any, Iterator, Optional

import matplotlib.pyplot as plt
from matplotlib.axes import Axes
from matplotlib.figure import Figure

from src.visualization.types import VenuePreset, FigureSize
from src.utils.logger import get_logger

LOGGER = get_logger("Theme")


class PBTuneTheme:
    """
    Publication-quality matplotlib theme engine.
    Ensures figures are correctly sized for target venues with consistent styling.
    """

    VENUE_PRESETS: dict[str, VenuePreset] = {
        "pvldb": VenuePreset(
            name="pvldb",
            single_col_width_in=3.33,
            double_col_width_in=7.00,
            base_font_size_pt=9,
            font_family="serif",
            use_latex=True,
        ),
        "springer": VenuePreset(
            name="springer",
            single_col_width_in=3.39,
            double_col_width_in=6.85,
            base_font_size_pt=10,
            font_family="serif",
            use_latex=True,
        ),
        "preview": VenuePreset(
            name="preview",
            single_col_width_in=5.0,
            double_col_width_in=10.0,
            base_font_size_pt=11,
            font_family="sans-serif",
            use_latex=False,
        ),
    }

    def __init__(self, venue: str = "pvldb"):
        if venue not in self.VENUE_PRESETS:
            LOGGER.warning("Unknown venue preset %s, falling back to 'preview'.", venue)
            venue = "preview"
        self.preset = self.VENUE_PRESETS[venue]

        # Check for LaTeX availability if requested
        if self.preset.use_latex:
            import shutil

            if not shutil.which("latex"):
                LOGGER.warning(
                    "LaTeX requested by venue preset but 'latex' not found in PATH. "
                    "Falling back to matplotlib mathtext."
                )
                # Create a modified preset without latex
                self.preset = VenuePreset(
                    name=self.preset.name,
                    single_col_width_in=self.preset.single_col_width_in,
                    double_col_width_in=self.preset.double_col_width_in,
                    base_font_size_pt=self.preset.base_font_size_pt,
                    font_family=self.preset.font_family,
                    use_latex=False,
                )

    def rc_params(self) -> dict[str, Any]:
        """Generate the full rcParams dictionary for this theme."""
        params = {
            # Typography
            "font.family": self.preset.font_family,
            "font.size": self.preset.base_font_size_pt,
            "axes.titlesize": self.preset.base_font_size_pt + 1,
            "axes.labelsize": self.preset.base_font_size_pt,
            "xtick.labelsize": self.preset.base_font_size_pt - 1,
            "ytick.labelsize": self.preset.base_font_size_pt - 1,
            "legend.fontsize": self.preset.base_font_size_pt - 1,
            "legend.title_fontsize": self.preset.base_font_size_pt,
            # Lines and markers
            "lines.linewidth": 1.5,
            "lines.markersize": 5,
            "lines.markeredgewidth": 0.5,
            # Axes and Spines
            "axes.linewidth": 0.8,
            "axes.spines.top": False,
            "axes.spines.right": False,
            "axes.axisbelow": True,  # Grid behind data
            # Grid
            "grid.linestyle": "--",
            "grid.linewidth": 0.5,
            "grid.alpha": 0.5,
            # Ticks
            "xtick.major.width": 0.8,
            "ytick.major.width": 0.8,
            "xtick.minor.width": 0.5,
            "ytick.minor.width": 0.5,
            # Legend
            "legend.frameon": False,
            "legend.loc": "best",
            # Export
            "savefig.bbox": "tight",
            "savefig.pad_inches": 0.05,
            "savefig.dpi": 300,
        }

        if self.preset.use_latex:
            params.update(
                {
                    "text.usetex": True,
                    "text.latex.preamble": r"\usepackage{amsmath} \usepackage{amssymb} \usepackage{xspace}",
                    "font.serif": ["Times", "Computer Modern Roman"],
                }
            )
        else:
            params.update(
                {
                    "text.usetex": False,
                    "mathtext.fontset": "stix",  # Good fallback that looks like Times
                }
            )
            if self.preset.font_family == "serif":
                params["font.serif"] = ["Times New Roman", "DejaVu Serif"]
            else:
                params["font.sans-serif"] = ["Arial", "Helvetica", "DejaVu Sans"]

        return params

    @contextmanager
    def apply(self) -> Iterator[None]:
        """
        Context manager to apply the theme for a specific plot block.
        Restores previous rcParams when exiting.
        """
        with plt.rc_context(self.rc_params()):
            yield

    def get_figure_size(
        self, size_hint: str, aspect: Optional[float] = None
    ) -> FigureSize:
        """Resolve the requested size hint into actual dimensions."""
        if size_hint == "single":
            aspect_ratio = aspect if aspect is not None else 0.75
            return FigureSize.single_column(self.preset, aspect=aspect_ratio)
        elif size_hint == "double":
            aspect_ratio = aspect if aspect is not None else 0.42
            return FigureSize.double_column(self.preset, aspect=aspect_ratio)
        else:
            LOGGER.warning("Unknown size_hint %s, defaulting to 'single'.", size_hint)
            return FigureSize.single_column(self.preset)

    def figure(
        self, size_hint: str = "single", aspect: Optional[float] = None, **kwargs
    ) -> tuple[Figure, Axes]:
        """
        Create a new figure and single axes with the correct dimensions.
        Must be called within a `with theme.apply():` block.
        """
        size = self.get_figure_size(size_hint, aspect)
        fig, ax = plt.subplots(figsize=(size.width_in, size.height_in), **kwargs)
        return fig, ax

    def subplots(
        self,
        nrows: int,
        ncols: int,
        size_hint: str = "single",
        aspect: Optional[float] = None,
        **kwargs,
    ) -> tuple[Figure, Any]:
        """
        Create a new figure and grid of subplots with the correct dimensions.
        Must be called within a `with theme.apply():` block.

        The second element is an ``np.ndarray`` of :class:`Axes` for
        ``nrows*ncols > 1`` and a single :class:`Axes` otherwise. The return
        is typed as ``Any`` because numpy's stubs cannot express
        "ndarray of Axes" without introducing recursive ndarray narrowing
        that breaks attribute access on indexed elements.
        """
        size = self.get_figure_size(size_hint, aspect)
        fig, axes = plt.subplots(
            nrows=nrows, ncols=ncols, figsize=(size.width_in, size.height_in), **kwargs
        )
        return fig, axes
