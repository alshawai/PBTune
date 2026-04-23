"""
Color Definitions for Terminal and HTML Logging
================================================

Unified color palette ensuring consistency between ANSI terminal output
and HTML log files. Provides level-based, module-based, and worker-based
coloring for structured, visually differentiated log output.

Usage::

    from src.utils.logger.colors import ColorPalette, ColorCode

    # Get ANSI escape code for INFO level
    ansi_green = ColorPalette.get_level_color('INFO', 'ansi')

    # Get hex color for Worker-3
    hex_yellow = ColorPalette.get_worker_color(3, 'html')
"""

import colorsys
from enum import Enum


class ModuleName(Enum):
    """Module name identifiers for color mapping."""

    MAIN = "main"
    EVALUATOR = "evaluator"
    APPLICATOR = "applicator"
    POPULATION = "population"
    WORKER = "worker"
    RESTART = "restart"
    INSTANCE = "instance"
    EVOLUTION = "evolution"
    SNAPSHOT = "snapshot"


class ColorPalette:
    """
    Unified color palette for consistent colors across ANSI (terminal) and HTML.

    This ensures that a given semantic color (e.g., INFO, Worker-0) appears
    the same in both console logs and HTML output.
    """

    _LEVEL_COLORS_RGB = {
        "DEBUG": (26, 142, 188),  # Cyan
        "INFO": (46, 204, 113),  # Green
        "WARNING": (243, 156, 18),  # Orange
        "ERROR": (231, 76, 60),  # Red
        "CRITICAL": (155, 89, 182),  # Purple
    }

    _MODULE_COLORS_RGB = {
        ModuleName.MAIN: (52, 152, 219),  # Blue
        ModuleName.EVALUATOR: (26, 188, 156),  # Teal
        ModuleName.APPLICATOR: (155, 89, 182),  # Purple
        ModuleName.POPULATION: (46, 204, 113),  # Green
        ModuleName.WORKER: (241, 196, 15),  # Yellow
        ModuleName.RESTART: (230, 126, 34),  # Orange
        ModuleName.INSTANCE: (52, 231, 228),  # Bright Cyan
        ModuleName.EVOLUTION: (175, 122, 197),  # Light Purple
        ModuleName.SNAPSHOT: (233, 30, 99),  # Pink/Magenta
    }

    _WORKER_COLORS_BASE_RGB = [
        (52, 152, 219),  # Blue (Worker-0)
        (46, 204, 113),  # Green (Worker-1)
        (0, 188, 212),  # Cyan (Worker-2)
        (241, 196, 15),  # Yellow (Worker-3)
        (233, 30, 99),  # Pink/Magenta (Worker-4)
        (231, 76, 60),  # Red (Worker-5)
        (236, 240, 241),  # White (Worker-6)
        (149, 165, 166),  # Gray (Worker-7)
    ]

    @staticmethod
    def _rgb_to_ansi(r: int, g: int, b: int) -> str:
        """Convert RGB to ANSI 24-bit color code."""
        return f"\033[38;2;{r};{g};{b}m"

    @staticmethod
    def _rgb_to_hex(r: int, g: int, b: int) -> str:
        """Convert RGB to hex color code."""
        return f"#{r:02x}{g:02x}{b:02x}"

    @classmethod
    def get_level_color(cls, level: str, format_type: str = "ansi") -> str:
        """Get color for log level."""
        rgb = cls._LEVEL_COLORS_RGB.get(level, (236, 240, 241))  # Default white
        if format_type == "ansi":
            return cls._rgb_to_ansi(*rgb)
        return cls._rgb_to_hex(*rgb)

    @classmethod
    def get_module_color(cls, module_name: str, format_type: str = "ansi") -> str:
        """Get color for module name."""
        module_lower = module_name.lower()

        # Detect module type
        for module_type in ModuleName:
            if module_type.value in module_lower or (
                module_type == ModuleName.MAIN and "__main__" in module_lower
            ):
                rgb = cls._MODULE_COLORS_RGB[module_type]
                if format_type == "ansi":
                    return f"\033[1m{cls._rgb_to_ansi(*rgb)}"  # Bold
                return cls._rgb_to_hex(*rgb)

        # Default color for unknown modules
        if format_type == "ansi":
            return "\033[37m"  # White
        return "#ecf0f1"  # Light gray

    @classmethod
    def get_worker_color(cls, worker_id: int, format_type: str = "ansi") -> str:
        """
        Get color for worker ID with dynamic generation for >8 workers.

        For workers 0-7: Use predefined colors (optimized contrast)
        For workers 8+:  Generate HSL-based colors dynamically

        Parameters
        ----------
        worker_id : int
            Worker identifier (0-indexed)
        format_type : str
            'ansi' for terminal, 'html' for HTML output

        Returns
        -------
        str
            ANSI color code or hex color
        """
        # Use predefined colors for first 8 workers
        if worker_id < len(cls._WORKER_COLORS_BASE_RGB):
            rgb = cls._WORKER_COLORS_BASE_RGB[worker_id]
        else:
            # Generate color dynamically using HSL
            hue = (worker_id * 137.5) % 360  # Golden angle for good distribution
            saturation = 0.7
            lightness = 0.6

            # Convert HSL to RGB
            r, g, b = colorsys.hls_to_rgb(hue / 360, lightness, saturation)
            rgb = (int(r * 255), int(g * 255), int(b * 255))

        if format_type == "ansi":
            return cls._rgb_to_ansi(*rgb)
        return cls._rgb_to_hex(*rgb)


class ColorCode:
    """ANSI control codes for terminal formatting."""

    RESET = "\033[0m"
    BOLD = "\033[1m"
    HEADER = "\033[95m"
    OKBLUE = "\033[94m"
    OKCYAN = "\033[96m"
    OKGREEN = "\033[92m"
    WARNING = "\033[93m"
    FAIL = "\033[91m"
    UNDERLINE = "\033[4m"
