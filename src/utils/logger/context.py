"""
Color Context Helper for Consistent Terminal and Formatting Colors
===================================================================

Provides a bundled context of ALL available color codes and formatting codes
so they can be imported once at module level without repetition or runtime overhead.

The returned ColorContext respects the global colors_enabled() policy and can
be safely used across all modules.

Usage:

    from src.utils.logger.context import get_color_context

    colors = get_color_context()

    # In logging
    logger.info(f"{colors.bold}{colors.info}Starting...{colors.reset}")

    # In print statements
    print(f"{colors.warning}Caution:{colors.reset} Check logs")

    # In dynamic messages
    status = f"{colors.green}OK{colors.reset}" if success else f"{colors.red}FAIL{colors.reset}"

    # Access any color
    msg = f"{colors.cyan}{colors.bold}Important{colors.reset}"
"""

from dataclasses import dataclass

from src.utils.logger.colors import ColorCode, ColorPalette


@dataclass(frozen=True)
class ColorContext:
    """
    Immutable bundle of ALL available color codes and formatting for easy reuse.

    Includes all 19 color attributes from ColorCode plus all 5 log-level colors
    from ColorPalette, ensuring every color is accessible without repeated calls
    to color initialization functions.

    All attributes are computed at retrieval time based on the global
    colors_enabled() policy, so they automatically adapt to --no-color.
    """

    # Text styling
    reset: str
    bold: str
    italic: str
    underline: str

    # Log levels (ANSI)
    debug: str
    info: str
    warning: str
    error: str
    critical: str

    # Base colors
    gray: str
    violet: str
    magenta: str
    purple: str
    blue: str
    sky_blue: str
    cyan: str
    teal: str
    green: str
    lime: str
    yellow: str
    orange: str
    red: str
    pale_red: str


def get_color_context() -> ColorContext:
    """
    Factory function that returns a ColorContext with all available colors.

    The context respects the global colors_enabled() policy, so calling
    this after set_colors_enabled(False) will return a context with
    empty color strings.

    Returns
    -------
    ColorContext
        Immutable dataclass with all formatted color codes and text styling.
        Safe to use in f-strings, format calls, and concatenation.

    Example
    -------
    >>> colors = get_color_context()
    >>> msg = f"{colors.bold}{colors.info}Started{colors.reset}"
    >>> warning = f"{colors.warning}Alert{colors.reset}"
    """
    return ColorContext(
        reset=ColorCode.RESET,
        bold=ColorCode.BOLD,
        italic=ColorCode.ITALIC,
        underline=ColorCode.UNDERLINE,
        debug=ColorPalette.get_level_color("DEBUG", "ansi"),
        info=ColorPalette.get_level_color("INFO", "ansi"),
        warning=ColorPalette.get_level_color("WARNING", "ansi"),
        error=ColorPalette.get_level_color("ERROR", "ansi"),
        critical=ColorPalette.get_level_color("CRITICAL", "ansi"),
        gray=ColorCode.GRAY,
        violet=ColorCode.VIOLET,
        magenta=ColorCode.MAGENTA,
        purple=ColorCode.PURPLE,
        blue=ColorCode.BLUE,
        sky_blue=ColorCode.SKY_BLUE,
        cyan=ColorCode.CYAN,
        teal=ColorCode.TEAL,
        green=ColorCode.GREEN,
        lime=ColorCode.LIME,
        yellow=ColorCode.YELLOW,
        orange=ColorCode.ORANGE,
        red=ColorCode.RED,
        pale_red=ColorCode.PALE_RED,
    )
