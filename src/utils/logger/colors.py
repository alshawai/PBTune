"""
Color Definitions for Terminal and HTML Logging
================================================

Unified color palette ensuring consistency between ANSI terminal output
and HTML log files. Provides level-based, module-based, and worker-based
coloring for structured, visually differentiated log output.

Usage:

    from src.utils.logger.colors import ColorPalette, ColorCode

    # Get ANSI escape code for INFO level
    ansi_green = ColorPalette.get_level_color('INFO', 'ansi')

    # Get hex color for Worker-3
    hex_yellow = ColorPalette.get_worker_color(3, 'html')
"""

import hashlib
import colorsys


_COLORS_ENABLED = True


def set_colors_enabled(enabled: bool) -> None:
    """Enable or disable ANSI/HTML color generation at runtime."""
    global _COLORS_ENABLED
    _COLORS_ENABLED = enabled


def colors_enabled() -> bool:
    """Return whether runtime color generation is enabled."""
    return _COLORS_ENABLED


class ColorCodeMeta(type):
    _COLOR_ATTRS = {
        "RESET",
        "BOLD",
        "ITALIC",
        "UNDERLINE",
        "GRAY",
        "VIOLET",
        "MAGENTA",
        "PURPLE",
        "BLUE",
        "SKY_BLUE",
        "CYAN",
        "TEAL",
        "GREEN",
        "LIME",
        "YELLOW",
        "ORANGE",
        "RED",
        "PALE_RED",
    }

    def __getattribute__(cls, name: str):  # type: ignore[override]
        if name in type.__getattribute__(cls, "_COLOR_ATTRS") and not colors_enabled():
            return ""
        return type.__getattribute__(cls, name)


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

    _PRIMARY_MODULE_COLORS_RGB = {
        "pbtune": (40, 149, 255),
        "evaluator": (255, 145, 60),
        "analyzer": (123, 97, 255),
        "visualizer": (0, 204, 170),
    }

    @staticmethod
    def _rgb_to_ansi(r: int, g: int, b: int) -> str:
        """Convert RGB to ANSI 24-bit color code."""
        return f"\033[38;2;{r};{g};{b}m"

    @staticmethod
    def _rgb_to_hex(r: int, g: int, b: int) -> str:
        """Convert RGB to hex color code."""
        return f"#{r:02x}{g:02x}{b:02x}"

    @staticmethod
    def _hsl_to_rgb(
        seed: int,
        saturation: float = 0.7,
        lightness: float = 0.6,
    ) -> tuple[int, int, int]:
        """Generate a stable RGB color from a seed using golden-angle spacing."""
        hue = (seed * 137.5) % 360
        red, green, blue = colorsys.hls_to_rgb(hue / 360, lightness, saturation)
        return (int(red * 255), int(green * 255), int(blue * 255))

    @staticmethod
    def _stable_seed(text: str) -> int:
        """Create a stable integer seed from text."""
        digest = hashlib.sha256(text.encode("utf-8")).hexdigest()
        return int(digest[:8], 16)

    @classmethod
    def get_level_color(cls, level: str, format_type: str = "ansi") -> str:
        """Get color for log level."""
        if not colors_enabled():
            return ""
        rgb = cls._LEVEL_COLORS_RGB.get(level, (236, 240, 241))  # Default white
        if format_type == "ansi":
            return cls._rgb_to_ansi(*rgb)
        return cls._rgb_to_hex(*rgb)

    @classmethod
    def get_module_color(cls, module_name: str, format_type: str = "ansi") -> str:
        """Get a color for a logger module name."""
        if not colors_enabled():
            return ""
        module_lower = module_name.strip().lower()
        if module_lower.startswith("src."):
            module_lower = module_lower.removeprefix("src.")

        for primary_name, rgb in cls._PRIMARY_MODULE_COLORS_RGB.items():
            if module_lower == primary_name:
                if format_type == "ansi":
                    return f"\033[1m{cls._rgb_to_ansi(*rgb)}"  # Bold
                return cls._rgb_to_hex(*rgb)

        rgb = cls._hsl_to_rgb(cls._stable_seed(module_lower), saturation=0.62)

        if format_type == "ansi":
            return cls._rgb_to_ansi(*rgb)
        return cls._rgb_to_hex(*rgb)

    @classmethod
    def get_worker_color(cls, worker_id: int, format_type: str = "ansi") -> str:
        """
        Get a deterministic color for a worker ID.

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
        if not colors_enabled():
            return ""
        rgb = cls._hsl_to_rgb(worker_id)

        if format_type == "ansi":
            return cls._rgb_to_ansi(*rgb)
        return cls._rgb_to_hex(*rgb)


class ColorCode(metaclass=ColorCodeMeta):
    """ANSI control codes for terminal formatting."""

    RESET = "\033[0m"
    BOLD = "\033[1m"
    ITALIC = "\033[3m"
    UNDERLINE = "\033[4m"
    GRAY = "\033[38;5;240m"
    VIOLET = "\033[38;5;141m"
    MAGENTA = "\033[38;5;171m"
    PURPLE = "\033[95m"
    BLUE = "\033[94m"
    SKY_BLUE = "\033[38;5;39m"
    CYAN = "\033[96m"
    TEAL = "\033[38;5;37m"
    GREEN = "\033[92m"
    LIME = "\033[38;5;84m"
    YELLOW = "\033[93m"
    ORANGE = "\033[38;5;208m"
    RED = "\033[91m"
    PALE_RED = "\033[38;5;203m"
