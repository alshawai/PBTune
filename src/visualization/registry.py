"""
Central registry for tracking, discovering, and generating registered figures.
"""

import importlib
import pkgutil
from pathlib import Path

from src.visualization.types import FigureSpec
from src.visualization.exceptions import FigureRegistryError
from src.utils.logger import get_logger

LOGGER = get_logger("Registry")


class FigureRegistry:
    """Catalog of all paper figures with metadata and generators."""

    def __init__(self):
        self._figures: dict[str, FigureSpec] = {}

    def register(self, spec: FigureSpec) -> None:
        """Register a new figure specification."""
        if spec.fig_id in self._figures:
            LOGGER.warning("Overwriting existing figure registration: %s", spec.fig_id)
        self._figures[spec.fig_id] = spec
        LOGGER.debug("Registered figure '%s' (%s)", spec.fig_id, spec.category)

    def get(self, fig_id: str) -> FigureSpec:
        """Get a figure specification by its ID."""
        if fig_id not in self._figures:
            raise FigureRegistryError(f"Figure '{fig_id}' not found in registry.")
        return self._figures[fig_id]

    def list_all(self) -> list[FigureSpec]:
        """Return all registered figure specifications."""
        return list(self._figures.values())

    def list_by_category(self, category: str) -> list[FigureSpec]:
        """Return figures matching a specific category."""
        return [f for f in self._figures.values() if f.category == category]

    def list_by_section(self, section: str) -> list[FigureSpec]:
        """Return figures belonging to a specific paper section."""
        return [f for f in self._figures.values() if f.section == section]

    def _discover_plots(self) -> None:
        """
        Auto-discover and load all modules in the src.visualization.plots package.
        This triggers their module-level register_figure() calls.
        """
        try:
            import src.visualization.plots as plots_pkg

            pkg_path = Path(plots_pkg.__file__).parent

            for _, module_name, _ in pkgutil.iter_modules([str(pkg_path)]):
                full_module_name = f"src.visualization.plots.{module_name}"
                try:
                    importlib.import_module(full_module_name)
                except Exception as e:
                    LOGGER.error(
                        "Failed to load plot module %s: %s", full_module_name, e
                    )

        except ImportError:
            LOGGER.warning(
                "Could not import src.visualization.plots for auto-discovery."
            )


# Global singleton instance
REGISTRY = FigureRegistry()


def register_figure(spec: FigureSpec) -> None:
    """Convenience function for plot modules to register figures."""
    REGISTRY.register(spec)
